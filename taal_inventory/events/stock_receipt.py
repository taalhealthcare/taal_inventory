import frappe
from frappe.utils import now_datetime


def on_submit(doc, method):
    """
    Triggered when Stock Receipt is submitted.
    Creates one Pharmacy Batch per line item.
    Updates current_stock on each Pharmacy Item.
    Creates batch-specific Item Price for correct FIFO rate.
    Calculates and updates totals on Stock Receipt.
    """
    total_amount = 0
    total_tax    = 0

    for row in doc.items:
        # Fetch full Pharmacy Item details
        pi = frappe.get_doc("Pharmacy Item", row.item)

        profit     = (row.selling_price or 0) - (row.cost_price or 0)
        line_total = (row.qty_received or 0) * (row.cost_price or 0)
        gst_pct    = row.gst_pct or 0
        pur_tax    = row.purchase_tax_pct or 0
        tax_amount = line_total * ((gst_pct + pur_tax) / 100)

        total_amount += line_total
        total_tax    += tax_amount

        # Create Pharmacy Batch
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
            "gst_pct":               gst_pct,
            "purchase_tax_pct":      pur_tax,
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

        # Update line_total on receipt row
        frappe.db.set_value(
            "Stock Receipt Item",
            row.name,
            "line_total",
            round(line_total, 2)
        )

        # Update current_stock
        item_doc = frappe.get_doc("Pharmacy Item", row.item)
        item_doc.current_stock = (
            item_doc.current_stock or 0
        ) + row.qty_received
        item_doc.save(ignore_permissions=True)

        # Create batch-specific Item Price
        if row.selling_price and row.selling_price > 0:
            actual_item_code = frappe.db.get_value(
                "Pharmacy Item", row.item, "item_code"
            )
            if actual_item_code:
                _sync_item_price(
                    actual_item_code,
                    row.selling_price,
                    batch.name
                )

    # Update Stock Receipt totals
    grand_total = total_amount + total_tax
    frappe.db.set_value("Stock Receipt", doc.name, {
        "total_amount": round(total_amount, 2),
        "total_tax":    round(total_tax, 2),
        "grand_total":  round(grand_total, 2)
    })

    frappe.db.commit()
    frappe.msgprint(
        "Stock Receipt submitted. "
        "Batches created and stock updated.",
        alert=True
    )


def on_cancel(doc, method):
    """
    Quarantines batches and reverses current_stock.
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
                "Pharmacy Batch", b.name, "status", "Quarantined"
            )
            item_doc = frappe.get_doc("Pharmacy Item", row.item)
            item_doc.current_stock = max(
                0,
                (item_doc.current_stock or 0) - b.remaining_qty
            )
            item_doc.save(ignore_permissions=True)

    frappe.db.set_value("Stock Receipt", doc.name, {
        "total_amount": 0,
        "total_tax":    0,
        "grand_total":  0
    })
    frappe.db.commit()


def on_adjustment_submit(doc, method):
    """
    Handles Stock Adjustment submit.
    """
    from taal_inventory.utils.fifo import (
        _recalculate_current_stock,
        _log_deduction
    )

    for row in doc.items:
        batch = frappe.get_doc("Pharmacy Batch", row.batch_id)
        balance_before = batch.remaining_qty
        new_qty        = max(0, balance_before - row.qty)

        frappe.db.set_value("Pharmacy Batch", batch.name, {
            "remaining_qty": new_qty,
            "status": "Exhausted" if new_qty == 0 else "Active"
        })

        frappe.db.set_value(
            "Stock Adjustment Item", row.name, {
                "balance_before": balance_before,
                "balance_after":  new_qty
            }
        )

        new_stock = _recalculate_current_stock(row.item)

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


def _sync_item_price(item_code, selling_price, batch_name):
    """
    Create one Item Price per batch using batch_no field.
    Also update default Item Price (no batch) for initial display.
    When Sales Invoice opens — get_fifo_rate() overrides this
    with the correct oldest batch rate.
    """
    try:
        # 1. Batch-specific Item Price
        batch_price = frappe.get_all(
            "Item Price",
            filters={
                "item_code":  item_code,
                "price_list": "Standard Selling",
                "selling":    1,
                "batch_no":   batch_name
            },
            fields=["name"],
            limit_page_length=1
        )

        if batch_price:
            frappe.db.set_value(
                "Item Price",
                batch_price[0].name,
                "price_list_rate",
                selling_price
            )
        else:
            ip = frappe.get_doc({
                "doctype":         "Item Price",
                "item_code":       item_code,
                "price_list":      "Standard Selling",
                "selling":         1,
                "price_list_rate": selling_price,
                "batch_no":        batch_name,
                "currency":        "INR"
            })
            ip.flags.ignore_permissions = True
            ip.insert()

        # 2. Default Item Price (no batch) — fallback display
        default_price = frappe.get_all(
            "Item Price",
            filters={
                "item_code":  item_code,
                "price_list": "Standard Selling",
                "selling":    1,
                "batch_no":   ["in", ["", None]]
            },
            fields=["name"],
            limit_page_length=1
        )

        if default_price:
            frappe.db.set_value(
                "Item Price",
                default_price[0].name,
                "price_list_rate",
                selling_price
            )
        else:
            ip_default = frappe.get_doc({
                "doctype":         "Item Price",
                "item_code":       item_code,
                "price_list":      "Standard Selling",
                "selling":         1,
                "price_list_rate": selling_price,
                "currency":        "INR"
            })
            ip_default.flags.ignore_permissions = True
            ip_default.insert()

        frappe.db.commit()

    except Exception as e:
        frappe.log_error(
            f"Item Price sync failed for {item_code}: {str(e)}",
            "Item Price Sync Error"
        )