"""
taal_inventory/api/refill_webhook.py  ← REPLACE your existing file with this

Handles incoming webhooks from Meta WhatsApp Business Platform for refill reminders.

Webhook URL (register in Meta Business Manager → WhatsApp → Configuration):
  https://erp.taalhealthcare.com/api/method/taal_inventory.api.refill_webhook.handle

Verify token: whatever you set in Refill Reminder Settings → Webhook Verify Token
"""

import frappe
import json
from frappe.utils import today


@frappe.whitelist(allow_guest=True)
def handle():
    """
    Unified handler for GET (Meta verification) and POST (incoming events).
    """
    if frappe.local.request.method == "GET":
        return _verify_webhook()
    return _process_webhook_event()


# ── Webhook verification (GET) ─────────────────────────────────────────────────

def _verify_webhook():
    """
    Meta sends a GET request with hub.challenge when you first register the webhook URL.
    We must respond with hub.challenge to confirm ownership.
    Token is stored in Refill Reminder Settings → webhook_verify_token.
    """
    params    = frappe.local.request.args
    mode      = params.get("hub.mode")
    token     = params.get("hub.verify_token")
    challenge = params.get("hub.challenge")

    settings     = frappe.get_single("Refill Reminder Settings")
    verify_token = settings.get("webhook_verify_token") or ""

    if mode == "subscribe" and token == verify_token:
        frappe.local.response["type"]   = "text"
        frappe.local.response["result"] = challenge
        return challenge

    frappe.local.response.http_status_code = 403
    return {"error": "Token mismatch — verification failed"}


# ── Webhook event processing (POST) ───────────────────────────────────────────

def _process_webhook_event():
    """
    Processes all incoming Meta webhook POST payloads.
    Two event categories:
      statuses[]  → delivered, read, failed
      messages[]  → button click (Request Refill), text reply
    """
    try:
        raw_body = frappe.local.request.get_data(as_text=True)
        payload  = json.loads(raw_body) if raw_body else {}
    except Exception:
        payload = frappe.local.form_dict

    entries = payload.get("entry") or []

    for entry in entries:
        changes = entry.get("changes") or []
        for change in changes:
            value = change.get("value") or {}

            for status_obj in (value.get("statuses") or []):
                _handle_status_update(status_obj)

            for message_obj in (value.get("messages") or []):
                _handle_incoming_message(message_obj)

    return {"status": "ok"}


# ── Status update handler ──────────────────────────────────────────────────────

def _handle_status_update(status_obj):
    """
    Meta status payload:
    {
        "id": "wamid.xxx",        ← our outgoing message ID (stored in meta_message_id)
        "status": "delivered",    ← or "read" or "failed"
        "errors": [...]           ← only on "failed"
    }
    """
    message_id = status_obj.get("id") or ""
    status     = status_obj.get("status") or ""

    if not message_id or not status:
        return

    log_name = frappe.db.get_value(
        "Refill Reminder Log",
        {"meta_message_id": message_id},
        "name"
    )
    if not log_name:
        return

    status_map = {
        "delivered": "Delivered",
        "read":      "Read",
        "failed":    "Failed",
    }
    new_status = status_map.get(status)
    if not new_status:
        return

    log = frappe.get_doc("Refill Reminder Log", log_name)

    # Don't downgrade — if status is already Read, don't set back to Delivered
    if log.reminder_status == "Read" and new_status == "Delivered":
        return

    log.reminder_status = new_status

    if new_status == "Failed":
        log.retry_flag = 1
        errors = status_obj.get("errors") or []
        if errors:
            log.failure_reason = str(errors[0].get("message") or errors[0])[:500]

    log.save(ignore_permissions=True)
    frappe.db.commit()


# ── Incoming message handler ───────────────────────────────────────────────────

def _handle_incoming_message(message_obj):
    """
    Handles button clicks ("Request Refill" CTA) and free-text replies.

    Button click payload:
    {
        "type": "button",
        "button": {"payload": "REQUEST_REFILL", "text": "Request Refill"},
        "context": {"id": "wamid.xxx"}   ← ID of our original outgoing message
    }

    Text reply payload:
    {
        "type": "text",
        "text": {"body": "yes please"},
        "context": {"id": "wamid.xxx"}
    }
    """
    msg_type        = message_obj.get("type") or ""
    context         = message_obj.get("context") or {}
    original_msg_id = context.get("id") or ""

    if not original_msg_id:
        return

    log_name = frappe.db.get_value(
        "Refill Reminder Log",
        {"meta_message_id": original_msg_id},
        "name"
    )
    if not log_name:
        return

    log = frappe.get_doc("Refill Reminder Log", log_name)

    if msg_type == "button":
        log.button_clicked  = 1
        log.reminder_status = "Button Clicked"

        if not log.order_created:
            new_so = _create_draft_sales_order(log)
            if new_so:
                log.order_created      = 1
                log.linked_sales_order = new_so
                log.reminder_status    = "Order Created"

    elif msg_type == "text":
        reply_body           = (message_obj.get("text") or {}).get("body") or ""
        log.customer_replied = 1
        log.reply_text       = reply_body[:1000]
        log.reminder_status  = "Replied"

    log.save(ignore_permissions=True)
    frappe.db.commit()


# ── Draft Sales Order on button click ─────────────────────────────────────────

def _create_draft_sales_order(log):
    """
    Copies items from the original Sales Order and creates a new Draft SO.
    Sales team reviews and submits it — not auto-submitted.
    Returns new SO name or None on failure.
    """
    if not log.sales_order:
        frappe.log_error(
            "Cannot create refill SO — no source sales_order in log {0}".format(log.name),
            "Refill Webhook"
        )
        return None

    try:
        source_so = frappe.get_doc("Sales Order", log.sales_order)

        new_so              = frappe.new_doc("Sales Order")
        new_so.customer     = log.customer
        new_so.order_type   = "Sales"
        new_so.delivery_date = frappe.utils.add_days(today(), 3)
        new_so.remarks      = "Refill — auto-created from WhatsApp reminder {0}".format(log.name)

        for item in source_so.items:
            new_so.append("items", {
                "item_code":     item.item_code,
                "item_name":     item.item_name,
                "qty":           item.qty,
                "rate":          item.rate,
                "delivery_date": new_so.delivery_date,
                "uom":           item.uom,
            })

        new_so.insert(ignore_permissions=True)
        return new_so.name

    except Exception as exc:
        frappe.log_error(str(exc), "Refill SO Creation Error — log {0}".format(log.name))
        return None