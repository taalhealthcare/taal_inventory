// ── Sales Invoice — FIFO Rate Fetch on Item Selection ────────
frappe.ui.form.on("Sales Invoice Item", {

    item_code: function(frm, cdt, cdn) {
        var row = locals[cdt][cdn];
        if (!row.item_code) return;

        // Fetch FIFO batch rate from server
        frappe.call({
            method: "taal_inventory.events.sales_invoice.get_fifo_rate",
            args: { item_code: row.item_code },
            callback: function(r) {
                if (!r.message) return;

                if (r.message.blocked) {
                    // No valid stock — block this item
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

                if (r.message.rate && r.message.rate > 0) {
                    // Set rate from oldest FIFO batch
                    frappe.model.set_value(
                        cdt, cdn, "rate", r.message.rate);
                    frappe.model.set_value(
                        cdt, cdn, "price_list_rate", r.message.rate);

                    frappe.show_alert({
                        message: "Rate ₹" + r.message.rate
                            + " from batch: "
                            + r.message.batch_id,
                        indicator: "green"
                    }, 4);
                }
            }
        });
    },

    qty: function(frm, cdt, cdn) {
        // Amount auto-calculated by ERPNext natively
        // No action needed here
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
                            message: "Added via barcode: "
                                + item_code,
                            indicator: "green"
                        }, 3);
                    }
                });
            }
        }
    });
}