// ── Sales Invoice — FEFO Rate + Qty Validation ───────────────
// taal_inventory/public/js/sales_invoice.js
//
// FEFO rate is fetched from Pharmacy Batch on item selection,
// SCOPED TO frm.doc.company — the rate and availability shown must
// come from the company actually raising the invoice.
// Rate is set ONCE — user can freely edit it to any value after.
// Item dropdown filtered to Pharmacy Items only.
// On refresh: validates all items from Sales Order (WooCommerce).

frappe.ui.form.on("Sales Invoice", {

    onload: function(frm) {
        if (!frm.doc.docstatus) {
            frm.set_value("ignore_pricing_rule", 1);
        }
        if (!frm.__fefo_rates) {
            frm.__fefo_rates = {};
        }

        // ── Filter item_code dropdown to Pharmacy Items only ──────────
        frm.set_query("item_code", "items", function() {
            return {
                query: "taal_inventory.events.sales_invoice.get_pharmacy_items_for_invoice"
            };
        });
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

        // ── Validate all existing items on form load ──────────────────
        // Handles Sales Invoice created from Sales Order (WooCommerce)
        // where item_code trigger never fired during item population.
        if (
            frm.doc.docstatus === 0 &&
            frm.doc.items &&
            frm.doc.items.length > 0
        ) {
            validate_all_items_on_load(frm);
        }
    },

    // Company drives which batches FEFO can see — clear cached rates
    // so the next interaction re-fetches against the new company.
    company: function(frm) {
        frm.__fefo_rates = {};
    }

});


frappe.ui.form.on("Sales Invoice Item", {

    item_code: function(frm, cdt, cdn) {
        var row = locals[cdt][cdn];
        if (!row.item_code) return;

        if (frm.__fefo_rates) {
            delete frm.__fefo_rates[cdn];
        }

        fetch_fefo_rate(frm, cdt, cdn, row.item_code);
    },

    // ── price_list_rate ──────────────────────────────────────
    // ERPNext fires this after get_item_details API returns.
    // Intercept ONLY on the first sync — when rate still equals
    // price_list_rate but neither matches our FEFO rate yet.
    price_list_rate: function(frm, cdt, cdn) {
        var fefo_rate = frm.__fefo_rates && frm.__fefo_rates[cdn];
        if (!fefo_rate || fefo_rate <= 0) return;

        var row = locals[cdt][cdn];
        if (row.rate === row.price_list_rate && row.rate !== fefo_rate) {
            frappe.model.set_value(cdt, cdn, "price_list_rate", fefo_rate);
            frappe.model.set_value(cdt, cdn, "rate", fefo_rate);
        }
    },

    // ── rate ─────────────────────────────────────────────────
    // NOT intercepted — user can freely change rate to any value.

    // ── qty ──────────────────────────────────────────────────
    qty: function(frm, cdt, cdn) {
        var row = locals[cdt][cdn];
        if (!row.item_code || !row.qty) return;
        validate_qty(frm, cdt, cdn, row);
    }

});


function validate_all_items_on_load(frm) {
    setTimeout(function() {
        var blocked_items = [];
        var checked       = 0;
        var total         = (frm.doc.items || []).filter(function(r) {
            return !!r.item_code;
        }).length;

        if (total === 0) return;

        (frm.doc.items || []).forEach(function(row) {
            if (!row.item_code) return;

            frappe.call({
                method: "taal_inventory.events.sales_invoice.get_fifo_rate",
                args: { item_code: row.item_code, company: frm.doc.company },
                callback: function(r) {
                    checked++;

                    if (r.message && r.message.blocked) {
                        blocked_items.push(
                            "<b>" + row.item_code + "</b>" +
                            (row.item_name ? " — " + row.item_name : "")
                        );
                    } else if (r.message && r.message.rate > 0) {
                        if (!frm.__fefo_rates) frm.__fefo_rates = {};
                        frm.__fefo_rates[row.name] = r.message.rate;
                    }

                    if (checked === total && blocked_items.length > 0) {
                        frappe.msgprint({
                            title: "⚠ Stock Warning — Items Have No Available Stock",
                            indicator: "red",
                            message:
                                "<div style='" +
                                "font-family:Arial,sans-serif;" +
                                "background:#fff5f5;" +
                                "border-left:4px solid #e74c3c;" +
                                "border-radius:8px;" +
                                "padding:16px 20px;" +
                                "'>" +
                                "<p style='margin:0 0 10px 0;color:#c0392b;font-weight:700;'>" +
                                "The following items have no active stock in " +
                                (frm.doc.company || "this company") +
                                " (all batches expired or exhausted):" +
                                "</p>" +
                                "<ul style='margin:0;padding-left:20px;color:#2c3e50;'>" +
                                blocked_items.map(function(item) {
                                    return "<li style='padding:4px 0;'>" + item + "</li>";
                                }).join("") +
                                "</ul>" +
                                "<p style='margin:12px 0 0 0;color:#7f8c8d;font-size:12px;'>" +
                                "This invoice was created from a Sales Order. " +
                                "Please ensure stock is available before submitting." +
                                "</p>" +
                                "</div>"
                        });

                        frm.dashboard.clear_comment();
                        frm.dashboard.add_comment(
                            "STOCK WARNING: " +
                            blocked_items.join(", ") +
                            " — No active batch. Do NOT submit without restocking.",
                            "red"
                        );
                    }
                }
            });
        });
    }, 800);
}


function fetch_fefo_rate(frm, cdt, cdn, item_code) {
    frappe.call({
        method: "taal_inventory.events.sales_invoice.get_fifo_rate",
        args: { item_code: item_code, company: frm.doc.company },
        callback: function(r) {
            if (!r.message) return;

            if (r.message.blocked) {
                frappe.msgprint({
                    title: "No Stock Available",
                    message: "All batches for <b>"
                        + item_code
                        + "</b> in <b>" + (frm.doc.company || "this company")
                        + "</b> are expired or exhausted.",
                    indicator: "red"
                });
                frappe.model.set_value(cdt, cdn, "item_code", "");
                return;
            }

            var rate          = r.message.rate          || 0;
            var remaining_qty = r.message.remaining_qty || 0;
            var batch_id      = r.message.batch_id      || "";
            var expiry_date   = r.message.expiry_date   || "";

            if (rate > 0) {
                if (!frm.__fefo_rates) {
                    frm.__fefo_rates = {};
                }
                frm.__fefo_rates[cdn] = rate;

                frappe.model.set_value(cdt, cdn, "price_list_rate", rate);
                frappe.model.set_value(cdt, cdn, "rate", rate);
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
    frappe.call({
        method: "taal_inventory.events.sales_invoice.get_fifo_rate",
        args: { item_code: row.item_code, company: frm.doc.company },
        callback: function(r) {
            if (!r.message || r.message.blocked) return;

            var batch_remaining = r.message.remaining_qty || 0;
            var batch_id        = r.message.batch_id      || "";
            var expiry_date     = r.message.expiry_date   || "";

            if (row.qty > batch_remaining) {
                frappe.model.set_value(cdt, cdn, "qty", batch_remaining);

                frappe.msgprint({
                    title: "⚠&nbsp; Stock Alert — Quantity Adjusted",
                    indicator: "orange",
                    message: `
                        <div style="
                            font-family: Arial, sans-serif;
                            background: #fff8f0;
                            border-left: 4px solid #e67e22;
                            border-radius: 8px;
                            padding: 18px 20px;
                        ">
                            <table style="
                                width: 100%;
                                border-collapse: collapse;
                                font-size: 13px;
                                color: #2c3e50;
                            ">
                                <tr style="border-bottom: 1px solid #f0e0cc;">
                                    <td style="padding: 7px 0; color: #888; width: 45%;">
                                        Batch ID
                                    </td>
                                    <td style="padding: 7px 0; font-weight: 700;">
                                        ${batch_id}
                                    </td>
                                </tr>
                                <tr style="border-bottom: 1px solid #f0e0cc;">
                                    <td style="padding: 7px 0; color: #888;">
                                        Expiry Date
                                    </td>
                                    <td style="padding: 7px 0; font-weight: 700; color: #e74c3c;">
                                        ${expiry_date}
                                    </td>
                                </tr>
                                <tr style="border-bottom: 1px solid #f0e0cc;">
                                    <td style="padding: 7px 0; color: #888;">
                                        Available Qty
                                    </td>
                                    <td style="padding: 7px 0; font-weight: 700; color: #27ae60;">
                                        ${batch_remaining} units
                                    </td>
                                </tr>
                                <tr style="border-bottom: 1px solid #f0e0cc;">
                                    <td style="padding: 7px 0; color: #888;">
                                        You Entered
                                    </td>
                                    <td style="padding: 7px 0; font-weight: 700; color: #e74c3c;">
                                        ${row.qty} units
                                    </td>
                                </tr>
                                <tr>
                                    <td style="padding: 10px 0 0 0; color: #888;">
                                        Qty Set To
                                    </td>
                                    <td style="padding: 10px 0 0 0; font-weight: 700; color: #27ae60; font-size: 15px;">
                                        ✅ ${batch_remaining} units
                                    </td>
                                </tr>
                            </table>
                        </div>
                    `
                });
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
                    args: { item_code: item_code, company: frm.doc.company },
                    callback: function(ir) {
                        if (ir.message && ir.message.blocked) {
                            frappe.msgprint({
                                title: "No Stock",
                                message: "No valid stock for "
                                    + item_code + " in "
                                    + (frm.doc.company || "this company"),
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