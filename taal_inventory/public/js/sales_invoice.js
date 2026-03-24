// ── Sales Invoice — FEFO Rate + Qty Validation ───────────────

frappe.ui.form.on("Sales Invoice Item", {

    item_code: function(frm, cdt, cdn) {
        var row = locals[cdt][cdn];
        if (!row.item_code) return;

        // Fetch nearest expiry batch rate (FEFO)
        frappe.call({
            method: "taal_inventory.events.sales_invoice.get_fifo_rate",
            args: { item_code: row.item_code },
            callback: function(r) {
                if (!r.message) return;

                // Block if no valid stock
                if (r.message.blocked) {
                    frappe.msgprint({
                        title: "No Stock Available",
                        message: "All batches for <b>"
                            + row.item_code
                            + "</b> are expired or exhausted.",
                        indicator: "red"
                    });
                    frappe.model.set_value(
                        cdt, cdn, "item_code", "");
                    return;
                }

                var rate          = r.message.rate          || 0;
                var remaining_qty = r.message.remaining_qty || 0;
                var batch_id      = r.message.batch_id      || "";
                var expiry_date   = r.message.expiry_date   || "";

                // Set rate from nearest expiry batch
                if (rate > 0) {
                    // Delay needed — ERPNext native pricing engine
                    // runs after item_code trigger and overrides rate.
                    // setTimeout ensures our FEFO rate sets last.
                    setTimeout(function() {
                        frappe.model.set_value(
                            cdt, cdn, "rate", rate);
                        frappe.model.set_value(
                            cdt, cdn, "price_list_rate", rate);
                        frm.refresh_field("items");
                    }, 800);
                }
                // Show batch info to user
                frappe.show_alert({
                    message: "Batch: <b>" + batch_id
                        + "</b>  |  Rate: ₹" + rate
                        + "  |  Expiry: " + expiry_date
                        + "  |  Available: " + remaining_qty,
                    indicator: "blue"
                }, 6);

                // Store total available qty for validation
                frappe.call({
                    method: "taal_inventory.events.sales_invoice.get_total_available_qty",
                    args: { item_code: row.item_code },
                    callback: function(qr) {
                        if (qr.message) {
                            // Store in row for qty validation
                            frappe.model.set_value(
                                cdt, cdn,
                                "__available_qty",
                                qr.message.total_qty
                            );
                        }
                    }
                });
            }
        });
    },

    qty: function(frm, cdt, cdn) {
        var row = locals[cdt][cdn];
        if (!row.item_code || !row.qty) return;

        // Validate qty against available stock
        frappe.call({
            method: "taal_inventory.events.sales_invoice.get_total_available_qty",
            args: { item_code: row.item_code },
            callback: function(r) {
                if (!r.message) return;
                var available = r.message.total_qty || 0;

                if (row.qty > available) {
                    frappe.msgprint({
                        title: "Insufficient Stock",
                        message: "You entered <b>" + row.qty
                            + "</b> but only <b>"
                            + available
                            + "</b> units are available for <b>"
                            + row.item_code + "</b>.<br><br>"
                            + "Quantity has been capped at "
                            + available + ".",
                        indicator: "orange"
                    });
                    // Cap qty at available
                    frappe.model.set_value(
                        cdt, cdn, "qty", available);
                }
            }
        });
    }
});

// ── Barcode Scanner ───────────────────────────────────────────
frappe.ui.form.on("Sales Invoice", {
    refresh: function(frm) {
        if (!frm.doc.docstatus) {
            frm.add_custom_button("Scan Barcode", function() {
                frappe.prompt(
                    [{
                        label: "Scan or Type Barcode",
                        fieldname: "barcode",
                        fieldtype: "Data",
                        reqd: 1,
                        description: "Scan with device or type manually"
                    }],
                    function(values) {
                        scan_and_add_item(frm, values.barcode);
                    },
                    "Barcode Scanner",
                    "Add Item"
                );
            }, "Actions");
        }
    }
});

function scan_and_add_item(frm, barcode) {
    if (!barcode) return;

    frappe.call({
        method: "frappe.client.get_list",
        args: {
            doctype: "Item Barcode",
            filters: { barcode: barcode },
            fields: ["parent", "barcode"],
            limit: 1
        },
        callback: function(r) {
            if (!r.message || r.message.length === 0) {
                frappe.msgprint({
                    title: "Barcode Not Found",
                    message: "No item found for barcode: <b>"
                        + barcode + "</b>.",
                    indicator: "red"
                });
                return;
            }

            var item_code = r.message[0].parent;
            var existing  = false;

            (frm.doc.items || []).forEach(function(row) {
                if (row.item_code === item_code) {
                    frappe.model.set_value(
                        row.doctype, row.name,
                        "qty", (row.qty || 0) + 1
                    );
                    existing = true;
                    frappe.show_alert({
                        message: "Qty +1: " + row.item_name,
                        indicator: "green"
                    }, 3);
                }
            });

            if (!existing) {
                frappe.call({
                    method: "taal_inventory.events.sales_invoice.get_fifo_rate",
                    args: { item_code: item_code },
                    callback: function(ir) {
                        if (ir.message && ir.message.blocked) {
                            frappe.msgprint({
                                title: "No Stock",
                                message: "No valid stock for "
                                    + item_code,
                                indicator: "red"
                            });
                            return;
                        }
                        var rate = (ir.message && ir.message.rate)
                            ? ir.message.rate : 0;
                        var child = frm.add_child("items");
                        frappe.model.set_value(
                            child.doctype, child.name, {
                                "item_code": item_code,
                                "qty":       1,
                                "rate":      rate
                            }
                        );
                        frm.refresh_field("items");
                        frappe.show_alert({
                            message: "Added: " + item_code
                                + " @ ₹" + rate,
                            indicator: "green"
                        }, 3);
                    }
                });
            }
        }
    });
}