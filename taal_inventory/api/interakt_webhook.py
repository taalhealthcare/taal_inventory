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
    Only processes events that contain a completed
    WhatsApp Form submission.
    """
    data = payload.get("data", {})

    # ── Step 1: Extract phone number ─────────────────────────────
    raw_number = data.get("customer_number", "")
    whatsapp_number = raw_number.replace("+", "").strip()

    if not whatsapp_number:
        return {"status": "ignored", "reason": "no phone number"}

    # ── Step 2: Extract customer name ────────────────────────────
    patient_name = str(
        data.get("customer_name", "")
    ).strip()[:50]
    if not patient_name:
        return {"status": "ignored", "reason": "no customer name"}

    # ── Step 3: Extract form data from workflow steps ─────────────
    steps = data.get("data", [])
    form_data = _extract_form_data(steps)

    # ── Step 4: STRICT FILTER ─────────────────────────────────────
    # Only process events that have an actual form submission.
    # A valid booking MUST have preferred_date.
    preferred_date = form_data.get("preferred_date", "")
    if not preferred_date:
        return {
            "status": "ignored",
            "reason": "no form submission in this event"
        }

    # ── Step 5: Deduplication ─────────────────────────────────────
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

    # ── Step 6: Build all field values ────────────────────────────
    city_location       = form_data.get("city_location", "")
    preferred_time_slot = form_data.get("preferred_time_slot", "")
    consultation_reason = form_data.get("consultation_reason", "")

    # Speciality: defaults to "Other" until the Interakt form
    # has the speciality dropdown wired up. "Other" means no
    # doctor assigned at payment time → no doctor WhatsApp →
    # patient still gets Meet link → admin still notified.
    speciality          = form_data.get("speciality", "") or "Other"

    # ── Step 7: Get or create Patient ────────────────────────────
    # WhatsApp number is the ONLY reliable unique key.
    # Name matching is exact only — no LIKE search ever.
    # UID is auto-assigned by Server Script on before_insert
    # using ORDER BY uid DESC — so no collision possible.
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
    booking.speciality          = speciality

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
            "consultation_reason": consultation_reason,
            "speciality": speciality
        }
    }


def _extract_form_data(steps):
    """
    Extracts all booking data from workflow steps.

    Two sources used together:
    1. InteractiveFlowReply — WhatsApp Form submission
       contains date, city, reason, raw time option ID, speciality
    2. Question message text — Thank You message sent to
       patient has the resolved readable time slot label
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
                        result["patient_name"] = \
                            str(value).strip()

                    elif key_lower.startswith("city"):
                        result["city_location"] = \
                            str(value).strip()

                    elif key_lower.startswith("preferred_date"):
                        result["preferred_date"] = \
                            str(value).strip()

                    elif key_lower.startswith("preferred_time"):
                        # Store raw — overridden below if
                        # readable value found in message text
                        result["preferred_time_slot_raw"] = \
                            str(value).strip()

                    elif key_lower.startswith("reason") or \
                         key_lower.startswith(
                             "consultation_reason"
                         ):
                        result["consultation_reason"] = \
                            str(value).strip()

                    elif key_lower.startswith("speciality"):
                        # New: captures the speciality dropdown
                        # value when the Interakt form has it.
                        # When absent, _handle_workflow_completion
                        # defaults to "Other".
                        result["speciality"] = \
                            str(value).strip()

            except Exception:
                frappe.log_error(
                    frappe.get_traceback(),
                    "Interakt: Form Parse Error"
                )

        # ── Source 2: Thank You Message (readable time) ──────────
        # The confirmation message sent to patient contains
        # the resolved human-readable time slot label.
        # e.g. "Date:  2026-08-27\nTime:  4 PM - 6 PM"
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

    # ── Final: resolve time slot ──────────────────────────────────
    # Priority:
    # 1. Readable time extracted from message text — best source
    # 2. Known option ID mapping — reliable fallback
    # 3. Raw option ID — last resort, always stored
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
    """
    Extracts ONLY the readable time slot value from message text.
    Handles non-breaking spaces and all newline formats.

    Input:  "Time:  4 PM - 6 PM \n\nConsultation Fee..."
    Output: "4 PM - 6 PM"
    """
    try:
        if "Time:" not in message_text:
            return ""

        parts = message_text.split("Time:")
        if len(parts) < 2:
            return ""

        after_time = parts[1]
        after_time = after_time.replace("\xa0", " ").strip()

        # Split on every possible newline — take first chunk only
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
    """
    Resolves Interakt option IDs to readable time labels.

    These option IDs are stable for your specific Interakt form.
    They only change if you delete and recreate the dropdown
    field inside Interakt WhatsApp Forms builder.

    To add a new mapping:
    1. Run a test booking selecting the unknown time slot
    2. Check Interakt Booking record for the raw option ID
    3. Add it to slot_map below with its readable label
    """
    slot_map = {
        # TAAL+ Doctor Consultation form — known mappings
        "option_1773381068873_bbg7g961e": "4:00 PM - 6:00 PM",
        "option_1773381057829_vihn2mczm": "9:00 AM - 11:00 AM",
        "option_1773381018418_s16i4ur2a": "11:00 AM - 1:00 PM",
    }

    if not raw_option_id:
        return ""

    # Already readable
    if "AM" in raw_option_id or "PM" in raw_option_id:
        return raw_option_id.strip()

    # Known mapping
    if raw_option_id in slot_map:
        return slot_map[raw_option_id]

    # Unknown — store raw so we can add mapping later
    return raw_option_id


def _get_or_create_patient(
    whatsapp_number, patient_name, city_location=""
):
    """
    Patient lookup — strict matching only.

    LIKE search permanently removed — caused wrong patient
    linking (Ram matched Ramesh Patil).

    UID is auto-assigned by Server Script on before_insert.
    Server Script uses ORDER BY uid DESC so always picks
    the highest existing UID and adds 1 — no collision possible.

    Search order:
    1. WhatsApp number — exact match, primary key
    2. patient_name — exact match only
    3. Create new Patient
    """
    # Search 1: by WhatsApp number — primary unique key
    existing = frappe.db.get_value(
        "Patient",
        {"custom_whatsapp_number": whatsapp_number},
        "name"
    )
    if existing:
        return existing

    # Search 2: exact patient_name match only
    # NEVER use LIKE — matches similar names incorrectly
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

    # Search 3: Create new Patient
    # UID auto-assigned by Server Script on before_insert
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

        # Check if concurrent request just created this patient
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