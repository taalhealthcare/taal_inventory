"""
taal_inventory/utils/refill_reminder.py  ← REPLACE previous version with this

CHANGE FROM PREVIOUS VERSION:
  All Customer field references now use "custom_" prefix because Frappe v15
  names every field created via Customize Form with custom_ prepended.

  custom_customer_source   (was: customer_source)
  custom_whatsapp_consent  (was: whatsapp_consent)
  custom_active_for_refill (was: active_for_refill)
  custom_next_refill_date  (was: next_refill_date)
  custom_last_refill_order (was: last_refill_order)
  custom_last_reminder_date(was: last_reminder_date)
  custom_reminder_status   (was: reminder_status)
"""

import frappe
from frappe.utils import today, add_days, date_diff


def run_refill_reminder_check():
    """
    Daily scheduler entry point.
    Registered in hooks.py → scheduler_events → daily.
    """
    refill_settings = frappe.get_single("Refill Reminder Settings")

    if not refill_settings.get("refill_reminder_enabled"):
        frappe.logger("refill_reminder").info(
            "Refill reminder disabled in Refill Reminder Settings. Skipping."
        )
        return

    template_name     = refill_settings.get("refill_reminder_template_name") or ""
    template_language = refill_settings.get("refill_reminder_template_language") or "en_US"
    days_before       = int(refill_settings.get("refill_reminder_days_before") or 2)

    # WhatsApp credentials from Doctor Consultation Meet Settings (shared)
    dc_settings     = frappe.get_single("Doctor Consultation Meet Settings")
    phone_number_id = dc_settings.get("whatsapp_phone_number_id") or ""
    access_token    = dc_settings.get("whatsapp_access_token") or ""

    if not phone_number_id or not access_token:
        frappe.log_error(
            "Refill reminder aborted: missing WhatsApp credentials in "
            "Doctor Consultation Meet Settings.",
            "Refill Reminder — Config Error"
        )
        return

    if not template_name:
        frappe.log_error(
            "Refill reminder aborted: refill_reminder_template_name not set "
            "in Refill Reminder Settings.",
            "Refill Reminder — Config Error"
        )
        return

    run_date       = today()
    trigger_cutoff = add_days(run_date, days_before)

    # ── Query eligible WooCommerce customers ───────────────────────────────────
    # NOTE: all filters use "custom_" prefix — Frappe v15 Customize Form convention
    customers = frappe.get_all(
        "Customer",
        filters=[
            ["custom_customer_source",   "=",  "WooCommerce"],
            ["custom_whatsapp_consent",  "=",  1],
            ["custom_active_for_refill", "=",  1],
            ["custom_next_refill_date",  "<=", trigger_cutoff],
            ["custom_next_refill_date",  "is", "set"],
        ],
        fields=[
            "name",
            "customer_name",
            "mobile_no",
            "custom_next_refill_date",
            "custom_last_refill_order",
        ],
        limit_page_length=0,
    )

    if not customers:
        frappe.logger("refill_reminder").info(
            "Refill reminder: no eligible customers found for {0}".format(run_date)
        )
        return

    frappe.logger("refill_reminder").info(
        "Refill reminder: {0} eligible customer(s) found for {1}".format(
            len(customers), run_date
        )
    )

    sent_count  = 0
    skip_count  = 0
    error_count = 0

    for customer in customers:
        result = _process_single_customer(
            customer          = customer,
            run_date          = run_date,
            phone_number_id   = phone_number_id,
            access_token      = access_token,
            template_name     = template_name,
            template_language = template_language,
        )
        if result == "sent":
            sent_count += 1
        elif result == "skip":
            skip_count += 1
        else:
            error_count += 1

    frappe.db.commit()

    frappe.logger("refill_reminder").info(
        "Refill reminder complete — sent: {0}, skipped (dedup): {1}, errors: {2}".format(
            sent_count, skip_count, error_count
        )
    )


def _process_single_customer(customer, run_date, phone_number_id,
                              access_token, template_name, template_language):
    """Process one customer. Returns 'sent' | 'skip' | 'error'."""
    customer_id = customer["name"]

    # ── Dedup guard ────────────────────────────────────────────────────────────
    reminder_ref = "RFL-{0}-{1}".format(customer_id, run_date.replace("-", ""))

    already_sent = frappe.db.get_value(
        "Refill Reminder Log",
        {"reminder_reference": reminder_ref},
        "name"
    )
    if already_sent:
        return "skip"

    # ── Validate mobile number ─────────────────────────────────────────────────
    raw_phone = (customer.get("mobile_no") or "").strip()
    wa_number = _clean_phone_to_wa_format(raw_phone)

    if not wa_number:
        frappe.logger("refill_reminder").warning(
            "Skipping {0} — invalid mobile_no: '{1}'".format(customer_id, raw_phone)
        )
        return "error"

    # ── Product context from last Sales Order ──────────────────────────────────
    # NOTE: field on Customer is custom_last_refill_order
    product_name, qty_supplied = _get_product_context(
        customer.get("custom_last_refill_order")
    )

    # ── Days remaining ─────────────────────────────────────────────────────────
    next_refill    = customer.get("custom_next_refill_date")
    days_remaining = date_diff(next_refill, run_date) if next_refill else 0
    days_remaining = max(0, days_remaining)

    # ── Meta WhatsApp Cloud API call ───────────────────────────────────────────
    api_url = "https://graph.facebook.com/v17.0/{0}/messages".format(phone_number_id)

    payload = {
        "messaging_product": "whatsapp",
        "to": wa_number,
        "type": "template",
        "template": {
            "name": template_name,
            "language": {"code": template_language},
            "components": [
                {
                    "type": "body",
                    "parameters": [
                        {"type": "text", "text": customer.get("customer_name") or "Customer"},
                        {"type": "text", "text": product_name},
                        {"type": "text", "text": str(days_remaining)},
                    ]
                }
            ]
        }
    }

    try:
        response = frappe.make_post_request(
            url     = api_url,
            headers = {
                "Authorization": "Bearer " + access_token,
                "Content-Type":  "application/json",
            },
            data = frappe.as_json(payload),
        )

        meta_message_id = ""
        messages_list   = response.get("messages") or []
        if messages_list:
            meta_message_id = messages_list[0].get("id") or ""

    except Exception as exc:
        err = str(exc)
        frappe.log_error(err, "Refill Reminder API Error — " + customer_id)
        _create_reminder_log(
            customer_id     = customer_id,
            sales_order     = customer.get("custom_last_refill_order"),
            reminder_ref    = reminder_ref,
            run_date        = run_date,
            next_refill     = next_refill,
            wa_number       = wa_number,
            meta_message_id = "",
            status          = "Failed",
            failure_reason  = err[:500],
            product_name    = product_name,
            qty_supplied    = qty_supplied,
            days_remaining  = days_remaining,
            retry_flag      = 1,
        )
        return "error"

    # ── Create Refill Reminder Log ─────────────────────────────────────────────
    _create_reminder_log(
        customer_id     = customer_id,
        sales_order     = customer.get("custom_last_refill_order"),
        reminder_ref    = reminder_ref,
        run_date        = run_date,
        next_refill     = next_refill,
        wa_number       = wa_number,
        meta_message_id = meta_message_id,
        status          = "Sent",
        failure_reason  = "",
        product_name    = product_name,
        qty_supplied    = qty_supplied,
        days_remaining  = days_remaining,
        retry_flag      = 0,
    )

    # ── Update Customer last_reminder_date ─────────────────────────────────────
    # NOTE: custom_ prefix required for both fields
    frappe.db.set_value(
        "Customer", customer_id,
        {
            "custom_last_reminder_date": run_date,
            "custom_reminder_status":    "Sent",
        },
        update_modified=False
    )

    return "sent"


def _create_reminder_log(customer_id, sales_order, reminder_ref, run_date,
                         next_refill, wa_number, meta_message_id, status,
                         failure_reason, product_name, qty_supplied,
                         days_remaining, retry_flag):
    log = frappe.new_doc("Refill Reminder Log")
    log.customer           = customer_id
    log.sales_order        = sales_order
    log.reminder_reference = reminder_ref
    log.reminder_date      = run_date
    log.next_refill_date   = next_refill
    log.days_remaining     = days_remaining
    log.whatsapp_number    = wa_number
    log.meta_message_id    = meta_message_id
    log.reminder_status    = status
    log.failure_reason     = failure_reason
    log.product_name       = product_name
    log.qty_supplied       = qty_supplied
    log.reminder_count     = 1
    log.retry_flag         = retry_flag
    log.reminder_cycle_key = "RFL-{0}-{1}".format(customer_id, sales_order or "")
    log.insert(ignore_permissions=True)


def _get_product_context(sales_order_name):
    """Returns (product_name, qty) from first item of the Sales Order."""
    if not sales_order_name:
        return "your medicine", 30

    items = frappe.get_all(
        "Sales Order Item",
        filters    = {"parent": sales_order_name},
        fields     = ["item_name", "item_code", "qty"],
        order_by   = "idx asc",
        limit_page_length=1,
    )

    if not items:
        return "your medicine", 30

    first = items[0]
    name  = first.get("item_name") or first.get("item_code") or "your medicine"
    qty   = first.get("qty") or 30
    return name, qty


def _clean_phone_to_wa_format(raw_phone):
    """All TAAL+ customers India-only (+91). Meta expects: 919876543210"""
    phone = raw_phone.strip()
    for ch in [" ", "-", "(", ")", "+"]:
        phone = phone.replace(ch, "")

    if phone.startswith("0091"):
        phone = phone[4:]
    elif phone.startswith("91") and len(phone) == 12:
        phone = phone[2:]

    if len(phone) == 10 and phone[0] in ("6", "7", "8", "9"):
        return "91" + phone

    return ""