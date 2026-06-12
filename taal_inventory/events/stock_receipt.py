"""
taal_inventory/events/stock_receipt.py

Handles Stock Receipt submit, cancel, and Stock Adjustment submit.

KEY FIX IN THIS VERSION:
  Stock Receipt Item.item stores the item_code value (e.g. TPCHC-PI-000054)
  NOT the Pharmacy Item document name (e.g. TPCHC-PI-2026-0055).
  All functions now call _get_pharmacy_item_name(row.item) first to resolve
  the correct document name before any frappe.get_doc() call.

  Root cause: when selecting a Pharmacy Item on the SR form, the Link field
  stores whatever value the user selected — which may be the item_code
  rather than the Pharmacy Item document name, since both fields exist.
"""

import frappe
from frappe.utils import now_datetime, flt


def _get_pharmacy_item_name(row_item):
    """
    Resolves the correct Pharmacy Item document name from a Stock Receipt
    Item row's `item` field value.

    Stock Receipt stores either:
      - The Pharmacy Item document name  (e.g. TPCHC-PI-2026-0055)  — new
      - The Pharmacy Item item_code      (e.g. TPCHC-PI-000054)     — old

    This function tries both and returns the document name, or None if the
    Pharmacy Item genuinely doesn't exist.

    Parameters
    ----------
    row_item : str
        The value from Stock Receipt Item.item field

    Returns
    -------
    str or None
        The Pharmacy Item document name (name field), or None if not found
    """
    if not row_item:
        return None

    # Strategy 1: Direct name lookup — works if item IS the document name
    direct = frappe.db.get_value("Pharmacy Item", row_item, "name")
    if direct:
        return direct

    # Strategy 2: item_code lookup — works if item is the item_code value
    by_code = frappe.db.get_value(
        "Pharmacy Item",
        {"item_code": row_item},
        "name"
    )
    return by_code  # None if not found in either lookup


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
        # ── Resolve Pharmacy Item document name ───────────────────────────────
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
        # on Pharmacy Batch — do not change until DocType rename + Migrate
        batch = frappe.get_doc({
            "doctype":               "Pharmacy Batch",
            "item":                  pi_name,         # use resolved name
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

        # Log Stock IN to deduction_log
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
        # Resolve Pharmacy Item name (same fix as on_submit)
        pi_name = _get_pharmacy_item_name(row.item)
        if not pi_name:
            # Item doesn't exist — skip gracefully, log the anomaly
            frappe.log_error(
                f"SR Cancel: Pharmacy Item '{row.item}' not found "
                f"(SR: {doc.name}, row #{row.idx}). Skipped.",
                "SR Cancel — Item Not Found"
            )
            continue

        # Find batches created by this SR for this item
        # Filter on stock_receipt + resolved pi_name (not row.item)
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

            # Quarantine the batch
            frappe.db.set_value(
                "Pharmacy Batch", b.name,
                "status", "Quarantined"
            )

            # Reduce current_stock by remaining qty, floor at 0
            item_doc = frappe.get_doc("Pharmacy Item", pi_name)
            item_doc.current_stock = max(
                0,
                flt(item_doc.current_stock or 0) - remaining
            )
            item_doc.save(ignore_permissions=True)

            # Log Reversal OUT
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

    # Zero out SR totals
    frappe.db.set_value("Stock Receipt", doc.name, {
        "total_amount": 0,
        "total_tax":    0,
        "grand_total":  0
    })
    frappe.db.commit()


def on_adjustment_submit(doc, method):
    """
    Handles Stock Adjustment submit.
    Deducts stock per adjustment rows.
    Logs "Stock OUT" via existing _log_deduction in fifo.py.

    NOTE: Stock Adjustment rows reference Pharmacy Batch directly,
    and Pharmacy Batch.item is always the document name (not item_code).
    No item_code resolution needed here.
    """
    from taal_inventory.utils.fifo import (
        _recalculate_current_stock,
        _log_deduction
    )

    for row in doc.items:
        batch          = frappe.get_doc("Pharmacy Batch", row.batch_id)
        balance_before = flt(batch.remaining_qty)
        new_qty        = max(0, balance_before - flt(row.qty))

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
            qty_deducted=flt(row.qty),
            reference_doctype="Stock Adjustment",
            reference_no=doc.name,
            patient_name="",
            balance_after=new_stock,
            deducted_by=frappe.session.user
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