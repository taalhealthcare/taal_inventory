frappe.ui.form.on("Pharmacy Item", {
    refresh(frm) {
        if (frm.is_new()) return;

        // No group argument -> renders as a top-level toolbar button
        // rather than hiding inside a "Stock" dropdown.
        frm.add_custom_button("Recalculate Stock", () => {
            frappe.call({
                method: "taal_inventory.utils.company_stock.recalculate_this_item",
                args: { pharmacy_item: frm.doc.name },
                freeze: true,
                freeze_message: "Recalculating stock from Active batches...",
                callback(r) {
                    if (!r.message) return;
                    frappe.show_alert({
                        message: `Total Stock recalculated: ${r.message.total_stock}`,
                        indicator: "green"
                    });
                    frm.reload_doc();
                }
            });
        });
    }
});