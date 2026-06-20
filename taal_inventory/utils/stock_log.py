"""
taal_inventory/utils/stock_log.py

Single source of truth for all stock movement logging.
Every stock event (IN, OUT, Reversal, Transfer) writes one row to
Pharmacy Item's deduction_log child table.

UPDATED: Added `company` parameter — every deduction_log entry now
records WHICH company the event happened in. Critical for multi-company
operations (Point 3 — Inter-Company Transfer) where the same Pharmacy
Item can have events happening across multiple companies, and audit
trail must clearly show which company each event belongs to.

CRITICAL PATTERN: Uses pi_doc.append() + pi_doc.save() — NOT frappe.db.insert().
frappe.db.insert() bypasses naming series, idx assignment, and produces
rows that never appear in the UI.

Called by:
  - events/sales_invoice.py        (Stock OUT, Reversal IN)
  - events/stock_receipt.py        (Stock IN, Reversal OUT)
  - events/purchase_invoice.py     (Stock IN, Reversal OUT)
  - events/pharmacy_stock_transfer.py (Transfer OUT, Transfer IN)

Event types (must match Deduction Log.event_type Select options):
  "Stock IN"      - stock added (SR submit, PI submit)
  "Stock OUT"     - stock deducted (SI submit, Stock Adjustment)
  "Reversal IN"   - stock restored (SI cancel)
  "Reversal OUT"  - stock removed (SR cancel, PI cancel)
  "Transfer IN"   - stock received from another company
  "Transfer OUT"  - stock sent to another company
"""

import frappe
from frappe.utils import now_datetime, flt


def append_stock_log(
    pharmacy_item_name,
    event_type,
    reference_doctype,
    reference_no,
    batch_id,
    qty,
    balance_after,
    cost_price=0,
    selling_price=0,
    mrp_price=0,
    warehouse="",
    company="",
    patient_name="",
    deducted_by=None,
    remarks=""
):
    """
    Appends one row to Pharmacy Item's deduction_log child table.

    Parameters
    ----------
    pharmacy_item_name : str
        The `name` of the Pharmacy Item (e.g. TPCHC-PI-2026-0009)
    event_type : str
        One of: Stock IN, Stock OUT, Reversal IN, Reversal OUT,
                Transfer IN, Transfer OUT
    reference_doctype : str
        Sales Invoice / Purchase Invoice / Stock Receipt /
        Stock Adjustment / Pharmacy Stock Transfer / Opening Stock
    reference_no : str
        The document name (e.g. SINV-26-00010)
    batch_id : str
        The `name` of the Pharmacy Batch (TPCHC-PB-2026-XXXXX)
    qty : float
        Quantity moved — always positive, event_type conveys direction
    balance_after : float
        Stock balance AFTER this event (company-specific when relevant)
    cost_price, selling_price, mrp_price : float
        Pricing at time of event
    warehouse : str
        Warehouse involved
    company : str
        Which company this event happened in — CRITICAL for multi-company
        audit trail. Always pass this explicitly; do not leave blank
        for any event going forward.
    patient_name : str
        Patient / customer name (for SI events)
    deducted_by : str
        User who triggered the event
    remarks : str
        Free text note
    """
    try:
        pi_doc = frappe.get_doc("Pharmacy Item", pharmacy_item_name)

        pi_doc.append("deduction_log", {
            "deduction_datetime": now_datetime(),
            "event_type":         event_type,
            "reference_doctype":  reference_doctype,
            "reference_no":       reference_no,
            "batch_id":           batch_id,
            "qty_deducted":       flt(qty),
            "balance_after":      flt(balance_after),
            "cost_price":         flt(cost_price),
            "selling_price":      flt(selling_price),
            "mrp_price":          flt(mrp_price),
            "warehouse":          warehouse or "",
            "company":            company or "",
            "patient_name":       patient_name or "",
            "deducted_by":        deducted_by or frappe.session.user,
            "remarks":            remarks or "",
        })

        pi_doc.save(ignore_permissions=True)
        frappe.db.commit()

    except Exception as e:
        frappe.log_error(
            f"Stock log write failed — item: {pharmacy_item_name}, "
            f"event: {event_type}, ref: {reference_no}\n"
            f"Error: {str(e)}",
            "Stock Log Error"
        )