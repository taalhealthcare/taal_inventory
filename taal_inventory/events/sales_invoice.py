"""
taal_inventory/events/sales_invoice.py

Handles Sales Invoice validate, submit and cancel for pharmacy stock.

  validate  : USER-CHOSEN BATCH per row (Sales Invoice Item.
              custom_pharmacy_batch). Any row left empty is filled with
              the nearest-expiry batch (FEFO) so Woo/API/loader invoices
              still work. Then every batch is checked: same item, same
              company, Active, unexpired, and the summed qty across rows
              on one batch must fit its remaining_qty. ONE BATCH PER ROW:
              a row never spills into a second batch. Runs on save and
              again on submit.
  on_submit : Deducts each row's qty from ITS chosen batch. Logs
              "Stock OUT" per row. COGS: posts Dr Cost of Goods Sold /
              Cr Stock In Hand (Inventory) at that batch's cost_price,
              per company.
  on_cancel : Restores batch qty and stock from the Deduction Log.
              Logs "Reversal IN" per batch restored.
              COGS: cancels the COGS Journal Entry.

  Fallback  : if the custom_pharmacy_batch field is not installed yet
              (fixtures not migrated), submit falls back to the old FEFO
              engine (_deduct_fefo) and logs that it did so.

=============================================================================
THREE CORRECTNESS RULES THIS FILE FOLLOWS
=============================================================================
1. BATCHES ARE PER-COMPANY. Every batch lookup and check filters on
   doc.company. Without this, a TPC invoice could consume an NMP batch,
   corrupting both companies' stock and posting COGS to the wrong ledger.

2. NO frappe.db.commit() ANYWHERE. If line 4 of a 5-line invoice throws
   "Insufficient stock", committed lines 1-3 could not be rolled back and
   stock vanished with no document behind it. Frappe commits at the end of
   the request, so submit and cancel are now atomic.

3. STOCK REFRESH GOES THROUGH rebuild_item_stock(). Writing current_stock
   directly left the company_stock child table stale on every single sale.
=============================================================================
"""

import frappe
from frappe.utils import today, now_datetime, flt, getdate


BATCH_FIELD     = "custom_pharmacy_batch"
BATCH_EXPIRY    = "custom_batch_expiry"
BATCH_AVAILABLE = "custom_batch_available"

_BATCH_FIELDS = [
    "name", "batch_id", "item", "company", "status",
    "remaining_qty", "expiry_date",
    "selling_price", "cost_price", "mrp_price", "warehouse",
]


def _batch_field_installed():
    """True once the custom_pharmacy_batch fixture has been migrated."""
    return frappe.get_meta("Sales Invoice Item").has_field(BATCH_FIELD)


def _pharmacy_item_for(item_code):
    """
    Returns (pharmacy_item_name, is_service_item) for an Item code,
    or (None, 0) when the item has no Pharmacy Item record.
    """
    if not item_code:
        return None, 0
    row = frappe.db.get_value(
        "Pharmacy Item", {"item_code": item_code},
        ["name", "is_service_item"], as_dict=True,
    )
    if not row:
        return None, 0
    return row.name, int(row.is_service_item or 0)


def _sellable_batch_filters(pi_name, company):
    """Active, has stock, not expired, this item, this company."""
    f = {
        "item":          pi_name,
        "status":        "Active",
        "remaining_qty": [">", 0],
        "expiry_date":   [">", today()],
    }
    if company:
        f["company"] = company
    return f


def _nearest_expiry_batch(pi_name, company):
    """The FEFO default: nearest expiry sellable batch, or None."""
    rows = frappe.get_all(
        "Pharmacy Batch",
        filters=_sellable_batch_filters(pi_name, company),
        fields=_BATCH_FIELDS,
        order_by="expiry_date ASC, creation ASC",
        limit_page_length=1,
    )
    return rows[0] if rows else None


def _batch_payload(batch):
    """Uniform dict the JS consumes for both default and chosen batch."""
    if not batch:
        return {
            "blocked":       True,
            "rate":          0,
            "batch_name":    "",
            "batch_id":      "",
            "remaining_qty": 0,
            "expiry_date":   "",
        }
    return {
        "blocked":       False,
        "rate":          flt(batch.selling_price),
        "batch_name":    batch.name,
        "batch_id":      batch.batch_id or "",
        "remaining_qty": flt(batch.remaining_qty),
        "expiry_date":   str(batch.expiry_date) if batch.expiry_date else "",
    }


# Per-company accounts for the COGS-on-sale posting.
# Cr side uses the non-protected "Stock In Hand (Inventory)" account
# (the same account Purchase Invoices sweep stock value INTO), so stock
# value flows out at cost when sold.
_COGS_CONFIG = {
    "Taalplus CHC Private Limited": {
        "cogs":        "Cost of Goods Sold - TPC",
        "inventory":   "Stock In Hand (Inventory) - TPC",
        "cost_center": "Main - TPC",
    },
    "NMP_Taal Plus": {
        "cogs":        "Cost of Goods Sold - NP",
        "inventory":   "Stock In Hand (Inventory) - NP",
        "cost_center": "Main - NP",
    },
}


def _resolve_config(company, config, context=""):
    """
    Case-insensitive company config lookup.

    A plain config[company] lookup is case-sensitive, so a Company record
    named "TAALPLUS CHC Private Limited" would silently miss a key written
    as "Taalplus CHC Private Limited" — no error, no GL entry, no trace.
    This matches case-insensitively and LOGS a real miss instead.
    """
    if not company:
        return None

    if company in config:
        return config[company]

    key = company.strip().lower()
    for k, v in config.items():
        if k.strip().lower() == key:
            return v

    frappe.log_error(
        f"No account configuration found for company '{company}'. "
        f"Configured companies: {list(config.keys())}. "
        f"Context: {context}",
        "Company Config Missing"
    )
    return None


# =============================================================================
#  CLIENT-FACING LOOKUPS
# =============================================================================

@frappe.whitelist()
def get_fifo_rate(item_code, company=None):
    """
    Called from Sales Invoice JS when an item is selected.
    Finds the batch with NEAREST EXPIRY DATE (FEFO) that is Active,
    has stock, and belongs to `company` when one is supplied.
    Returns selling price, remaining qty, expiry date.
    """
    # ── Service item guard ────────────────────────────────────────────────────
    # If the Pharmacy Item has is_service_item=1, bypass all FEFO batch
    # logic entirely and return the fixed Item Price rate. No batch lookup,
    # no stock check, no FEFO. batch_id="SERVICE" signals the JS to show
    # a clean "Service Item" alert instead of the batch detail alert.
    is_service = frappe.db.get_value(
        "Pharmacy Item",
        {"item_code": item_code},
        "is_service_item",
    )
    if is_service:
        rate = float(
            frappe.db.get_value(
                "Item Price",
                {
                    "item_code":  item_code,
                    "selling":    1,
                    "price_list": "Standard Selling",
                },
                "price_list_rate",
            ) or 0
        )
        return {
            "blocked":       False,
            "rate":          rate,
            "batch_id":      "SERVICE",
            "remaining_qty": 999999,
            "expiry_date":   "",
        }
    # ── End service item guard ────────────────────────────────────────────────

    pi_name, _ = _pharmacy_item_for(item_code)
    if not pi_name:
        # Not a pharmacy item: nothing to block, nothing to prefill.
        return {
            "blocked":       False,
            "rate":          0,
            "batch_name":    "",
            "batch_id":      "",
            "remaining_qty": 0,
            "expiry_date":   "",
        }

    return _batch_payload(_nearest_expiry_batch(pi_name, company))


@frappe.whitelist()
def get_batch_details(batch_name, company=None):
    """
    Called from Sales Invoice JS when the user picks a batch.
    Returns the same payload shape as get_fifo_rate so the JS can treat
    "default batch" and "chosen batch" identically.
    blocked=True when the batch is not sellable any more (sold out,
    expired, wrong company, or deleted).
    """
    if not batch_name:
        return _batch_payload(None)

    batch = frappe.db.get_value(
        "Pharmacy Batch", batch_name, _BATCH_FIELDS, as_dict=True
    )
    if not batch:
        return _batch_payload(None)

    if company and (batch.company or "") != company:
        return _batch_payload(None)
    if (batch.status or "") != "Active" or flt(batch.remaining_qty) <= 0:
        return _batch_payload(None)
    if not batch.expiry_date or getdate(batch.expiry_date) <= getdate(today()):
        return _batch_payload(None)

    return _batch_payload(batch)


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def get_batches_for_invoice(doctype, txt, searchfield, start, page_len, filters):
    """
    Link-field search for Sales Invoice Item.custom_pharmacy_batch.
    Called by frm.set_query in sales_invoice.js with
    filters = {item_code, company}.

    Lists ONLY batches the user is allowed to sell from:
      same Pharmacy Item, same company, Active, qty > 0, not expired.
    Nearest expiry first. Each option shows batch id, expiry and
    available qty so the user can choose with full information.
    """
    filters = filters or {}
    item_code = filters.get("item_code")
    company   = filters.get("company")

    pi_name, is_service = _pharmacy_item_for(item_code)
    if not pi_name or is_service:
        return []

    conditions = ["pb.item = %(pi)s", "pb.status = 'Active'",
                  "pb.remaining_qty > 0", "pb.expiry_date > %(today)s"]
    values = {
        "pi":       pi_name,
        "today":    today(),
        "txt":      "%" + (txt or "") + "%",
        "start":    start,
        "page_len": page_len,
    }
    if company:
        conditions.append("pb.company = %(company)s")
        values["company"] = company
    if txt:
        conditions.append("(pb.name LIKE %(txt)s OR pb.batch_id LIKE %(txt)s)")

    return frappe.db.sql("""
        SELECT
            pb.name,
            pb.batch_id,
            CONCAT(
                'Exp ', DATE_FORMAT(pb.expiry_date, '%%d-%%m-%%Y'),
                ' | Avail ', FORMAT(pb.remaining_qty, 0),
                ' | Rs ', FORMAT(pb.selling_price, 2)
            ) AS details
        FROM `tabPharmacy Batch` pb
        WHERE {conditions}
        ORDER BY pb.expiry_date ASC, pb.creation ASC
        LIMIT %(page_len)s OFFSET %(start)s
    """.format(conditions=" AND ".join(conditions)), values)


@frappe.whitelist()
def get_total_available_qty(item_code, company=None):
    """
    Total available qty across all Active non-expired batches,
    scoped to `company` when one is supplied.
    """
    pharmacy_items = frappe.get_all(
        "Pharmacy Item",
        filters={"item_code": item_code},
        fields=["name"],
        limit_page_length=1
    )

    if not pharmacy_items:
        return {"total_qty": 0}

    pi_name = pharmacy_items[0].name

    if company:
        result = frappe.db.sql("""
            SELECT COALESCE(SUM(remaining_qty), 0) as total
            FROM `tabPharmacy Batch`
            WHERE item = %s
              AND company = %s
              AND status = 'Active'
              AND remaining_qty > 0
              AND expiry_date > %s
        """, (pi_name, company, today()), as_dict=True)
    else:
        result = frappe.db.sql("""
            SELECT COALESCE(SUM(remaining_qty), 0) as total
            FROM `tabPharmacy Batch`
            WHERE item = %s
              AND status = 'Active'
              AND remaining_qty > 0
              AND expiry_date > %s
        """, (pi_name, today()), as_dict=True)

    total = float(result[0].total) if result else 0.0
    return {"total_qty": total}


# =============================================================================
#  VALIDATE  (save + submit)
# =============================================================================

def validate(doc, method=None):
    """
    Enforces the user-chosen batch rule on every pharmacy row.

    1. Empty batch  -> filled with the nearest-expiry sellable batch for
                       doc.company (FEFO default). Covers invoices made
                       from WooCommerce Sales Orders, the API and loaders.
    2. Batch set    -> must be a Pharmacy Batch of THIS row's item, in
                       THIS company, Active, qty > 0, not expired.
    3. Qty          -> summed per batch across all rows; must not exceed
                       that batch's remaining_qty. One batch per row,
                       no spill-over.
    4. Stamps custom_batch_expiry / custom_batch_available on the row.

    Skipped for service items and for items with no Pharmacy Item.
    Runs on save (docstatus 0) and on submit (docstatus 1); cancel
    (docstatus 2) is left alone.
    """
    if doc.docstatus == 2:
        return
    if not _batch_field_installed():
        frappe.log_error(
            "custom_pharmacy_batch is not installed on Sales Invoice Item. "
            "Run `bench --site <site> migrate` to import the taal_inventory "
            "fixtures. Falling back to FEFO at submit.",
            "Pharmacy Batch field missing",
        )
        return

    demand = {}          # batch name -> total qty requested on this doc
    batch_cache = {}     # batch name -> batch row

    def load(batch_name):
        if batch_name not in batch_cache:
            batch_cache[batch_name] = frappe.db.get_value(
                "Pharmacy Batch", batch_name, _BATCH_FIELDS, as_dict=True
            )
        return batch_cache[batch_name]

    for row in doc.items:
        pi_name, is_service = _pharmacy_item_for(row.item_code)
        if not pi_name or is_service:
            # Not stock-tracked: never carry a batch on such a row.
            row.set(BATCH_FIELD, None)
            row.set(BATCH_EXPIRY, None)
            row.set(BATCH_AVAILABLE, 0)
            continue

        batch_name = row.get(BATCH_FIELD)

        # 1. Fill the FEFO default when the user (or an integration)
        #    did not pick one.
        if not batch_name:
            default = _nearest_expiry_batch(pi_name, doc.company)
            if not default:
                frappe.throw(
                    f"Row {row.idx}: no sellable stock for "
                    f"<b>{row.item_code}</b> in <b>{doc.company}</b>. "
                    f"All batches are expired or exhausted.",
                    title="No Stock Available",
                )
            batch_name = default.name
            row.set(BATCH_FIELD, batch_name)
            batch_cache[batch_name] = default

        # 2. The chosen batch must be legitimate for this row.
        batch = load(batch_name)
        if not batch:
            frappe.throw(
                f"Row {row.idx}: Pharmacy Batch <b>{batch_name}</b> "
                f"does not exist.", title="Invalid Batch",
            )
        if batch.item != pi_name:
            frappe.throw(
                f"Row {row.idx}: batch <b>{batch.batch_id or batch.name}</b> "
                f"belongs to a different item, not <b>{row.item_code}</b>.",
                title="Wrong Batch",
            )
        if (batch.company or "") != (doc.company or ""):
            frappe.throw(
                f"Row {row.idx}: batch <b>{batch.batch_id or batch.name}</b> "
                f"belongs to <b>{batch.company}</b>, not <b>{doc.company}</b>.",
                title="Wrong Company",
            )
        if (batch.status or "") != "Active" or flt(batch.remaining_qty) <= 0:
            frappe.throw(
                f"Row {row.idx}: batch <b>{batch.batch_id or batch.name}</b> "
                f"has no stock left (status {batch.status}, "
                f"remaining {flt(batch.remaining_qty)}). Pick another batch.",
                title="Batch Exhausted",
            )
        if not batch.expiry_date or getdate(batch.expiry_date) <= getdate(today()):
            frappe.throw(
                f"Row {row.idx}: batch <b>{batch.batch_id or batch.name}</b> "
                f"expired on {batch.expiry_date}. Pick another batch.",
                title="Batch Expired",
            )

        # 3. Accumulate demand per batch, check after the loop.
        demand[batch_name] = demand.get(batch_name, 0) + flt(row.qty)

        # 4. Stamp the display fields.
        row.set(BATCH_EXPIRY, batch.expiry_date)
        row.set(BATCH_AVAILABLE, flt(batch.remaining_qty))

    for batch_name, qty in demand.items():
        batch = batch_cache[batch_name]
        if qty > flt(batch.remaining_qty):
            rows = [str(r.idx) for r in doc.items if r.get(BATCH_FIELD) == batch_name]
            frappe.throw(
                f"Batch <b>{batch.batch_id or batch.name}</b> has "
                f"<b>{flt(batch.remaining_qty)}</b> left, but rows "
                f"{', '.join(rows)} ask for <b>{qty}</b> in total. "
                f"Reduce the quantity or add another row with a different batch.",
                title="Insufficient Stock in Batch",
            )


# =============================================================================
#  SUBMIT
# =============================================================================

def on_submit(doc, method):
    """
    Deducts each row's qty from the batch chosen on that row.
    Logs each deduction as "Stock OUT" in deduction_log, with company.
    Accumulates the cost of stock consumed per company, then posts a
    Cost of Goods Sold Journal Entry after all items.

    validate() has already run on submit, so every pharmacy row carries
    a legitimate batch with enough stock. If the batch field is not
    installed yet, the old FEFO engine is used instead.
    """
    company_cogs_totals = {}
    use_chosen_batch    = _batch_field_installed()

    for row in doc.items:
        pi_name, is_service = _pharmacy_item_for(row.item_code)
        if not pi_name or is_service:
            continue

        if use_chosen_batch:
            consumed = _deduct_from_batch(doc=doc, row=row, pi_name=pi_name)
        else:
            consumed = _deduct_fefo(doc=doc, row=row, pi_name=pi_name)

        for entry in consumed:
            company    = entry["company"]
            cogs_value = flt(entry["qty"]) * flt(entry["cost_price"])
            if cogs_value > 0 and company:
                company_cogs_totals[company] = (
                    company_cogs_totals.get(company, 0) + cogs_value
                )

    for company, total_cogs in company_cogs_totals.items():
        if total_cogs > 0:
            _post_cogs_journal_entry(doc, company, total_cogs)


def _deduct_from_batch(doc, row, pi_name):
    """
    Deducts row.qty from the ONE batch chosen on the row
    (custom_pharmacy_batch). Never spills into another batch.

    The batch row is locked (SELECT ... FOR UPDATE) and re-checked here,
    so two invoices submitted at the same second cannot both take the
    last units. validate() already guaranteed the batch is legitimate.

    RETURNS a one-entry `consumed` list in the same shape as _deduct_fefo
    so COGS, logging and cancel keep working unchanged.
    """
    from taal_inventory.utils.stock_log import append_stock_log

    batch_name = row.get(BATCH_FIELD)
    if not batch_name:
        frappe.throw(
            f"Row {row.idx}: no Pharmacy Batch selected for {row.item_code}.",
            title="Batch Missing",
        )

    locked = frappe.db.sql(
        """
        SELECT name, batch_id, item, company, status, remaining_qty,
               expiry_date, selling_price, cost_price, mrp_price, warehouse
        FROM `tabPharmacy Batch`
        WHERE name = %s
        FOR UPDATE
        """,
        batch_name, as_dict=True,
    )
    batch = locked[0] if locked else None
    if not batch:
        frappe.throw(
            f"Row {row.idx}: Pharmacy Batch {batch_name} no longer exists.",
            title="Invalid Batch",
        )

    qty = flt(row.qty)
    if (batch.status != "Active" or flt(batch.remaining_qty) < qty
            or batch.item != pi_name
            or (batch.company or "") != (doc.company or "")
            or not batch.expiry_date
            or getdate(batch.expiry_date) <= getdate(today())):
        frappe.throw(
            f"Row {row.idx}: batch <b>{batch.batch_id or batch.name}</b> "
            f"can no longer cover <b>{qty}</b> of {row.item_code} "
            f"(remaining {flt(batch.remaining_qty)}, status {batch.status}, "
            f"expiry {batch.expiry_date}). Reload and pick another batch.",
            title="Batch Changed Since Validation",
        )

    new_qty = flt(batch.remaining_qty) - qty
    frappe.db.set_value("Pharmacy Batch", batch.name, {
        "remaining_qty": new_qty,
        "status": "Exhausted" if new_qty == 0 else "Active",
    })

    consumed = [{
        "batch_name":    batch.name,
        "qty":           qty,
        "selling_price": flt(batch.selling_price),
        "cost_price":    flt(batch.cost_price),
        "mrp_price":     flt(batch.mrp_price),
        "warehouse":     batch.warehouse or "",
        "company":       batch.company or "",
    }]

    # Rebuilds current_stock AND the company_stock child table
    _recalculate_stock(pi_name)

    company_balance = _company_balance(pi_name, doc.company)
    patient         = doc.patient_name or doc.customer_name or ""

    for entry in consumed:
        append_stock_log(
            pharmacy_item_name=pi_name,
            event_type="Stock OUT",
            reference_doctype="Sales Invoice",
            reference_no=doc.name,
            batch_id=entry["batch_name"],
            qty=entry["qty"],
            balance_after=company_balance,
            cost_price=entry["cost_price"],
            selling_price=entry["selling_price"],
            mrp_price=entry["mrp_price"],
            warehouse=entry["warehouse"],
            company=entry["company"],
            patient_name=patient,
            remarks=f"Batch chosen on row {row.idx}",
        )

    try:
        _check_notify(pi_name, company_balance)
    except Exception as notify_err:
        frappe.log_error(
            f"Stock alert notification failed for {pi_name}: {str(notify_err)}",
            "Stock Alert Notification Error",
        )

    return consumed


def _deduct_fefo(doc, row, pi_name):
    """
    FALLBACK ONLY: used when custom_pharmacy_batch is not installed.
    FEFO deduction engine, SCOPED TO doc.company.
    Nearest expiry batch consumed first, spanning batches when needed.
    Logs each batch consumed as "Stock OUT", with company.

    RETURNS the `consumed` list so the caller can compute COGS.
    """
    from taal_inventory.utils.stock_log import append_stock_log

    batches = frappe.get_all(
        "Pharmacy Batch",
        filters={
            "item":          pi_name,
            "company":       doc.company,          # ← FEFO is per-company
            "status":        "Active",
            "remaining_qty": [">", 0],
            "expiry_date":   [">", today()]
        },
        fields=[
            "name", "batch_id",
            "remaining_qty", "expiry_date",
            "selling_price", "cost_price",
            "mrp_price", "warehouse", "company"
        ],
        order_by="expiry_date ASC",
        limit_page_length=0
    )

    if not batches:
        frappe.throw(
            f"No valid stock available for {row.item_code} in {doc.company}. "
            f"All batches for this company are expired or exhausted."
        )

    total_available = sum(flt(b.remaining_qty) for b in batches)
    if total_available < flt(row.qty):
        frappe.throw(
            f"Insufficient stock for {row.item_code} in {doc.company}. "
            f"Available: {total_available}, Required: {flt(row.qty)}. "
            f"Please reduce quantity."
        )

    remaining = flt(row.qty)
    consumed  = []

    for batch in batches:
        if remaining <= 0:
            break
        deduct  = min(flt(batch.remaining_qty), remaining)
        new_qty = flt(batch.remaining_qty) - deduct

        frappe.db.set_value("Pharmacy Batch", batch.name, {
            "remaining_qty": new_qty,
            "status": "Exhausted" if new_qty == 0 else "Active"
        })

        consumed.append({
            "batch_name":    batch.name,
            "qty":           deduct,
            "selling_price": flt(batch.selling_price),
            "cost_price":    flt(batch.cost_price),
            "mrp_price":     flt(batch.mrp_price),
            "warehouse":     batch.warehouse or "",
            "company":       batch.company or "",
        })
        remaining -= deduct

    # Rebuilds current_stock AND the company_stock child table
    _recalculate_stock(pi_name)

    company_balance = _company_balance(pi_name, doc.company)
    patient         = doc.patient_name or doc.customer_name or ""

    for entry in consumed:
        append_stock_log(
            pharmacy_item_name=pi_name,
            event_type="Stock OUT",
            reference_doctype="Sales Invoice",
            reference_no=doc.name,
            batch_id=entry["batch_name"],
            qty=entry["qty"],
            balance_after=company_balance,
            cost_price=entry["cost_price"],
            selling_price=entry["selling_price"],
            mrp_price=entry["mrp_price"],
            warehouse=entry["warehouse"],
            company=entry["company"],
            patient_name=patient,
            remarks=""
        )

    try:
        _check_notify(pi_name, company_balance)
    except Exception as notify_err:
        frappe.log_error(
            f"Stock alert notification failed for {pi_name}: {str(notify_err)}",
            "Stock Alert Notification Error"
        )

    return consumed


# =============================================================================
#  CANCEL
# =============================================================================

def on_cancel(doc, method):
    """
    Reverses FEFO stock deduction when Sales Invoice is cancelled.

    For each Deduction Log row linked to this SI:
      1. Restore remaining_qty on the Pharmacy Batch
      2. Set batch status back to Active
      3. Rebuild current_stock + company_stock on the Pharmacy Item
      4. Append "Reversal IN" to deduction_log, with company

    Cancels the COGS Journal Entry so inventory value is restored.

    NOTE: flt() on log.qty_deducted is required — frappe.get_all returns
    Float child table fields as strings, causing TypeError on arithmetic.
    """
    from taal_inventory.utils.stock_log import append_stock_log

    _reverse_cogs_journal_entry(doc)

    for row in doc.items:
        # ── Service item guard: nothing was deducted, nothing to reverse ──────
        if frappe.db.get_value(
            "Pharmacy Item", {"item_code": row.item_code}, "is_service_item"
        ):
            continue
        # ── End service item guard ────────────────────────────────────────────

        pharmacy_items = frappe.get_all(
            "Pharmacy Item",
            filters={"item_code": row.item_code},
            fields=["name"],
            limit_page_length=1
        )

        if not pharmacy_items:
            continue

        pi_name = pharmacy_items[0].name

        logs = frappe.get_all(
            "Deduction Log",
            filters={
                "reference_no":      doc.name,
                "reference_doctype": "Sales Invoice",
                "event_type":        "Stock OUT",
                "parent":            pi_name
            },
            fields=[
                "batch_id", "qty_deducted",
                "cost_price", "selling_price",
                "mrp_price", "warehouse"
            ]
        )

        for log in logs:
            qty_to_restore = flt(log.qty_deducted)

            current = flt(
                frappe.db.get_value(
                    "Pharmacy Batch", log.batch_id, "remaining_qty"
                ) or 0
            )

            frappe.db.set_value(
                "Pharmacy Batch",
                log.batch_id,
                {
                    "remaining_qty": current + qty_to_restore,
                    "status":        "Active"
                }
            )

        if not logs:
            continue

        _recalculate_stock(pi_name)

        company_balance = _company_balance(pi_name, doc.company)
        patient         = doc.patient_name or doc.customer_name or ""

        for log in logs:
            qty_to_restore = flt(log.qty_deducted)

            batch_data = frappe.db.get_value(
                "Pharmacy Batch",
                log.batch_id,
                ["cost_price", "selling_price", "mrp_price",
                 "warehouse", "company"],
                as_dict=True
            ) or {}

            append_stock_log(
                pharmacy_item_name=pi_name,
                event_type="Reversal IN",
                reference_doctype="Sales Invoice",
                reference_no=doc.name,
                batch_id=log.batch_id,
                qty=qty_to_restore,
                balance_after=company_balance,
                cost_price=flt(batch_data.get("cost_price", 0)),
                selling_price=flt(batch_data.get("selling_price", 0)),
                mrp_price=flt(batch_data.get("mrp_price", 0)),
                warehouse=batch_data.get("warehouse", ""),
                company=batch_data.get("company", ""),
                patient_name=patient,
                remarks=f"Cancelled SI {doc.name} - stock restored"
            )


# =============================================================================
#  GL — COST OF GOODS SOLD
# =============================================================================

def _post_cogs_journal_entry(doc, company, total_cogs):
    """
    Post Cost of Goods Sold for a sale (per company).

        Dr Cost of Goods Sold - {abbr}          (expense -> P&L)
        Cr Stock In Hand (Inventory) - {abbr}   (inventory asset reduced)

    Valued at FEFO batch cost (qty consumed x cost_price). The SI name
    is stamped into cheque_no so on_cancel can find and reverse this JE.

    This posts COGS only — NOT profit. Profit (Sales - COGS) is computed
    in reports, never posted here.
    """
    cfg = _resolve_config(company, _COGS_CONFIG,
                          context=f"COGS posting for Sales Invoice {doc.name}")
    if not cfg:
        return

    je = frappe.get_doc({
        "doctype": "Journal Entry",
        "voucher_type": "Journal Entry",
        "company": company,
        "posting_date": doc.posting_date or frappe.utils.nowdate(),
        "cheque_no": doc.name,                       # link back to the SI
        "cheque_date": doc.posting_date or frappe.utils.nowdate(),
        "user_remark": (
            f"Cost of Goods Sold for Sales Invoice {doc.name} "
            f"(FEFO batch cost). Inventory reduced at cost."
        ),
        "accounts": [
            {
                "account": cfg["cogs"],
                "debit_in_account_currency": total_cogs,
                "cost_center": cfg["cost_center"],
            },
            {
                "account": cfg["inventory"],
                "credit_in_account_currency": total_cogs,
                "cost_center": cfg["cost_center"],
            },
        ]
    })
    je.insert(ignore_permissions=True)
    je.submit()

    frappe.msgprint(
        f"Cost of Goods Sold "
        f"{frappe.utils.fmt_money(total_cogs, currency='INR')} "
        f"posted ({company}) - Journal Entry {je.name}.",
        alert=True
    )


def _reverse_cogs_journal_entry(doc):
    """
    Cancel the COGS Journal Entry(ies) created for this Sales Invoice at
    submit (found via cheque_no = SI name), restoring inventory value.
    """
    linked_jes = frappe.get_all(
        "Journal Entry",
        filters={
            "cheque_no": doc.name,
            "docstatus": 1,
        },
        fields=["name"]
    )
    for je in linked_jes:
        frappe.get_doc("Journal Entry", je.name).cancel()


# =============================================================================
#  INTERNAL HELPERS
# =============================================================================

def _recalculate_stock(pi_name):
    """
    Rebuilds current_stock AND the company_stock child table from all
    Active batches. Returns the global total (contract unchanged).
    """
    from taal_inventory.utils.company_stock import rebuild_item_stock
    return flt(rebuild_item_stock(pi_name) or 0)


def _company_balance(pi_name, company):
    """This company's stock after the movement — used as balance_after."""
    from taal_inventory.utils.company_stock import get_company_stock
    return get_company_stock(pi_name, company)


def _check_notify(pi_name, new_stock):
    """Fire notifications if stock crosses thresholds."""
    from taal_inventory.utils.notifications import send_stock_alert
    pi = frappe.get_doc("Pharmacy Item", pi_name)

    if new_stock <= float(pi.min_stock_qty or 0):
        send_stock_alert(pi, new_stock, "MIN")
    elif new_stock <= float(pi.alert_qty or 0):
        send_stock_alert(pi, new_stock, "ALERT")
    elif new_stock <= float(pi.reorder_value or 0):
        send_stock_alert(pi, new_stock, "REORDER")


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def get_pharmacy_items_for_invoice(doctype, txt, searchfield, start, page_len, filters):
    """
    Search filter for item_code field in Sales Invoice Item.
    Called by frm.set_query in sales_invoice.js.

    Returns only Item Master records that have a matching active
    Pharmacy Item — so the dropdown never shows non-pharmacy items.

    Searches across:
      - Item.name       (TPIC 38)
      - Item.item_name  (Taffic Tablet)
      - PI.item_name    (alternate pharmacy name)
    """
    return frappe.db.sql("""
        SELECT
            i.name,
            i.item_name,
            i.description
        FROM `tabItem` i
        INNER JOIN `tabPharmacy Item` pi
            ON  pi.item_code = i.name
            AND pi.is_active = 1
        WHERE
            i.disabled      = 0
            AND i.is_sales_item = 1
            AND (
                i.name       LIKE %(txt)s
                OR i.item_name   LIKE %(txt)s
                OR pi.item_name  LIKE %(txt)s
            )
        ORDER BY
            i.item_name ASC
        LIMIT %(page_len)s OFFSET %(start)s
    """, {
        "txt":      "%" + (txt or "") + "%",
        "start":    start,
        "page_len": page_len
    })