// taal_inventory/public/js/pharmacy_stock_transfer.js
//
// Client Script for Pharmacy Stock Transfer.
//
// FIX APPLIED: Replaced the unreliable
//   frm.fields_dict.items.grid.get_field(...).get_query = ...
// pattern (which returns undefined if the grid isn't fully
// initialized at onload/refresh time) with the standard, reliable
// frm.set_query() pattern. frm.set_query() registers the filter
// against the fieldname declaratively and Frappe guarantees it is
// applied whenever that field's dropdown is opened, regardless of
// grid render timing.
//
// FEATURES:
//   1. source_batch Link field filtered to only show batches
//      matching the row's selected pharmacy_item AND from_company.
//   2. On selecting source_batch, immediately (live, before submit)
//      populates: available_qty, from_cost_price, from_selling_price,
//      from_mrp_price — all fetched directly from the Pharmacy Batch.
//   3. Validates company match and Active status, warns immediately.
//   4. Validates qty does not exceed available_qty.

frappe.ui.form.on("Pharmacy Stock Transfer", {

    setup: function(frm) {
        // setup() fires before onload/refresh and is the correct place
        // to register set_query for child table fields — guaranteed
        // to be available regardless of grid render timing.
        frm.set_query("source_batch", "items", function(doc, cdt, cdn) {
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
        });
    },

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

frappe.ui.form.on("Pharmacy Stock Transfer Item", {

    pharmacy_item: function(frm, cdt, cdn) {
        var row = locals[cdt][cdn];
        // Clear source_batch when pharmacy_item changes — forces
        // re-selection so the filter applies cleanly
        if (row.source_batch) {
            frappe.model.set_value(cdt, cdn, "source_batch", "");
            frappe.model.set_value(cdt, cdn, "available_qty", 0);
            frappe.model.set_value(cdt, cdn, "from_cost_price", 0);
            frappe.model.set_value(cdt, cdn, "from_selling_price", 0);
            frappe.model.set_value(cdt, cdn, "from_mrp_price", 0);
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