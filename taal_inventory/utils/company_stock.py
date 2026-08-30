"""
taal_inventory/utils/company_stock.py

Single source of truth for Pharmacy Item stock.

INVARIANT (guaranteed by construction, not by convention):
    Pharmacy Item.current_stock == SUM(company_stock[].current_stock)

Both sides come from ONE GROUP BY query over Active Pharmacy Batches,
so they cannot drift. Every batch mutation rebuilds the whole picture
for that item rather than patching one company row.

RULE: never call frappe.db.commit() from inside a document hook. Frappe
commits at the end of the request, so a failure rolls everything back.
"""

import frappe
from frappe.utils import flt, cint, now_datetime, today, getdate

ITEM_DT  = "Pharmacy Item"
BATCH_DT = "Pharmacy Batch"


# ─────────────────────────────────────────────────────────────────────────────
#  READ LAYER
# ─────────────────────────────────────────────────────────────────────────────

def get_stock_breakdown(pharmacy_item_name):
    """
    Returns (rows, unassigned, global_qty).

    rows       : one dict per company -> company, qty, batch_count
    unassigned : Active batches with no company (should always be zero
                 once `company` is mandatory on Pharmacy Batch)
    global_qty : rows total + unassigned total
    """
    rows = frappe.db.sql("""
        SELECT company,
               COALESCE(SUM(remaining_qty), 0) AS qty,
               COUNT(*)                        AS batch_count
        FROM `tabPharmacy Batch`
        WHERE item = %s
          AND status = 'Active'
          AND IFNULL(company, '') != ''
        GROUP BY company
        ORDER BY company
    """, pharmacy_item_name, as_dict=True)

    unassigned = frappe.db.sql("""
        SELECT COALESCE(SUM(remaining_qty), 0) AS qty,
               COUNT(*)                        AS batch_count
        FROM `tabPharmacy Batch`
        WHERE item = %s
          AND status = 'Active'
          AND IFNULL(company, '') = ''
    """, pharmacy_item_name, as_dict=True)[0]

    global_qty = sum(flt(r.qty) for r in rows) + flt(unassigned.qty)
    return rows, unassigned, global_qty


def get_company_stock(pharmacy_item_name, company):
    """Read-only lookup for validation before a sale / transfer."""
    if not pharmacy_item_name or not company:
        return 0.0

    result = frappe.db.sql("""
        SELECT COALESCE(SUM(remaining_qty), 0) AS qty
        FROM `tabPharmacy Batch`
        WHERE item = %s AND status = 'Active' AND company = %s
    """, (pharmacy_item_name, company), as_dict=True)

    return flt(result[0].qty) if result else 0.0


# ─────────────────────────────────────────────────────────────────────────────
#  WRITE LAYER
# ─────────────────────────────────────────────────────────────────────────────

def rebuild_item_stock(pharmacy_item_name):
    """
    Rebuilds company_stock and current_stock for ONE Pharmacy Item.
    Idempotent — safe to call as often as you like.
    Returns the new global total.
    """
    if not pharmacy_item_name or not frappe.db.exists(ITEM_DT, pharmacy_item_name):
        return None

    rows, unassigned, global_qty = get_stock_breakdown(pharmacy_item_name)

    item = frappe.get_doc(ITEM_DT, pharmacy_item_name)
    item.set("company_stock", [])

    stamp = now_datetime()
    for r in rows:
        item.append("company_stock", {
            "company":            r.company,
            "current_stock":      flt(r.qty),
            "active_batch_count": cint(r.batch_count),
            "last_updated":       stamp,
        })

    item.current_stock = global_qty

    # Do NOT re-trigger Item Master / Item Price sync from a stock rebuild
    item.flags.ignore_taal_item_sync = True
    item.flags.ignore_permissions    = True
    item.flags.ignore_version        = True
    item.save()

    if flt(unassigned.qty):
        frappe.log_error(
            "Pharmacy Item {0}: {1} Active batch(es) carrying {2} qty have no "
            "Company set. They are counted in Total Stock but cannot appear "
            "in Stock by Company.".format(
                pharmacy_item_name, cint(unassigned.batch_count), flt(unassigned.qty)
            ),
            "Unassigned Pharmacy Batch Company"
        )

    return global_qty


# ─────────────────────────────────────────────────────────────────────────────
#  BACKWARD COMPATIBILITY
#
#  events/purchase_invoice.py, events/stock_receipt.py and
#  events/pharmacy_stock_transfer.py all call recalculate_company_stock().
#  The old function patched ONE company row and left the rest stale.
#  This wrapper keeps every existing call site working unchanged while
#  routing it through the full rebuild.
#
#  Return value contract is unchanged: the stock for THAT company only,
#  which is what callers pass to append_stock_log(balance_after=...).
# ─────────────────────────────────────────────────────────────────────────────

def recalculate_company_stock(pharmacy_item_name, company=None):
    rebuild_item_stock(pharmacy_item_name)

    if company:
        return get_company_stock(pharmacy_item_name, company)

    _, _, global_qty = get_stock_breakdown(pharmacy_item_name)
    return global_qty


# ─────────────────────────────────────────────────────────────────────────────
#  PHARMACY BATCH HOOKS
# ─────────────────────────────────────────────────────────────────────────────

def validate_batch(doc, method=None):
    """Data integrity at the source. Registered on Pharmacy Batch validate."""
    if not doc.company:
        frappe.throw("Company is mandatory on a Pharmacy Batch — "
                     "stock cannot be attributed without it.")

    if flt(doc.remaining_qty) < 0:
        frappe.throw("Remaining Qty cannot be negative.")

    if flt(doc.remaining_qty) > flt(doc.qty_received):
        frappe.throw("Remaining Qty ({0}) cannot exceed Qty Received ({1}).".format(
            flt(doc.remaining_qty), flt(doc.qty_received)))

    if not doc.status:
        doc.status = "Active"

    # Auto status transitions so stock never counts what it shouldn't.
    # Quarantined and Expired batches are left alone.
    if doc.status == "Active":
        if doc.expiry_date and getdate(doc.expiry_date) < getdate(today()):
            doc.status = "Expired"
        elif flt(doc.remaining_qty) == 0 and flt(doc.qty_received) > 0:
            doc.status = "Exhausted"


def on_batch_change(doc, method=None):
    """
    Registered on Pharmacy Batch on_update (which also fires on insert).
    Rebuilds the old item too, in case `item` was edited on the batch.

    This is the safety net for MANUAL batch edits made in the UI. The
    server-side flows (SI / PI / SR / Transfer) already rebuild explicitly,
    so a small amount of duplicated work here is deliberate and harmless.
    """
    targets = set()
    if doc.item:
        targets.add(doc.item)

    before = doc.get_doc_before_save()
    if before and before.item:
        targets.add(before.item)

    for item_name in targets:
        rebuild_item_stock(item_name)


def on_batch_trash(doc, method=None):
    if doc.item:
        frappe.enqueue(rebuild_item_stock, pharmacy_item_name=doc.item,
                       enqueue_after_commit=True, queue="short")


# ─────────────────────────────────────────────────────────────────────────────
#  MAINTENANCE / BACKFILL
# ─────────────────────────────────────────────────────────────────────────────

def mark_expired_batches():
    """Flip Active batches past expiry to Expired, then rebuild their items."""
    expired = frappe.get_all(
        BATCH_DT,
        filters={"status": "Active", "expiry_date": ["<", today()]},
        fields=["name", "item"]
    )
    for b in expired:
        frappe.db.set_value(BATCH_DT, b.name, "status", "Expired",
                            update_modified=False)

    for item_name in {b.item for b in expired if b.item}:
        rebuild_item_stock(item_name)

    return len(expired)


@frappe.whitelist()
def rebuild_all(only_mismatched=False):
    """
    Full backfill.

        bench --site <site> execute \
          taal_inventory.utils.company_stock.rebuild_all
    """
    names = frappe.get_all(ITEM_DT, pluck="name")
    done = 0
    for name in names:
        if only_mismatched and not _is_mismatched(name):
            continue
        rebuild_item_stock(name)
        done += 1
        if done % 50 == 0:
            frappe.db.commit()
    frappe.db.commit()
    return done


def _is_mismatched(pharmacy_item_name):
    stored_total = flt(frappe.db.get_value(ITEM_DT, pharmacy_item_name, "current_stock"))
    row_total = flt(frappe.db.sql("""
        SELECT COALESCE(SUM(current_stock), 0)
        FROM `tabPharmacy Item Company Stock`
        WHERE parent = %s AND parenttype = %s
    """, (pharmacy_item_name, ITEM_DT))[0][0])
    _, _, live_total = get_stock_breakdown(pharmacy_item_name)
    return abs(stored_total - row_total) > 0.001 or abs(stored_total - live_total) > 0.001


def daily_stock_maintenance():
    """Scheduler entry point: expire, then self-heal any drift."""
    mark_expired_batches()
    rebuild_all(only_mismatched=True)


# ─────────────────────────────────────────────────────────────────────────────
#  AUDIT / UI HELPERS
# ─────────────────────────────────────────────────────────────────────────────

@frappe.whitelist()
def audit_stock_mismatches():
    """
    Every Pharmacy Item where Total Stock, the sum of its Stock by Company
    rows, and the live Active-batch total disagree. Empty list = balanced.

        bench --site <site> execute \
          taal_inventory.utils.company_stock.audit_stock_mismatches
    """
    return frappe.db.sql("""
        SELECT pi.name, pi.item_name, pi.item_code,
               pi.current_stock            AS total_stock,
               COALESCE(cs.company_sum, 0) AS company_sum,
               COALESCE(pb.live_sum, 0)    AS live_batch_sum
        FROM `tabPharmacy Item` pi
        LEFT JOIN (
            SELECT parent, SUM(current_stock) AS company_sum
            FROM `tabPharmacy Item Company Stock`
            WHERE parenttype = 'Pharmacy Item'
            GROUP BY parent
        ) cs ON cs.parent = pi.name
        LEFT JOIN (
            SELECT item, SUM(remaining_qty) AS live_sum
            FROM `tabPharmacy Batch`
            WHERE status = 'Active'
            GROUP BY item
        ) pb ON pb.item = pi.name
        WHERE ABS(pi.current_stock - COALESCE(cs.company_sum, 0)) > 0.001
           OR ABS(pi.current_stock - COALESCE(pb.live_sum, 0))    > 0.001
        ORDER BY pi.item_name
    """, as_dict=True)


@frappe.whitelist()
def recalculate_this_item(pharmacy_item):
    """Bound to the 'Recalculate Stock' button on the Pharmacy Item form."""
    total = rebuild_item_stock(pharmacy_item)
    frappe.db.commit()
    return {"pharmacy_item": pharmacy_item, "total_stock": flt(total)}