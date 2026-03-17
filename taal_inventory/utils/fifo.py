import frappe
from frappe.utils import now_datetime, today


def deduct_stock_fifo(item_code, qty_to_deduct, reference_doctype,
                      reference_no, patient_name=None, deducted_by=None):
    """
    Core FIFO deduction engine.
    Called from Sales Invoice on_submit.
    """
    # Step 1: Fetch active, non-expired batches — oldest first
    batches = frappe.get_all(
        "Pharmacy Batch",
        filters={
            "item": item_code,
            "status": "Active",
            "remaining_qty": [">", 0],
            "expiry_date": [">", today()]
        },
        fields=["name", "batch_id", "remaining_qty", "creation_date"],
        order_by="creation_date ASC"
    )

    # Step 2: Validate stock availability
    if not batches:
        frappe.throw(
            f"No valid stock available for {item_code}. "
            f"All batches are expired or exhausted."
        )

    total_available = sum(b.remaining_qty for b in batches)
    if total_available < qty_to_deduct:
        frappe.throw(
            f"Insufficient stock for {item_code}. "
            f"Available: {total_available}, Required: {qty_to_deduct}"
        )

    # Step 3: Deduct batch by batch (FIFO order)
    remaining = qty_to_deduct
    consumed = []

    for batch in batches:
        if remaining <= 0:
            break
        deduct = min(batch.remaining_qty, remaining)
        new_qty = batch.remaining_qty - deduct

        frappe.db.set_value("Pharmacy Batch", batch.name, {
            "remaining_qty": new_qty,
            "status": "Exhausted" if new_qty == 0 else "Active"
        })

        consumed.append({
            "batch": batch.name,
            "batch_id": batch.batch_id,
            "qty": deduct
        })
        remaining -= deduct

    # Step 4: Recalculate current stock on item
    new_stock = _recalculate_current_stock(item_code)

    # Step 5: Write deduction log entries
    for entry in consumed:
        _log_deduction(
            item_code=item_code,
            batch_id=entry["batch"],
            qty_deducted=entry["qty"],
            reference_doctype=reference_doctype,
            reference_no=reference_no,
            patient_name=patient_name or "",
            balance_after=new_stock,
            deducted_by=deducted_by or frappe.session.user
        )

    # Step 6: Check thresholds and send notifications
    _check_and_notify(item_code, new_stock)

    frappe.db.commit()
    return consumed


def reverse_stock_fifo(item_code, qty_to_restore, batch_name):
    """Reverse a deduction when Sales Invoice is cancelled."""
    current = frappe.db.get_value(
        "Pharmacy Batch", batch_name, "remaining_qty"
    ) or 0
    frappe.db.set_value("Pharmacy Batch", batch_name, {
        "remaining_qty": current + qty_to_restore,
        "status": "Active"
    })
    _recalculate_current_stock(item_code)
    frappe.db.commit()


def _recalculate_current_stock(item_code):
    """Sum remaining_qty of all Active batches for the item."""
    result = frappe.db.sql("""
        SELECT COALESCE(SUM(remaining_qty), 0) as total
        FROM `tabPharmacy Batch`
        WHERE item = %s AND status = 'Active'
    """, item_code, as_dict=True)

    total = result[0].total if result else 0.0
    frappe.db.set_value("Pharmacy Item", item_code, "current_stock", total)
    return total


def _log_deduction(item_code, batch_id, qty_deducted, reference_doctype,
                   reference_no, patient_name, balance_after, deducted_by):
    """Append a row to Deduction Log child table inside Pharmacy Item."""
    item_doc = frappe.get_doc("Pharmacy Item", item_code)
    item_doc.append("deduction_log", {
        "deduction_datetime": now_datetime(),
        "reference_doctype":  reference_doctype,
        "reference_no":       reference_no,
        "patient_name":       patient_name,
        "batch_id":           batch_id,
        "qty_deducted":       qty_deducted,
        "balance_after":      balance_after,
        "deducted_by":        deducted_by
    })
    item_doc.save(ignore_permissions=True)


def _check_and_notify(item_code, current_stock):
    """Fire notifications if stock crosses any threshold."""
    from taal_inventory.utils.notifications import send_stock_alert
    item = frappe.get_doc("Pharmacy Item", item_code)

    if current_stock <= (item.min_stock_qty or 0):
        send_stock_alert(item, current_stock, "MIN")
    elif current_stock <= (item.alert_qty or 0):
        send_stock_alert(item, current_stock, "ALERT")
    elif current_stock <= (item.reorder_value or 0):
        send_stock_alert(item, current_stock, "REORDER")


@frappe.whitelist()
def get_available_batches(item_code):
    """
    Called from Client Script on Sales Invoice.
    Returns blocked=True if no valid batches exist.
    """
    batches = frappe.get_all(
        "Pharmacy Batch",
        filters={
            "item": item_code,
            "status": "Active",
            "remaining_qty": [">", 0],
            "expiry_date": [">", today()]
        },
        fields=["name", "remaining_qty", "expiry_date", "batch_id"]
    )
    return {
        "blocked": len(batches) == 0,
        "batches": batches
    }