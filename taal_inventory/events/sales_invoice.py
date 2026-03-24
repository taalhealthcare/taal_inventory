import frappe
from frappe.utils import today, now_datetime


@frappe.whitelist()
def get_fifo_rate(item_code):
    """
    Called from Sales Invoice JS when item is selected.
    Finds the batch with NEAREST EXPIRY DATE (FEFO)
    that is active and has stock.
    Returns that batch's selling price, remaining qty,
    and expiry date so the correct rate shows upfront.
    """
    # Look up Pharmacy Item by item_code field
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

    # Fetch batch with nearest expiry date first (FEFO)
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
        "rate":          batch.selling_price or 0,
        "batch_id":      batch.batch_id or "",
        "remaining_qty": batch.remaining_qty or 0,
        "expiry_date":   str(batch.expiry_date) if batch.expiry_date else ""
    }


@frappe.whitelist()
def get_total_available_qty(item_code):
    """
    Returns total available qty across all active
    non-expired batches for this item.
    Used to validate qty on Sales Invoice.
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
    FEFO deduction — nearest expiry batch consumed first.
    """
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
        _deduct_fefo(doc=doc, row=row, pi_name=pi_name)


def on_cancel(doc, method):
    """
    Reverses FEFO stock deduction on cancel.
    """
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
            fields=["batch_id", "qty_deducted"]
        )

        for log in logs:
            current = frappe.db.get_value(
                "Pharmacy Batch",
                log.batch_id,
                "remaining_qty"
            ) or 0
            frappe.db.set_value(
                "Pharmacy Batch",
                log.batch_id, {
                    "remaining_qty": current + log.qty_deducted,
                    "status":        "Active"
                }
            )

        _recalculate_stock(pi_name)
        frappe.db.commit()


def _deduct_fefo(doc, row, pi_name):
    """
    FEFO deduction engine.
    Nearest expiry batch consumed first.
    If qty spans multiple batches — deducts across them in order.
    """
    # Fetch all active non-expired batches
    # ordered by nearest expiry first (FEFO)
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
            "selling_price"
        ],
        order_by="expiry_date ASC",
        limit_page_length=0
    )

    if not batches:
        frappe.throw(
            f"No valid stock available for {row.item_code}. "
            f"All batches are expired or exhausted."
        )

    total_available = sum(b.remaining_qty for b in batches)
    if total_available < row.qty:
        frappe.throw(
            f"Insufficient stock for {row.item_code}. "
            f"Available: {total_available}, "
            f"Required: {row.qty}. "
            f"Please reduce quantity."
        )

    remaining = row.qty
    consumed  = []

    # Deduct from nearest expiry batch first
    for batch in batches:
        if remaining <= 0:
            break
        deduct  = min(batch.remaining_qty, remaining)
        new_qty = batch.remaining_qty - deduct

        frappe.db.set_value("Pharmacy Batch", batch.name, {
            "remaining_qty": new_qty,
            "status": "Exhausted" if new_qty == 0 else "Active"
        })

        consumed.append({
            "batch":         batch.name,
            "batch_id":      batch.batch_id,
            "qty":           deduct,
            "selling_price": batch.selling_price or 0,
            "expiry_date":   str(batch.expiry_date)
        })
        remaining -= deduct

    # Recalculate current stock
    new_stock = _recalculate_stock(pi_name)

    # Get patient name safely
    patient = ""
    if doc.patient_name:
        patient = doc.patient_name
    elif doc.customer_name:
        patient = doc.customer_name

    # Log each batch consumed in Deduction Log
    for entry in consumed:
        pi_doc = frappe.get_doc("Pharmacy Item", pi_name)
        pi_doc.append("deduction_log", {
            "deduction_datetime": now_datetime(),
            "reference_doctype":  "Sales Invoice",
            "reference_no":       doc.name,
            "patient_name":       patient,
            "batch_id":           entry["batch"],
            "qty_deducted":       entry["qty"],
            "balance_after":      new_stock,
            "deducted_by":        frappe.session.user
        })
        pi_doc.save(ignore_permissions=True)

    frappe.db.commit()

    # Fire notifications if stock crosses thresholds
    _check_notify(pi_name, new_stock)


def _recalculate_stock(pi_name):
    """Recalculate current_stock from all active batches."""
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