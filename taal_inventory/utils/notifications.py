import frappe
import requests
import json


def _cfg(field):
    """Read a value from Pharmacy Settings Single Doctype."""
    return frappe.db.get_single_value("Pharmacy Settings", field)


# ── Email Alert ─────────────────────────────────────────────────
def send_email_alert(item, current_stock, alert_type):
    if not _cfg("enable_email_alerts"):
        return

    subjects = {
        "REORDER": f"[Reorder Required] {item.item_name} — Stock Low",
        "ALERT":   f"[URGENT ALERT] {item.item_name} — Critical Stock Level",
        "MIN":     f"[CRITICAL] {item.item_name} — BELOW MINIMUM STOCK!",
    }

    suggested = max(0, (item.max_stock_qty or 0) - current_stock)

    body = f"""
TAAL+ Healthcare — Automated Stock Alert
==========================================
Item Name     : {item.item_name}
Item Code     : {item.item_code}
Current Stock : {current_stock} {item.uom}
Alert Level   : {item.alert_qty} {item.uom}
Reorder Level : {item.reorder_value} {item.uom}
Min Stock     : {item.min_stock_qty} {item.uom}
Max Stock     : {item.max_stock_qty} {item.uom}

Suggested Reorder Qty : {suggested} {item.uom}

Please arrange restocking immediately.
— TAAL+ Inventory System (Websar)
    """

    recipients = []
    admin = _cfg("admin_email")
    receptionist = _cfg("receptionist_email")

    if admin:
        recipients.append(admin)
    if receptionist and alert_type in ("REORDER", "ALERT"):
        recipients.append(receptionist)

    if recipients:
        frappe.sendmail(
            recipients=recipients,
            subject=subjects.get(alert_type, "Stock Alert"),
            message=body
        )


# ── WhatsApp Alert (Meta Cloud API) ─────────────────────────────
def send_whatsapp_alert(item, current_stock, alert_type):
    if not _cfg("enable_wa_alerts"):
        return

    token    = _cfg("meta_whatsapp_token")
    phone_id = _cfg("meta_phone_number_id")
    to_num   = _cfg("admin_whatsapp_number")

    if not all([token, phone_id, to_num]):
        frappe.log_error(
            "WhatsApp config incomplete in Pharmacy Settings",
            "WA Config Error"
        )
        return

    urgency = "🔴 CRITICAL STOCK" if alert_type == "MIN" else "🟠 STOCK ALERT"
    suggested = max(0, (item.max_stock_qty or 0) - current_stock)

    msg = (
        f"{urgency} — TAAL+ Healthcare\n\n"
        f"*Item:* {item.item_name}\n"
        f"*Current Stock:* {current_stock} {item.uom}\n"
        f"*Alert Threshold:* {item.alert_qty} {item.uom}\n"
        f"*Reorder Level:* {item.reorder_value} {item.uom}\n"
        f"*Suggested Reorder:* {suggested} {item.uom}\n\n"
        f"Please restock immediately."
    )

    url = f"https://graph.facebook.com/v18.0/{phone_id}/messages"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": to_num,
        "type": "text",
        "text": {
            "preview_url": False,
            "body": msg
        }
    }

    try:
        resp = requests.post(
            url,
            headers=headers,
            data=json.dumps(payload),
            timeout=10
        )
        if resp.status_code != 200:
            frappe.log_error(
                f"WA send failed: {resp.text}",
                "WA Notification Error"
            )
    except Exception as e:
        frappe.log_error(str(e), "WA Notification Exception")


# ── Unified Dispatcher ───────────────────────────────────────────
def send_stock_alert(item, current_stock, alert_type):
    """
    Main entry point for all stock alerts.
    Email fires for all alert types.
    WhatsApp fires only for ALERT and MIN (critical levels).
    """
    send_email_alert(item, current_stock, alert_type)
    if alert_type in ("ALERT", "MIN"):
        send_whatsapp_alert(item, current_stock, alert_type)