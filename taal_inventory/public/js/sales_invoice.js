// ── Sales Invoice — User-chosen Pharmacy Batch per row ───────────
// taal_inventory/public/js/sales_invoice.js
//
// Each Sales Invoice Item row carries custom_pharmacy_batch (Link to
// Pharmacy Batch). The dropdown lists only sellable batches of that row's
// item in frm.doc.company: Active, qty > 0, not expired, nearest expiry
// first, each showing expiry + available qty + selling price.
//
//   item_code chosen  -> nearest-expiry batch is PRE-FILLED (user may change)
//   batch chosen      -> rate set ONCE from that batch's selling price,
//                        expiry + available qty stamped on the row
//   qty entered       -> capped at that batch's available qty
//   company changed   -> all batches cleared and re-prefilled
//   form opened       -> rows with no batch (Sales Order / WooCommerce)
//                        get the nearest-expiry batch filled in
//
// Rate is set once per batch pick; the user can still edit it freely.
// The server re-checks everything in validate(), so this file is UX only.

var BATCH_FIELD  = "custom_pharmacy_batch";
var BATCH_EXPIRY = "custom_batch_expiry";
var BATCH_AVAIL  = "custom_batch_available";

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

        // ── Batch dropdown: only sellable batches of this row's item ──
        frm.set_query(BATCH_FIELD, "items", function(doc, cdt, cdn) {
            var row = locals[cdt][cdn];
            return {
                query: "taal_inventory.events.sales_invoice.get_batches_for_invoice",
                filters: {
                    item_code: row.item_code || "",
                    company:   frm.doc.company || ""
                }
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

        // ── Fill batches on existing rows (Sales Order / WooCommerce) ──
        if (
            frm.doc.docstatus === 0 &&
            frm.doc.items &&
            frm.doc.items.length > 0
        ) {
            prefill_batches_on_load(frm);
        }
    },

    // Batches are per company. Clear every row's batch and re-prefill.
    company: function(frm) {
        frm.__fefo_rates = {};
        if (frm.doc.docstatus !== 0) return;
        (frm.doc.items || []).forEach(function(row) {
            if (!row.item_code) return;
            frappe.model.set_value(row.doctype, row.name, BATCH_FIELD, null);
            fetch_default_batch(frm, row.doctype, row.name, row.item_code, true);
        });
    }

});


frappe.ui.form.on("Sales Invoice Item", {

    item_code: function(frm, cdt, cdn) {
        var row = locals[cdt][cdn];
        if (frm.__fefo_rates) {
            delete frm.__fefo_rates[cdn];
        }
        if (!row.item_code) {
            clear_batch(cdt, cdn);
            return;
        }
        fetch_default_batch(frm, cdt, cdn, row.item_code, false);
    },

    // ── custom_pharmacy_batch ────────────────────────────────
    // User picked (or we prefilled) a batch. Pull its details and set the
    // rate once. Guarded by __batch_prefill so a programmatic prefill does
    // not run the lookup twice.
    custom_pharmacy_batch: function(frm, cdt, cdn) {
        var row = locals[cdt][cdn];
        if (row.__batch_prefill) {
            delete row.__batch_prefill;
            return;
        }
        if (!row[BATCH_FIELD]) {
            frappe.model.set_value(cdt, cdn, BATCH_EXPIRY, null);
            frappe.model.set_value(cdt, cdn, BATCH_AVAIL, 0);
            return;
        }
        apply_batch(frm, cdt, cdn, row[BATCH_FIELD]);
    },

    // ── price_list_rate ──────────────────────────────────────
    // ERPNext fires this after get_item_details API returns.
    // Intercept ONLY on the first sync — when rate still equals
    // price_list_rate but neither matches the batch rate yet.
    price_list_rate: function(frm, cdt, cdn) {
        var batch_rate = frm.__fefo_rates && frm.__fefo_rates[cdn];
        if (!batch_rate || batch_rate <= 0) return;

        var row = locals[cdt][cdn];
        if (row.rate === row.price_list_rate && row.rate !== batch_rate) {
            frappe.model.set_value(cdt, cdn, "price_list_rate", batch_rate);
            frappe.model.set_value(cdt, cdn, "rate", batch_rate);
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


// =============================================================================
//  HELPERS
// =============================================================================

function clear_batch(cdt, cdn) {
    frappe.model.set_value(cdt, cdn, BATCH_FIELD, null);
    frappe.model.set_value(cdt, cdn, BATCH_EXPIRY, null);
    frappe.model.set_value(cdt, cdn, BATCH_AVAIL, 0);
}


// Writes batch details onto the row and sets the rate once.
function stamp_batch(frm, cdt, cdn, m, prefill) {
    var row = locals[cdt][cdn];

    if (prefill && m.batch_name && row[BATCH_FIELD] !== m.batch_name) {
        // Mark so the custom_pharmacy_batch trigger skips its own lookup.
        row.__batch_prefill = true;
        frappe.model.set_value(cdt, cdn, BATCH_FIELD, m.batch_name);
    }
    frappe.model.set_value(cdt, cdn, BATCH_EXPIRY, m.expiry_date || null);
    frappe.model.set_value(cdt, cdn, BATCH_AVAIL,  m.remaining_qty || 0);

    var rate = m.rate || 0;
    if (rate > 0) {
        frm.__fefo_rates[cdn] = rate;
        frappe.model.set_value(cdt, cdn, "price_list_rate", rate);
        frappe.model.set_value(cdt, cdn, "rate", rate);
    }

    if (row.qty && m.remaining_qty && row.qty > m.remaining_qty) {
        frappe.model.set_value(cdt, cdn, "qty", m.remaining_qty);
        frappe.show_alert({
            message: "Qty reduced to " + m.remaining_qty
                + " (all that batch " + m.batch_id + " has left)",
            indicator: "orange"
        }, 6);
    }

    frm.refresh_field("items");
}


// Nearest-expiry batch for the row's item. prefill=true means the batch
// field is written too (used on item select, company change and load).
function fetch_default_batch(frm, cdt, cdn, item_code, quiet) {
    frappe.call({
        method: "taal_inventory.events.sales_invoice.get_fifo_rate",
        args: { item_code: item_code, company: frm.doc.company },
        callback: function(r) {
            var m = r.message;
            if (!m) return;

            if (m.blocked) {
                frappe.msgprint({
                    title: "No Stock Available",
                    message: "All batches for <b>" + item_code
                        + "</b> in <b>" + (frm.doc.company || "this company")
                        + "</b> are expired or exhausted.",
                    indicator: "red"
                });
                frappe.model.set_value(cdt, cdn, "item_code", "");
                clear_batch(cdt, cdn);
                return;
            }

            if (m.batch_id === "SERVICE" || !m.batch_name) {
                // Service item or non-pharmacy item: no batch on this row.
                clear_batch(cdt, cdn);
                if (m.rate > 0) {
                    frm.__fefo_rates[cdn] = m.rate;
                    frappe.model.set_value(cdt, cdn, "price_list_rate", m.rate);
                    frappe.model.set_value(cdt, cdn, "rate", m.rate);
                }
                if (!quiet && m.batch_id === "SERVICE") {
                    frappe.show_alert({
                        message: "Service item | Rate: ₹" + m.rate,
                        indicator: "blue"
                    }, 4);
                }
                return;
            }

            stamp_batch(frm, cdt, cdn, m, true);

            if (!quiet) {
                frappe.show_alert({
                    message: "Default batch (nearest expiry): <b>" + m.batch_id
                        + "</b>  |  Rate: ₹" + m.rate
                        + "  |  Expiry: " + m.expiry_date
                        + "  |  Available: " + m.remaining_qty
                        + "<br><small>Change it in the Pharmacy Batch column if needed.</small>",
                    indicator: "blue"
                }, 7);
            }
        }
    });
}


// User picked a batch by hand.
function apply_batch(frm, cdt, cdn, batch_name) {
    frappe.call({
        method: "taal_inventory.events.sales_invoice.get_batch_details",
        args: { batch_name: batch_name, company: frm.doc.company },
        callback: function(r) {
            var m = r.message;
            if (!m || m.blocked) {
                frappe.msgprint({
                    title: "Batch Not Sellable",
                    message: "Batch <b>" + batch_name + "</b> is sold out, "
                        + "expired, or belongs to another company. "
                        + "Please pick another batch.",
                    indicator: "red"
                });
                clear_batch(cdt, cdn);
                return;
            }

            stamp_batch(frm, cdt, cdn, m, false);

            frappe.show_alert({
                message: "Batch: <b>" + m.batch_id
                    + "</b>  |  Rate: ₹" + m.rate
                    + "  |  Expiry: " + m.expiry_date
                    + "  |  Available: " + m.remaining_qty,
                indicator: "green"
            }, 6);
        }
    });
}


// Fills nearest-expiry batches into rows that arrived without one
// (Sales Invoice made from a Sales Order / WooCommerce order), and warns
// about rows whose item has no stock at all.
function prefill_batches_on_load(frm) {
    setTimeout(function() {
        var blocked_items = [];
        var checked       = 0;
        var rows          = (frm.doc.items || []).filter(function(r) {
            return !!r.item_code;
        });
        if (rows.length === 0) return;

        rows.forEach(function(row) {
            // Row already has a batch: refresh its live numbers only.
            if (row[BATCH_FIELD]) {
                frappe.call({
                    method: "taal_inventory.events.sales_invoice.get_batch_details",
                    args: { batch_name: row[BATCH_FIELD], company: frm.doc.company },
                    callback: function(r) {
                        checked++;
                        var m = r.message;
                        if (m && !m.blocked) {
                            frm.__fefo_rates[row.name] = m.rate;
                            frappe.model.set_value(row.doctype, row.name, BATCH_AVAIL, m.remaining_qty);
                            frappe.model.set_value(row.doctype, row.name, BATCH_EXPIRY, m.expiry_date || null);
                        } else {
                            blocked_items.push("<b>" + row.item_code + "</b> (batch "
                                + row[BATCH_FIELD] + " no longer sellable)");
                            row.__batch_prefill = true;
                            frappe.model.set_value(row.doctype, row.name, BATCH_FIELD, null);
                        }
                        if (checked === rows.length) show_blocked(frm, blocked_items);
                    }
                });
                return;
            }

            frappe.call({
                method: "taal_inventory.events.sales_invoice.get_fifo_rate",
                args: { item_code: row.item_code, company: frm.doc.company },
                callback: function(r) {
                    checked++;
                    var m = r.message;

                    if (m && m.blocked) {
                        blocked_items.push(
                            "<b>" + row.item_code + "</b>" +
                            (row.item_name ? " — " + row.item_name : "")
                        );
                    } else if (m && m.batch_name) {
                        // Do NOT touch rate here: rows from a Sales Order
                        // already carry the agreed price. Only the batch,
                        // expiry and available qty are filled.
                        row.__batch_prefill = true;
                        frappe.model.set_value(row.doctype, row.name, BATCH_FIELD, m.batch_name);
                        frappe.model.set_value(row.doctype, row.name, BATCH_EXPIRY, m.expiry_date || null);
                        frappe.model.set_value(row.doctype, row.name, BATCH_AVAIL, m.remaining_qty || 0);
                        frm.__fefo_rates[row.name] = m.rate;
                    }

                    if (checked === rows.length) show_blocked(frm, blocked_items);
                }
            });
        });
    }, 800);
}


function show_blocked(frm, blocked_items) {
    frm.refresh_field("items");
    if (blocked_items.length === 0) return;

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
            "Please ensure stock is available and a batch is chosen " +
            "before submitting." +
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


// Qty is checked against the batch chosen on THIS row.
function validate_qty(frm, cdt, cdn, row) {
    if (!row[BATCH_FIELD]) return;   // service / non-pharmacy row, or not loaded yet

    frappe.call({
        method: "taal_inventory.events.sales_invoice.get_batch_details",
        args: { batch_name: row[BATCH_FIELD], company: frm.doc.company },
        callback: function(r) {
            var m = r.message;
            if (!m || m.blocked) return;

            var batch_remaining = m.remaining_qty || 0;
            var batch_id        = m.batch_id      || "";
            var expiry_date     = m.expiry_date   || "";

            frappe.model.set_value(cdt, cdn, BATCH_AVAIL, batch_remaining);

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
                            <p style="margin:12px 0 0 0; color:#7f8c8d; font-size:12px;">
                                Need more? Add another row for the same item and pick a different batch.
                            </p>
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
                var child = frm.add_child("items");
                // item_code trigger does the batch prefill + rate.
                frappe.model.set_value(child.doctype, child.name, {
                    "item_code": item_code,
                    "qty":       1
                });
                frm.refresh_field("items");
                frappe.show_alert({
                    message: "Added: " + item_code,
                    indicator: "green"
                }, 3);
            }
        }
    });
}