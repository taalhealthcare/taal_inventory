"""
taal_inventory/events/stock_receipt.py

Handles Stock Receipt submit, cancel, and Stock Adjustment submit.

FIXES IN THIS VERSION:
  1. _get_pharmacy_item_name() resolves PI name from item_code or direct name
  2. on_adjustment_submit now uses append_stock_log directly instead of
     _log_deduction from fifo.py — ensures event_type, cost_price,
     selling_price, mrp_price, warehouse, remarks are all written correctly
  3. flt() on all numeric reads
  4. "purchsase_tax_pct" (typo) used to match Pharmacy Batch DB column
"""

import frappe
from frappe.utils import now_datetime, flt


def _get_pharmacy_item_name(row_item):
    """
    Resolves the correct Pharmacy Item document name from a Stock Receipt
    Item row's `item` field value.

    Stock Receipt stores either:
      - The Pharmacy Item document name  (e.g. TPCHC-PI-2026-0055) — new
      - The Pharmacy Item item_code      (e.g. TPCHC-PI-000054)    — old

    This function tries both and returns the document name, or None.

    NOTE: Stock Adjustment Item.item is a direct Link → Pharmacy Item
    and always stores the document name — no resolution needed there.
    This function is only needed for Stock Receipt.
    """
    if not row_item:
        return None

    # Strategy 1: Direct name lookup
    direct = frappe.db.get_value("Pharmacy Item", row_item, "name")
    if direct:
        return direct

    # Strategy 2: item_code lookup
    by_code = frappe.db.get_value(
        "Pharmacy Item",
        {"item_code": row_item},
        "name"
    )
    return by_code


def on_submit(doc, method):
    """
    Triggered when Stock Receipt is submitted.
    Creates one Pharmacy Batch per line item.
    Updates current_stock on each Pharmacy Item.
    Syncs Item Price (Standard Selling).
    Calculates Stock Receipt totals.
    Logs each batch creation as "Stock IN" in deduction_log.
    """
    from taal_inventory.utils.stock_log import append_stock_log

    total_amount = 0
    total_tax    = 0

    for row in doc.items:
        # Resolve Pharmacy Item document name
        pi_name = _get_pharmacy_item_name(row.item)
        if not pi_name:
            frappe.throw(
                f"Row #{row.idx}: Pharmacy Item '{row.item}' not found. "
                f"Please select a valid Pharmacy Item."
            )

        pi = frappe.get_doc("Pharmacy Item", pi_name)

        profit     = flt(row.selling_price or 0) - flt(row.cost_price or 0)
        line_total = flt(row.qty_received or 0) * flt(row.cost_price or 0)
        gst_pct    = flt(row.gst_pct or 0)
        pur_tax    = flt(row.purchase_tax_pct or 0)
        tax_amount = line_total * ((gst_pct + pur_tax) / 100)

        total_amount += line_total
        total_tax    += tax_amount

        # Create Pharmacy Batch
        # NOTE: "purchsase_tax_pct" (extra 's') is the ACTUAL DB fieldname
        batch = frappe.get_doc({
            "doctype":               "Pharmacy Batch",
            "item":                  pi_name,
            "item_name":             pi.item_name,
            "batch_id":              row.batch_id,
            "manufacturing_date":    row.manufacturing_date,
            "expiry_date":           row.expiry_date,
            "mrp_price":             flt(row.mrp_price or 0),
            "selling_price":         flt(row.selling_price or 0),
            "cost_price":            flt(row.cost_price or 0),
            "profit_per_uom":        profit,
            "gst_pct":               gst_pct,
            "purchsase_tax_pct":     pur_tax,
            "supplier_discount_pct": flt(row.discount_pct or 0),
            "hsn_sac_code":          pi.hsn_sac_code or "",
            "manufacturer_name":     pi.manufacturer_name or "",
            "supplier_name":         doc.supplier,
            "supplier_invoice_no":   doc.supplier_invoice_no,
            "qty_received":          flt(row.qty_received),
            "remaining_qty":         flt(row.qty_received),
            "warehouse":             doc.warehouse,
            "stock_receipt":         doc.name,
            "status":                "Active",
            "creation_date":         now_datetime()
        })
        batch.insert(ignore_permissions=True)
        frappe.db.commit()

        # Update line_total on the SR row
        frappe.db.set_value(
            "Stock Receipt Item",
            row.name,
            "line_total",
            round(line_total, 2)
        )

        # Update current_stock on Pharmacy Item
        item_doc = frappe.get_doc("Pharmacy Item", pi_name)
        item_doc.current_stock = (
            flt(item_doc.current_stock or 0)
        ) + flt(row.qty_received)
        item_doc.save(ignore_permissions=True)

        # Sync Item Price (Standard Selling)
        if row.selling_price and flt(row.selling_price) > 0:
            actual_item_code = frappe.db.get_value(
                "Pharmacy Item", pi_name, "item_code"
            )
            if actual_item_code:
                _sync_item_price(
                    item_code=actual_item_code,
                    selling_price=flt(row.selling_price),
                    batch_name=batch.name,
                    expiry_date=row.expiry_date
                )

        # Log Stock IN
        append_stock_log(
            pharmacy_item_name=pi_name,
            event_type="Stock IN",
            reference_doctype="Stock Receipt",
            reference_no=doc.name,
            batch_id=batch.name,
            qty=flt(row.qty_received),
            balance_after=flt(item_doc.current_stock),
            cost_price=flt(row.cost_price or 0),
            selling_price=flt(row.selling_price or 0),
            mrp_price=flt(row.mrp_price or 0),
            warehouse=doc.warehouse or "",
            remarks=f"Batch {row.batch_id} created via SR {doc.name}"
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
    Triggered when Stock Receipt is cancelled.
    Quarantines batches, reverses current_stock.
    Logs "Reversal OUT" per batch quarantined.
    """
    from taal_inventory.utils.stock_log import append_stock_log

    for row in doc.items:
        pi_name = _get_pharmacy_item_name(row.item)
        if not pi_name:
            frappe.log_error(
                f"SR Cancel: Pharmacy Item '{row.item}' not found "
                f"(SR: {doc.name}, row #{row.idx}). Skipped.",
                "SR Cancel — Item Not Found"
            )
            continue

        batches = frappe.get_all(
            "Pharmacy Batch",
            filters={
                "stock_receipt": doc.name,
                "item":          pi_name
            },
            fields=[
                "name", "remaining_qty",
                "cost_price", "selling_price",
                "mrp_price", "warehouse"
            ]
        )

        for b in batches:
            remaining = flt(b.remaining_qty or 0)

            frappe.db.set_value(
                "Pharmacy Batch", b.name,
                "status", "Quarantined"
            )

            item_doc = frappe.get_doc("Pharmacy Item", pi_name)
            item_doc.current_stock = max(
                0,
                flt(item_doc.current_stock or 0) - remaining
            )
            item_doc.save(ignore_permissions=True)

            append_stock_log(
                pharmacy_item_name=pi_name,
                event_type="Reversal OUT",
                reference_doctype="Stock Receipt",
                reference_no=doc.name,
                batch_id=b.name,
                qty=remaining,
                balance_after=flt(item_doc.current_stock),
                cost_price=flt(b.cost_price or 0),
                selling_price=flt(b.selling_price or 0),
                mrp_price=flt(b.mrp_price or 0),
                warehouse=b.warehouse or "",
                remarks=f"Cancelled SR {doc.name} — batch quarantined"
            )

    frappe.db.set_value("Stock Receipt", doc.name, {
        "total_amount": 0,
        "total_tax":    0,
        "grand_total":  0
    })
    frappe.db.commit()


def on_adjustment_submit(doc, method):
    """
    Handles Stock Adjustment submit.

    For each row in doc.items:
      1. Reads Pharmacy Batch remaining_qty (balance_before)
      2. Deducts row.qty → new remaining_qty (balance_after)
      3. Sets batch status Active or Exhausted
      4. Writes balance_before and balance_after to Stock Adjustment Item row
      5. Recalculates Pharmacy Item.current_stock from all active batches
      6. Logs "Stock OUT" with full pricing to deduction_log

    NOTE: Stock Adjustment Item.item is a direct Link → Pharmacy Item
    and stores the document name directly (e.g. TPCHC-PI-2026-0055).
    No _get_pharmacy_item_name() resolution needed here.

    NOTE: Previously used _log_deduction from fifo.py which did not write
    event_type, cost_price, selling_price, mrp_price, warehouse, remarks.
    Now uses append_stock_log directly for complete, consistent logging.
    """
    from taal_inventory.utils.stock_log import append_stock_log

    for row in doc.items:
        # row.item is the Pharmacy Item document name (direct Link)
        # row.batch_id is the Pharmacy Batch document name (direct Link)
        batch = frappe.get_doc("Pharmacy Batch", row.batch_id)

        balance_before = flt(batch.remaining_qty)
        new_qty        = max(0, balance_before - flt(row.qty))
        new_status     = "Exhausted" if new_qty == 0 else "Active"

        # Update batch
        frappe.db.set_value("Pharmacy Batch", batch.name, {
            "remaining_qty": new_qty,
            "status":        new_status
        })

        # Write balance before/after to the adjustment row
        frappe.db.set_value(
            "Stock Adjustment Item", row.name, {
                "balance_before": balance_before,
                "balance_after":  new_qty
            }
        )

        # Recalculate Pharmacy Item current_stock from all active batches
        result = frappe.db.sql("""
            SELECT COALESCE(SUM(remaining_qty), 0) as total
            FROM `tabPharmacy Batch`
            WHERE item = %s AND status = 'Active'
        """, row.item, as_dict=True)
        new_stock = float(result[0].total) if result else 0.0

        frappe.db.set_value(
            "Pharmacy Item",
            row.item,
            "current_stock",
            new_stock
        )

        # Build remarks from adjustment type and reason
        adj_type = doc.adjustment_type or ""
        reason   = doc.reason or ""
        remarks  = f"{adj_type} adjustment — {reason}" if reason else adj_type

        # Log Stock OUT with full pricing from the batch
        append_stock_log(
            pharmacy_item_name=row.item,
            event_type="Stock OUT",
            reference_doctype="Stock Adjustment",
            reference_no=doc.name,
            batch_id=row.batch_id,
            qty=flt(row.qty),
            balance_after=new_stock,
            cost_price=flt(batch.cost_price or 0),
            selling_price=flt(batch.selling_price or 0),
            mrp_price=flt(batch.mrp_price or 0),
            warehouse=doc.warehouse or batch.warehouse or "",
            remarks=remarks
        )

    frappe.db.commit()


def _sync_item_price(item_code, selling_price, batch_name, expiry_date):
    """Create or update Item Price in Standard Selling price list."""
    try:
        existing = frappe.get_all(
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

        if existing:
            frappe.db.set_value(
                "Item Price",
                existing[0].name, {
                    "price_list_rate": selling_price,
                    "valid_upto":      expiry_date
                }
            )
        else:
            ip = frappe.get_doc({
                "doctype":         "Item Price",
                "item_code":       item_code,
                "price_list":      "Standard Selling",
                "selling":         1,
                "price_list_rate": selling_price,
                "valid_upto":      expiry_date,
                "currency":        "INR"
            })
            ip.flags.ignore_permissions = True
            ip.flags.ignore_mandatory   = True
            ip.insert()

        frappe.db.commit()

        frappe.logger().info(
            f"Item Price synced: {item_code} "
            f"rate {selling_price} expiry {expiry_date}"
        )

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