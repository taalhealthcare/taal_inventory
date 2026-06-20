// taal_inventory/public/js/pharmacy_stock_transfer.js
//
// Client Script for Pharmacy Stock Transfer.
//
// PURPOSE:
//   When the user selects a Source Batch in the child table row,
//   immediately fetch and display:
//     - Available Qty (remaining_qty on that batch)
//     - From Cost Price (cost_price on that batch)
//     - Item Name auto-fill from the batch's item link
//   This gives live visibility BEFORE submit — previously these
//   fields stayed blank until after submit, since the Python
//   on_submit hook only writes them as part of the actual transfer.
//
// Also validates that the selected batch belongs to from_company,
// catching mismatches early instead of letting submit fail later.

frappe.ui.form.on("Pharmacy Stock Transfer Item", {
    source_batch: function(frm, cdt, cdn) {
        var row = locals[cdt][cdn];

        if (!row.source_batch) {
            frappe.model.set_value(cdt, cdn, "available_qty", 0);
            frappe.model.set_value(cdt, cdn, "from_cost_price", 0);
            return;
        }

        frappe.db.get_doc("Pharmacy Batch", row.source_batch).then(function(batch) {
            // Populate available qty and cost price immediately
            frappe.model.set_value(cdt, cdn, "available_qty", batch.remaining_qty || 0);
            frappe.model.set_value(cdt, cdn, "from_cost_price", batch.cost_price || 0);

            // Auto-fill pharmacy_item if not already set
            if (!row.pharmacy_item && batch.item) {
                frappe.model.set_value(cdt, cdn, "pharmacy_item", batch.item);
            }

            // Validate batch belongs to the selected from_company
            if (frm.doc.from_company && batch.company !== frm.doc.from_company) {
                frappe.show_alert({
                    message: "⚠️ Warning: Batch " + row.source_batch +
                        " belongs to '" + batch.company + "', not '" +
                        frm.doc.from_company + "'. This batch cannot be " +
                        "transferred from the selected From Company.",
                    indicator: "red"
                }, 8);
            }

            // Validate batch status is Active
            if (batch.status !== "Active") {
                frappe.show_alert({
                    message: "⚠️ Warning: Batch " + row.source_batch +
                        " is '" + batch.status + "', not Active. " +
                        "Only Active batches can be transferred.",
                    indicator: "orange"
                }, 6);
            }

            frm.refresh_field("items");
        });
    },

    qty: function(frm, cdt, cdn) {
        var row = locals[cdt][cdn];

        // Live validation: qty cannot exceed available_qty
        if (row.qty && row.available_qty && row.qty > row.available_qty) {
            frappe.show_alert({
                message: "⚠️ Qty to Transfer (" + row.qty +
                    ") exceeds Available Qty (" + row.available_qty + ").",
                indicator: "red"
            }, 6);
        }
    }
});

frappe.ui.form.on("Pharmacy Stock Transfer", {
    from_company: function(frm) {
        // Re-validate all existing rows when from_company changes
        if (frm.doc.items && frm.doc.items.length) {
            frm.doc.items.forEach(function(row) {
                if (row.source_batch) {
                    frappe.db.get_value(
                        "Pharmacy Batch", row.source_batch, "company"
                    ).then(function(r) {
                        if (r.message && r.message.company !== frm.doc.from_company) {
                            frappe.show_alert({
                                message: "⚠️ Row with batch " + row.source_batch +
                                    " does not belong to the newly selected " +
                                    "From Company. Please review.",
                                indicator: "orange"
                            }, 6);
                        }
                    });
                }
            });
        }
    },

    to_company: function(frm) {
        if (frm.doc.from_company && frm.doc.to_company &&
            frm.doc.from_company === frm.doc.to_company) {
            frappe.show_alert({
                message: "⚠️ From Company and To Company cannot be the same.",
                indicator: "red"
            }, 6);
        }
    }
});