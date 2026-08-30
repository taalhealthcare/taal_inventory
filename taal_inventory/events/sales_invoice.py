"""
taal_inventory/events/sales_invoice.py

Handles Sales Invoice submit and cancel for pharmacy stock.

  on_submit : FEFO deduction — nearest expiry batch consumed first,
              SCOPED TO doc.company. Logs "Stock OUT" per batch consumed.
              COGS: posts Dr Cost of Goods Sold / Cr Stock In Hand
              (Inventory) for the cost of stock sold, per company.
  on_cancel : Reverses FEFO deduction, restores batch qty and stock.
              Logs "Reversal IN" per batch restored.
              COGS: cancels the COGS Journal Entry.

=============================================================================
THREE CORRECTNESS RULES THIS FILE NOW FOLLOWS
=============================================================================
1. FEFO IS PER-COMPANY. Batch selection filters on doc.company. Without
   this, a TPC invoice could consume an NMP batch, corrupting both
   companies' stock and posting COGS to the wrong ledger.

2. NO frappe.db.commit() ANYWHERE. If line 4 of a 5-line invoice throws
   "Insufficient stock", committed lines 1-3 could not be rolled back and
   stock vanished with no document behind it. Frappe commits at the end of
   the request, so submit and cancel are now atomic.

3. STOCK REFRESH GOES THROUGH rebuild_item_stock(). Writing current_stock
   directly left the company_stock child table stale on every single sale.
=============================================================================
"""

import frappe
from frappe.utils import today, now_datetime, flt


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

    pharmacy_items = frappe.get_all(
        "Pharmacy Item",
        filters={"item_code": item_code},
        fields=["name"],
        limit_page_length=1
    )

    if not pharmacy_items:
        return {
            "blocked":       False,
            "rate":          0,
            "batch_id":      "",
            "remaining_qty": 0,
            "expiry_date":   ""
        }

    pi_name = pharmacy_items[0].name

    batch_filters = {
        "item":          pi_name,
        "status":        "Active",
        "remaining_qty": [">", 0],
        "expiry_date":   [">", today()]
    }
    if company:
        batch_filters["company"] = company

    batches = frappe.get_all(
        "Pharmacy Batch",
        filters=batch_filters,
        fields=[
            "name", "batch_id",
            "selling_price", "remaining_qty",
            "expiry_date"
        ],
        order_by="expiry_date ASC",
        limit_page_length=1
    )

    if not batches:
        return {
            "blocked":       True,
            "rate":          0,
            "batch_id":      "",
            "remaining_qty": 0,
            "expiry_date":   ""
        }

    batch = batches[0]
    return {
        "blocked":       False,
        "rate":          flt(batch.selling_price),
        "batch_id":      batch.batch_id or "",
        "remaining_qty": flt(batch.remaining_qty),
        "expiry_date":   str(batch.expiry_date) if batch.expiry_date else ""
    }


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
#  SUBMIT
# =============================================================================

def on_submit(doc, method):
    """
    FEFO deduction — nearest expiry batch of doc.company consumed first.
    Logs each deduction as "Stock OUT" in deduction_log, with company.
    Accumulates the cost of stock consumed per company, then posts a
    Cost of Goods Sold Journal Entry after all items.
    """
    company_cogs_totals = {}

    for row in doc.items:
        # ── Service item guard: skip FEFO deduction ───────────────────────────
        if frappe.db.get_value(
            "Pharmacy Item", {"item_code": row.item_code}, "is_service_item"
        ):
            continue
        # ── End service item guard ────────────────────────────────────────────

        pharmacy_items = frappe.get_all(
            "Pharmacy Item",
            filters={"item_code": row.item_code},
            fields=[
                "name", "item_name", "uom",
                "min_stock_qty", "alert_qty",
                "reorder_value", "max_stock_qty",
                "current_stock"
            ],
            limit_page_length=1
        )

        if not pharmacy_items:
            continue

        pi_name  = pharmacy_items[0].name
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


def _deduct_fefo(doc, row, pi_name):
    """
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