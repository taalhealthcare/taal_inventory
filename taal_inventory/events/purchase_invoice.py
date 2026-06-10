import frappe
from frappe.utils import now_datetime, getdate


DEFAULT_WAREHOUSE = "Finished Goods - TM"

# ─────────────────────────────────────────────────────────────────────────────
# Batch resolution outcomes — returned by _resolve_batch_situation()
# ─────────────────────────────────────────────────────────────────────────────
_BATCH_CREATE    = "create"     # New batch — create + update stock
_BATCH_FINANCE   = "finance"    # Opening-stock batch — PI is finance-only
_BATCH_DUPLICATE = "duplicate"  # Real-transaction duplicate — block submit


def on_submit(doc, method):
    """
    Triggered when Purchase Invoice is submitted.

    THREE scenarios handled automatically per pharmacy row:

    SCENARIO 1 — NEW BATCH (batch_id never seen before)
        Creates Pharmacy Batch, updates Pharmacy Item current_stock,
        syncs Item Price (Standard Selling).

    SCENARIO 2 — OPENING STOCK BATCH
        (batch exists but has no stock_receipt AND no purchase_invoice —
        i.e. it was imported as opening balance before the system went live)
        PI is the accounting document for stock already in the system.
        Action: links this PI to the existing batch for traceability.
        Does NOT create a duplicate batch or touch current_stock.

    SCENARIO 3 — REAL TRANSACTION DUPLICATE
        (batch exists AND is already linked to a Stock Receipt or another PI)
        Blocks submit with a clear error. Each purchase transaction must
        use a unique Batch ID.

    NON-PHARMACY LINES: no matching Pharmacy Item found → skipped silently.
    PURCHASE RETURNS: doc.is_return = True → entire handler exits immediately.
    WAREHOUSE FALLBACK: row.warehouse → doc.set_warehouse → Finished Goods - TM
    """
    if doc.is_return:
        return

    batch_ids_in_invoice = set()   # Tracks batch IDs seen in THIS invoice
    batches_created      = 0
    finance_only_rows    = 0

    for row in doc.items:

        # ── Step 1: Is this a pharmacy item? ─────────────────────────────────
        pharmacy_item_name = frappe.db.get_value(
            "Pharmacy Item",
            {"item_code": row.item_code},
            "name"
        )
        if not pharmacy_item_name:
            continue   # Not a pharmacy item — skip silently

        # ── Step 2: Validate required fields and date logic ───────────────────
        # Throws on first failure — rolls back the entire submit cleanly.
        _validate_pharmacy_row(row, doc, batch_ids_in_invoice)

        # ── Step 3: Resolve batch situation (new / opening / duplicate) ───────
        outcome, existing_batch_doc = _resolve_batch_situation(row, doc)

        # _BATCH_DUPLICATE: frappe.throw() is called inside
        # _resolve_batch_situation, so execution never reaches here for that
        # case. The line below is a safety guard only.
        if outcome == _BATCH_DUPLICATE:
            return

        # ── SCENARIO 2: Finance-only (opening stock batch) ────────────────────
        if outcome == _BATCH_FINANCE:
            # Link this PI to the existing opening-stock batch so that
            # the batch now has a source document for full traceability.
            # On PI cancel, this link is cleanly removed (see on_cancel).
            frappe.db.set_value(
                "Pharmacy Batch",
                existing_batch_doc.name,
                "purchase_invoice",
                doc.name
            )
            frappe.db.commit()

            finance_only_rows += 1
            batch_ids_in_invoice.add(row.custom_batch_id)

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
            continue   # Move to next row

        # ── SCENARIO 1: New batch — create everything ─────────────────────────
        pi = frappe.get_doc("Pharmacy Item", pharmacy_item_name)

        # Derive computed values
        # GST: use IGST if inter-state, else CGST + SGST for intra-state
        gst_pct = (row.igst_rate or 0) or (
            (row.cgst_rate or 0) + (row.sgst_rate or 0)
        )
        cost_price    = row.rate or 0
        selling_price = row.custom_selling_price or 0
        profit        = selling_price - cost_price
        warehouse     = (
            row.warehouse
            or doc.set_warehouse
            or DEFAULT_WAREHOUSE
        )

        # Create Pharmacy Batch
        batch = frappe.get_doc({
            "doctype":               "Pharmacy Batch",
            "item":                  pharmacy_item_name,
            "item_name":             pi.item_name,
            "batch_id":              row.custom_batch_id,
            "manufacturing_date":    row.custom_manufacturing_date,
            "expiry_date":           row.custom_expiry_date,
            "mrp_price":             row.custom_mrp_price or 0,
            "selling_price":         selling_price,
            "cost_price":            cost_price,
            "profit_per_uom":        profit,
            "gst_pct":               gst_pct,
            "purchase_tax_pct":      gst_pct,
            "supplier_discount_pct": row.discount_percentage or 0,
            "hsn_sac_code":          row.gst_hsn_code or pi.hsn_sac_code or "",
            "manufacturer_name":     pi.manufacturer_name or "",
            "supplier_name":         doc.supplier,
            "supplier_invoice_no":   doc.bill_no,
            "qty_received":          row.qty,
            "remaining_qty":         row.qty,
            "warehouse":             warehouse,
            "purchase_invoice":      doc.name,
            "stock_receipt":         None,
            "status":                "Active",
            "creation_date":         now_datetime()
        })
        batch.insert(ignore_permissions=True)
        frappe.db.commit()

        # Update current_stock on Pharmacy Item
        item_doc = frappe.get_doc("Pharmacy Item", pharmacy_item_name)
        item_doc.current_stock = (
            item_doc.current_stock or 0
        ) + (row.qty or 0)
        item_doc.save(ignore_permissions=True)

        # Sync Item Price (Standard Selling) — mirrors stock_receipt pattern
        if selling_price and selling_price > 0:
            _sync_item_price(
                item_code=row.item_code,
                selling_price=selling_price,
                batch_name=batch.name,
                expiry_date=row.custom_expiry_date
            )

        batch_ids_in_invoice.add(row.custom_batch_id)
        batches_created += 1

    frappe.db.commit()

    # ── Final summary alert ───────────────────────────────────────────────────
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

    CASE A — Finance-only batch (qty_received = 0):
        This PI only linked itself to a pre-existing opening-stock batch.
        On cancel: unlink the PI from that batch.
        Do NOT quarantine or touch stock.

    CASE B — Real batch (qty_received > 0):
        This PI created the batch and added to current_stock.
        On cancel: quarantine the batch + reverse remaining_qty
        from current_stock (floored at 0).
        Mirrors stock_receipt.on_cancel exactly.

    PURCHASE RETURNS: exits immediately.
    """
    if doc.is_return:
        return

    # Fetch all batches linked to this PI using only the purchase_invoice
    # field — which is guaranteed to exist after Migrate runs.
    batches = frappe.get_all(
        "Pharmacy Batch",
        filters={"purchase_invoice": doc.name},
        fields=["name", "item", "qty_received", "remaining_qty", "status"]
    )

    for b in batches:

        if (b.qty_received or 0) == 0:
            # CASE A: Finance-only — just unlink
            frappe.db.set_value(
                "Pharmacy Batch",
                b.name,
                "purchase_invoice",
                None
            )

        else:
            # CASE B: Real batch — quarantine + reverse stock
            frappe.db.set_value(
                "Pharmacy Batch",
                b.name,
                "status",
                "Quarantined"
            )
            item_doc = frappe.get_doc("Pharmacy Item", b.item)
            item_doc.current_stock = max(
                0,
                (item_doc.current_stock or 0) - (b.remaining_qty or 0)
            )
            item_doc.save(ignore_permissions=True)

    frappe.db.commit()


# ─────────────────────────────────────────────────────────────────────────────
# INTERNAL HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _resolve_batch_situation(row, doc):
    """
    Determines what to do with the batch_id on this row.

    Returns a tuple: (outcome_constant, existing_batch_doc_or_None)

    Logic:
      - batch_id not in DB at all              → _BATCH_CREATE,  None
      - batch_id exists, no SR, no PI          → _BATCH_FINANCE, <batch doc>
      - batch_id exists, has SR or PI          → frappe.throw()

    DEFENSIVE APPROACH:
    We fetch fields in two stages:
      Stage 1: fetch only "name" — guaranteed to exist on every DocType.
      Stage 2: use frappe.get_doc() on the found record to access all fields
               safely via Python attribute access (no raw SQL column list).
    This avoids OperationalError: (1054, "Unknown column 'X' in 'SELECT'")
    if a field exists in the DocType definition but Migrate hasn't run yet.
    """
    if not row.custom_batch_id:
        return _BATCH_CREATE, None

    # Stage 1: Does this batch_id exist at all?
    existing_name = frappe.db.get_value(
        "Pharmacy Batch",
        {"batch_id": row.custom_batch_id},
        "name"
    )

    if not existing_name:
        # Brand new batch ID — safe to create
        return _BATCH_CREATE, None

    # Stage 2: Load the full batch document safely
    existing = frappe.get_doc("Pharmacy Batch", existing_name)

    # getattr with defaults prevents AttributeError if a field doesn't exist
    has_stock_receipt    = bool(getattr(existing, "stock_receipt", None))
    has_purchase_invoice = bool(getattr(existing, "purchase_invoice", None))

    if not has_stock_receipt and not has_purchase_invoice:
        # No source document = opening stock import
        # This PI is the accounting document for that physical stock
        return _BATCH_FINANCE, existing

    # Batch is already linked to a real prior transaction — block submit
    if getattr(existing, "stock_receipt", None):
        source_label = (
            f"Stock Receipt <b>{existing.stock_receipt}</b>"
        )
    else:
        source_label = (
            f"Purchase Invoice <b>{existing.purchase_invoice}</b>"
        )

    frappe.throw(
        f"Row #{row.idx} ({row.item_name}): Batch ID "
        f"<b>{row.custom_batch_id}</b> already exists in Pharmacy Batch "
        f"<b>{existing.name}</b>, which is linked to {source_label}.<br>"
        f"Each new purchase shipment must use a unique Batch ID."
    )


def _validate_pharmacy_row(row, doc, batch_ids_in_invoice):
    """
    Validates one pharmacy row before any DB write.
    Throws frappe.ValidationError on the first failure.
    All messages are prefixed with row index and item name.

    Checks (in order):
      1. custom_batch_id is set
      2. custom_expiry_date is set
      3. custom_mrp_price > 0
      4. custom_selling_price > 0
      5. rate (cost price) > 0
      6. expiry_date strictly after posting_date
      7. manufacturing_date < expiry_date (only if MFG date provided)
      8. batch_id not duplicated within this same invoice
    """
    idx  = row.idx
    name = row.item_name or row.item_code

    # 1. Batch ID
    if not row.custom_batch_id:
        frappe.throw(
            f"Row #{idx} ({name}): "
            f"Batch ID is required for pharmacy items."
        )

    # 2. Expiry Date
    if not row.custom_expiry_date:
        frappe.throw(
            f"Row #{idx} ({name}): "
            f"Expiry Date is required for pharmacy items."
        )

    # 3. MRP Price
    if not row.custom_mrp_price or row.custom_mrp_price <= 0:
        frappe.throw(
            f"Row #{idx} ({name}): "
            f"MRP Price is required and must be greater than 0."
        )

    # 4. Selling Price
    if not row.custom_selling_price or row.custom_selling_price <= 0:
        frappe.throw(
            f"Row #{idx} ({name}): "
            f"Selling Price is required and must be greater than 0."
        )

    # 5. Rate (cost price)
    if not row.rate or row.rate <= 0:
        frappe.throw(
            f"Row #{idx} ({name}): "
            f"Rate (cost price) must be greater than 0."
        )

    # 6. Expiry must be strictly after posting date
    posting = getdate(doc.posting_date)
    expiry  = getdate(row.custom_expiry_date)
    if expiry <= posting:
        frappe.throw(
            f"Row #{idx} ({name}): "
            f"Expiry Date ({expiry}) must be after "
            f"Posting Date ({posting})."
        )

    # 7. Manufacturing Date (optional) must be before Expiry Date
    if row.custom_manufacturing_date:
        mfg = getdate(row.custom_manufacturing_date)
        if mfg >= expiry:
            frappe.throw(
                f"Row #{idx} ({name}): "
                f"Manufacturing Date ({mfg}) must be before "
                f"Expiry Date ({expiry})."
            )

    # 8. No duplicate batch_id within this same invoice
    if row.custom_batch_id in batch_ids_in_invoice:
        frappe.throw(
            f"Row #{idx} ({name}): "
            f"Batch ID '{row.custom_batch_id}' appears more than once "
            f"in this invoice. Each row must have a unique Batch ID."
        )


def _sync_item_price(item_code, selling_price, batch_name, expiry_date):
    """
    Create or update Item Price in the Standard Selling price list.
    VERBATIM copy of stock_receipt._sync_item_price for consistency.

    Does NOT use batch_no field — that links to ERPNext's native Batch
    doctype, not our Pharmacy Batch. Uses valid_upto for expiry visibility.
    FEFO rate selection is handled by get_fifo_rate() in sales_invoice.py.
    """
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
                existing[0].name,
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