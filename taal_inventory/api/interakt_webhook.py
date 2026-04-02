import frappe
import json
from frappe.utils import now_datetime


@frappe.whitelist(allow_guest=True)
def receive_interakt_booking():
    """
    Receives native Interakt Developer webhook events.
    Only processes workflow_response_update events.
    All other event types are silently ignored.

    URL configured in Interakt Developer Settings:
    https://demoerp.m.frappe.cloud/api/method/
    taal_inventory.api.interakt_webhook.receive_interakt_booking
    """
    try:
        raw = frappe.request.data
        if not raw:
            return {"status": "ignored", "reason": "empty body"}

        payload = json.loads(raw)
        event_type = payload.get("type", "")

        # Only act on workflow completion events
        if event_type == "workflow_response_update":
            return _handle_workflow_completion(payload)
        else:
            # Silently ignore all other Interakt event types
            return {"status": "ignored", "event_type": event_type}

    except Exception as e:
        frappe.log_error(
            frappe.get_traceback(),
            "Interakt Webhook Error"
        )
        return {"status": "error", "message": str(e)}


def _handle_workflow_completion(payload):
    """
    Parses workflow_response_update event from Interakt.
    Extracts patient data and creates Interakt Booking + Patient.
    """
    data = payload.get("data", {})

    # --- Extract phone number ---
    # Interakt sends as '+918604449446' — remove + sign
    raw_number = data.get("customer_number", "")
    whatsapp_number = raw_number.replace("+", "").strip()
    patient_name = data.get("customer_name", "")

    if not whatsapp_number:
        frappe.log_error(
            "No customer_number found in workflow_response_update payload",
            "Interakt: Missing Phone Number"
        )
        return {"status": "error", "reason": "no phone number"}

    # --- Extract form fields from workflow steps ---
    form_data = _extract_form_data(data.get("data", []))

    # --- Deduplicate using workflow instance ID ---
    workflow_instance_id = data.get("id", "")
    record_id = f"IKT-{workflow_instance_id}" if workflow_instance_id \
        else _generate_record_id()

    if frappe.db.exists("Interakt Booking", {"record_id": record_id}):
        existing = frappe.db.get_value(
            "Interakt Booking",
            {"record_id": record_id},
            "name"
        )
        return {"status": "duplicate", "booking": existing}

    # --- Get or create Patient ---
    patient_id = _get_or_create_patient(
        whatsapp_number=whatsapp_number,
        patient_name=patient_name,
        city_location=form_data.get("city_location", "")
    )

    # --- Create Interakt Booking ---
    booking = frappe.new_doc("Interakt Booking")

    # Section A: Source Identity
    booking.record_id           = record_id
    booking.booking_source      = "Interakt WhatsApp"
    booking.interakt_flow_name  = "Doctor Consultation"
    booking.lead_created_at     = now_datetime()

    # Section B: Patient Details
    booking.patient             = patient_id
    booking.patient_name        = patient_name
    booking.whatsapp_number     = whatsapp_number
    booking.city_location       = form_data.get("city_location", "")

    # Section C: Service Details
    booking.selected_service    = "Doctor Consultation"
    booking.consultation_type   = "Doctor"
    booking.preferred_date      = form_data.get("preferred_date", "")
    booking.preferred_time_slot = form_data.get("preferred_time_slot", "")
    booking.consultation_reason = form_data.get("consultation_reason", "")

    # Section D: Payment
    booking.payment_amount_inr  = 499
    booking.payment_status      = "Pending"

    # Section E: Operations
    booking.booking_status      = "Pending Payment"

    booking.insert(ignore_permissions=True)
    frappe.db.commit()

    return {
        "status": "success",
        "booking": booking.name,
        "patient": patient_id
    }


def _extract_form_data(steps):
    """
    Loops through workflow steps and finds the WhatsApp Form response.
    The form response is in the step with message_content_type
    = InteractiveFlowReply.
    Parses the response_json and maps fields by key prefix.
    """
    result = {}

    for step in steps:
        answer = step.get("answer", {})
        msg_type = answer.get("message_content_type", "")

        if msg_type == "InteractiveFlowReply":
            try:
                # The message is a JSON string containing nfm_reply
                msg = json.loads(answer.get("message", "{}"))
                response_json_str = msg.get(
                    "nfm_reply", {}
                ).get("response_json", "{}")
                form_fields = json.loads(response_json_str)

                # Keys have format: field_label_RANDOMID
                # Match by prefix to extract values reliably
                for key, value in form_fields.items():
                    key_lower = key.lower()

                    if key_lower.startswith("full_name") or \
                       key_lower.startswith("patient_name"):
                        result["patient_name"] = value

                    elif key_lower.startswith("city"):
                        result["city_location"] = value

                    elif key_lower.startswith("preferred_date"):
                        result["preferred_date"] = value

                    elif key_lower.startswith("preferred_time_slot"):
                        # Convert option ID to readable label if needed
                        result["preferred_time_slot"] = \
                            _resolve_time_slot(value)

                    elif key_lower.startswith("reason"):
                        result["consultation_reason"] = value

            except Exception:
                frappe.log_error(
                    frappe.get_traceback(),
                    "Interakt: Form Data Parse Error"
                )

    return result


def _resolve_time_slot(option_value):
    """
    Interakt sends time slot as an option ID like:
    option_1773381068873_bbg7g961e
    Map known option IDs to readable labels.
    If unknown, return as-is.
    """
    slot_map = {
        "option_1": "9:00 AM - 11:00 AM",
        "option_2": "11:00 AM - 1:00 PM",
        "option_3": "12:00 PM - 2:00 PM",
        "option_4": "2:00 PM - 4:00 PM",
        "option_5": "4:00 PM - 6:00 PM",
    }
    # Try exact match first
    if option_value in slot_map:
        return slot_map[option_value]
    # If it already looks like a readable time, return as-is
    if "AM" in option_value or "PM" in option_value:
        return option_value
    # Return raw value — we will map it properly after first test
    return option_value


def _get_or_create_patient(whatsapp_number, patient_name, city_location=""):
    """
    Search for existing Patient by WhatsApp number first.
    If not found, search by name as fallback.
    If still not found, create new Patient.
    Updates WhatsApp number on existing patient if missing.
    """
    # Search 1: by WhatsApp number — primary key
    existing = frappe.db.get_value(
        "Patient",
        {"custom_whatsapp_number": whatsapp_number},
        "name"
    )
    if existing:
        return existing

    # Search 2: by name — catches patients created before
    # integration or from previous test runs
    existing_by_name = frappe.db.get_value(
        "Patient",
        {"first_name": patient_name},
        "name"
    )
    if existing_by_name:
        # Update their WhatsApp number so next lookup works
        frappe.db.set_value(
            "Patient",
            existing_by_name,
            {
                "custom_whatsapp_number": whatsapp_number,
                "custom_city_location": city_location
            }
        )
        frappe.db.commit()
        return existing_by_name

    # Not found anywhere — create new Patient
    try:
        patient = frappe.new_doc("Patient")
        patient.first_name             = patient_name
        patient.sex                    = "Other"
        patient.custom_whatsapp_number = whatsapp_number
        patient.custom_city_location   = city_location
        patient.status                 = "Active"
        patient.insert(ignore_permissions=True)
        frappe.db.commit()
        return patient.name

    except frappe.UniqueValidationError:
        # Race condition — patient was just created by another
        # request. Fetch and return it.
        fallback = frappe.db.get_value(
            "Patient",
            {"first_name": patient_name},
            "name"
        )
        if fallback:
            frappe.db.set_value(
                "Patient",
                fallback,
                "custom_whatsapp_number",
                whatsapp_number
            )
            frappe.db.commit()
            return fallback
        raise


def _generate_record_id():
    """Fallback Record ID if workflow has no instance ID."""
    from datetime import datetime
    date_str = datetime.now().strftime("%Y%m%d")
    count = frappe.db.count("Interakt Booking") + 1
    return f"INT-{date_str}-{str(count).zfill(5)}"