"""
taal_inventory/events/stock_receipt.py

Handles Stock Receipt submit, cancel, and Stock Adjustment submit.

UPDATED FOR MULTI-COMPANY SUPPORT:
  Stock Receipt now has a mandatory `company` field (added via
  Customize Form). Every new Pharmacy Batch created from a Stock
  Receipt now correctly carries the `company` value, so multi-company
  stock tracking (Point 3 — Inter-Company Transfer) works correctly
  going forward.

  Also calls recalculate_company_stock() to keep the new
  Pharmacy Item.company_stock child table in sync on every
  Stock Receipt submit/cancel.

PRIOR FIXES RETAINED:
  - _get_pharmacy_item_name() resolves PI name from item_code or direct name
  - on_adjustment_submit uses append_stock_log directly
  - flt() on all numeric reads
  - "purchsase_tax_pct" (typo) used to match Pharmacy Batch DB column
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
    """
    if not row_item:
        return None

    direct = frappe.db.get_value("Pharmacy Item", row_item, "name")
    if direct:
        return direct

    by_code = frappe.db.get_value(
        "Pharmacy Item",
        {"item_code": row_item},
        "name"
    )
    return by_code


def on_submit(doc, method):
    """
    Triggered when Stock Receipt is submitted.
    Creates one Pharmacy Batch per line item, tagged with doc.company.
    Updates current_stock on each Pharmacy Item (global + per-company).
    Syncs Item Price (Standard Selling).
    Calculates Stock Receipt totals.
    Logs each batch creation as "Stock IN" in deduction_log.
    """
    from taal_inventory.utils.stock_log import append_stock_log
    from taal_inventory.utils.company_stock import recalculate_company_stock

    if not doc.company:
        frappe.throw(
            "Company is mandatory on Stock Receipt. "
            "Please select which company this stock belongs to."
        )

    total_amount = 0
    total_tax    = 0

    for row in doc.items:
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

        # Create Pharmacy Batch — NOW tagged with company
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
            "company":               doc.company,   # ← CRITICAL FIX
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

        # Refresh company_stock child table AND global current_stock
        new_company_stock = recalculate_company_stock(pi_name, doc.company)

        # Log Stock IN
        append_stock_log(
            pharmacy_item_name=pi_name,
            event_type="Stock IN",
            reference_doctype="Stock Receipt",
            reference_no=doc.name,
            batch_id=batch.name,
            qty=flt(row.qty_received),
            balance_after=new_company_stock,
            cost_price=flt(row.cost_price or 0),
            selling_price=flt(row.selling_price or 0),
            mrp_price=flt(row.mrp_price or 0),
            warehouse=doc.warehouse or "",
            remarks=f"Batch {row.batch_id} created via SR {doc.name} "
                    f"for company {doc.company}"
        )

    grand_total = total_amount + total_tax
    frappe.db.set_value("Stock Receipt", doc.name, {
        "total_amount": round(total_amount, 2),
        "total_tax":    round(total_tax, 2),
        "grand_total":  round(grand_total, 2)
    })

    frappe.db.commit()
    frappe.msgprint(
        f"Stock Receipt submitted for {doc.company}. "
        f"Batches created and stock updated.",
        alert=True
    )


def on_cancel(doc, method):
    """
    Triggered when Stock Receipt is cancelled.
    Quarantines batches, reverses current_stock (global + per-company).
    Logs "Reversal OUT" per batch quarantined.
    """
    from taal_inventory.utils.stock_log import append_stock_log
    from taal_inventory.utils.company_stock import recalculate_company_stock

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
                "name", "remaining_qty", "company",
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

            # Refresh company_stock for this batch's company
            new_company_stock = recalculate_company_stock(pi_name, b.company)

            append_stock_log(
                pharmacy_item_name=pi_name,
                event_type="Reversal OUT",
                reference_doctype="Stock Receipt",
                reference_no=doc.name,
                batch_id=b.name,
                qty=remaining,
                balance_after=new_company_stock,
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
    Deducts stock per adjustment rows.
    Logs "Stock OUT" with full pricing to deduction_log.
    Refreshes company_stock for the batch's company.

    NOTE: Stock Adjustment Item.item is a direct Link → Pharmacy Item.
    Stock Adjustment Item.batch_id is a direct Link → Pharmacy Batch,
    which already carries its own `company` field — read from there.
    """
    from taal_inventory.utils.stock_log import append_stock_log
    from taal_inventory.utils.company_stock import recalculate_company_stock

    for row in doc.items:
        batch = frappe.get_doc("Pharmacy Batch", row.batch_id)

        balance_before = flt(batch.remaining_qty)
        new_qty        = max(0, balance_before - flt(row.qty))
        new_status     = "Exhausted" if new_qty == 0 else "Active"

        frappe.db.set_value("Pharmacy Batch", batch.name, {
            "remaining_qty": new_qty,
            "status":        new_status
        })

        frappe.db.set_value(
            "Stock Adjustment Item", row.name, {
                "balance_before": balance_before,
                "balance_after":  new_qty
            }
        )

        # Refresh company_stock for the batch's own company
        # (Stock Adjustment doesn't need its own company field —
        # the batch being adjusted already belongs to a company)
        new_company_stock = recalculate_company_stock(row.item, batch.company)

        adj_type = doc.adjustment_type or ""
        reason   = doc.reason or ""
        remarks  = f"{adj_type} adjustment — {reason}" if reason else adj_type

        append_stock_log(
            pharmacy_item_name=row.item,
            event_type="Stock OUT",
            reference_doctype="Stock Adjustment",
            reference_no=doc.name,
            batch_id=row.batch_id,
            qty=flt(row.qty),
            balance_after=new_company_stock,
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