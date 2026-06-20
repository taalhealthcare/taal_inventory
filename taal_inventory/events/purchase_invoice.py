"""
taal_inventory/events/purchase_invoice.py

Handles Purchase Invoice submit and cancel for pharmacy stock.

THREE scenarios on submit per pharmacy row:

  SCENARIO 1 — NEW BATCH (batch_id never seen before)
    Creates Pharmacy Batch (tagged with doc.company), updates
    current_stock, syncs Item Price, logs "Stock IN" with company.

  SCENARIO 2 — OPENING STOCK BATCH
    (batch exists, no SR, no PI linked — imported as opening balance)
    PI is finance-only: links PI to batch, logs "Stock IN" (finance-only)
    with company. Does NOT create duplicate batch or change stock.

  SCENARIO 3 — REAL TRANSACTION DUPLICATE
    (batch exists AND linked to another SR or PI)
    Blocks submit with clear error.
"""

import frappe
from frappe.utils import now_datetime, getdate, flt


DEFAULT_WAREHOUSE = "Finished Goods - TM"

_BATCH_CREATE    = "create"
_BATCH_FINANCE   = "finance"
_BATCH_DUPLICATE = "duplicate"


def on_submit(doc, method):
    """
    Triggered when Purchase Invoice is submitted.
    See module docstring for full scenario breakdown.
    """
    from taal_inventory.utils.stock_log import append_stock_log

    if doc.is_return:
        return

    batch_ids_in_invoice = set()
    batches_created      = 0
    finance_only_rows    = 0

    for row in doc.items:

        pharmacy_item_name = frappe.db.get_value(
            "Pharmacy Item",
            {"item_code": row.item_code},
            "name"
        )
        if not pharmacy_item_name:
            continue

        _validate_pharmacy_row(row, doc, batch_ids_in_invoice)

        outcome, existing_batch_doc = _resolve_batch_situation(row, doc)

        if outcome == _BATCH_DUPLICATE:
            return

        # ── SCENARIO 2: Finance-only (opening stock batch) ────────────────────
        if outcome == _BATCH_FINANCE:
            frappe.db.set_value(
                "Pharmacy Batch",
                existing_batch_doc.name,
                "purchase_invoice",
                doc.name
            )
            frappe.db.commit()

            finance_only_rows += 1
            batch_ids_in_invoice.add(row.custom_batch_id)

            current_stock = flt(
                frappe.db.get_value(
                    "Pharmacy Item", pharmacy_item_name, "current_stock"
                ) or 0
            )

            append_stock_log(
                pharmacy_item_name=pharmacy_item_name,
                event_type="Stock IN",
                reference_doctype="Purchase Invoice",
                reference_no=doc.name,
                batch_id=existing_batch_doc.name,
                qty=flt(row.qty),
                balance_after=current_stock,
                cost_price=flt(row.rate or 0),
                selling_price=flt(row.custom_selling_price or 0),
                mrp_price=flt(row.custom_mrp_price or 0),
                warehouse=getattr(existing_batch_doc, "warehouse", ""),
                company=doc.company,
                remarks=(
                    f"Finance-only — opening stock batch "
                    f"{row.custom_batch_id} linked to PI {doc.name}"
                )
            )

            frappe.msgprint(
                f"Row #{row.idx} ({row.item_name}): Batch "
                f"<b>{row.custom_batch_id}</b> already exists as opening "
                f"stock (<b>{existing_batch_doc.name}</b>).<br>"
                f"Purchase Invoice recorded as <b>finance-only</b> — "
                f"no duplicate batch created, stock is unchanged.<br>"
                f"Batch linked to this PI for traceability.",
                title="Finance-Only Row",
                indicator="blue"
            )
            continue

        # ── SCENARIO 1: New batch — create everything ─────────────────────────
        pi = frappe.get_doc("Pharmacy Item", pharmacy_item_name)

        gst_pct = flt(row.igst_rate or 0) or (
            flt(row.cgst_rate or 0) + flt(row.sgst_rate or 0)
        )
        cost_price    = flt(row.rate or 0)
        selling_price = flt(row.custom_selling_price or 0)
        profit        = selling_price - cost_price
        warehouse     = (
            row.warehouse
            or doc.set_warehouse
            or DEFAULT_WAREHOUSE
        )

        batch = frappe.get_doc({
            "doctype":               "Pharmacy Batch",
            "item":                  pharmacy_item_name,
            "item_name":             pi.item_name,
            "batch_id":              row.custom_batch_id,
            "manufacturing_date":    row.custom_manufacturing_date,
            "expiry_date":           row.custom_expiry_date,
            "mrp_price":             flt(row.custom_mrp_price or 0),
            "selling_price":         selling_price,
            "cost_price":            cost_price,
            "profit_per_uom":        profit,
            "gst_pct":               gst_pct,
            "purchsase_tax_pct":     gst_pct,
            "supplier_discount_pct": flt(row.discount_percentage or 0),
            "hsn_sac_code":          row.gst_hsn_code or pi.hsn_sac_code or "",
            "manufacturer_name":     pi.manufacturer_name or "",
            "supplier_name":         doc.supplier,
            "supplier_invoice_no":   doc.bill_no,
            "qty_received":          flt(row.qty),
            "remaining_qty":         flt(row.qty),
            "warehouse":             warehouse,
            "company":               doc.company,
            "purchase_invoice":      doc.name,
            "stock_receipt":         None,
            "status":                "Active",
            "creation_date":         now_datetime()
        })
        batch.insert(ignore_permissions=True)
        frappe.db.commit()

        item_doc = frappe.get_doc("Pharmacy Item", pharmacy_item_name)
        item_doc.current_stock = (
            flt(item_doc.current_stock or 0)
        ) + flt(row.qty)
        item_doc.save(ignore_permissions=True)

        if selling_price > 0:
            _sync_item_price(
                item_code=row.item_code,
                selling_price=selling_price,
                batch_name=batch.name,
                expiry_date=row.custom_expiry_date
            )

        append_stock_log(
            pharmacy_item_name=pharmacy_item_name,
            event_type="Stock IN",
            reference_doctype="Purchase Invoice",
            reference_no=doc.name,
            batch_id=batch.name,
            qty=flt(row.qty),
            balance_after=flt(item_doc.current_stock),
            cost_price=cost_price,
            selling_price=selling_price,
            mrp_price=flt(row.custom_mrp_price or 0),
            warehouse=warehouse,
            company=doc.company,
            remarks=f"Batch {row.custom_batch_id} created via PI {doc.name}"
        )

        batch_ids_in_invoice.add(row.custom_batch_id)
        batches_created += 1

    frappe.db.commit()

    parts = []
    if batches_created:
        parts.append(
            f"{batches_created} Pharmacy Batch(es) created and stock updated"
        )
    if finance_only_rows:
        parts.append(
            f"{finance_only_rows} row(s) recorded as finance-only "
            f"(opening stock — batch already in system, stock unchanged)"
        )
    if parts:
        frappe.msgprint(
            "Purchase Invoice submitted. " + ". ".join(parts) + ".",
            alert=True
        )


def on_cancel(doc, method):
    """
    Triggered when Purchase Invoice is cancelled.

    CASE A — Finance-only (qty_received = 0):
        Unlinks PI from batch. Logs Reversal OUT with qty=0 and company.

    CASE B — Real batch (qty_received > 0):
        Quarantines batch, reverses remaining_qty from current_stock.
        Logs Reversal OUT with actual qty and company.
    """
    from taal_inventory.utils.stock_log import append_stock_log

    if doc.is_return:
        return

    batches = frappe.get_all(
        "Pharmacy Batch",
        filters={"purchase_invoice": doc.name},
        fields=[
            "name", "item", "qty_received", "remaining_qty",
            "status", "cost_price", "selling_price",
            "mrp_price", "warehouse", "company"
        ]
    )

    for b in batches:
        qty_received = flt(b.qty_received or 0)
        remaining    = flt(b.remaining_qty or 0)

        if qty_received == 0:
            # CASE A: Finance-only — unlink only
            frappe.db.set_value(
                "Pharmacy Batch", b.name,
                "purchase_invoice", None
            )

            current_stock = flt(
                frappe.db.get_value(
                    "Pharmacy Item", b.item, "current_stock"
                ) or 0
            )

            append_stock_log(
                pharmacy_item_name=b.item,
                event_type="Reversal OUT",
                reference_doctype="Purchase Invoice",
                reference_no=doc.name,
                batch_id=b.name,
                qty=0,
                balance_after=current_stock,
                cost_price=flt(b.cost_price or 0),
                selling_price=flt(b.selling_price or 0),
                mrp_price=flt(b.mrp_price or 0),
                warehouse=b.warehouse or "",
                company=b.company,
                remarks=(
                    f"Cancelled PI {doc.name} — finance-only batch "
                    f"unlinked (opening stock, no stock change)"
                )
            )

        else:
            # CASE B: Real batch — quarantine + reverse stock
            frappe.db.set_value(
                "Pharmacy Batch", b.name,
                "status", "Quarantined"
            )

            item_doc = frappe.get_doc("Pharmacy Item", b.item)
            item_doc.current_stock = max(
                0,
                flt(item_doc.current_stock or 0) - remaining
            )
            item_doc.save(ignore_permissions=True)

            append_stock_log(
                pharmacy_item_name=b.item,
                event_type="Reversal OUT",
                reference_doctype="Purchase Invoice",
                reference_no=doc.name,
                batch_id=b.name,
                qty=remaining,
                balance_after=flt(item_doc.current_stock),
                cost_price=flt(b.cost_price or 0),
                selling_price=flt(b.selling_price or 0),
                mrp_price=flt(b.mrp_price or 0),
                warehouse=b.warehouse or "",
                company=b.company,
                remarks=(
                    f"Cancelled PI {doc.name} — batch quarantined, "
                    f"{remaining} units reversed from stock"
                )
            )

    frappe.db.commit()


# ─────────────────────────────────────────────────────────────────────────────
# INTERNAL HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _resolve_batch_situation(row, doc):
    """
    Determines what to do with the batch_id on this row.
    Defensive two-stage fetch — avoids Unknown column SQL errors.
    """
    if not row.custom_batch_id:
        return _BATCH_CREATE, None

    existing_name = frappe.db.get_value(
        "Pharmacy Batch",
        {"batch_id": row.custom_batch_id},
        "name"
    )

    if not existing_name:
        return _BATCH_CREATE, None

    existing = frappe.get_doc("Pharmacy Batch", existing_name)

    has_stock_receipt    = bool(getattr(existing, "stock_receipt", None))
    has_purchase_invoice = bool(getattr(existing, "purchase_invoice", None))

    if not has_stock_receipt and not has_purchase_invoice:
        return _BATCH_FINANCE, existing

    if getattr(existing, "stock_receipt", None):
        source_label = f"Stock Receipt <b>{existing.stock_receipt}</b>"
    else:
        source_label = f"Purchase Invoice <b>{existing.purchase_invoice}</b>"

    frappe.throw(
        f"Row #{row.idx} ({row.item_name}): Batch ID "
        f"<b>{row.custom_batch_id}</b> already exists in Pharmacy Batch "
        f"<b>{existing.name}</b>, which is linked to {source_label}.<br>"
        f"Each new purchase shipment must use a unique Batch ID."
    )


def _validate_pharmacy_row(row, doc, batch_ids_in_invoice):
    """Validates one pharmacy row. Throws on first failure."""
    idx  = row.idx
    name = row.item_name or row.item_code

    if not row.custom_batch_id:
        frappe.throw(f"Row #{idx} ({name}): Batch ID is required.")
    if not row.custom_expiry_date:
        frappe.throw(f"Row #{idx} ({name}): Expiry Date is required.")
    if not row.custom_mrp_price or flt(row.custom_mrp_price) <= 0:
        frappe.throw(f"Row #{idx} ({name}): MRP Price must be > 0.")
    if not row.custom_selling_price or flt(row.custom_selling_price) <= 0:
        frappe.throw(f"Row #{idx} ({name}): Selling Price must be > 0.")
    if not row.rate or flt(row.rate) <= 0:
        frappe.throw(f"Row #{idx} ({name}): Rate (cost price) must be > 0.")

    posting = getdate(doc.posting_date)
    expiry  = getdate(row.custom_expiry_date)
    if expiry <= posting:
        frappe.throw(
            f"Row #{idx} ({name}): Expiry ({expiry}) must be "
            f"after Posting Date ({posting})."
        )

    if row.custom_manufacturing_date:
        mfg = getdate(row.custom_manufacturing_date)
        if mfg >= expiry:
            frappe.throw(
                f"Row #{idx} ({name}): MFG date ({mfg}) must be "
                f"before Expiry ({expiry})."
            )

    if row.custom_batch_id in batch_ids_in_invoice:
        frappe.throw(
            f"Row #{idx} ({name}): Batch ID '{row.custom_batch_id}' "
            f"appears more than once in this invoice."
        )


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
                "Item Price", existing[0].name,
                {
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