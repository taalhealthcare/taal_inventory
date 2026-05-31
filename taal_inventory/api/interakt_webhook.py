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

        # ════════════════════════════════════════════════════════════════
        # DIAGNOSTIC LOGGING (temporary — remove after issue is solved)
        # Logs every incoming webhook to Error Log so we can see
        # exactly what Interakt is sending.
        # ════════════════════════════════════════════════════════════════
        try:
            _log_incoming_for_debug(raw)
        except Exception:
            pass  # Never let logging break the actual flow

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


def _log_incoming_for_debug(raw):
    """
    DIAGNOSTIC ONLY — Logs every incoming Interakt webhook request to
    Error Log with the title "INTERAKT DEBUG: Incoming Webhook".

    Remove this function and its caller in receive_interakt_booking
    once the booking-creation issue is resolved.
    """
    if not raw:
        frappe.log_error(
            "Body: (empty)",
            "INTERAKT DEBUG: Incoming Webhook"
        )
        return

    try:
        payload = json.loads(raw)
    except Exception as parse_err:
        try:
            raw_preview = raw.decode("utf-8", errors="replace")[:1000]
        except Exception:
            raw_preview = str(raw)[:1000]
        frappe.log_error(
            f"JSON parse failed: {parse_err}\n\nRaw body preview:\n{raw_preview}",
            "INTERAKT DEBUG: Parse Failed"
        )
        return

    event_type      = payload.get("type", "?")
    data            = payload.get("data", {}) or {}
    customer_number = data.get("customer_number", "?")
    customer_name   = data.get("customer_name", "?")
    workflow_id     = data.get("id", "?")
    steps           = data.get("data", [])
    num_steps       = len(steps) if isinstance(steps, list) else 0

    # Detect form submission step
    has_form_submission = False
    if isinstance(steps, list):
        for step in steps:
            if isinstance(step, dict):
                answer = step.get("answer", {}) or {}
                if answer.get("message_content_type") == "InteractiveFlowReply":
                    has_form_submission = True
                    break

    try:
        raw_preview = raw.decode('utf-8', errors='replace')[:3000]
    except Exception:
        raw_preview = str(raw)[:3000]

    summary = (
        f"Event Type:           {event_type}\n"
        f"Customer Name:        {customer_name}\n"
        f"Customer Number:      {customer_number}\n"
        f"Workflow Instance ID: {workflow_id}\n"
        f"Number of Steps:      {num_steps}\n"
        f"Has Form Submission:  {has_form_submission}\n"
        f"\n"
        f"-- Raw payload (first 3000 chars) --\n"
        f"{raw_preview}"
    )

    frappe.log_error(summary, "INTERAKT DEBUG: Incoming Webhook")


def _handle_workflow_completion(payload):
    """
    Parses workflow_response_update event.
    Only processes events that contain a completed
    WhatsApp Form submission.
    """
    data = payload.get("data", {})

    raw_number = data.get("customer_number", "")
    whatsapp_number = raw_number.replace("+", "").strip()

    if not whatsapp_number:
        return {"status": "ignored", "reason": "no phone number"}

    patient_name = str(
        data.get("customer_name", "")
    ).strip()[:50]
    if not patient_name:
        return {"status": "ignored", "reason": "no customer name"}

    steps = data.get("data", [])
    form_data = _extract_form_data(steps)

    preferred_date = form_data.get("preferred_date", "")
    if not preferred_date:
        return {
            "status": "ignored",
            "reason": "no form submission in this event"
        }

    workflow_instance_id = data.get("id", "")
    record_id = f"IKT-{workflow_instance_id}" \
        if workflow_instance_id else _generate_record_id()

    if frappe.db.exists(
        "Interakt Booking", {"record_id": record_id}
    ):
        existing = frappe.db.get_value(
            "Interakt Booking",
            {"record_id": record_id},
            "name"
        )
        return {"status": "duplicate", "booking": existing}

    city_location       = form_data.get("city_location", "")
    preferred_time_slot = form_data.get("preferred_time_slot", "")
    consultation_reason = form_data.get("consultation_reason", "")
    speciality          = form_data.get("speciality", "") or "Other"

    patient_id = _get_or_create_patient(
        whatsapp_number=whatsapp_number,
        patient_name=patient_name,
        city_location=city_location
    )

    booking = frappe.new_doc("Interakt Booking")
    booking.record_id           = record_id
    booking.booking_source      = "Interakt WhatsApp"
    booking.interakt_flow_name  = "Doctor Consultation"
    booking.lead_created_at     = now_datetime()
    booking.patient             = patient_id
    booking.patient_name        = patient_name
    booking.whatsapp_number     = whatsapp_number
    booking.city_location       = city_location
    booking.selected_service    = "Doctor Consultation"
    booking.consultation_type   = "Doctor"
    booking.preferred_date      = preferred_date
    booking.preferred_time_slot = preferred_time_slot
    booking.consultation_reason = consultation_reason
    booking.speciality          = speciality
    booking.payment_amount_inr  = 499
    booking.payment_status      = "Pending"
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
            "consultation_reason": consultation_reason,
            "speciality": speciality
        }
    }


def _extract_form_data(steps):
    """Extracts all booking data from workflow steps."""
    result = {}

    for step in steps:
        question = step.get("question", {})
        answer   = step.get("answer", {})
        msg_type = answer.get("message_content_type", "")

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
                        result["patient_name"] = \
                            str(value).strip()

                    elif key_lower.startswith("city"):
                        result["city_location"] = \
                            str(value).strip()

                    elif key_lower.startswith("preferred_date"):
                        result["preferred_date"] = \
                            str(value).strip()

                    elif key_lower.startswith("preferred_time"):
                        result["preferred_time_slot_raw"] = \
                            str(value).strip()

                    elif key_lower.startswith("reason") or \
                         key_lower.startswith(
                             "consultation_reason"
                         ):
                        result["consultation_reason"] = \
                            str(value).strip()

                    elif key_lower.startswith("speciality"):
                        result["speciality"] = \
                            str(value).strip()

            except Exception:
                frappe.log_error(
                    frappe.get_traceback(),
                    "Interakt: Form Parse Error"
                )

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

    readable_from_message = result.get("preferred_time_slot", "")
    raw_option_id = result.pop("preferred_time_slot_raw", "")

    if readable_from_message and (
        "AM" in readable_from_message or
        "PM" in readable_from_message
    ):
        result["preferred_time_slot"] = readable_from_message
    else:
        result["preferred_time_slot"] = _resolve_time_slot(
            raw_option_id
        )

    return result


def _extract_time_from_message(message_text):
    """Extracts ONLY the readable time slot value from message text."""
    try:
        if "Time:" not in message_text:
            return ""

        parts = message_text.split("Time:")
        if len(parts) < 2:
            return ""

        after_time = parts[1]
        after_time = after_time.replace("\xa0", " ").strip()

        for separator in ["\n", "\\n", "\r\n", "\r"]:
            if separator in after_time:
                after_time = after_time.split(separator)[0]
                break

        after_time = after_time.replace("\xa0", " ").strip()
        after_time = after_time.rstrip(".,;:")

        if "AM" in after_time or "PM" in after_time:
            return after_time.strip()

        return ""
    except Exception:
        return ""


def _resolve_time_slot(raw_option_id):
    """Resolves Interakt option IDs to readable time labels."""
    slot_map = {
        "option_1773381068873_bbg7g961e": "4:00 PM - 6:00 PM",
        "option_1773381057829_vihn2mczm": "9:00 AM - 11:00 AM",
        "option_1773381018418_s16i4ur2a": "11:00 AM - 1:00 PM",
    }

    if not raw_option_id:
        return ""

    if "AM" in raw_option_id or "PM" in raw_option_id:
        return raw_option_id.strip()

    if raw_option_id in slot_map:
        return slot_map[raw_option_id]

    return raw_option_id


def _get_or_create_patient(
    whatsapp_number, patient_name, city_location=""
):
    """Patient lookup — strict matching only."""
    existing = frappe.db.get_value(
        "Patient",
        {"custom_whatsapp_number": whatsapp_number},
        "name"
    )
    if existing:
        return existing

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

    except frappe.UniqueValidationError as e:
        frappe.db.rollback()

        concurrent = frappe.db.get_value(
            "Patient",
            {"patient_name": patient_name},
            "name"
        )
        if concurrent:
            frappe.db.set_value(
                "Patient", concurrent,
                "custom_whatsapp_number",
                whatsapp_number
            )
            frappe.db.commit()
            return concurrent

        frappe.log_error(
            f"Patient creation failed.\n"
            f"Patient: {patient_name}\n"
            f"Error: {str(e)}",
            "Patient Creation Failed"
        )
        raise

    except Exception as e:
        frappe.db.rollback()
        frappe.log_error(
            f"Patient creation unexpected error: "
            f"{patient_name}\n{str(e)}",
            "Patient Creation Failed"
        )
        raise


def _generate_record_id():
    """Fallback Record ID if workflow has no instance ID."""
    from datetime import datetime
    date_str = datetime.now().strftime("%Y%m%d")
    count = frappe.db.count("Interakt Booking") + 1
    return f"INT-{date_str}-{str(count).zfill(5)}"