import frappe
import json
from frappe.utils import now_datetime


@frappe.whitelist(allow_guest=True)
def receive_interakt_booking():
    """
    Receives native Interakt Developer webhook events.
    Only processes workflow_response_update events
    that contain a completed WhatsApp Form submission.
    All other events are silently ignored.
    """
    try:
        raw = frappe.request.data
        if not raw:
            return {"status": "ignored", "reason": "empty body"}

        payload = json.loads(raw)
        event_type = payload.get("type", "")

        if event_type == "workflow_response_update":
            return _handle_workflow_completion(payload)
        else:
            return {"status": "ignored", "event_type": event_type}

    except Exception as e:
        frappe.log_error(
            frappe.get_traceback(),
            "Interakt Webhook Error"
        )
        return {"status": "error", "message": str(e)}


def _handle_workflow_completion(payload):
    """
    Parses workflow_response_update event.
    Only processes events that contain a completed form submission.
    """
    data = payload.get("data", {})

    # ── Step 1: Extract phone number ─────────────────────────────
    raw_number = data.get("customer_number", "")
    whatsapp_number = raw_number.replace("+", "").strip()

    if not whatsapp_number:
        return {"status": "ignored", "reason": "no phone number"}

    # ── Step 2: Extract customer name ────────────────────────────
    patient_name = str(data.get("customer_name", "")).strip()[:50]
    if not patient_name:
        return {"status": "ignored", "reason": "no customer name"}

    # ── Step 3: Read ALL user traits ─────────────────────────────
    # Interakt stores form responses as user traits
    # after form submission — this is the most reliable source
    traits = data.get("customer", {}).get("traits", {})

    # ── Step 4: Extract form data from workflow steps ────────────
    steps = data.get("data", [])
    form_data = _extract_form_data(steps)

    # ── Step 5: STRICT FILTER ────────────────────────────────────
    # Only process if we have a form submission
    # A valid booking MUST have at least a preferred_date
    # from either form_data or traits
    preferred_date = (
        form_data.get("preferred_date")
        or traits.get("consultation_date", "")
    )

    if not preferred_date:
        return {
            "status": "ignored",
            "reason": "no form submission in this event"
        }

    # ── Step 6: Deduplication ─────────────────────────────────────
    # Use workflow instance ID as unique key
    workflow_instance_id = data.get("id", "")
    record_id = f"IKT-{workflow_instance_id}" \
        if workflow_instance_id else _generate_record_id()

    if frappe.db.exists("Interakt Booking", {"record_id": record_id}):
        existing = frappe.db.get_value(
            "Interakt Booking",
            {"record_id": record_id},
            "name"
        )
        return {"status": "duplicate", "booking": existing}

    # ── Step 7: Build all field values ───────────────────────────
    # For each field: use form_data first, fall back to traits

    city_location = (
        form_data.get("city_location")
        or traits.get("patient_city", "")
    )

    preferred_time_slot = _resolve_time_slot(
        form_data.get("preferred_time_slot", ""),
        traits.get("consultation_time", "")
    )

    consultation_reason = (
        form_data.get("consultation_reason")
        or traits.get("consultation_reason", "")
    )

    # ── Step 8: Get or create Patient ────────────────────────────
    patient_id = _get_or_create_patient(
        whatsapp_number=whatsapp_number,
        patient_name=patient_name,
        city_location=city_location
    )

    # ── Step 9: Create Interakt Booking ──────────────────────────
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
    booking.city_location       = city_location

    # Section C: Service Details
    booking.selected_service    = "Doctor Consultation"
    booking.consultation_type   = "Doctor"
    booking.preferred_date      = preferred_date
    booking.preferred_time_slot = preferred_time_slot
    booking.consultation_reason = consultation_reason

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
        "patient": patient_id,
        "data": {
            "preferred_date": preferred_date,
            "preferred_time_slot": preferred_time_slot,
            "city_location": city_location,
            "consultation_reason": consultation_reason
        }
    }


def _extract_form_data(steps):
    """
    Loops through workflow steps looking for
    InteractiveFlowReply — the WhatsApp Form response.
    Parses the nested JSON and maps fields by key prefix.
    """
    result = {}

    for step in steps:
        answer = step.get("answer", {})
        msg_type = answer.get("message_content_type", "")

        if msg_type != "InteractiveFlowReply":
            continue

        try:
            msg_str = answer.get("message", "{}")
            msg = json.loads(msg_str)
            response_json_str = (
                msg.get("nfm_reply", {})
                   .get("response_json", "{}")
            )
            form_fields = json.loads(response_json_str)

            for key, value in form_fields.items():
                key_lower = key.lower()

                if key_lower.startswith("full_name") or \
                   key_lower.startswith("patient_name"):
                    result["patient_name"] = str(value).strip()

                elif key_lower.startswith("city"):
                    result["city_location"] = str(value).strip()

                elif key_lower.startswith("preferred_date"):
                    result["preferred_date"] = str(value).strip()

                elif key_lower.startswith("preferred_time"):
                    result["preferred_time_slot"] = str(value).strip()

                elif key_lower.startswith("reason") or \
                     key_lower.startswith("consultation_reason"):
                    result["consultation_reason"] = str(value).strip()

        except Exception:
            frappe.log_error(
                frappe.get_traceback(),
                "Interakt: Form Parse Error"
            )

    return result


def _resolve_time_slot(form_value, trait_value):
    """
    Permanent solution — no hardcoded option ID mappings.

    Priority:
    1. trait_value — always the human readable label
       e.g. '4 PM - 6 PM' or '9:00 AM - 11:00 AM'
    2. form_value — only if it contains AM/PM (readable)
    3. Raw form_value — stored as-is if nothing better
    """
    # Trait value is always readable — use it first
    if trait_value and (
        "AM" in trait_value or "PM" in trait_value
    ):
        return trait_value.strip()

    # Form value is readable — use it
    if form_value and (
        "AM" in form_value or "PM" in form_value
    ):
        return form_value.strip()

    # Return whatever we have
    return (trait_value or form_value or "").strip()


def _get_or_create_patient(
    whatsapp_number, patient_name, city_location=""
):
    """
    Search for existing Patient by WhatsApp number.
    Fall back to name search for pre-existing patients.
    Create new if not found anywhere.
    """
    # Search 1: by WhatsApp number
    existing = frappe.db.get_value(
        "Patient",
        {"custom_whatsapp_number": whatsapp_number},
        "name"
    )
    if existing:
        return existing

    # Search 2: by patient_name field
    existing = frappe.db.get_value(
        "Patient",
        {"patient_name": patient_name},
        "name"
    )
    if existing:
        frappe.db.set_value(
            "Patient", existing,
            {
                "custom_whatsapp_number": whatsapp_number,
                "custom_city_location": city_location
            }
        )
        frappe.db.commit()
        return existing

    # Search 3: LIKE search
    existing = frappe.db.get_value(
        "Patient",
        {"patient_name": ("like", f"%{patient_name}%")},
        "name"
    )
    if existing:
        frappe.db.set_value(
            "Patient", existing,
            {
                "custom_whatsapp_number": whatsapp_number,
                "custom_city_location": city_location
            }
        )
        frappe.db.commit()
        return existing

    # Not found — create new Patient
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

    except Exception as e:
        frappe.db.rollback()
        fallback = frappe.db.get_value(
            "Patient",
            {"patient_name": ("like", f"%{patient_name}%")},
            "name"
        )
        if fallback:
            frappe.db.set_value(
                "Patient", fallback,
                "custom_whatsapp_number", whatsapp_number
            )
            frappe.db.commit()
            return fallback
        frappe.log_error(
            f"Patient create failed: {patient_name}\n{str(e)}",
            "Patient Creation Failed"
        )
        raise


def _generate_record_id():
    """Fallback Record ID if workflow has no instance ID."""
    from datetime import datetime
    date_str = datetime.now().strftime("%Y%m%d")
    count = frappe.db.count("Interakt Booking") + 1
    return f"INT-{date_str}-{str(count).zfill(5)}"