import frappe
from frappe.utils import now_datetime


def on_submit(doc, method):
    """
    Triggered when Stock Receipt is submitted.
    Creates one Pharmacy Batch per line item.
    Updates current_stock on each Pharmacy Item.
    """
    for row in doc.items:
        profit = (row.selling_price or 0) - (row.cost_price or 0)

        batch = frappe.get_doc({
            "doctype":               "Pharmacy Batch",
            "item":                  row.item,
            "batch_id":              row.batch_id,
            "manufacturing_date":    row.manufacturing_date,
            "expiry_date":           row.expiry_date,
            "mrp_price":             row.mrp_price or 0,
            "selling_price":         row.selling_price or 0,
            "cost_price":            row.cost_price or 0,
            "profit_per_uom":        profit,
            "gst_pct":               row.gst_pct or 0,
            "purchase_tax_pct":      row.purchase_tax_pct or 0,
            "supplier_discount_pct": row.discount_pct or 0,
            "supplier_name":         doc.supplier,
            "supplier_invoice_no":   doc.supplier_invoice_no,
            "qty_received":          row.qty_received,
            "remaining_qty":         row.qty_received,
            "warehouse":             doc.warehouse,
            "stock_receipt":         doc.name,
            "status":                "Active",
            "creation_date":         now_datetime()
        })
        batch.insert(ignore_permissions=True)

        # Update current_stock on Pharmacy Item
        item_doc = frappe.get_doc("Pharmacy Item", row.item)
        item_doc.current_stock = (item_doc.current_stock or 0) + row.qty_received
        item_doc.save(ignore_permissions=True)

    frappe.db.commit()
    frappe.msgprint(
        "Stock Receipt submitted successfully. "
        "Batches created and stock updated.",
        alert=True
    )


def on_cancel(doc, method):
    """
    Triggered when Stock Receipt is cancelled.
    Quarantines all batches created by this receipt.
    Reverses current_stock on each Pharmacy Item.
    """
    for row in doc.items:
        batches = frappe.get_all(
            "Pharmacy Batch",
            filters={
                "stock_receipt": doc.name,
                "item": row.item
            },
            fields=["name", "remaining_qty"]
        )
        for b in batches:
            frappe.db.set_value(
                "Pharmacy Batch", b.name, "status", "Quarantined"
            )
            item_doc = frappe.get_doc("Pharmacy Item", row.item)
            item_doc.current_stock = max(
                0, (item_doc.current_stock or 0) - b.remaining_qty
            )
            item_doc.save(ignore_permissions=True)

    frappe.db.commit()


def on_adjustment_submit(doc, method):
    """
    Triggered when Stock Adjustment is submitted.
    Deducts stock from the selected batch.
    Logs the deduction in the Deduction Log child table.
    """
    from taal_inventory.utils.fifo import (
        _recalculate_current_stock,
        _log_deduction
    )

    for row in doc.items:
        batch = frappe.get_doc("Pharmacy Batch", row.batch_id)
        balance_before = batch.remaining_qty
        new_qty = max(0, balance_before - row.qty)

        # Update batch remaining quantity
        frappe.db.set_value("Pharmacy Batch", batch.name, {
            "remaining_qty": new_qty,
            "status": "Exhausted" if new_qty == 0 else "Active"
        })

        # Write balance fields on the child row
        frappe.db.set_value(
            "Stock Adjustment Item", row.name, {
                "balance_before": balance_before,
                "balance_after":  new_qty
            }
        )

        # Recalculate item current_stock
        new_stock = _recalculate_current_stock(row.item)

        # Log to Deduction Log
        _log_deduction(
            item_code=row.item,
            batch_id=row.batch_id,
            qty_deducted=row.qty,
            reference_doctype="Stock Adjustment",
            reference_no=doc.name,
            patient_name="",
            balance_after=new_stock,
            deducted_by=frappe.session.user
        )

    frappe.db.commit()