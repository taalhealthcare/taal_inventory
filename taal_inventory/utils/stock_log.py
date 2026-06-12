"""
taal_inventory/utils/stock_log.py

Single source of truth for all stock movement logging.
Every stock event (IN, OUT, Reversal) writes one row to
Pharmacy Item's deduction_log child table.

CRITICAL PATTERN: Uses pi_doc.append() + pi_doc.save() — NOT frappe.db.insert().
frappe.db.insert() bypasses naming series (TPCHC-DL-.YYYY.-.########),
bypasses idx assignment, and produces rows that never appear in the UI.
The append+save pattern is the only correct way to write child table rows.

Called by:
  - events/sales_invoice.py    (Stock OUT on submit, Reversal IN on cancel)
  - events/stock_receipt.py    (Stock IN on submit, Reversal OUT on cancel)
  - events/purchase_invoice.py (Stock IN on submit, Reversal OUT on cancel)
  - utils/fifo.py              (_log_deduction for Stock Adjustment)

Event types (must match Deduction Log.event_type Select options):
  "Stock IN"      - stock added (SR submit, PI submit)
  "Stock OUT"     - stock deducted (SI submit)
  "Reversal IN"   - stock restored (SI cancel)
  "Reversal OUT"  - stock removed (SR cancel, PI cancel)
  "Transfer IN"   - stock received from another company (future use)
  "Transfer OUT"  - stock sent to another company (future use)
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
        The `name` of the Pharmacy Item (e.g. TPCHC-PI-2026-0009)
    event_type : str
        One of: Stock IN, Stock OUT, Reversal IN, Reversal OUT,
                Transfer IN, Transfer OUT
    reference_doctype : str
        Sales Invoice / Purchase Invoice / Stock Receipt /
        Stock Adjustment / Opening Stock
    reference_no : str
        The document name (e.g. SINV-26-00010)
    batch_id : str
        The `name` of the Pharmacy Batch (TPCHC-PB-2026-XXXXX)
    qty : float
        Quantity moved — always positive, event_type conveys direction
    balance_after : float
        current_stock AFTER this event
    cost_price, selling_price, mrp_price : float
        Pricing at time of event
    warehouse : str
        Warehouse involved
    patient_name : str
        Patient / customer name (for SI events)
    deducted_by : str
        User who triggered the event
    remarks : str
        Free text note
    """
    try:
        # Load the Pharmacy Item document
        pi_doc = frappe.get_doc("Pharmacy Item", pharmacy_item_name)

        # Append a new row to the deduction_log child table
        # This is the ONLY correct pattern — ensures naming series,
        # idx, parent/parentfield/parenttype are all set correctly
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
            "patient_name":       patient_name or "",
            "deducted_by":        deducted_by or frappe.session.user,
            "remarks":            remarks or "",
        })

        # Save with ignore_permissions to avoid permission blocks
        pi_doc.save(ignore_permissions=True)
        frappe.db.commit()

    except Exception as e:
        # Log errors but NEVER crash the parent transaction
        # A logging failure must never block a SI/PI/SR submit or cancel
        frappe.log_error(
            f"Stock log write failed — item: {pharmacy_item_name}, "
            f"event: {event_type}, ref: {reference_no}\n"
            f"Error: {str(e)}",
            "Stock Log Error"
        )