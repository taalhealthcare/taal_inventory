// Sales Invoice — Barcode Scanner Support
frappe.ui.form.on("Sales Invoice", {
    refresh: function(frm) {
        // Add Scan Barcode button
        if (!frm.doc.docstatus) {
            frm.add_custom_button("Scan Barcode", function() {
                frappe.prompt(
                    [{
                        label: "Scan or Type Barcode",
                        fieldname: "barcode",
                        fieldtype: "Data",
                        reqd: 1,
                        description: "Scan barcode with scanner or type manually"
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

            // If item already in table — increase qty
            (frm.doc.items || []).forEach(function(row) {
                if (row.item_code === item_code) {
                    frappe.model.set_value(
                        row.doctype, row.name,
                        "qty", (row.qty || 0) + 1
                    );
                    existing = true;
                    frappe.show_alert({
                        message: "Qty +1 for: " + row.item_name,
                        indicator: "green"
                    }, 3);
                }
            });

            if (!existing) {
                // Add new row
                frappe.call({
                    method: "frappe.client.get",
                    args: { doctype: "Item", name: item_code },
                    callback: function(ir) {
                        var item = ir.message;
                        if (!item) return;
                        var child = frm.add_child("items");
                        frappe.model.set_value(
                            child.doctype, child.name, {
                                "item_code": item_code,
                                "item_name": item.item_name,
                                "qty": 1,
                                "uom": item.stock_uom,
                                "rate": item.standard_rate || 0
                            }
                        );
                        frm.refresh_field("items");
                        frappe.show_alert({
                            message: "Added: " + item.item_name,
                            indicator: "green"
                        }, 3);
                    }
                });
            }
        }
    });
}