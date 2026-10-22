// ── Sales Invoice — app-level basics only ────────────────────────
// taal_inventory/public/js/sales_invoice.js
//
// All Pharmacy Batch logic (prefill, dropdown, rate, qty cap, Sales Order
// prefill on load) lives in the Client Script "Sales Invoice - Pharmacy
// Batch" on the site, so it can be changed without an asset build.
//
// This file keeps only:
//   - ignore_pricing_rule on new invoices
//   - item_code dropdown limited to Pharmacy Items
//   - Scan Barcode button (adds the row; the Client Script handles the rest)

frappe.ui.form.on("Sales Invoice", {

    onload: function(frm) {
        if (!frm.doc.docstatus) {
            frm.set_value("ignore_pricing_rule", 1);
        }
        frm.set_query("item_code", "items", function() {
            return {
                query: "taal_inventory.events.sales_invoice.get_pharmacy_items_for_invoice"
            };
        });
    },

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
                    message: "No item found for barcode: <b>" + barcode + "</b>.",
                    indicator: "red"
                });
                return;
            }

            var item_code = r.message[0].parent;
            var existing  = false;

            (frm.doc.items || []).forEach(function(row) {
                if (row.item_code === item_code) {
                    frappe.model.set_value(row.doctype, row.name, "qty", (row.qty || 0) + 1);
                    existing = true;
                    frappe.show_alert({ message: "Qty +1: " + row.item_name, indicator: "green" }, 3);
                }
            });

            if (!existing) {
                var child = frm.add_child("items");
                // item_code trigger (Client Script) prefills batch + rate.
                frappe.model.set_value(child.doctype, child.name, { "item_code": item_code, "qty": 1 });
                frm.refresh_field("items");
                frappe.show_alert({ message: "Added: " + item_code, indicator: "green" }, 3);
            }
        }
    });
}