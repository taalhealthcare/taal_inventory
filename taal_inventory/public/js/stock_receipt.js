// Stock Receipt — Auto Fetch Item Details + Calculate Totals

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
                "gst_pct",
                "manufacturer_name"
            ]
        ).then(function(r) {
            if (!r.message) return;
            frappe.model.set_value(cdt, cdn,
                "item_name",
                r.message.item_name || "");
            frappe.model.set_value(cdt, cdn,
                "uom",
                r.message.uom || "");
            frappe.model.set_value(cdt, cdn,
                "purchase_tax_pct",
                r.message.purchase_tax_pct || 0);
            frappe.model.set_value(cdt, cdn,
                "gst_pct",
                r.message.gst_pct || 0);
            frappe.model.set_value(cdt, cdn,
                "hsn_sac_code",
                r.message.hsn_sac_code || "");
        });
    },

    // Recalculate when any pricing field changes
    qty_received: function(frm, cdt, cdn) {
        calculate_line_total(frm, cdt, cdn);
    },
    cost_price: function(frm, cdt, cdn) {
        calculate_line_total(frm, cdt, cdn);
    },
    discount_pct: function(frm, cdt, cdn) {
        calculate_line_total(frm, cdt, cdn);
    },
    purchase_tax_pct: function(frm, cdt, cdn) {
        calculate_line_total(frm, cdt, cdn);
    }
});

function calculate_line_total(frm, cdt, cdn) {
    var row      = locals[cdt][cdn];
    var qty      = flt(row.qty_received)   || 0;
    var rate     = flt(row.cost_price)     || 0;
    var discount = flt(row.discount_pct)   || 0;
    var tax_pct  = flt(row.purchase_tax_pct) || 0;

    // Subtotal after discount
    var subtotal = qty * rate;
    var after_discount = subtotal - (subtotal * discount / 100);

    // Line total before tax
    var line_total = parseFloat(after_discount.toFixed(2));
    frappe.model.set_value(cdt, cdn, "line_total", line_total);

    // Recalculate Stock Receipt header totals
    calculate_header_totals(frm);
}

function calculate_header_totals(frm) {
    var total_amount = 0;
    var total_tax    = 0;

    (frm.doc.items || []).forEach(function(row) {
        var qty      = flt(row.qty_received)    || 0;
        var rate     = flt(row.cost_price)      || 0;
        var discount = flt(row.discount_pct)    || 0;
        var tax_pct  = flt(row.purchase_tax_pct) || 0;

        var subtotal       = qty * rate;
        var after_discount = subtotal - (subtotal * discount / 100);
        var tax_amount     = after_discount * (tax_pct / 100);

        total_amount += after_discount;
        total_tax    += tax_amount;
    });

    var grand_total = total_amount + total_tax;

    frm.set_value("total_amount",
        parseFloat(total_amount.toFixed(2)));
    frm.set_value("total_tax",
        parseFloat(total_tax.toFixed(2)));
    frm.set_value("grand_total",
        parseFloat(grand_total.toFixed(2)));
}