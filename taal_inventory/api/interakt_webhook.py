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

    # ── Step 3: Extract form data from workflow steps ────────────
    steps = data.get("data", [])
    form_data = _extract_form_data(steps)

    # ── Step 4: STRICT FILTER ────────────────────────────────────
    # Only process events that have an actual form submission.
    # A valid booking MUST have preferred_date.
    preferred_date = form_data.get("preferred_date", "")
    if not preferred_date:
        return {
            "status": "ignored",
            "reason": "no form submission in this event"
        }

    # ── Step 5: Deduplication ────────────────────────────────────
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

    # ── Step 6: Build all field values ───────────────────────────
    city_location        = form_data.get("city_location", "")
    preferred_time_slot  = form_data.get("preferred_time_slot", "")
    consultation_reason  = form_data.get("consultation_reason", "")

    # ── Step 7: Get or create Patient ────────────────────────────
    patient_id = _get_or_create_patient(
        whatsapp_number=whatsapp_number,
        patient_name=patient_name,
        city_location=city_location
    )

    # ── Step 8: Create Interakt Booking ──────────────────────────
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
    Extracts all booking data from workflow steps.

    Two sources used together:
    1. InteractiveFlowReply — the WhatsApp Form submission
       (has date, city, reason, raw time option ID)
    2. Question message text — the Thank You message sent
       to patient has the resolved readable time slot value
       e.g. "Time:  4 PM - 6 PM"
    """
    result = {}

    for step in steps:
        question = step.get("question", {})
        answer   = step.get("answer", {})
        msg_type = answer.get("message_content_type", "")

        # ── Source 1: WhatsApp Form Submission ───────────────────
        if msg_type == "InteractiveFlowReply":
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
                        # Store raw — will be overridden by readable
                        # value extracted from message text below
                        result["preferred_time_slot_raw"] = \
                            str(value).strip()

                    elif key_lower.startswith("reason") or \
                         key_lower.startswith("consultation_reason"):
                        result["consultation_reason"] = \
                            str(value).strip()

            except Exception:
                frappe.log_error(
                    frappe.get_traceback(),
                    "Interakt: Form Parse Error"
                )

        # ── Source 2: Thank You Message (readable time) ──────────
        # The confirmation message Interakt sends to the patient
        # contains the resolved human-readable time slot.
        # e.g. "Date:  2026-08-27\nTime:  4 PM - 6 PM"
        # We extract the time value from here — this is permanent
        # and works for any time slot label changes in Interakt.
        q_message = question.get("message", "")
        if "Time:" in q_message and (
            "AM" in q_message or "PM" in q_message
        ):
            time_val = _extract_time_from_message(q_message)
            if time_val:
                result["preferred_time_slot"] = time_val

        a_message = str(answer.get("message", ""))
        if "Time:" in a_message and (
            "AM" in a_message or "PM" in a_message
        ):
            time_val = _extract_time_from_message(a_message)
            if time_val:
                result["preferred_time_slot"] = time_val

    # ── Final: resolve time slot ─────────────────────────────────
    # If we got a readable time from message text — use it
    # Otherwise fall back to raw option ID from form
    if not result.get("preferred_time_slot"):
        raw = result.pop("preferred_time_slot_raw", "")
        result["preferred_time_slot"] = raw
    else:
        result.pop("preferred_time_slot_raw", None)

    return result


def _extract_time_from_message(message_text):
    """
    Extracts the readable time slot from Thank You message text.

    Message format:
    "...Date:  2026-08-27\\nTime:  4 PM - 6 PM\\n..."

    Returns readable string e.g. "4 PM - 6 PM"
    Works permanently regardless of time slot label changes.
    """
    try:
        parts = message_text.split("Time:")
        if len(parts) < 2:
            return ""

        after_time = parts[1].strip()

        # Time value ends at newline
        first_line = after_time.split("\\n")[0].strip()
        if not first_line:
            first_line = after_time.split("\n")[0].strip()

        if "AM" in first_line or "PM" in first_line:
            return first_line

        return ""
    except Exception:
        return ""


def _get_or_create_patient(
    whatsapp_number, patient_name, city_location=""
):
    """
    Search for existing Patient by WhatsApp number first.
    Fall back to name search for pre-existing patients.
    Create new if not found anywhere.
    Gender set to Other — required by ERPNext Healthcare.
    """
    # Search 1: by WhatsApp number — primary key
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

    # Search 3: LIKE search as last resort
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