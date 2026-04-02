import frappe
import json
from frappe.utils import now_datetime


@frappe.whitelist(allow_guest=True)
def receive_interakt_booking():
    """
    POST endpoint for Interakt webhook.
    Full URL to configure in Interakt dashboard:
    https://YOUR-SITE.frappe.cloud/api/method/taal_inventory.api.interakt_webhook.receive_interakt_booking
    """
    try:
        # --- Read incoming payload ---
        payload = frappe.local.form_dict
        if frappe.request.data:
            payload = json.loads(frappe.request.data)

        # --- TEMPORARY: Log full payload for debugging ---
        frappe.log_error(
            str(payload),
            "Interakt Payload Debug"
        )
        return {"status": "debug", "payload_received": str(payload)}

        # --- Deduplicate: if same record_id arrives twice, skip ---
        record_id = payload.get("record_id")
        if record_id:
            existing = frappe.db.get_value(
                "Interakt Booking",
                {"record_id": record_id},
                "name"
            )
            if existing:
                return {
                    "status": "duplicate",
                    "message": "Booking already exists",
                    "booking": existing
                }

        # --- Step 1: Find or create Patient ---
        patient_id = _get_or_create_patient(
            whatsapp_number=payload["whatsapp_number"],
            patient_name=payload["patient_name"],
            city_location=payload.get("city_location", "")
        )

        # --- Step 2: Create Interakt Booking record ---
        booking = frappe.new_doc("Interakt Booking")

        # Section A: Source Identity
        booking.record_id           = record_id or _generate_record_id()
        booking.booking_source      = "Interakt WhatsApp"
        booking.interakt_flow_name  = payload.get("interakt_flow_name", "")
        booking.lead_created_at     = payload.get("lead_created_at") or now_datetime()
        booking.chat_transcript_ref = payload.get("chat_transcript_ref", "")

        # Section B: Patient Details
        booking.patient             = patient_id
        booking.patient_name        = payload["patient_name"]
        booking.whatsapp_number     = payload["whatsapp_number"]
        booking.city_location       = payload.get("city_location", "")

        # Section C: Service Details
        booking.selected_service    = payload["selected_service"]
        booking.consultation_type   = payload["consultation_type"]
        booking.preferred_date      = payload.get("preferred_date")
        booking.preferred_time_slot = payload.get("preferred_time_slot", "")
        booking.consultation_reason = payload.get("consultation_reason", "")

        # Section D: Payment Details
        booking.payment_amount_inr  = payload.get("payment_amount_inr", 499)
        booking.payment_link_url    = payload.get("payment_link_url", "")
        booking.payment_status      = "Pending"
        booking.razorpay_order_id   = payload.get("razorpay_order_id", "")

        # Section E: Operations
        booking.booking_status      = "Pending Payment"

        booking.insert(ignore_permissions=True)
        frappe.db.commit()

        return {
            "status": "success",
            "booking": booking.name,
            "patient": patient_id
        }

    except Exception as e:
        frappe.log_error(
            frappe.get_traceback(),
            "Interakt Webhook Error"
        )
        return {
            "status": "error",
            "message": str(e)
        }


def _get_or_create_patient(whatsapp_number, patient_name, city_location=""):
    """
    Search for existing Patient by WhatsApp number.
    If not found, create a new Patient record.
    Returns the Patient document name.
    """
    # Search by WhatsApp number — primary deduplication key
    existing = frappe.db.get_value(
        "Patient",
        {"custom_whatsapp_number": whatsapp_number},
        "name"
    )
    if existing:
        return existing

    # Not found — create new Patient
    patient = frappe.new_doc("Patient")
    patient.first_name             = patient_name
    patient.custom_whatsapp_number = whatsapp_number
    patient.custom_city_location   = city_location
    patient.status                 = "Active"
    patient.insert(ignore_permissions=True)
    frappe.db.commit()

    return patient.name


def _generate_record_id():
    """
    Fallback Record ID generator if Interakt does not send one.
    Format: INT-YYYYMMDD-NNNNN
    """
    from datetime import datetime
    date_str = datetime.now().strftime("%Y%m%d")
    count = frappe.db.count("Interakt Booking") + 1
    return f"INT-{date_str}-{str(count).zfill(5)}"