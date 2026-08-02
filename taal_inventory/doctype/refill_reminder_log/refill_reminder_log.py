"""
Refill Reminder Log — DocType controller
taal_inventory/taal_inventory/doctype/refill_reminder_log/refill_reminder_log.py

No custom logic needed here — all business logic lives in
taal_inventory/utils/refill_reminder.py (the scheduler)
and taal_inventory/api/refill_webhook.py (the webhook handler).
"""

import frappe
from frappe.model.document import Document


class RefillReminderLog(Document):
    pass