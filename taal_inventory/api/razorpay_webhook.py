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
RAZORPAY_WEBHOOK_SECRET = "taalplus_razorpay_secret_2026"


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


# ──────────────────────────────────────────────────────────────────────────────
# Test-only wrapper — DELETE BEFORE PRODUCTION GO-LIVE
# Allows triggering the full post-payment orchestration manually from
# System Console without making an actual Razorpay payment. Builds a synthetic
# Razorpay event from the booking and calls the real _handle_payment_success.
#
# Usage from System Console:
#     result = frappe.call(
#         "taal_inventory.api.razorpay_webhook.simulate_post_payment",
#         booking_name="IB-20260602-00003",
#     )
#     print(result)
# ──────────────────────────────────────────────────────────────────────────────

@frappe.whitelist()
def simulate_post_payment(
    booking_name=None,
    real_payment_id=None,
    real_order_id=None,
):
    """
    Manually trigger the post-payment flow for an Interakt Booking, bypassing
    Razorpay entirely. Useful for demo/QA and for manually completing bookings
    where the real webhook failed (e.g. signature mismatch).

    Parameters:
      booking_name      — required, e.g. "IB-20260603-00004"
      real_payment_id   — optional, the actual Razorpay payment_id (pay_xxxx)
      real_order_id     — optional, the actual Razorpay order/plink_id

    If real IDs are provided, they're stored on the booking instead of
    placeholder MANUAL_TEST_* values.
    """
    if not booking_name:
        return {"error": "booking_name parameter required"}

    if not frappe.db.exists("Interakt Booking", booking_name):
        return {"error": f"Booking {booking_name} not found"}

    booking = frappe.get_doc("Interakt Booking", booking_name)

    if booking.payment_status == "Paid":
        return {
            "error": "Booking already Paid",
            "booking": booking_name,
            "hint": "Run the reset script first, or test a different booking.",
        }

    # Use real Razorpay IDs if provided, otherwise placeholder
    order_id   = real_order_id   or ("plink_MANUAL_TEST_" + booking_name)
    payment_id = real_payment_id or ("pay_MANUAL_TEST_"   + booking_name)

    # Build a synthetic Razorpay payment_link.paid event
    fake_event = {
        "event": "payment_link.paid",
        "payload": {
            "payment_link": {
                "entity": {
                    "id": order_id,
                    "customer": {
                        "contact": "+" + (booking.whatsapp_number or ""),
                    },
                },
            },
            "payment": {
                "entity": {
                    "id": payment_id,
                    "amount": int((booking.payment_amount_inr or 499) * 100),
                    "contact": "+" + (booking.whatsapp_number or ""),
                },
            },
        },
    }

    # Run the same handler the live webhook calls
    _handle_payment_success(fake_event)

    # Reload and return the post-execution state (defensive — fields may not exist)
    booking.reload()

    def safe(field, default="(none)"):
        return getattr(booking, field, None) or default

    return {
        "booking_name":             booking_name,
        "payment_status":           safe("payment_status"),
        "razorpay_order_id":        safe("razorpay_order_id"),
        "payment_id":               safe("payment_id"),
        "payment_received_at":      str(safe("payment_received_at", "")),
        "booking_status":           safe("booking_status"),
        "assigned_team_member":     safe("assigned_team_member"),
        "patient_appointment":      safe("patient_appointment"),
        "payment_entry":            safe("payment_entry"),
        "google_meet_link":         safe("google_meet_link"),
        "google_calendar_event_id": safe("google_calendar_event_id"),
        "meet_creation_status":     safe("meet_creation_status"),
        "meet_creation_error":      safe("meet_creation_error"),
        "patient_message_sent":     getattr(booking, "patient_message_sent", 0),
        "doctor_message_sent":      getattr(booking, "doctor_message_sent", 0),
        "admin_message_sent":       getattr(booking, "admin_message_sent", 0),
    }


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
        # Parse 24h time from preferred_time_slot like "04:00 PM - 04:30 PM" → "16:00:00"
        appt_time_24h = "09:00:00"
        try:
            slot = (booking.preferred_time_slot or "").strip()
            start_str = slot.split(" - ")[0].strip()
            parts = start_str.split()
            hr_min = parts[0].split(":")
            hour = int(hr_min[0])
            minute = int(hr_min[1])
            period = parts[1].upper() if len(parts) > 1 else "AM"
            if period == "PM" and hour != 12:
                hour += 12
            elif period == "AM" and hour == 12:
                hour = 0
            appt_time_24h = f"{hour:02d}:{minute:02d}:00"
        except Exception:
            pass

        appt = frappe.new_doc("Patient Appointment")
        appt.patient                       = booking.patient
        appt.appointment_for               = "Practitioner"
        appt.appointment_type              = booking.selected_service
        appt.appointment_date              = booking.preferred_date
        appt.appointment_time              = appt_time_24h
        appt.duration                      = 30
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


def _get_default_company():
    """
    Resolve the company to use for accounting entries. Order of preference:
      1. Booking API Settings → default_company (if configured)
      2. Global default company (frappe.defaults)
      3. First Company record in the system
    """
    try:
        company = frappe.db.get_single_value("Booking API Settings", "default_company")
        if company and frappe.db.exists("Company", company):
            return company
    except Exception:
        pass

    global_default = frappe.defaults.get_global_default("company")
    if global_default and frappe.db.exists("Company", global_default):
        return global_default

    companies = frappe.get_all("Company", limit=1, order_by="creation asc")
    return companies[0]["name"] if companies else None


def _resolve_customer_from_patient(patient_name):
    """
    Find the Customer record linked to a Patient.
    Falls back to using patient_name as a Customer if no link exists.
    """
    if not patient_name:
        return None

    customer = frappe.db.get_value("Patient", patient_name, "customer")
    if customer and frappe.db.exists("Customer", customer):
        return customer

    if frappe.db.exists("Customer", patient_name):
        return patient_name

    return None


def _create_payment_entry(booking, amount):
    """
    Auto-creates a Payment Entry for accounting reconciliation.
    Uses the configured company's default accounts and cost center — works
    on any environment (TCPL, TPC, or whatever the live abbreviation is).
    """
    try:
        # 1. Resolve company dynamically
        company = _get_default_company()
        if not company:
            raise Exception("No company configured in the system")

        # 2. Pull company's default accounts + cost center
        company_doc = frappe.db.get_value(
            "Company", company,
            [
                "default_receivable_account",
                "default_cash_account",
                "default_bank_account",
                "cost_center",
                "default_currency",
            ],
            as_dict=True,
        )
        if not company_doc:
            raise Exception(f"Company '{company}' not found")

        paid_from   = company_doc.get("default_receivable_account")
        paid_to     = (
            company_doc.get("default_cash_account")
            or company_doc.get("default_bank_account")
        )
        currency    = company_doc.get("default_currency") or "INR"
        cost_center = company_doc.get("cost_center")

        if not paid_from:
            raise Exception(
                f"Company '{company}' has no default_receivable_account set. "
                f"Configure it at /app/company/{company}"
            )
        if not paid_to:
            raise Exception(
                f"Company '{company}' has no default_cash_account or default_bank_account set. "
                f"Configure it at /app/company/{company}"
            )

        # 3. Resolve Customer from Patient
        customer = _resolve_customer_from_patient(booking.patient)
        if not customer:
            raise Exception(
                f"No Customer linked to Patient '{booking.patient}'. "
                f"Set Customer field on the Patient record."
            )

        # 4. Create Payment Entry with all required fields
        pe = frappe.new_doc("Payment Entry")
        pe.company                       = company
        pe.posting_date                  = frappe.utils.nowdate()
        pe.payment_type                  = "Receive"
        pe.party_type                    = "Customer"
        pe.party                         = customer
        pe.paid_from                     = paid_from
        pe.paid_to                       = paid_to
        pe.paid_from_account_currency    = currency
        pe.paid_to_account_currency      = currency
        pe.paid_amount                   = amount
        pe.received_amount               = amount
        pe.source_exchange_rate          = 1
        pe.target_exchange_rate          = 1
        pe.mode_of_payment               = "Cash"
        if cost_center:
            pe.cost_center               = cost_center
        pe.reference_no                  = booking.payment_id
        pe.reference_date                = booking.payment_received_at
        pe.remarks = (
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
            pe.name,
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