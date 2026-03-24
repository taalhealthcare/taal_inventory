import frappe
from frappe.utils import now_datetime


def on_submit(doc, method):
    """
    Triggered when Stock Receipt is submitted.
    Creates one Pharmacy Batch per line item.
    Updates current_stock on each Pharmacy Item.
    Auto-creates or updates Item Price for Sales Invoice.
    Calculates and updates totals on Stock Receipt.
    """
    total_amount = 0
    total_tax    = 0

    for row in doc.items:
        # Fetch full Pharmacy Item details
        pi = frappe.get_doc("Pharmacy Item", row.item)

        profit = (row.selling_price or 0) - (row.cost_price or 0)

        # Calculate line total
        line_total = (row.qty_received or 0) * (row.cost_price or 0)

        # Combined tax = GST % + Purchase Tax %
        gst_pct      = row.gst_pct or 0
        purchase_tax = row.purchase_tax_pct or 0
        combined_tax = gst_pct + purchase_tax
        tax_amount   = line_total * (combined_tax / 100)

        total_amount += line_total
        total_tax    += tax_amount

        # Create one Pharmacy Batch per receipt line
        batch = frappe.get_doc({
            "doctype":               "Pharmacy Batch",
            "item":                  row.item,
            "item_name":             pi.item_name,
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
            "hsn_sac_code":          pi.hsn_sac_code or "",
            "manufacturer_name":     pi.manufacturer_name or "",
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

        # Update line_total on the row
        frappe.db.set_value(
            "Stock Receipt Item",
            row.name,
            "line_total",
            line_total
        )

        # Update current_stock on Pharmacy Item
        item_doc = frappe.get_doc("Pharmacy Item", row.item)
        item_doc.current_stock = (
            item_doc.current_stock or 0
        ) + row.qty_received
        item_doc.save(ignore_permissions=True)

        # Auto-create or update Item Price for Sales Invoice
        if row.selling_price and row.selling_price > 0:
            actual_item_code = frappe.db.get_value(
                "Pharmacy Item", row.item, "item_code"
            )
            if actual_item_code:
                _sync_item_price(actual_item_code, row.selling_price)

    # Update Stock Receipt totals
    grand_total = total_amount + total_tax
    frappe.db.set_value("Stock Receipt", doc.name, {
        "total_amount": round(total_amount, 2),
        "total_tax":    round(total_tax, 2),
        "grand_total":  round(grand_total, 2)
    })

    frappe.db.commit()
    frappe.msgprint(
        "Stock Receipt submitted successfully. "
        "Batches created, stock updated and totals calculated.",
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
                "item":          row.item
            },
            fields=["name", "remaining_qty"]
        )
        for b in batches:
            frappe.db.set_value(
                "Pharmacy Batch",
                b.name,
                "status",
                "Quarantined"
            )
            item_doc = frappe.get_doc("Pharmacy Item", row.item)
            item_doc.current_stock = max(
                0,
                (item_doc.current_stock or 0) - b.remaining_qty
            )
            item_doc.save(ignore_permissions=True)

    # Reset totals on cancel
    frappe.db.set_value("Stock Receipt", doc.name, {
        "total_amount": 0,
        "total_tax":    0,
        "grand_total":  0
    })

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
            "Stock Adjustment Item",
            row.name, {
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


def _sync_item_price(item_code, selling_price):
    """
    Auto-create or update Item Price in Standard Selling price list.
    Called on Stock Receipt submit.
    """
    try:
        existing = frappe.get_all(
            "Item Price",
            filters={
                "item_code":  item_code,
                "price_list": "Standard Selling",
                "selling":    1
            },
            fields=["name"],
            limit_page_length=1
        )

        if existing:
            frappe.db.set_value(
                "Item Price",
                existing[0].name,
                "price_list_rate",
                selling_price
            )
        else:
            item_price = frappe.get_doc({
                "doctype":         "Item Price",
                "item_code":       item_code,
                "price_list":      "Standard Selling",
                "selling":         1,
                "price_list_rate": selling_price,
                "currency":        "INR"
            })
            item_price.flags.ignore_permissions = True
            item_price.insert()

        frappe.db.commit()

    except Exception as e:
        frappe.log_error(
            f"Item Price sync failed for {item_code}: {str(e)}",
            "Item Price Sync Error"
        )
        frappe.msgprint(
            f"Item Price sync failed for {item_code}: {str(e)}",
            title="Item Price Sync Error",
            indicator="red"
        )