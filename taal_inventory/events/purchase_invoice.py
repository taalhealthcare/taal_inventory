"""
taal_inventory/events/purchase_invoice.py

Handles Purchase Invoice validate, submit and cancel for pharmacy stock.

=============================================================================
TWO-PASS ARCHITECTURE (critical — do not collapse back into one loop)
=============================================================================
PASS 1 - _build_plan(doc)   : READ-ONLY. Resolves and validates EVERY row,
                              builds an in-memory plan. All frappe.throw()
                              calls happen here, before a single write.
PASS 2 - _execute_plan(...)  : WRITES ONLY. Applies the plan. No throws.

WHY: the old code validated and wrote row-by-row, and called
frappe.db.commit() mid-loop. When row N threw, rows 1..N-1 were already
committed and could not be rolled back — including the document's own
docstatus=1. That left submitted invoices whose stock effects were only
partially applied. The two-pass split makes a Purchase Invoice all-or-nothing.

RULE: never call frappe.db.commit() inside on_submit / on_cancel. Let Frappe
commit at the end of the request so a failure rolls everything back.

=============================================================================
BATCH HANDLING (two outcomes only)
=============================================================================
CREATE : batch_id not seen for this item+company -> create a new Pharmacy Batch.
TOPUP  : batch_id already exists for this item+company -> ADD the invoice qty
         to that batch's qty_received and remaining_qty. Batch pricing
         (cost/selling/MRP/expiry) is NOT overwritten — the PI is expected to
         mirror the existing batch's pricing (client script auto-fetches it).

Reusing a batch ID is normal and always adds stock.
"""

import frappe
from frappe.utils import now_datetime, getdate, flt


DEFAULT_WAREHOUSE = "Finished Goods - TPC"

_ACTION_CREATE = "create"
_ACTION_TOPUP  = "topup"


# Maps each company to its SRBNB source account and inventory-asset target.
_INVENTORY_SWEEP_CONFIG = {
    "Taalplus CHC Private Limited": {
        "srbnb":       "Stock Received But Not Billed - TPC",
        "inventory":   "Stock In Hand (Inventory) - TPC",
        "cost_center": "Main - TPC",
    },
    "NMP_Taal Plus": {
        "srbnb":       "Stock Received But Not Billed - NP",
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
# ENTRY POINTS
# =============================================================================

def validate(doc, method):
    """
    Pharmacy-only guard. Runs on EVERY save, draft and submit.

    Blocks any Purchase Invoice row whose item_code has no Pharmacy Item
    record. This closes every path: manual entry, copy-paste, Data Import,
    API inserts, and the Purchase Excel Upload tool. The item_code dropdown
    filter (get_pharmacy_items_for_purchase) is convenience; this function
    is the enforcement.

    All offending rows are collected and shown together, same two-pass
    spirit as the rest of this file: read everything, then act.

    NOTE: this also blocks non-pharmacy purchases (services, expenses,
    assets) on every Purchase Invoice. _build_plan used to skip such lines
    silently. If you ever need them again, relax this to skip rows where
    frappe.db.get_value("Item", row.item_code, "is_stock_item") == 0.
    """
    if doc.is_return:
        return

    missing = []
    for row in doc.items:
        if not _get_pharmacy_item(row.item_code):
            missing.append(
                f"Row #{row.idx}: {row.item_name or row.item_code or '(no item)'}"
            )

    if missing:
        frappe.throw(
            "These items have no Pharmacy Item record, so they cannot be "
            "used on a Purchase Invoice:<br>"
            + "<br>".join(missing)
            + "<br><br>Create the Pharmacy Item first (with the correct "
            "Item Code), then try again."
        )


def on_submit(doc, method):
    """Purchase Invoice submit. Two-pass: validate everything, then write."""
    if doc.is_return:
        return

    plan = _build_plan(doc)          # PASS 1 — may throw, writes nothing
    if not plan:
        return

    _execute_plan(doc, plan)         # PASS 2 — writes, does not throw

    _sweep_srbnb_to_inventory(doc)   # GL: SRBNB -> Stock In Hand (Inventory)

    _notify_shared_batches(doc)      # visibility only: same batch id on several items

    created = len([p for p in plan if p["action"] == _ACTION_CREATE])
    topped  = len([p for p in plan if p["action"] == _ACTION_TOPUP])

    parts = []
    if created:
        parts.append(f"{created} new Pharmacy Batch(es) created")
    if topped:
        parts.append(f"{topped} existing batch(es) topped up")
    if parts:
        frappe.msgprint(
            "Purchase Invoice submitted. " + ", ".join(parts) + ".",
            alert=True
        )


def on_cancel(doc, method):
    """
    Reverses exactly what submit applied:

      - Batches CREATED by this PI (purchase_invoice == doc.name and
        qty_received > 0) -> quarantined, remaining stock reversed.
      - Batches TOPPED UP by this PI -> the invoice qty is subtracted back
        out of qty_received and remaining_qty.

    Also cancels the SRBNB -> Inventory sweep Journal Entry.
    """
    if doc.is_return:
        return

    _reverse_inventory_sweep(doc)

    from taal_inventory.utils.stock_log import append_stock_log
    from taal_inventory.utils.company_stock import recalculate_company_stock

    for row in doc.items:
        pharmacy_item_name = _get_pharmacy_item(row.item_code)
        if not pharmacy_item_name or not row.custom_batch_id:
            continue

        batch = _find_batch(row.custom_batch_id, pharmacy_item_name, doc.company)
        if not batch:
            continue

        qty = flt(row.qty)

        if batch.purchase_invoice == doc.name and flt(batch.qty_received) > 0:
            # --- Batch was created by THIS invoice: quarantine it ---
            remaining = flt(batch.remaining_qty)

            frappe.db.set_value("Pharmacy Batch", batch.name, {
                "status": "Quarantined"
            })

            new_stock = recalculate_company_stock(pharmacy_item_name, doc.company)

            append_stock_log(
                pharmacy_item_name=pharmacy_item_name,
                event_type="Reversal OUT",
                reference_doctype="Purchase Invoice",
                reference_no=doc.name,
                batch_id=batch.name,
                qty=remaining,
                balance_after=new_stock,
                cost_price=flt(batch.cost_price),
                selling_price=flt(batch.selling_price),
                mrp_price=flt(batch.mrp_price),
                warehouse=batch.warehouse or "",
                company=doc.company,
                remarks=(
                    f"Cancelled PI {doc.name} - batch created by this invoice "
                    f"quarantined, {remaining} units reversed"
                )
            )

        else:
            # --- Batch was TOPPED UP by this invoice: subtract the qty back ---
            new_received  = max(0, flt(batch.qty_received) - qty)
            new_remaining = max(0, flt(batch.remaining_qty) - qty)

            frappe.db.set_value("Pharmacy Batch", batch.name, {
                "qty_received":  new_received,
                "remaining_qty": new_remaining,
                "status": "Exhausted" if new_remaining == 0 else "Active"
            })

            new_stock = recalculate_company_stock(pharmacy_item_name, doc.company)

            append_stock_log(
                pharmacy_item_name=pharmacy_item_name,
                event_type="Reversal OUT",
                reference_doctype="Purchase Invoice",
                reference_no=doc.name,
                batch_id=batch.name,
                qty=qty,
                balance_after=new_stock,
                cost_price=flt(batch.cost_price),
                selling_price=flt(batch.selling_price),
                mrp_price=flt(batch.mrp_price),
                warehouse=batch.warehouse or "",
                company=doc.company,
                remarks=(
                    f"Cancelled PI {doc.name} - {qty} units removed from "
                    f"batch {batch.batch_id} (top-up reversed)"
                )
            )


# =============================================================================
# PASS 1 — VALIDATE AND PLAN (no writes)
# =============================================================================

def _build_plan(doc):
    """
    Reads every pharmacy row, validates it, and decides CREATE vs TOPUP.
    Throws on the first problem — nothing has been written yet, so the whole
    submit rolls back cleanly.

    Returns a list of plan entries:
        {action, row, pharmacy_item_name, batch_name (topup only)}
    """
    plan = []
    seen_batch_ids = set()

    for row in doc.items:
        pharmacy_item_name = _get_pharmacy_item(row.item_code)

        # Non-pharmacy line (e.g. a service item) — skip silently.
        # (validate() above now blocks these from being saved at all,
        #  so this skip only matters for documents saved before it.)
        if not pharmacy_item_name:
            continue

        # Duplicate check is per ITEM + batch. The same batch id on two
        # different items is valid: Pharmacy Batches are keyed by
        # batch + item + company, so each item gets its own batch record.
        batch_key = (pharmacy_item_name, row.custom_batch_id)
        _validate_pharmacy_row(row, doc, seen_batch_ids, batch_key)
        seen_batch_ids.add(batch_key)

        existing = _find_batch(row.custom_batch_id, pharmacy_item_name, doc.company)

        if existing:
            plan.append({
                "action":             _ACTION_TOPUP,
                "row":                row,
                "pharmacy_item_name": pharmacy_item_name,
                "batch_name":         existing.name,
            })
        else:
            plan.append({
                "action":             _ACTION_CREATE,
                "row":                row,
                "pharmacy_item_name": pharmacy_item_name,
                "batch_name":         None,
            })

    return plan


def _validate_pharmacy_row(row, doc, seen_batch_ids, batch_key=None):
    """
    Validates one pharmacy row. Throws on first failure. No writes.
    batch_key is (pharmacy_item_name, batch_id); the duplicate check is
    scoped to that pair, so one batch id may appear under different items.
    """
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
    if flt(row.qty) <= 0:
        frappe.throw(f"Row #{idx} ({name}): Quantity must be > 0.")

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

    key = batch_key or row.custom_batch_id
    if key in seen_batch_ids:
        frappe.throw(
            f"Row #{idx} ({name}): Batch ID '{row.custom_batch_id}' "
            f"appears more than once for this item in this invoice. "
            f"Combine those rows into a single line with the total quantity."
        )


# =============================================================================
# PASS 2 — EXECUTE (writes only, no throws)
# =============================================================================

def _execute_plan(doc, plan):
    """Applies the validated plan. Must not throw."""
    from taal_inventory.utils.stock_log import append_stock_log
    from taal_inventory.utils.company_stock import recalculate_company_stock

    for entry in plan:
        row                = entry["row"]
        pharmacy_item_name = entry["pharmacy_item_name"]
        qty                = flt(row.qty)

        if entry["action"] == _ACTION_TOPUP:
            batch = frappe.get_doc("Pharmacy Batch", entry["batch_name"])

            new_received  = flt(batch.qty_received)  + qty
            new_remaining = flt(batch.remaining_qty) + qty

            # NOTE: batch pricing/expiry deliberately NOT overwritten — the
            #       batch is the source of truth and the client script pulls
            #       cost/selling/MRP/expiry FROM it into the invoice row.
            # NOTE: purchase_invoice link deliberately NOT overwritten — it
            #       marks which PI CREATED the batch, which on_cancel relies on.
            # NOTE: creation_date IS refreshed to now, so the batch reflects
            #       the date its most recent stock arrived.
            frappe.db.set_value("Pharmacy Batch", batch.name, {
                "qty_received":  new_received,
                "remaining_qty": new_remaining,
                "status":        "Active",
                "creation_date": now_datetime(),
            })

            new_stock = recalculate_company_stock(pharmacy_item_name, doc.company)

            append_stock_log(
                pharmacy_item_name=pharmacy_item_name,
                event_type="Stock IN",
                reference_doctype="Purchase Invoice",
                reference_no=doc.name,
                batch_id=batch.name,
                qty=qty,
                balance_after=new_stock,
                cost_price=flt(batch.cost_price),
                selling_price=flt(batch.selling_price),
                mrp_price=flt(batch.mrp_price),
                warehouse=batch.warehouse or "",
                company=doc.company,
                remarks=(
                    f"Existing batch {batch.batch_id} topped up by {qty} "
                    f"via PI {doc.name} (received {new_received}, "
                    f"remaining {new_remaining})"
                )
            )
            continue

        # ---- CREATE ----------------------------------------------------------
        pi_item = frappe.get_doc("Pharmacy Item", pharmacy_item_name)

        gst_pct = flt(row.igst_rate or 0) or (
            flt(row.cgst_rate or 0) + flt(row.sgst_rate or 0)
        )
        cost_price    = flt(row.rate or 0)
        selling_price = flt(row.custom_selling_price or 0)
        warehouse     = row.warehouse or doc.set_warehouse or DEFAULT_WAREHOUSE

        batch = frappe.get_doc({
            "doctype":               "Pharmacy Batch",
            "item":                  pharmacy_item_name,
            "item_name":             pi_item.item_name,
            "batch_id":              row.custom_batch_id,
            "manufacturing_date":    row.custom_manufacturing_date,
            "expiry_date":           row.custom_expiry_date,
            "mrp_price":             flt(row.custom_mrp_price or 0),
            "selling_price":         selling_price,
            "cost_price":            cost_price,
            "profit_per_uom":        selling_price - cost_price,
            "gst_pct":               gst_pct,
            "purchsase_tax_pct":     gst_pct,
            "supplier_discount_pct": flt(row.discount_percentage or 0),
            "hsn_sac_code":          row.gst_hsn_code or pi_item.hsn_sac_code or "",
            "manufacturer_name":     pi_item.manufacturer_name or "",
            "supplier_name":         doc.supplier,
            "supplier_invoice_no":   doc.bill_no,
            "qty_received":          qty,
            "remaining_qty":         qty,
            "warehouse":             warehouse,
            "company":               doc.company,
            "purchase_invoice":      doc.name,
            "stock_receipt":         None,
            "status":                "Active",
            "creation_date":         now_datetime()
        })
        batch.insert(ignore_permissions=True)

        if selling_price > 0:
            _sync_item_price(
                item_code=row.item_code,
                selling_price=selling_price,
                expiry_date=row.custom_expiry_date
            )

        new_stock = recalculate_company_stock(pharmacy_item_name, doc.company)

        append_stock_log(
            pharmacy_item_name=pharmacy_item_name,
            event_type="Stock IN",
            reference_doctype="Purchase Invoice",
            reference_no=doc.name,
            batch_id=batch.name,
            qty=qty,
            balance_after=new_stock,
            cost_price=cost_price,
            selling_price=selling_price,
            mrp_price=flt(row.custom_mrp_price or 0),
            warehouse=warehouse,
            company=doc.company,
            remarks=f"Batch {row.custom_batch_id} created via PI {doc.name}"
        )


# =============================================================================
# SHARED BATCH VISIBILITY (read-only, never blocks)
# =============================================================================

def _shared_batch_groups(doc):
    """
    Returns {batch_id: [rows]} for every batch id that appears under more
    than one item on this invoice. Allowed by design (Pharmacy Batches are
    keyed by batch + item + company); this only makes it visible.
    """
    groups = {}
    for row in doc.items:
        if row.custom_batch_id:
            groups.setdefault(row.custom_batch_id, []).append(row)
    return {
        batch_id: rows for batch_id, rows in groups.items()
        if len({r.item_code for r in rows}) > 1
    }


def _notify_shared_batches(doc):
    """One dialog listing each shared batch with its items. Informational."""
    shared = _shared_batch_groups(doc)
    if not shared:
        return

    blocks = []
    for batch_id, rows in shared.items():
        lines = "".join(
            f"<li>Row #{r.idx}: <b>{r.item_name or r.item_code}</b> ({r.item_code}), "
            f"qty {flt(r.qty):g}</li>"
            for r in rows
        )
        blocks.append(
            f"<b>Batch {batch_id}</b> is on {len(rows)} items:<ul>{lines}</ul>"
        )

    frappe.msgprint(
        msg="".join(blocks)
            + "<p>This is allowed. Each item gets its own Pharmacy Batch. "
              "Shown here so you can confirm the batch id is correct on every item.</p>",
        title="Same Batch ID on more than one item",
        indicator="orange",
    )


# =============================================================================
# LOOKUP HELPERS (read-only)
# =============================================================================

def _get_pharmacy_item(item_code):
    """Resolves a Pharmacy Item name from an ERPNext item_code."""
    if not item_code:
        return None
    return frappe.db.get_value("Pharmacy Item", {"item_code": item_code}, "name")


def _find_batch(batch_id, pharmacy_item_name, company):
    """
    Finds an existing Pharmacy Batch for this batch_id + item + company.

    Scoped by company so a TPC invoice never tops up an NP batch.

    Only Active and Exhausted batches are eligible for top-up:
      - Active     -> add stock to it
      - Exhausted  -> add stock, status returns to Active
      - Expired    -> NOT eligible. Topping one up would revive it while
                      keeping its past expiry date, and FEFO (which filters
                      expiry_date > today) could never sell that stock.
                      A new batch is created instead, with the new expiry.
      - Quarantined-> NOT eligible (it was cancelled out).

    Newest first, so if an expired batch and a fresh one share a batch_id,
    the fresh one wins.
    """
    if not batch_id or not pharmacy_item_name:
        return None

    rows = frappe.get_all(
        "Pharmacy Batch",
        filters={
            "batch_id": batch_id,
            "item":     pharmacy_item_name,
            "company":  company,
            "status":   ["in", ["Active", "Exhausted"]],
        },
        fields=[
            "name", "batch_id", "qty_received", "remaining_qty",
            "cost_price", "selling_price", "mrp_price",
            "warehouse", "purchase_invoice", "status"
        ],
        order_by="creation DESC",
        limit_page_length=1
    )
    return rows[0] if rows else None


@frappe.whitelist()
def get_batches_for_item(item_code, company=None):
    """
    Called from the Purchase Invoice client script to populate the
    Batch ID dropdown with this item's existing batches.
    """
    pharmacy_item_name = _get_pharmacy_item(item_code)
    if not pharmacy_item_name:
        return []

    filters = {
        "item":   pharmacy_item_name,
        "status": ["in", ["Active", "Exhausted"]],
    }
    if company:
        filters["company"] = company

    return frappe.get_all(
        "Pharmacy Batch",
        filters=filters,
        fields=[
            "name", "batch_id", "expiry_date", "manufacturing_date",
            "mrp_price", "selling_price", "cost_price",
            "remaining_qty", "qty_received"
        ],
        order_by="expiry_date ASC",
        limit_page_length=0
    )


# =============================================================================
# GL — SRBNB -> Stock In Hand (Inventory)
# =============================================================================

def _sweep_srbnb_to_inventory(doc):
    """
    ERPNext parks purchased stock value in SRBNB. This moves THIS invoice's
    SRBNB debit into the Stock In Hand (Inventory) asset account so purchased
    stock shows as an asset on the Balance Sheet in real time.

    The PI name is stamped into cheque_no so on_cancel can reverse it.
    """
    cfg = _resolve_config(doc.company, _INVENTORY_SWEEP_CONFIG,
                          context=f"SRBNB sweep for Purchase Invoice {doc.name}")
    if not cfg:
        return

    srbnb_rows = frappe.get_all(
        "GL Entry",
        filters={
            "voucher_no":   doc.name,
            "account":      cfg["srbnb"],
            "is_cancelled": 0,
        },
        fields=["debit", "credit"]
    )

    srbnb_value = 0
    for r in srbnb_rows:
        srbnb_value += flt(r.debit) - flt(r.credit)

    if srbnb_value <= 0:
        return

    je = frappe.get_doc({
        "doctype":       "Journal Entry",
        "voucher_type":  "Journal Entry",
        "company":       doc.company,
        "posting_date":  doc.posting_date or frappe.utils.nowdate(),
        "cheque_no":     doc.name,
        "cheque_date":   doc.posting_date or frappe.utils.nowdate(),
        "user_remark": (
            f"Auto-reclassification: move stock value {srbnb_value} from "
            f"Stock Received But Not Billed to Stock In Hand (Inventory) "
            f"for Purchase Invoice {doc.name}."
        ),
        "accounts": [
            {
                "account": cfg["inventory"],
                "debit_in_account_currency": srbnb_value,
                "cost_center": cfg["cost_center"],
            },
            {
                "account": cfg["srbnb"],
                "credit_in_account_currency": srbnb_value,
                "cost_center": cfg["cost_center"],
            },
        ]
    })
    je.insert(ignore_permissions=True)
    je.submit()

    frappe.msgprint(
        f"Stock value of "
        f"{frappe.utils.fmt_money(srbnb_value, currency='INR')} "
        f"reclassified to Stock In Hand (Inventory) - Journal Entry {je.name}.",
        alert=True
    )


def _reverse_inventory_sweep(doc):
    """Cancels the sweep Journal Entry created for this PI at submit."""
    for je in frappe.get_all(
        "Journal Entry",
        filters={"cheque_no": doc.name, "docstatus": 1},
        fields=["name"]
    ):
        frappe.get_doc("Journal Entry", je.name).cancel()


# =============================================================================
# ITEM PRICE SYNC
# =============================================================================

def _sync_item_price(item_code, selling_price, expiry_date):
    """Create or update Item Price in the Standard Selling price list."""
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

    except Exception as e:
        frappe.log_error(
            f"Item Price sync failed for {item_code}: {str(e)}",
            "Item Price Sync Error"
        )


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def get_pharmacy_items_for_purchase(doctype, txt, searchfield, start, page_len, filters):
    """
    Search filter for item_code field in Purchase Invoice Item.
    Returns only Item Master records that have a matching Pharmacy Item —
    active or inactive (both can receive new stock).
    """
    return frappe.db.sql("""
        SELECT
            i.name,
            i.item_name,
            i.description
        FROM `tabItem` i
        INNER JOIN `tabPharmacy Item` pi
            ON pi.item_code = i.name
        WHERE
            i.disabled          = 0
            AND i.is_purchase_item  = 1
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