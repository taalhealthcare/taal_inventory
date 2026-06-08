import frappe
import json
from frappe.utils import now_datetime


# ──────────────────────────────────────────────────────────────────────────────
# Speciality option ID → label mapping
# Fill these in as you discover the IDs by testing each speciality option
# and checking the "Interakt: Unknown Speciality Option" error log.
# ──────────────────────────────────────────────────────────────────────────────
SPECIALITY_OPTION_MAP = {
    # All 7 speciality option IDs mapped (form revision: 2026-06-01)
    "option_1746003238318_a35cczxk9": "Mental Health",
    "option_1746003263847_vikrtx2f5": "HIV Care (ART)",
    "option_1746003269445_7edlabgtb": "PrEP & PEP Support",
    "option_1779967711791_x7emnvtxp": "STI/STD Care",
    "option_1779967725446_9liljnhsf": "Mens Health",
    "option_1779967727051_mfasrm9pb": "Womens Health",
    "option_1779967728316_y1cwf2g05": "LGBTQ+ Health",
    # NOTE: "Other" option ID not yet captured — unknown option IDs default to "Other" anyway,
    # so this is functionally complete. Add the Other option ID here when discovered.
}

VALID_SPECIALITIES = {
    "Mental Health",
    "HIV Care (ART)",
    "PrEP & PEP Support",
    "STI/STD Care",
    "Mens Health",
    "Womens Health",
    "LGBTQ+ Health",
    "Other",
}


@frappe.whitelist(allow_guest=True)
def receive_interakt_booking():
    """Receives native Interakt Developer webhook events."""
    try:
        raw = frappe.request.data

        # Diagnostic logging — remove after issue is fully resolved
        try:
            _log_incoming_for_debug(raw)
        except Exception:
            pass

        if not raw:
            return {"status": "ignored", "reason": "empty body"}

        payload = json.loads(raw)
        event_type = payload.get("type", "")

        if event_type == "workflow_response_update":
            return _handle_workflow_completion(payload)
        elif event_type == "message_received":
            return _handle_message_received(payload)
        else:
            return {"status": "ignored", "event_type": event_type}

    except Exception as e:
        frappe.log_error(
            frappe.get_traceback(),
            "Interakt Webhook Error"
        )
        return {"status": "error", "message": str(e)}


def _handle_message_received(payload):
    """
    Handles `message_received` events. Specifically: when a patient submits the
    WhatsApp Form (InteractiveFlowReply), Interakt updates customer.traits with
    RESOLVED values (e.g. consultation_time = "12:00 PM - 12:30 PM" instead of
    the raw option_id).

    Two purposes:
      1. BACKFILL — if a booking already exists for this phone with a raw
         option_id time slot, update it with the resolved label.
      2. CACHE — store the resolved traits keyed by phone so a SUBSEQUENT
         workflow_response_update event (which arrives milliseconds later
         and creates the booking) can pick up the resolved value even if
         the booking didn't exist at this moment.

    This solves the timing/ordering issue where message_received arrives
    BEFORE workflow_response_update creates the booking.

    Idempotent — only updates the booking if the current value still looks
    like a raw option ID.
    """
    data = payload.get("data", {}) or {}
    message = data.get("message", {}) or {}

    # Only process WhatsApp Form submissions (not text, buttons, etc.)
    if message.get("message_content_type") != "InteractiveFlowReply":
        return {"status": "ignored", "reason": "not a flow reply"}

    customer = data.get("customer", {}) or {}
    phone = (customer.get("channel_phone_number") or "").lstrip("+").strip()
    traits = customer.get("traits", {}) or {}

    if not phone:
        return {"status": "ignored", "reason": "no phone in event"}

    # Extract resolved values from traits
    resolved_time = (traits.get("consultation_time") or "").strip()
    resolved_date = (traits.get("consultation_date") or "").strip()

    # ── CACHE the resolved traits keyed by phone (5-minute TTL) ──
    # This bridges the gap between this event and the subsequent
    # workflow_response_update event that creates the booking.
    if resolved_time and not resolved_time.startswith("option_"):
        cache_payload = {
            "consultation_time": resolved_time,
            "consultation_date": resolved_date,
        }
        try:
            frappe.cache().set_value(
                f"interakt_traits:{phone}",
                json.dumps(cache_payload),
                expires_in_sec=300,  # 5 minutes
            )
        except Exception:
            pass  # Cache failure is non-fatal

    # ── BACKFILL an existing booking, if one exists ──
    from frappe.utils import add_to_date
    window_start = add_to_date(now_datetime(), minutes=-10)

    bookings = frappe.get_all(
        "Interakt Booking",
        filters={
            "whatsapp_number": phone,
            "creation": [">", window_start],
            "preferred_time_slot": ["like", "option_%"],
        },
        fields=["name", "preferred_time_slot"],
        order_by="creation desc",
        limit=1,
    )

    if not bookings:
        return {"status": "cached_for_pending_booking", "phone": phone}

    booking = bookings[0]

    if not resolved_time or resolved_time.startswith("option_"):
        return {"status": "no_resolved_time_in_traits", "booking": booking["name"]}

    frappe.db.set_value(
        "Interakt Booking",
        booking["name"],
        "preferred_time_slot",
        resolved_time,
    )
    frappe.db.commit()

    return {
        "status": "time_slot_resolved",
        "booking": booking["name"],
        "old_value": booking["preferred_time_slot"],
        "new_value": resolved_time,
    }


def _log_incoming_for_debug(raw):
    """DIAGNOSTIC ONLY — logs every webhook for visibility."""
    if not raw:
        frappe.log_error("Body: (empty)", "INTERAKT DEBUG: Incoming Webhook")
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
    Only processes events that contain a completed WhatsApp Form submission.
    """
    data = payload.get("data", {})

    # ── Step 1: Extract phone number ─────────────────────────────
    raw_number = data.get("customer_number", "")
    whatsapp_number = raw_number.replace("+", "").strip()

    if not whatsapp_number:
        return {"status": "ignored", "reason": "no phone number"}

    # ── Step 2: Extract form data from workflow steps ─────────────
    steps = data.get("data", [])
    form_data = _extract_form_data(steps)

    # ── Step 3: STRICT FILTER — must have preferred_date ─────────
    preferred_date = form_data.get("preferred_date", "")
    if not preferred_date:
        return {
            "status": "ignored",
            "reason": "no form submission in this event"
        }

    # ── Step 4: Determine patient name ───────────────────────────
    # Prefer the name from the form (more accurate); fall back to
    # the WhatsApp profile name from Interakt.
    patient_name_from_form = form_data.get("patient_name", "").strip()[:50]
    customer_name_from_event = str(data.get("customer_name", "")).strip()[:50]
    patient_name = patient_name_from_form or customer_name_from_event

    if not patient_name:
        return {"status": "ignored", "reason": "no customer name"}

    # ── Step 5: Deduplication ─────────────────────────────────────
    workflow_instance_id = data.get("id", "")
    record_id = f"IKT-{workflow_instance_id}" \
        if workflow_instance_id else _generate_record_id()

    if frappe.db.exists("Interakt Booking", {"record_id": record_id}):
        existing = frappe.db.get_value(
            "Interakt Booking", {"record_id": record_id}, "name"
        )
        return {"status": "duplicate", "booking": existing}

    # ── Step 6: Read other fields ─────────────────────────────────
    city_location       = form_data.get("city_location", "")
    preferred_time_slot = form_data.get("preferred_time_slot", "")
    consultation_reason = form_data.get("consultation_reason", "")
    speciality          = form_data.get("speciality", "") or "Other"

    # ── Step 6a: Resolve raw option_id from cache (set by message_received) ──
    # When the form is submitted, Interakt fires `message_received` first
    # (with InteractiveFlowReply and resolved customer.traits) and THEN
    # fires `workflow_response_update` (which we're handling now). The
    # message_received handler cached the resolved label for us — pick it up.
    if preferred_time_slot.startswith("option_"):
        try:
            cached_raw = frappe.cache().get_value(f"interakt_traits:{whatsapp_number}")
            if cached_raw:
                cached = json.loads(cached_raw)
                cached_time = (cached.get("consultation_time") or "").strip()
                if cached_time and not cached_time.startswith("option_"):
                    preferred_time_slot = cached_time
                    frappe.log_error(
                        f"Resolved raw option_id from cache for phone {whatsapp_number}: "
                        f"option → '{cached_time}'",
                        "Interakt: Time Slot Cache Hit"
                    )
        except Exception:
            pass  # Cache miss is non-fatal — we'll fall through with raw value

    # ── Step 7: Get or create Patient ────────────────────────────
    patient_id = _get_or_create_patient(
        whatsapp_number=whatsapp_number,
        patient_name=patient_name,
        city_location=city_location
    )

    # ── Step 8: Create Interakt Booking ──────────────────────────
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
    """
    Extracts booking data from workflow steps.

    Uses FLEXIBLE substring matching to handle Interakt's form field
    names which may contain random suffixes (e.g. "your_name_ySmgDZuQAUtPL").
    Also handles values that come as lists (e.g. speciality multi-select).
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
                    msg.get("nfm_reply", {}).get("response_json", "{}")
                )
                form_fields = json.loads(response_json_str)

                for key, raw_value in form_fields.items():
                    key_lower = key.lower()

                    # Normalize list values → take first element
                    if isinstance(raw_value, list):
                        value = raw_value[0] if raw_value else ""
                    else:
                        value = raw_value

                    value_str = str(value).strip()
                    if not value_str:
                        continue

                    # Skip Interakt's internal flow token
                    if "flow_token" in key_lower:
                        continue

                    # ── PATIENT NAME ──
                    if (
                        "full_name" in key_lower
                        or "patient_name" in key_lower
                        or "your_name" in key_lower
                    ):
                        result["patient_name"] = value_str

                    # ── CITY / LOCATION ──
                    elif (
                        "city" in key_lower
                        or "location" in key_lower
                    ):
                        result["city_location"] = value_str

                    # ── DATE (check before TIME — both can have "appointment") ──
                    elif (
                        "appointment_date" in key_lower
                        or "preferred_date" in key_lower
                        or "consult_date" in key_lower
                        or "consultation_date" in key_lower
                    ):
                        result["preferred_date"] = value_str

                    # ── TIME ──
                    elif (
                        "appointment_time" in key_lower
                        or "preferred_time" in key_lower
                        or "consult_time" in key_lower
                        or "consultation_time" in key_lower
                    ):
                        result["preferred_time_slot_raw"] = value_str

                    # ── SPECIALITY ──
                    elif (
                        "specialty" in key_lower
                        or "speciality" in key_lower
                    ):
                        result["speciality_raw"] = value_str

                    # ── CONSULTATION REASON ──
                    elif (
                        "consultation_reason" in key_lower
                        or key_lower.startswith("reason")
                    ):
                        result["consultation_reason"] = value_str

                    # ── EMAIL ──
                    elif "email" in key_lower:
                        result["patient_email"] = value_str

                    # ── ALTERNATE PHONE (not WhatsApp number) ──
                    elif (
                        "phone_number" in key_lower
                        or "contact_number" in key_lower
                    ) and "whatsapp" not in key_lower:
                        result["patient_phone"] = value_str

            except Exception:
                frappe.log_error(
                    frappe.get_traceback(),
                    "Interakt: Form Parse Error"
                )

        # ── Source 2: Thank You Message text (readable time) ─────
        q_message = question.get("message", "")
        if "Time:" in q_message and ("AM" in q_message or "PM" in q_message):
            time_val = _extract_time_from_message(q_message)
            if time_val:
                result["preferred_time_slot"] = time_val

        a_message = str(answer.get("message", ""))
        if "Time:" in a_message and ("AM" in a_message or "PM" in a_message):
            time_val = _extract_time_from_message(a_message)
            if time_val:
                result["preferred_time_slot"] = time_val

    # ── Resolve time slot ─────────────────────────────────────────
    readable_from_message = result.get("preferred_time_slot", "")
    raw_option_id = result.pop("preferred_time_slot_raw", "")

    if readable_from_message and (
        "AM" in readable_from_message or "PM" in readable_from_message
    ):
        result["preferred_time_slot"] = readable_from_message
    else:
        result["preferred_time_slot"] = _resolve_time_slot(raw_option_id)

    # ── Resolve speciality ────────────────────────────────────────
    speciality_raw = result.pop("speciality_raw", "")
    result["speciality"] = _resolve_speciality(speciality_raw)

    return result


def _resolve_speciality(raw_value):
    """
    Resolves the speciality value to one of the 8 valid labels.

    Handles three cases:
    1. Direct label (e.g. "Mental Health") → returned as-is
    2. Option ID (e.g. "option_1746003238318_a35cczxk9") → mapped via
       SPECIALITY_OPTION_MAP (top of file)
    3. Unknown → defaults to "Other" and logs the raw value so you
       can add it to the mapping
    """
    if not raw_value:
        return "Other"

    raw_stripped = str(raw_value).strip()

    if raw_stripped in VALID_SPECIALITIES:
        return raw_stripped

    if raw_stripped in SPECIALITY_OPTION_MAP:
        resolved = SPECIALITY_OPTION_MAP[raw_stripped]
        if resolved in VALID_SPECIALITIES:
            return resolved

    if raw_stripped.startswith("option_"):
        frappe.log_error(
            f"Unknown speciality option ID received: {raw_stripped}\n"
            f"Add this to SPECIALITY_OPTION_MAP in interakt_webhook.py "
            f"to enable proper routing.",
            "Interakt: Unknown Speciality Option"
        )

    return "Other"


def _extract_time_from_message(message_text):
    """Extracts the readable time slot from Thank You message text."""
    try:
        if "Time:" not in message_text:
            return ""
        parts = message_text.split("Time:")
        if len(parts) < 2:
            return ""
        after_time = parts[1].replace("\xa0", " ").strip()
        for separator in ["\n", "\\n", "\r\n", "\r"]:
            if separator in after_time:
                after_time = after_time.split(separator)[0]
                break
        after_time = after_time.replace("\xa0", " ").strip().rstrip(".,;:")
        if "AM" in after_time or "PM" in after_time:
            return after_time.strip()
        return ""
    except Exception:
        return ""


def _resolve_time_slot(raw_option_id):
    """Resolves Interakt time slot option IDs to readable labels."""
    slot_map = {
        # Older form options
        "option_1773381068873_bbg7g961e": "4:00 PM - 6:00 PM",
        "option_1773381057829_vihn2mczm": "9:00 AM - 11:00 AM",
        "option_1773381018418_s16i4ur2a": "11:00 AM - 1:00 PM",
        # New form options (build out as we discover them)
        "option_1779967868409_virlndpus": "03:00 PM - 03:30 PM",
        # "option_1779967870405_05y9nt205": "<verify and add>",
    }
    if not raw_option_id:
        return ""
    if "AM" in raw_option_id or "PM" in raw_option_id:
        return raw_option_id.strip()
    if raw_option_id in slot_map:
        return slot_map[raw_option_id]
    return raw_option_id


def _get_or_create_patient(whatsapp_number, patient_name, city_location=""):
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
            f"Patient: {patient_name}\nError: {str(e)}",
            "Patient Creation Failed"
        )
        raise

    except Exception as e:
        frappe.db.rollback()
        frappe.log_error(
            f"Patient creation unexpected error: {patient_name}\n{str(e)}",
            "Patient Creation Failed"
        )
        raise


def _generate_record_id():
    """Fallback Record ID if workflow has no instance ID."""
    from datetime import datetime
    date_str = datetime.now().strftime("%Y%m%d")
    count = frappe.db.count("Interakt Booking") + 1
    return f"INT-{date_str}-{str(count).zfill(5)}"