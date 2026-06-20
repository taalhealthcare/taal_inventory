// taal_inventory/public/js/pharmacy_stock_transfer.js
//
// Client Script for Pharmacy Stock Transfer.
//
// FEATURES:
//   1. source_batch Link field is filtered to only show batches
//      matching the row's selected pharmacy_item AND from_company.
//   2. On selecting source_batch, immediately (live, before submit)
//      populates: available_qty, from_cost_price, from_selling_price,
//      from_mrp_price — all fetched directly from the Pharmacy Batch.
//   3. Validates company match and Active status, warns immediately.
//   4. Validates qty does not exceed available_qty.

frappe.ui.form.on("Pharmacy Stock Transfer Item", {

    pharmacy_item: function(frm, cdt, cdn) {
        var row = locals[cdt][cdn];
        // Clear source_batch when pharmacy_item changes — forces
        // re-selection so the filter applies cleanly
        if (row.source_batch) {
            frappe.model.set_value(cdt, cdn, "source_batch", "");
        }
    },

    source_batch: function(frm, cdt, cdn) {
        var row = locals[cdt][cdn];

        if (!row.source_batch) {
            frappe.model.set_value(cdt, cdn, "available_qty", 0);
            frappe.model.set_value(cdt, cdn, "from_cost_price", 0);
            frappe.model.set_value(cdt, cdn, "from_selling_price", 0);
            frappe.model.set_value(cdt, cdn, "from_mrp_price", 0);
            return;
        }

        frappe.db.get_doc("Pharmacy Batch", row.source_batch).then(function(batch) {
            // Live-populate all 4 fields immediately on selection
            frappe.model.set_value(cdt, cdn, "available_qty", batch.remaining_qty || 0);
            frappe.model.set_value(cdt, cdn, "from_cost_price", batch.cost_price || 0);
            frappe.model.set_value(cdt, cdn, "from_selling_price", batch.selling_price || 0);
            frappe.model.set_value(cdt, cdn, "from_mrp_price", batch.mrp_price || 0);

            // Auto-fill pharmacy_item if not already set
            if (!row.pharmacy_item && batch.item) {
                frappe.model.set_value(cdt, cdn, "pharmacy_item", batch.item);
            }

            // Validate batch belongs to the selected from_company
            if (frm.doc.from_company && batch.company !== frm.doc.from_company) {
                frappe.show_alert({
                    message: "⚠️ Batch " + row.source_batch +
                        " belongs to '" + batch.company + "', not '" +
                        frm.doc.from_company + "'. This batch cannot be " +
                        "transferred from the selected From Company.",
                    indicator: "red"
                }, 8);
            }

            // Validate batch status is Active
            if (batch.status !== "Active") {
                frappe.show_alert({
                    message: "⚠️ Batch " + row.source_batch +
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
        // Refresh the source_batch filter on all rows
        frm.fields_dict.items.grid.get_field("source_batch").get_query =
            get_source_batch_query(frm);
    },

    to_company: function(frm) {
        if (frm.doc.from_company && frm.doc.to_company &&
            frm.doc.from_company === frm.doc.to_company) {
            frappe.show_alert({
                message: "⚠️ From Company and To Company cannot be the same.",
                indicator: "red"
            }, 6);
        }
    },

    onload: function(frm) {
        // Set up the source_batch filter as soon as the form loads
        frm.fields_dict.items.grid.get_field("source_batch").get_query =
            get_source_batch_query(frm);
    },

    refresh: function(frm) {
        frm.fields_dict.items.grid.get_field("source_batch").get_query =
            get_source_batch_query(frm);
    }
});

// ── Filter Query: source_batch dropdown ───────────────────────
// Only show batches that:
//   1. Belong to the currently selected From Company
//   2. Match the row's selected Pharmacy Item (if one is set)
//   3. Are Active with remaining_qty > 0
function get_source_batch_query(frm) {
    return function(doc, cdt, cdn) {
        var row = locals[cdt][cdn];
        var filters = {
            "status": "Active",
            "remaining_qty": [">", 0]
        };

        if (frm.doc.from_company) {
            filters["company"] = frm.doc.from_company;
        }

        if (row.pharmacy_item) {
            filters["item"] = row.pharmacy_item;
        }

        return { filters: filters };
    };
}