import frappe
from frappe.utils import today, add_days, date_diff, getdate


def check_expiry_alerts():
    """Run daily. Alert on batches near expiry or already expired."""
    batches = frappe.get_all(
        "Pharmacy Batch",
        filters={"status": "Active", "remaining_qty": [">", 0]},
        fields=["name", "item", "batch_id", "expiry_date", "remaining_qty"]
    )
    for b in batches:
        item = frappe.get_doc("Pharmacy Item", b.item)
        threshold = item.expiry_alert_days or 30
        alert_date = add_days(today(), threshold)

        if getdate(b.expiry_date) < getdate(today()):
            # Already expired — mark batch
            frappe.db.set_value("Pharmacy Batch", b.name, "status", "Expired")
            _send_expiry_email(item, b, expired=True)

        elif getdate(b.expiry_date) <= getdate(alert_date):
            # Expiring soon
            _send_expiry_email(item, b, expired=False)

    frappe.db.commit()


def _send_expiry_email(item, batch, expired=False):
    from taal_inventory.utils.notifications import _cfg
    admin = _cfg("admin_email")
    if not admin or not _cfg("enable_email_alerts"):
        return

    days_left = date_diff(batch.expiry_date, today())
    if expired:
        status_text = "ALREADY EXPIRED"
    else:
        status_text = f"Expires in {days_left} days"

    frappe.sendmail(
        recipients=[admin],
        subject=f"[Expiry Alert] {item.item_name} — Batch {batch.batch_id} — {status_text}",
        message=f"""
TAAL+ Healthcare — Expiry Alert
=================================
Item        : {item.item_name}
Batch ID    : {batch.batch_id}
Expiry Date : {batch.expiry_date}
Status      : {status_text}
Qty in Stock: {batch.remaining_qty} {item.uom}

Action Required: Quarantine, return to supplier, or dispose as per protocol.
— TAAL+ Inventory System
        """
    )


def check_low_stock_daily():
    """Run daily. Re-send alerts for items still below alert_qty."""
    items = frappe.get_all(
        "Pharmacy Item",
        filters={"is_active": 1},
        fields=["name"]
    )
    for i in items:
        item = frappe.get_doc("Pharmacy Item", i.name)
        cs = item.current_stock or 0
        if cs <= (item.alert_qty or 0) and item.alert_qty > 0:
            from taal_inventory.utils.notifications import send_stock_alert
            send_stock_alert(item, cs, "ALERT")