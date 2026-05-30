"""
Razorpay Webhook Handler

Receives Razorpay webhook events (payment_link.paid / payment.failed /
payment_link.cancelled), matches the payment to the most recent Pending
Interakt Booking by phone number, and orchestrates the full post-payment flow:

  1. Update payment fields on Interakt Booking
  2. Resolve assigned doctor (speciality-based, load-balanced per slot)
  3. Create Patient Appointment
  4. Create Payment Entry
  5. Create Google Meet (Calendar event + Meet link)
  6. Notify patient via Interakt WhatsApp
  7. Notify doctor via Interakt WhatsApp (if assigned and toggle enabled)
  8. Notify admin via Interakt WhatsApp + email (always, even if Meet failed)

URL to configure in Razorpay Dashboard:
  https://erp.taalhealthcare.com/api/method/taal_inventory.api.razorpay_webhook.handle_razorpay_payment
"""

import json
import hmac
import hashlib
import re
import frappe
from frappe.utils import now_datetime

from taal_inventory.services import doctor_assignment
from taal_inventory.services import google_meet_creator
from taal_inventory.services import interakt_outbound
from taal_inventory.services import admin_notifier


# ──────────────────────────────────────────────────────────────────────────────
# IMPORTANT: Razorpay Webhook Secret
# Get from: Razorpay Dashboard → Settings → Webhooks → (your webhook) → Secret
# This must MATCH the secret configured for your webhook in Razorpay.
# ──────────────────────────────────────────────────────────────────────────────
RAZORPAY_WEBHOOK_SECRET = "3a_JN39BAkMXYub"


# ──────────────────────────────────────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────────────────────────────────────

@frappe.whitelist(allow_guest=True)
def handle_razorpay_payment():
    """POST endpoint for Razorpay webhooks."""
    try:
        raw_body = frappe.request.data
        signature = frappe.request.headers.get("X-Razorpay-Signature", "")

        # --- Verify signature (skip only when placeholder secret) ---
        if RAZORPAY_WEBHOOK_SECRET != "REPLACE_THIS_LATER":
            if not _verify_razorpay_signature(raw_body, signature):
                frappe.throw(
                    "Invalid Razorpay signature",
                    frappe.AuthenticationError
                )

        event = json.loads(raw_body)
        event_type = event.get("event", "")

        if event_type == "payment_link.paid":
            _handle_payment_success(event)

        elif event_type in ("payment.failed", "payment_link.cancelled"):
            _handle_payment_failed(event)

        else:
            frappe.log_error(
                f"Unhandled Razorpay event: {event_type}",
                "Razorpay Webhook: Unknown Event"
            )

        return {"status": "ok"}

    except Exception as e:
        frappe.log_error(
            frappe.get_traceback(),
            "Razorpay Webhook Error"
        )
        return {"status": "error", "message": str(e)}


def _verify_razorpay_signature(raw_body, signature):
    """HMAC-SHA256 verification using the webhook secret."""
    expected = hmac.new(
        RAZORPAY_WEBHOOK_SECRET.encode("utf-8"),
        raw_body,
        hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


# ──────────────────────────────────────────────────────────────────────────────
# Payment Success Handler — the main orchestration
# ──────────────────────────────────────────────────────────────────────────────

def _handle_payment_success(event):
    """
    Triggered on payment_link.paid.
    Full orchestration of post-payment flow.
    """
    try:
        # ── 1. Extract Razorpay IDs and customer phone ─────────────────
        payment_link_entity = event["payload"]["payment_link"]["entity"]
        payment_entity      = event["payload"]["payment"]["entity"]

        rzp_order_id = payment_link_entity.get("id", "")
        payment_id   = payment_entity.get("id", "")
        amount_paise = payment_entity.get("amount", 0)
        amount_inr   = amount_paise / 100

        customer_phone_raw = (
            payment_link_entity.get("customer", {}).get("contact")
            or payment_entity.get("contact", "")
            or ""
        )
        customer_phone = _normalize_phone(customer_phone_raw)

        if not customer_phone:
            frappe.log_error(
                f"No customer phone in Razorpay event. Order: {rzp_order_id}",
                "Razorpay Webhook: Missing Phone"
            )
            return

        # ── 2. Idempotency check (Razorpay retries the same event) ────
        already_processed = frappe.db.get_value(
            "Interakt Booking",
            filters={
                "razorpay_order_id": rzp_order_id,
                "payment_status": "Paid"
            },
            fieldname="name"
        )
        if already_processed:
            return  # Already handled this exact payment_link.paid

        # ── 3. Phone-based booking lookup ──────────────────────────────
        booking_name = frappe.db.get_value(
            "Interakt Booking",
            filters={
                "whatsapp_number": customer_phone,
                "payment_status": "Pending",
                "booking_status": "Pending Payment"
            },
            fieldname="name",
            order_by="creation DESC"
        )

        if not booking_name:
            frappe.log_error(
                f"No pending booking for phone {customer_phone}, order {rzp_order_id}",
                "Razorpay Webhook: Booking Not Found"
            )
            return

        booking = frappe.get_doc("Interakt Booking", booking_name)

        # ── 4. Update payment fields ───────────────────────────────────
        booking.razorpay_order_id   = rzp_order_id
        booking.payment_status      = "Paid"
        booking.payment_id          = payment_id
        booking.payment_received_at = now_datetime()
        booking.booking_status      = "Payment Confirmed"
        booking.save(ignore_permissions=True)
        frappe.db.commit()

        # ── 5. Resolve assigned doctor (speciality + load-balancing) ──
        speciality = (booking.speciality or "Other").strip()
        doctor_name = doctor_assignment.resolve_doctor(
            speciality,
            booking.preferred_date,
            booking.preferred_time_slot
        )

        doctor_details = None
        if doctor_name:
            booking.assigned_team_member = doctor_name
            booking.save(ignore_permissions=True)
            frappe.db.commit()
            doctor_details = doctor_assignment.get_doctor_details(doctor_name)

        # ── 6. Create Patient Appointment ──────────────────────────────
        _create_patient_appointment(booking)

        # ── 7. Create Payment Entry ────────────────────────────────────
        _create_payment_entry(booking, amount_inr)

        # ── 8. Refresh booking and create Google Meet ──────────────────
        booking.reload()
        booking.meet_creation_status = "Pending"
        booking.save(ignore_permissions=True)
        frappe.db.commit()

        meet_result = google_meet_creator.create_meet_for_booking(booking)

        if meet_result.get("success"):
            booking.google_meet_link         = meet_result.get("meet_link", "")
            booking.google_calendar_event_id = meet_result.get("event_id", "")
            booking.meet_creation_status     = "Created"
            booking.meet_creation_error      = ""
        else:
            booking.meet_creation_status = "Failed"
            booking.meet_creation_error  = (meet_result.get("error") or "Unknown")[:500]

        booking.save(ignore_permissions=True)
        frappe.db.commit()

        # ── 9. Patient WhatsApp (only if Meet succeeded) ───────────────
        if meet_result.get("success"):
            _send_patient_whatsapp(booking, doctor_details, speciality)

        # ── 10. Doctor WhatsApp (only if assigned + Meet succeeded) ───
        settings = frappe.get_single("Booking API Settings")
        if (
            meet_result.get("success")
            and doctor_details
            and doctor_details.get("exists")
            and settings.send_doctor_notification
        ):
            _send_doctor_whatsapp(booking, doctor_details, speciality)

        # ── 11. Admin notification (always — WhatsApp + email) ────────
        doctor_display = (
            doctor_details["display_name"]
            if (doctor_details and doctor_details.get("exists"))
            else None  # admin_notifier will fall back to "Unassigned (Other)"
        )

        admin_result = admin_notifier.notify_admin(booking, doctor_display)

        if admin_result.get("whatsapp_sent") or admin_result.get("email_sent"):
            booking.admin_message_sent = 1
            booking.save(ignore_permissions=True)
            frappe.db.commit()

        if admin_result.get("errors"):
            frappe.log_error(
                "\n".join(admin_result["errors"]),
                f"Admin notification errors: {booking.name}"
            )

    except Exception as e:
        frappe.log_error(
            frappe.get_traceback(),
            "Razorpay Payment Success Handler Error"
        )


# ──────────────────────────────────────────────────────────────────────────────
# Patient / Doctor WhatsApp senders
# ──────────────────────────────────────────────────────────────────────────────

def _send_patient_whatsapp(booking, doctor_details, speciality):
    """Send `patient_consultation_confirmed` template to the patient."""
    settings = frappe.get_single("Booking API Settings")

    doctor_label = (
        doctor_details["display_name"]
        if (doctor_details and doctor_details.get("exists"))
        else "Our medical team"
    )

    body_values = [
        booking.patient_name or "-",
        str(booking.preferred_date or "-"),
        booking.preferred_time_slot or "-",
        doctor_label,
        speciality,
        booking.google_meet_link or "-"
    ]

    result = interakt_outbound.send_template_message(
        phone_number=booking.whatsapp_number,
        template_name=settings.patient_template_name or "patient_consultation_confirmed",
        body_values=body_values
    )

    if result.get("success"):
        frappe.db.set_value(
            "Interakt Booking", booking.name, "patient_message_sent", 1
        )
        frappe.db.commit()
    else:
        frappe.log_error(
            result.get("error", "Unknown"),
            f"Patient WhatsApp failed: {booking.name}"
        )


def _send_doctor_whatsapp(booking, doctor_details, speciality):
    """Send `doctor_new_consultation_alert` template to the assigned doctor."""
    settings = frappe.get_single("Booking API Settings")

    if not doctor_details.get("mobile_phone"):
        frappe.log_error(
            f"Doctor {doctor_details.get('name')} has no mobile_phone — skipping WhatsApp",
            f"Doctor WhatsApp skipped: {booking.name}"
        )
        return

    body_values = [
        doctor_details["display_name"],
        booking.patient_name or "-",
        booking.whatsapp_number or "-",
        booking.city_location or "-",
        speciality,
        str(booking.preferred_date or "-"),
        booking.preferred_time_slot or "-",
        (booking.consultation_reason or "-")[:300],
        booking.google_meet_link or "-"
    ]

    result = interakt_outbound.send_template_message(
        phone_number=doctor_details["mobile_phone"],
        template_name=settings.doctor_template_name or "doctor_new_consultation_alert",
        body_values=body_values
    )

    if result.get("success"):
        frappe.db.set_value(
            "Interakt Booking", booking.name, "doctor_message_sent", 1
        )
        frappe.db.commit()
    else:
        frappe.log_error(
            result.get("error", "Unknown"),
            f"Doctor WhatsApp failed: {booking.name}"
        )


# ──────────────────────────────────────────────────────────────────────────────
# Patient Appointment + Payment Entry creators
# ──────────────────────────────────────────────────────────────────────────────

def _create_patient_appointment(booking):
    """Auto-creates a Patient Appointment record and links it back to the booking."""
    try:
        appt = frappe.new_doc("Patient Appointment")
        appt.patient                       = booking.patient
        appt.appointment_type              = booking.selected_service
        appt.appointment_date              = booking.preferred_date
        appt.notes                         = booking.consultation_reason
        appt.status                        = "Open"
        appt.custom_booking_source         = "Interakt WhatsApp"
        appt.custom_interakt_booking_id    = booking.name
        appt.custom_interakt_flow_name     = booking.interakt_flow_name

        if booking.assigned_team_member:
            appt.practitioner = booking.assigned_team_member

        appt.insert(ignore_permissions=True)

        frappe.db.set_value(
            "Interakt Booking",
            booking.name,
            {
                "patient_appointment": appt.name,
                "booking_status": "Appointment Scheduled"
            }
        )
        frappe.db.commit()

        return appt.name

    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "Auto Create Patient Appointment Error"
        )


def _create_payment_entry(booking, amount):
    """Auto-creates a Payment Entry for accounting reconciliation."""
    try:
        pe = frappe.new_doc("Payment Entry")
        pe.payment_type    = "Receive"
        pe.party_type      = "Customer"
        pe.party           = booking.patient
        pe.paid_amount     = amount
        pe.received_amount = amount
        pe.reference_no    = booking.payment_id
        pe.reference_date  = booking.payment_received_at
        pe.remarks         = (
            f"Razorpay Payment | "
            f"Booking: {booking.name} | "
            f"Service: {booking.selected_service} | "
            f"Patient: {booking.patient_name}"
        )
        pe.insert(ignore_permissions=True)

        frappe.db.set_value(
            "Interakt Booking",
            booking.name,
            "payment_entry",
            pe.name
        )
        frappe.db.commit()

        return pe.name

    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "Auto Create Payment Entry Error"
        )


# ──────────────────────────────────────────────────────────────────────────────
# Payment Failure Handler
# ──────────────────────────────────────────────────────────────────────────────

def _handle_payment_failed(event):
    """
    Triggered on payment.failed / payment_link.cancelled.
    Marks the most recent pending booking for that phone as Cancelled.
    """
    try:
        payment_link_entity = event["payload"]["payment_link"]["entity"]

        customer_phone_raw = (
            payment_link_entity.get("customer", {}).get("contact") or ""
        )
        customer_phone = _normalize_phone(customer_phone_raw)

        if not customer_phone:
            return

        booking_name = frappe.db.get_value(
            "Interakt Booking",
            filters={
                "whatsapp_number": customer_phone,
                "payment_status": "Pending",
                "booking_status": "Pending Payment"
            },
            fieldname="name",
            order_by="creation DESC"
        )

        if booking_name:
            frappe.db.set_value(
                "Interakt Booking",
                booking_name,
                {
                    "payment_status": "Failed",
                    "booking_status": "Cancelled"
                }
            )
            frappe.db.commit()

    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "Razorpay Payment Failed Handler Error"
        )


# ──────────────────────────────────────────────────────────────────────────────
# Phone normalization
# ──────────────────────────────────────────────────────────────────────────────

def _normalize_phone(phone):
    """
    Normalize phone to '91XXXXXXXXXX' (12 digits, no '+').
    Accepts '+917679944473', '917679944473', '07679944473', '7679944473', etc.
    """
    if not phone:
        return ""

    s = str(phone).strip()
    s = re.sub(r"[\s\-\(\)\+]", "", s)

    if not s.isdigit():
        return ""

    if len(s) == 10:
        return "91" + s
    if len(s) == 11 and s.startswith("0"):
        return "91" + s[1:]
    if len(s) == 12 and s.startswith("91"):
        return s

    return s  # let downstream decide