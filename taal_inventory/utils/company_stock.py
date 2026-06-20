"""
taal_inventory/utils/company_stock.py

Manages the per-company stock breakdown on Pharmacy Item via the new
`company_stock` child table (DocType: Pharmacy Item Company Stock).

ARCHITECTURE:
  Pharmacy Item.current_stock      = GLOBAL total across ALL companies
                                      (unchanged — existing reports/code
                                      keep working exactly as before)
  Pharmacy Item.company_stock      = NEW child table, one row per company,
                                      showing that company's stock only

  This is purely additive. No existing Pharmacy Item record needs to be
  duplicated. One medicine = one Pharmacy Item, regardless of how many
  companies hold stock of it.

Called by:
  - events/pharmacy_stock_transfer.py (after every transfer submit/cancel)
  - Can also be called manually to refresh stock for any item+company
"""

import frappe
from frappe.utils import now_datetime, flt


def recalculate_company_stock(pharmacy_item_name, company):
    """
    Recalculates and updates the company_stock child table row for the
    given company on the given Pharmacy Item. Also recalculates and
    updates the GLOBAL current_stock field (sum across all companies).

    Parameters
    ----------
    pharmacy_item_name : str
        The Pharmacy Item document name
    company : str
        The company whose stock row needs to be refreshed

    Returns
    -------
    float
        The recalculated stock for THIS company only (not global)
    """
    # ── Step 1: Compute stock for THIS company only ───────────────────────────
    company_result = frappe.db.sql("""
        SELECT
            COALESCE(SUM(remaining_qty), 0) as total_qty,
            COUNT(*) as batch_count
        FROM `tabPharmacy Batch`
        WHERE item = %s AND status = 'Active' AND company = %s
    """, (pharmacy_item_name, company), as_dict=True)

    company_stock_qty = float(company_result[0].total_qty) if company_result else 0.0
    active_batches     = int(company_result[0].batch_count) if company_result else 0

    # ── Step 2: Compute GLOBAL stock across ALL companies ──────────────────────
    global_result = frappe.db.sql("""
        SELECT COALESCE(SUM(remaining_qty), 0) as total_qty
        FROM `tabPharmacy Batch`
        WHERE item = %s AND status = 'Active'
    """, pharmacy_item_name, as_dict=True)

    global_stock = float(global_result[0].total_qty) if global_result else 0.0

    # ── Step 3: Update or insert the company_stock child row ───────────────────
    pi_doc = frappe.get_doc("Pharmacy Item", pharmacy_item_name)

    existing_row = None
    for row in pi_doc.company_stock:
        if row.company == company:
            existing_row = row
            break

    if existing_row:
        existing_row.current_stock      = company_stock_qty
        existing_row.active_batch_count = active_batches
        existing_row.last_updated       = now_datetime()
    else:
        pi_doc.append("company_stock", {
            "company":            company,
            "current_stock":      company_stock_qty,
            "active_batch_count": active_batches,
            "last_updated":       now_datetime()
        })

    # ── Step 4: Update the GLOBAL current_stock field ───────────────────────────
    pi_doc.current_stock = global_stock

    pi_doc.save(ignore_permissions=True)
    frappe.db.commit()

    return company_stock_qty


def get_company_stock(pharmacy_item_name, company):
    """
    Quick read-only lookup of a Pharmacy Item's stock for one specific
    company, without loading the full document. Used for validation
    checks before allowing a transfer/sale.
    """
    result = frappe.db.sql("""
        SELECT COALESCE(SUM(remaining_qty), 0) as total_qty
        FROM `tabPharmacy Batch`
        WHERE item = %s AND status = 'Active' AND company = %s
    """, (pharmacy_item_name, company), as_dict=True)

    return float(result[0].total_qty) if result else 0.0