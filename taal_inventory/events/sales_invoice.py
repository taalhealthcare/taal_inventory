import frappe
from taal_inventory.utils.fifo import deduct_stock_fifo, reverse_stock_fifo


def on_submit(doc, method):
    """
    Triggered when Sales Invoice is submitted.
    Calls FIFO engine for every pharmacy item in the invoice.
    """
    for row in doc.items:
        # Only process items that exist in Pharmacy Item master
        if frappe.db.exists("Pharmacy Item", row.item_code):
            deduct_stock_fifo(
                item_code=row.item_code,
                qty_to_deduct=row.qty,
                reference_doctype="Sales Invoice",
                reference_no=doc.name,
                patient_name=(
                    getattr(doc, "patient_name", None)
                    or doc.customer_name
                ),
                deducted_by=frappe.session.user
            )


def on_cancel(doc, method):
    """
    Triggered when Sales Invoice is cancelled.
    Reverses stock deduction for every pharmacy item in the invoice.
    """
    for row in doc.items:
        if frappe.db.exists("Pharmacy Item", row.item_code):
            # Fetch deduction log entries for this invoice
            logs = frappe.get_all(
                "Deduction Log",
                filters={
                    "reference_no":       doc.name,
                    "reference_doctype":  "Sales Invoice",
                    "parent":             row.item_code
                },
                fields=["batch_id", "qty_deducted"]
            )
            for log in logs:
                reverse_stock_fifo(
                    item_code=row.item_code,
                    qty_to_restore=log.qty_deducted,
                    batch_name=log.batch_id
                )