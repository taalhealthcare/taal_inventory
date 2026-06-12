"""
taal_inventory/utils/stock_log.py

Single source of truth for all stock movement logging.
Every stock event (IN, OUT, Reversal) writes one row to
Pharmacy Item's deduction_log child table via this function.

Called by:
  - events/sales_invoice.py   (Stock OUT on submit, Reversal IN on cancel)
  - events/stock_receipt.py   (Stock IN on submit, Reversal OUT on cancel)
  - events/purchase_invoice.py(Stock IN on submit, Reversal OUT on cancel)
  - utils/fifo.py             (_log_deduction for Stock Adjustment)

Event types (must match Deduction Log.event_type Select options):
  "Stock IN"      — stock added (SR submit, PI submit new batch)
  "Stock OUT"     — stock deducted (SI submit)
  "Reversal IN"   — stock restored (SI cancel)
  "Reversal OUT"  — stock removed (SR cancel, PI cancel)
  "Transfer IN"   — stock received from another company
  "Transfer OUT"  — stock sent to another company
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
    patient_name="",
    deducted_by=None,
    remarks=""
):
    """
    Appends one row to Pharmacy Item's deduction_log child table.

    Parameters
    ----------
    pharmacy_item_name : str
        The `name` field of the Pharmacy Item (e.g. TPCHC-PI-2026-0009)
    event_type : str
        One of: Stock IN, Stock OUT, Reversal IN, Reversal OUT,
                Transfer IN, Transfer OUT
    reference_doctype : str
        The source DocType: Sales Invoice / Purchase Invoice /
        Stock Receipt / Stock Adjustment / Opening Stock
    reference_no : str
        The name/ID of the source document
    batch_id : str
        The `name` of the Pharmacy Batch (TPCHC-PB-2026-XXXXX)
        NOT the batch_id string — the document name
    qty : float
        Quantity moved. Always positive — event_type conveys direction.
    balance_after : float
        current_stock on Pharmacy Item AFTER this event
    cost_price : float
        CP at time of event
    selling_price : float
        SP at time of event
    mrp_price : float
        MRP at time of event
    warehouse : str
        Warehouse involved
    patient_name : str
        Patient / customer name (for SI events)
    deducted_by : str
        User who triggered the event (defaults to current session user)
    remarks : str
        Free text note e.g. "Cancelled SI SINV-26-00010 — stock restored"
    """
    try:
        frappe.db.insert({
            "doctype":            "Deduction Log",
            "name":               frappe.generate_hash(length=10),
            "parent":             pharmacy_item_name,
            "parentfield":        "deduction_log",
            "parenttype":         "Pharmacy Item",
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
            "patient_name":       patient_name or "",
            "deducted_by":        deducted_by or frappe.session.user,
            "remarks":            remarks or "",
        })
    except Exception as e:
        # Log errors but never crash the parent transaction
        frappe.log_error(
            f"Stock log write failed for {pharmacy_item_name} "
            f"({event_type} / {reference_no}): {str(e)}",
            "Stock Log Error"
        )