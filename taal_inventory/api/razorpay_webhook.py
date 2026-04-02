import frappe
import json
import hmac
import hashlib
from frappe.utils import now_datetime


# ──────────────────────────────────────────────────────────────────────────────
# IMPORTANT: Replace this with your actual Razorpay Webhook Secret
# You will get this from Razorpay Dashboard → Settings → Webhooks
# We will do this in Step 10 — leave it as is for now
# ──────────────────────────────────────────────────────────────────────────────
RAZORPAY_WEBHOOK_SECRET = "REPLACE_THIS_LATER"


@frappe.whitelist(allow_guest=True)
def handle_razorpay_payment():
    """
    POST endpoint for Razorpay payment webhook.
    Full URL to configure in Razorpay dashboard:
    https://YOUR-SITE.frappe.cloud/api/method/taal_inventory.api.razorpay_webhook.handle_razorpay_payment
    """
    try:
        raw_body = frappe.request.data
        signature = frappe.request.headers.get(
            "X-Razorpay-Signature", ""
        )

        # --- Verify the request genuinely came from Razorpay ---
        if RAZORPAY_WEBHOOK_SECRET != "REPLACE_THIS_LATER":
            if not _verify_razorpay_signature(raw_body, signature):
                frappe.throw(
                    "Invalid Razorpay signature",
                    frappe.AuthenticationError
                )

        event = json.loads(raw_body)
        event_type = event.get("event", "")

        # --- Route to correct handler based on event type ---
        if event_type == "payment_link.paid":
            _handle_payment_success(event)

        elif event_type in ("payment.failed", "payment_link.cancelled"):
            _handle_payment_failed(event)

        else:
            # Log unknown events for visibility — do nothing else
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
    """
    Verify that the webhook request genuinely came from Razorpay.
    Uses HMAC-SHA256 with the webhook secret.
    """
    expected = hmac.new(
        RAZORPAY_WEBHOOK_SECRET.encode("utf-8"),
        raw_body,
        hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


def _handle_payment_success(event):
    """
    Called when Razorpay fires payment_link.paid event.
    Updates Interakt Booking, creates Patient Appointment
    and Payment Entry automatically.
    """
    try:
        # Extract Razorpay IDs from event payload
        payment_link_entity = event["payload"]["payment_link"]["entity"]
        payment_entity      = event["payload"]["payment"]["entity"]

        rzp_order_id = payment_link_entity.get("id", "")
        payment_id   = payment_entity.get("id", "")
        amount_paise = payment_entity.get("amount", 0)
        amount_inr   = amount_paise / 100

        # --- Find the matching Interakt Booking ---
        booking_name = frappe.db.get_value(
            "Interakt Booking",
            {"razorpay_order_id": rzp_order_id},
            "name"
        )

        if not booking_name:
            frappe.log_error(
                f"No Interakt Booking found for Razorpay Order ID: {rzp_order_id}",
                "Razorpay Webhook: Booking Not Found"
            )
            return

        # --- Update payment fields on Interakt Booking ---
        booking = frappe.get_doc("Interakt Booking", booking_name)
        booking.payment_status      = "Paid"
        booking.payment_id          = payment_id
        booking.payment_received_at = now_datetime()
        booking.booking_status      = "Payment Confirmed"
        booking.save(ignore_permissions=True)
        frappe.db.commit()

        # --- Auto-create Patient Appointment ---
        appointment_name = _create_patient_appointment(booking)

        # --- Auto-create Payment Entry ---
        _create_payment_entry(booking, amount_inr)

        frappe.db.commit()

    except Exception as e:
        frappe.log_error(
            frappe.get_traceback(),
            "Razorpay Payment Success Handler Error"
        )


def _handle_payment_failed(event):
    """
    Called when Razorpay fires payment.failed
    or payment_link.cancelled event.
    Marks the Interakt Booking as Cancelled.
    """
    try:
        payment_link_entity = event["payload"]["payment_link"]["entity"]
        rzp_order_id = payment_link_entity.get("id", "")

        booking_name = frappe.db.get_value(
            "Interakt Booking",
            {"razorpay_order_id": rzp_order_id},
            "name"
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

    except Exception as e:
        frappe.log_error(
            frappe.get_traceback(),
            "Razorpay Payment Failed Handler Error"
        )


def _create_patient_appointment(booking):
    """
    Creates a Patient Appointment record in ERPNext Healthcare module
    automatically after payment is confirmed.
    Links it back to the Interakt Booking.
    """
    try:
        appt = frappe.new_doc("Patient Appointment")
        appt.patient                        = booking.patient
        appt.appointment_type               = booking.selected_service
        appt.appointment_date               = booking.preferred_date
        appt.notes                          = booking.consultation_reason
        appt.status                         = "Open"
        appt.custom_booking_source          = "Interakt WhatsApp"
        appt.custom_interakt_booking_id     = booking.name
        appt.custom_interakt_flow_name      = booking.interakt_flow_name
        appt.insert(ignore_permissions=True)

        # Link appointment back to Interakt Booking
        frappe.db.set_value(
            "Interakt Booking",
            booking.name,
            {
                "patient_appointment": appt.name,
                "booking_status": "Appointment Scheduled"
            }
        )

        return appt.name

    except Exception as e:
        frappe.log_error(
            frappe.get_traceback(),
            "Auto Create Patient Appointment Error"
        )


def _create_payment_entry(booking, amount):
    """
    Creates a Payment Entry record in ERPNext Accounts module
    for reconciliation and billing records.
    """
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

        # Link Payment Entry back to Interakt Booking
        frappe.db.set_value(
            "Interakt Booking",
            booking.name,
            "payment_entry",
            pe.name
        )

        return pe.name

    except Exception as e:
        frappe.log_error(
            frappe.get_traceback(),
            "Auto Create Payment Entry Error"
        )