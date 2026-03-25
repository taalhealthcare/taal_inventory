// ── Sales Invoice — FEFO Rate + Qty Validation ───────────────
// Strategy: store FEFO rate in frm.__fefo_rates map
// Hook into price_list_rate trigger to override ERPNext
// every time it tries to reset rate from Item Price

frappe.ui.form.on("Sales Invoice", {

    onload: function(frm) {
        if (!frm.doc.docstatus) {
            frm.set_value("ignore_pricing_rule", 1);
        }
        // Initialize FEFO rate storage map on form
        if (!frm.__fefo_rates) {
            frm.__fefo_rates = {};
        }
    },

    refresh: function(frm) {
        if (!frm.__fefo_rates) {
            frm.__fefo_rates = {};
        }
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

frappe.ui.form.on("Sales Invoice Item", {

    item_code: function(frm, cdt, cdn) {
        var row = locals[cdt][cdn];
        if (!row.item_code) return;

        // Clear stored rate for this row when item changes
        if (frm.__fefo_rates) {
            delete frm.__fefo_rates[cdn];
        }

        fetch_fefo_rate(frm, cdt, cdn, row.item_code);
    },

    // ── THIS IS THE KEY FIX ──────────────────────────────────
    // ERPNext calls set_value("price_list_rate") after
    // get_item_details API returns. We intercept it here
    // and immediately override with our stored FEFO rate.
    price_list_rate: function(frm, cdt, cdn) {
        var fefo_rate = frm.__fefo_rates
            && frm.__fefo_rates[cdn];
        if (!fefo_rate || fefo_rate <= 0) return;

        var row = locals[cdt][cdn];
        // Only override if ERPNext is trying to set wrong rate
        if (row.price_list_rate !== fefo_rate) {
            frappe.model.set_value(
                cdt, cdn, "price_list_rate", fefo_rate);
            frappe.model.set_value(
                cdt, cdn, "rate", fefo_rate);
        }
    },

    // Also intercept rate changes from ERPNext
    rate: function(frm, cdt, cdn) {
        var fefo_rate = frm.__fefo_rates
            && frm.__fefo_rates[cdn];
        if (!fefo_rate || fefo_rate <= 0) return;

        var row = locals[cdt][cdn];
        if (row.rate !== fefo_rate) {
            frappe.model.set_value(
                cdt, cdn, "rate", fefo_rate);
        }
    },

    qty: function(frm, cdt, cdn) {
        var row = locals[cdt][cdn];
        if (!row.item_code || !row.qty) return;

        // Re-apply stored FEFO rate — qty trigger
        // causes ERPNext to recalculate pricing
        var fefo_rate = frm.__fefo_rates
            && frm.__fefo_rates[cdn];
        if (fefo_rate && fefo_rate > 0) {
            frappe.model.set_value(
                cdt, cdn, "rate", fefo_rate);
            frappe.model.set_value(
                cdt, cdn, "price_list_rate", fefo_rate);
        }

        validate_qty(frm, cdt, cdn, row);
    }
});

function fetch_fefo_rate(frm, cdt, cdn, item_code) {
    frappe.call({
        method: "taal_inventory.events.sales_invoice.get_fifo_rate",
        args: { item_code: item_code },
        callback: function(r) {
            if (!r.message) return;

            if (r.message.blocked) {
                frappe.msgprint({
                    title: "No Stock Available",
                    message: "All batches for <b>"
                        + item_code
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

            if (rate > 0) {
                // Store FEFO rate in form-level map
                // keyed by row cdn (unique per row)
                if (!frm.__fefo_rates) {
                    frm.__fefo_rates = {};
                }
                frm.__fefo_rates[cdn] = rate;

                // Set rate — price_list_rate trigger above
                // will defend this value if ERPNext overrides
                frappe.model.set_value(
                    cdt, cdn, "price_list_rate", rate);
                frappe.model.set_value(
                    cdt, cdn, "rate", rate);
                frm.refresh_field("items");
            }

            frappe.show_alert({
                message: "Batch: <b>" + batch_id
                    + "</b>  |  Rate: ₹" + rate
                    + "  |  Expiry: " + expiry_date
                    + "  |  Available: " + remaining_qty,
                indicator: "blue"
            }, 6);
        }
    });
}

function validate_qty(frm, cdt, cdn, row) {
    // Fetch nearest expiry batch details (FEFO)
    frappe.call({
        method: "taal_inventory.events.sales_invoice.get_fifo_rate",
        args: { item_code: row.item_code },
        callback: function(r) {
            if (!r.message || r.message.blocked) return;

            var batch_remaining = r.message.remaining_qty || 0;
            var batch_id        = r.message.batch_id      || "";
            var expiry_date     = r.message.expiry_date   || "";

            if (row.qty > batch_remaining) {
                // Cap qty at batch remaining
                frappe.model.set_value(
                    cdt, cdn, "qty", batch_remaining);

                // Re-apply FEFO rate after qty correction
                var fefo_rate = frm.__fefo_rates
                    && frm.__fefo_rates[cdn];
                if (fefo_rate && fefo_rate > 0) {
                    frappe.model.set_value(
                        cdt, cdn, "rate", fefo_rate);
                    frappe.model.set_value(
                        cdt, cdn, "price_list_rate", fefo_rate);
                }

                // Blue alert — bottom right — no popup
                frappe.show_alert({
                    message: "Batch <b>" + batch_id
                        + "</b> has only <b>"
                        + batch_remaining
                        + "</b> units remaining"
                        + " (Expiry: " + expiry_date + ")."
                        + " Quantity set to "
                        + batch_remaining + ".",
                    indicator: "blue"
                }, 6);
            }
        }
    });
}

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
                        var cdn   = child.name;

                        if (!frm.__fefo_rates) {
                            frm.__fefo_rates = {};
                        }
                        frm.__fefo_rates[cdn] = rate;

                        frappe.model.set_value(
                            child.doctype, cdn, {
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