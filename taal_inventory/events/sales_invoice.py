"""
taal_inventory/events/sales_invoice.py

Handles Sales Invoice submit and cancel for pharmacy stock.
  on_submit : FEFO deduction - nearest expiry batch consumed first.
              Logs "Stock OUT" per batch consumed, with company.
              POINT 4 (COGS): posts Dr Cost of Goods Sold / Cr Stock In
              Hand (Inventory) for the cost of stock sold, per company.
  on_cancel : Reverses FEFO deduction, restores batch qty and stock.
              Logs "Reversal IN" per batch restored, with company.
              POINT 4 (COGS): cancels the COGS Journal Entry.
"""

import frappe
from frappe.utils import today, now_datetime, flt


# Per-company accounts for the COGS-on-sale posting (Point 4).
# Cr side uses the non-protected "Stock In Hand (Inventory)" account
# (same account Purchase Invoices sweep stock value INTO), so stock
# value flows out at cost when sold - matching the accountant's Step 4.
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


@frappe.whitelist()
def get_fifo_rate(item_code):
    """
    Called from Sales Invoice JS when item is selected.
    Finds the batch with NEAREST EXPIRY DATE (FEFO) that is active
    and has stock. Returns selling price, remaining qty, expiry date.
    """
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

    batches = frappe.get_all(
        "Pharmacy Batch",
        filters={
            "item":          pi_name,
            "status":        "Active",
            "remaining_qty": [">", 0],
            "expiry_date":   [">", today()]
        },
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
def get_total_available_qty(item_code):
    """
    Returns total available qty across all active non-expired batches.
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


def on_submit(doc, method):
    """
    Triggered when Sales Invoice is submitted.
    FEFO deduction - nearest expiry batch consumed first.
    Logs each deduction as "Stock OUT" in deduction_log, with company.

    POINT 4 (COGS): accumulates the cost of stock consumed per company,
    then posts a Cost of Goods Sold Journal Entry after all items.
    """
    # Accumulate COGS value per company across all line items.
    company_cogs_totals = {}

    for row in doc.items:
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

        pi_name = pharmacy_items[0].name
        consumed = _deduct_fefo(doc=doc, row=row, pi_name=pi_name)

        # Accumulate cost of goods sold per company (Point 4)
        for entry in consumed:
            company = entry["company"]
            cogs_value = flt(entry["qty"]) * flt(entry["cost_price"])
            if cogs_value > 0 and company:
                if company not in company_cogs_totals:
                    company_cogs_totals[company] = 0
                company_cogs_totals[company] += cogs_value

    # Post COGS Journal Entry per company (Point 4)
    for company, total_cogs in company_cogs_totals.items():
        if total_cogs > 0:
            _post_cogs_journal_entry(doc, company, total_cogs)


def on_cancel(doc, method):
    """
    Reverses FEFO stock deduction when Sales Invoice is cancelled.

    For each Deduction Log row linked to this SI:
      1. Restore remaining_qty on the Pharmacy Batch
      2. Set batch status back to Active
      3. Recalculate current_stock on Pharmacy Item
      4. Append "Reversal IN" to deduction_log, with company

    POINT 4 (COGS): cancels the COGS Journal Entry so the inventory
    value is restored and COGS reversed.

    KEY FIX: flt() on log.qty_deducted - frappe.get_all returns
    Float child table fields as strings, causing TypeError on arithmetic.
    """
    from taal_inventory.utils.stock_log import append_stock_log

    # POINT 4: reverse the COGS JE(s) first
    _reverse_cogs_journal_entry(doc)

    for row in doc.items:
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

        new_stock = _recalculate_stock(pi_name)

        patient = doc.patient_name or doc.customer_name or ""

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
                balance_after=new_stock,
                cost_price=flt(batch_data.get("cost_price", 0)),
                selling_price=flt(batch_data.get("selling_price", 0)),
                mrp_price=flt(batch_data.get("mrp_price", 0)),
                warehouse=batch_data.get("warehouse", ""),
                company=batch_data.get("company", ""),
                patient_name=patient,
                remarks=f"Cancelled SI {doc.name} - stock restored"
            )

        frappe.db.commit()


def _deduct_fefo(doc, row, pi_name):
    """
    FEFO deduction engine.
    Nearest expiry batch consumed first.
    Deducts across multiple batches if qty spans them.
    Logs each batch consumed as "Stock OUT" in deduction_log, with company.

    RETURNS the `consumed` list so the caller can compute COGS (Point 4).
    """
    from taal_inventory.utils.stock_log import append_stock_log

    batches = frappe.get_all(
        "Pharmacy Batch",
        filters={
            "item":          pi_name,
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
            f"No valid stock available for {row.item_code}. "
            f"All batches are expired or exhausted."
        )

    total_available = sum(flt(b.remaining_qty) for b in batches)
    if total_available < row.qty:
        frappe.throw(
            f"Insufficient stock for {row.item_code}. "
            f"Available: {total_available}, "
            f"Required: {row.qty}. "
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

    new_stock = _recalculate_stock(pi_name)

    patient = doc.patient_name or doc.customer_name or ""

    for entry in consumed:
        append_stock_log(
            pharmacy_item_name=pi_name,
            event_type="Stock OUT",
            reference_doctype="Sales Invoice",
            reference_no=doc.name,
            batch_id=entry["batch_name"],
            qty=entry["qty"],
            balance_after=new_stock,
            cost_price=entry["cost_price"],
            selling_price=entry["selling_price"],
            mrp_price=entry["mrp_price"],
            warehouse=entry["warehouse"],
            company=entry["company"],
            patient_name=patient,
            remarks=""
        )

    frappe.db.commit()

    try:
        _check_notify(pi_name, new_stock)
    except Exception as notify_err:
        frappe.log_error(
            f"Stock alert notification failed for {pi_name}: "
            f"{str(notify_err)}",
            "Stock Alert Notification Error"
        )

    return consumed


def _post_cogs_journal_entry(doc, company, total_cogs):
    """
    POINT 4: Post Cost of Goods Sold for a sale (per company).

        Dr Cost of Goods Sold - {abbr}         (expense -> P&L)
        Cr Stock In Hand (Inventory) - {abbr}   (inventory asset reduced)

    Valued at FEFO batch cost (qty consumed x cost_price). The SI name
    is stamped into cheque_no so on_cancel can find and reverse this JE.

    Per the accountant's rule: this posts COGS only - NOT profit.
    Profit (Sales - COGS) is computed in reports, never posted here.
    """
    cfg = _COGS_CONFIG.get(company)
    if not cfg:
        # Company not configured for COGS posting - skip safely.
        return

    cogs_account = cfg["cogs"]
    inv_account  = cfg["inventory"]
    cost_center  = cfg["cost_center"]

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
                "account": cogs_account,
                "debit_in_account_currency": total_cogs,
                "cost_center": cost_center,
            },
            {
                "account": inv_account,
                "credit_in_account_currency": total_cogs,
                "cost_center": cost_center,
            },
        ]
    })
    je.insert(ignore_permissions=True)
    je.submit()
    frappe.db.commit()

    frappe.msgprint(
        f"Cost of Goods Sold "
        f"{frappe.utils.fmt_money(total_cogs, currency='INR')} "
        f"posted ({company}) - Journal Entry {je.name}.",
        alert=True
    )


def _reverse_cogs_journal_entry(doc):
    """
    POINT 4: Cancel the COGS Journal Entry(ies) created for this Sales
    Invoice at submit (found via cheque_no = SI name), restoring
    inventory value and reversing COGS.
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
        je_doc = frappe.get_doc("Journal Entry", je.name)
        je_doc.cancel()

    if linked_jes:
        frappe.db.commit()


def _recalculate_stock(pi_name):
    """Recalculate current_stock from sum of all active batch remaining_qty."""
    result = frappe.db.sql("""
        SELECT COALESCE(SUM(remaining_qty), 0) as total
        FROM `tabPharmacy Batch`
        WHERE item = %s AND status = 'Active'
    """, pi_name, as_dict=True)

    new_stock = float(result[0].total) if result else 0.0
    frappe.db.set_value(
        "Pharmacy Item",
        pi_name,
        "current_stock",
        new_stock
    )
    return new_stock


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