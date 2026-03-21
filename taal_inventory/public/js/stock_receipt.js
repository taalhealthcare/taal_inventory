// Stock Receipt — Auto Fetch Item Details on Item Selection
frappe.ui.form.on("Stock Receipt Item", {

    item: function(frm, cdt, cdn) {
        var row = locals[cdt][cdn];
        if (!row.item) return;

        // Fetch all details from Pharmacy Item
        frappe.db.get_value(
            "Pharmacy Item",
            row.item,
            [
                "item_name",
                "hsn_sac_code",
                "uom",
                "purchase_tax_pct",
                "gst_pct"
            ]
        ).then(function(r) {
            if (!r.message) return;
            frappe.model.set_value(cdt, cdn,
                "item_name", r.message.item_name || "");
            frappe.model.set_value(cdt, cdn,
                "uom", r.message.uom || "");
            frappe.model.set_value(cdt, cdn,
                "purchase_tax_pct",
                r.message.purchase_tax_pct || 0);
            frappe.model.set_value(cdt, cdn,
                "gst_pct", r.message.gst_pct || 0);
            frappe.model.set_value(cdt, cdn,
                "hsn_sac_code",
                r.message.hsn_sac_code || "");
        });
    },

    // Auto-calculate Line Total when Qty or Cost Price changes
    qty_received: function(frm, cdt, cdn) {
        calculate_line_total(frm, cdt, cdn);
    },

    cost_price: function(frm, cdt, cdn) {
        calculate_line_total(frm, cdt, cdn);
    },

    discount_pct: function(frm, cdt, cdn) {
        calculate_line_total(frm, cdt, cdn);
    }
});

function calculate_line_total(frm, cdt, cdn) {
    var row = locals[cdt][cdn];
    var qty      = flt(row.qty_received) || 0;
    var rate     = flt(row.cost_price)   || 0;
    var discount = flt(row.discount_pct) || 0;
    var subtotal = qty * rate;
    var total    = subtotal - (subtotal * discount / 100);
    frappe.model.set_value(cdt, cdn, "line_total",
        parseFloat(total.toFixed(2)));
}