"""
Razorpay Webhook Handler — DEFENSIVE VERSION

Handles ALL Razorpay payment success events (payment_link.paid,
payment.captured, order.paid) with flexible payload extraction. Same payment
firing multiple events is deduplicated via strong idempotency checks.

Deploy location: taal_inventory/api/razorpay_webhook.py
Webhook URL: https://<site>/api/method/taal_inventory.api.razorpay_webhook.handle_razorpay_payment
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
# Razorpay Webhook Secret (from Razorpay Dashboard → Settings → Webhooks)
# ──────────────────────────────────────────────────────────────────────────────
RAZORPAY_WEBHOOK_SECRET = "3a_JN39BAkMXYub"


# ──────────────────────────────────────────────────────────────────────────────
# Event classification — Razorpay may fire MULTIPLE events for one payment.
# All success events route to the same handler; idempotency prevents dupes.
# ──────────────────────────────────────────────────────────────────────────────
SUCCESS_EVENTS = (
    "payment_link.paid",   # Payment link flow
    "payment.captured",    # Standard checkout / any successful capture
    "order.paid",          # Order-based flow
)

FAILURE_EVENTS = (
    "payment.failed",
    "payment_link.cancelled",
    "payment_link.expired",
    "order.cancelled",
)


# ──────────────────────────────────────────────────────────────────────────────
# Flexible payload extraction — handles varying Razorpay event structures
# ──────────────────────────────────────────────────────────────────────────────

def _extract_payment_info(event):
    """
    Extract payment info from ANY Razorpay event, regardless of structure.

    Different event types have different payload shapes:
      • payment_link.paid: payload = {payment_link: {entity: ...}, payment: {entity: ...}}
      • payment.captured:  payload = {payment: {entity: ...}}
      • order.paid:        payload = {order: {entity: ...}, payment: {entity: ...}}

    Returns dict with all extracted fields, or None if event is malformed.
    Safe against missing keys throughout.
    """
    if not isinstance(event, dict):
        return None

    payload = event.get("payload") or {}
    if not isinstance(payload, dict):
        return None

    payment_link_entity = ((payload.get("payment_link") or {}).get("entity")) or {}
    payment_entity      = ((payload.get("payment") or {}).get("entity")) or {}
    order_entity        = ((payload.get("order") or {}).get("entity")) or {}

    # payment_id — unique per payment attempt
    payment_id = payment_entity.get("id", "") or ""

    # order_id — try multiple sources in priority order
    order_id = (
        payment_link_entity.get("id") or
        payment_entity.get("order_id") or
        order_entity.get("id") or
        ""
    )

    # Contact phone — try multiple sources
    contact = (
        (payment_link_entity.get("customer") or {}).get("contact") or
        payment_entity.get("contact") or
        (payment_entity.get("notes") or {}).get("contact") or
        (order_entity.get("notes") or {}).get("contact") or
        ""
    )

    # Email — secondary matching key
    email = (
        (payment_link_entity.get("customer") or {}).get("email") or
        payment_entity.get("email") or
        ""
    )

    # Amount in paise
    amount_paise = payment_entity.get("amount", 0) or 0

    return {
        "event_type":    event.get("event", ""),
        "payment_id":    payment_id,
        "order_id":      order_id,
        "contact":       contact,
        "email":         email,
        "amount_paise":  amount_paise,
        "amount_inr":    (amount_paise / 100) if amount_paise else 0,
    }


# ──────────────────────────────────────────────────────────────────────────────
# Entry point — routes ALL success events through the same handler
# ──────────────────────────────────────────────────────────────────────────────

@frappe.whitelist(allow_guest=True)
def handle_razorpay_payment():
    """POST endpoint for Razorpay webhooks."""
    event_type = "(unknown)"
    raw_body = None
    try:
        raw_body = frappe.request.data
        signature = frappe.request.headers.get("X-Razorpay-Signature", "")

        if RAZORPAY_WEBHOOK_SECRET != "REPLACE_THIS_LATER":
            if not _verify_razorpay_signature(raw_body, signature):
                frappe.throw("Invalid Razorpay signature", frappe.AuthenticationError)

        event = json.loads(raw_body)
        event_type = event.get("event", "")

        if event_type in SUCCESS_EVENTS:
            _handle_payment_success(event)
        elif event_type in FAILURE_EVENTS:
            _handle_payment_failed(event)
        else:
            frappe.log_error(
                f"Unhandled Razorpay event: {event_type}\n\n"
                f"Payload:\n{json.dumps(event, indent=2, default=str)[:3000]}",
                "Razorpay Webhook: Unknown Event"
            )

        return {"status": "ok"}

    except Exception as e:
        try:
            body_snippet = raw_body.decode("utf-8", errors="replace")[:3000] if raw_body else "(no body)"
        except Exception:
            body_snippet = "(could not decode body)"

        frappe.log_error(
            f"Event type: {event_type}\n\n"
            f"Raw body:\n{body_snippet}\n\n"
            f"Traceback:\n{frappe.get_traceback()}",
            "Razorpay Webhook Error"
        )
        return {"status": "error", "message": str(e)}


# ──────────────────────────────────────────────────────────────────────────────
# Test / Recovery utilities — WHITELISTED
# ──────────────────────────────────────────────────────────────────────────────

@frappe.whitelist()
def simulate_post_payment(booking_name=None, real_payment_id=None, real_order_id=None):
    """Trigger the post-payment flow manually. DELETE BEFORE PRODUCTION GO-LIVE."""
    if not booking_name:
        return {"error": "booking_name parameter required"}

    if not frappe.db.exists("Interakt Booking", booking_name):
        return {"error": f"Booking {booking_name} not found"}

    booking = frappe.get_doc("Interakt Booking", booking_name)

    if booking.payment_status == "Paid":
        return {
            "error": "Booking already Paid",
            "booking": booking_name,
            "hint": "Use retry_post_payment_for_paid_booking to re-run Meet/notifications.",
        }

    order_id = real_order_id or ("plink_MANUAL_TEST_" + booking_name)
    payment_id = real_payment_id or ("pay_MANUAL_TEST_" + booking_name)

    fake_event = {
        "event": "payment_link.paid",
        "payload": {
            "payment_link": {
                "entity": {
                    "id": order_id,
                    "customer": {"contact": "+" + (booking.whatsapp_number or "")},
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

    _handle_payment_success(fake_event)
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


@frappe.whitelist()
def retry_post_payment_for_paid_booking(booking_name=None, real_payment_id=None, real_order_id=None):
    """
    Retry Meet + notifications for a Paid booking that partially failed.
    USE BEFORE consultation happens. After consultation, use retry_silent_recovery.
    """
    if not booking_name:
        return {"error": "booking_name parameter required"}

    if not frappe.db.exists("Interakt Booking", booking_name):
        return {"error": f"Booking {booking_name} not found"}

    booking = frappe.get_doc("Interakt Booking", booking_name)

    updated_ids = []
    if real_payment_id and booking.payment_id and "MANUAL_TEST" in (booking.payment_id or ""):
        frappe.db.set_value("Interakt Booking", booking_name, "payment_id", real_payment_id)
        updated_ids.append("payment_id")
    if real_order_id and booking.razorpay_order_id and "MANUAL_TEST" in (booking.razorpay_order_id or ""):
        frappe.db.set_value("Interakt Booking", booking_name, "razorpay_order_id", real_order_id)
        updated_ids.append("razorpay_order_id")
    if updated_ids:
        frappe.db.commit()

    booking.reload()

    _resolve_raw_time_slot_if_needed(booking)
    booking.reload()

    doctor_details = None
    if booking.assigned_team_member:
        doctor_details = doctor_assignment.get_doctor_details(booking.assigned_team_member)

    speciality = (getattr(booking, "speciality", None) or "Other").strip()

    step_results = {
        "ids_updated": updated_ids,
        "meet": "skipped",
        "patient_whatsapp": "skipped",
        "doctor_whatsapp": "skipped",
        "admin": "skipped",
        "errors": [],
    }

    try:
        booking.meet_creation_status = "Pending"
        booking.save(ignore_permissions=True)
        frappe.db.commit()

        meet_result = google_meet_creator.create_meet_for_booking(booking)

        if meet_result.get("success"):
            booking.google_meet_link         = meet_result.get("meet_link", "")
            booking.google_calendar_event_id = meet_result.get("event_id", "")
            booking.meet_creation_status     = "Created"
            booking.meet_creation_error      = ""
            booking.save(ignore_permissions=True)
            frappe.db.commit()
            booking.reload()
            step_results["meet"] = "success"
        else:
            booking.meet_creation_status = "Failed"
            booking.meet_creation_error  = (meet_result.get("error") or "Unknown")[:500]
            booking.save(ignore_permissions=True)
            frappe.db.commit()
            step_results["meet"] = "failed: " + (meet_result.get("error") or "Unknown")[:200]
    except Exception as e:
        step_results["meet"] = f"exception: {e}"
        step_results["errors"].append(f"Meet: {e}")
        frappe.log_error(frappe.get_traceback(), "Retry Meet Creation Error")

    try:
        _send_patient_whatsapp(booking, doctor_details, speciality)
        booking.reload()
        step_results["patient_whatsapp"] = "sent" if booking.patient_message_sent else "failed (see error log)"
    except Exception as e:
        step_results["patient_whatsapp"] = f"exception: {e}"
        step_results["errors"].append(f"Patient WhatsApp: {e}")
        frappe.log_error(frappe.get_traceback(), "Retry Patient WhatsApp Error")

    try:
        settings_doc = frappe.get_single("Booking API Settings")
        if (
            doctor_details
            and doctor_details.get("exists")
            and getattr(settings_doc, "send_doctor_notification", 1)
        ):
            _send_doctor_whatsapp(booking, doctor_details, speciality)
            booking.reload()
            step_results["doctor_whatsapp"] = "sent" if booking.doctor_message_sent else "failed (see error log)"
        else:
            step_results["doctor_whatsapp"] = "skipped (no doctor or disabled)"
    except Exception as e:
        step_results["doctor_whatsapp"] = f"exception: {e}"
        step_results["errors"].append(f"Doctor WhatsApp: {e}")
        frappe.log_error(frappe.get_traceback(), "Retry Doctor WhatsApp Error")

    try:
        doctor_display = (
            doctor_details["display_name"]
            if (doctor_details and doctor_details.get("exists"))
            else None
        )
        admin_result = admin_notifier.notify_admin(booking, doctor_display)

        if admin_result.get("whatsapp_sent") or admin_result.get("email_sent"):
            frappe.db.set_value("Interakt Booking", booking_name, "admin_message_sent", 1)
            frappe.db.commit()
            step_results["admin"] = (
                f"whatsapp={admin_result.get('whatsapp_sent')} "
                f"email={admin_result.get('email_sent')}"
            )
        else:
            step_results["admin"] = "no channel succeeded"

        if admin_result.get("errors"):
            step_results["errors"].extend(admin_result["errors"])
    except Exception as e:
        step_results["admin"] = f"exception: {e}"
        step_results["errors"].append(f"Admin: {e}")
        frappe.log_error(frappe.get_traceback(), "Retry Admin Notification Error")

    booking.reload()

    def safe(field, default="(none)"):
        return getattr(booking, field, None) or default

    return {
        "booking_name":             booking_name,
        "step_results":             step_results,
        "payment_status":           safe("payment_status"),
        "razorpay_order_id":        safe("razorpay_order_id"),
        "payment_id":               safe("payment_id"),
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


@frappe.whitelist()
def retry_silent_recovery(booking_name=None, real_payment_id=None, real_order_id=None):
    """
    SILENT RECOVERY — for bookings where consultation has ALREADY happened.

    Restores ERPNext records (Payment Entry, Patient Appointment, doctor
    assignment) WITHOUT sending any WhatsApp/email to patient/doctor/admin.

    Use this when a patient paid but our webhook missed it, AND their
    consultation time has already passed.
    """
    if not booking_name:
        return {"error": "booking_name parameter required"}

    if not frappe.db.exists("Interakt Booking", booking_name):
        return {"error": f"Booking {booking_name} not found"}

    booking = frappe.get_doc("Interakt Booking", booking_name)

    updates = {}
    if booking.payment_status != "Paid":
        updates["payment_status"] = "Paid"
        updates["booking_status"] = "Payment Confirmed"
    if real_payment_id and not booking.payment_id:
        updates["payment_id"] = real_payment_id
    if real_order_id and not booking.razorpay_order_id:
        updates["razorpay_order_id"] = real_order_id
    if not booking.payment_received_at:
        updates["payment_received_at"] = now_datetime()
    if not booking.payment_amount_inr:
        updates["payment_amount_inr"] = 499

    if updates:
        frappe.db.set_value("Interakt Booking", booking_name, updates)
        frappe.db.commit()
        booking.reload()

    _resolve_raw_time_slot_if_needed(booking)
    booking.reload()

    results = {"ids_updated": list(updates.keys()), "errors": []}

    try:
        if not booking.assigned_team_member:
            speciality = (getattr(booking, "speciality", None) or "Other").strip()
            doctor_name = doctor_assignment.resolve_doctor(
                speciality,
                booking.preferred_date,
                booking.preferred_time_slot
            )
            if doctor_name:
                frappe.db.set_value("Interakt Booking", booking_name, "assigned_team_member", doctor_name)
                frappe.db.commit()
                booking.reload()
                results["doctor_assigned"] = doctor_name
            else:
                results["doctor_assigned"] = "(none — fallback)"
        else:
            results["doctor_assigned"] = "(already assigned)"
    except Exception as e:
        results["errors"].append(f"Doctor assignment: {e}")

    try:
        if not booking.patient_appointment:
            _create_patient_appointment(booking)
            booking.reload()
            results["patient_appointment"] = booking.patient_appointment or "(failed)"
        else:
            results["patient_appointment"] = "(already exists)"
    except Exception as e:
        results["errors"].append(f"Patient Appointment: {e}")

    try:
        if not booking.payment_entry:
            amount = float(booking.payment_amount_inr or 499)
            _create_payment_entry(booking, amount)
            booking.reload()
            results["payment_entry"] = booking.payment_entry or "(failed)"
        else:
            results["payment_entry"] = "(already exists)"
    except Exception as e:
        results["errors"].append(f"Payment Entry: {e}")

    results["booking_state"] = {
        "payment_status":       booking.payment_status,
        "booking_status":       booking.booking_status,
        "payment_id":           booking.payment_id,
        "razorpay_order_id":    booking.razorpay_order_id,
        "assigned_doctor":      booking.assigned_team_member,
        "patient_appointment":  booking.patient_appointment,
        "payment_entry":        booking.payment_entry,
    }
    results["note"] = "SILENT RECOVERY — no WhatsApp/email sent to patient/doctor/admin"

    return results


@frappe.whitelist()
def create_missing_payment_entry(booking_name=None):
    """Create a missing Payment Entry for a Paid booking."""
    if not booking_name:
        return {"error": "booking_name parameter required"}

    if not frappe.db.exists("Interakt Booking", booking_name):
        return {"error": f"Booking {booking_name} not found"}

    booking = frappe.get_doc("Interakt Booking", booking_name)

    if booking.payment_entry:
        return {
            "skipped": True,
            "reason": "Payment Entry already exists",
            "payment_entry": booking.payment_entry,
        }

    if booking.payment_status != "Paid":
        return {
            "error": f"Booking is not Paid (status: {booking.payment_status}). "
                     f"Cannot create Payment Entry for non-paid bookings."
        }

    amount = float(booking.payment_amount_inr or 499)
    _create_payment_entry(booking, amount)

    booking.reload()
    if booking.payment_entry:
        return {
            "success": True,
            "payment_entry": booking.payment_entry,
            "amount": amount,
        }
    else:
        return {
            "error": "Payment Entry creation failed — check Error Log for details"
        }


@frappe.whitelist()
def health_check_deployment():
    """Verify deployment has expected fixes."""
    import inspect

    results = {}

    try:
        src = inspect.getsource(_create_payment_entry)
        results["payment_entry_has_set_user"]      = "frappe.set_user" in src
        results["payment_entry_has_original_user"] = "original_user" in src
        results["payment_entry_has_finally_block"] = "finally:" in src
    except Exception as e:
        results["payment_entry_check_error"] = str(e)

    results["razorpay_secret_correct"] = (RAZORPAY_WEBHOOK_SECRET == "3a_JN39BAkMXYub")

    try:
        from taal_inventory.api import interakt_webhook as iw
        iw_src = inspect.getsource(iw)
        results["interakt_cache_write_present"] = "interakt_traits:" in iw_src
        results["interakt_cache_read_present"]  = "cached_raw" in iw_src
    except Exception as e:
        results["interakt_check_error"] = str(e)

    results["retry_function_exists"]     = ("retry_post_payment_for_paid_booking" in globals())
    results["silent_recovery_exists"]    = ("retry_silent_recovery" in globals())
    results["flexible_extraction_exists"] = ("_extract_payment_info" in globals())
    results["multi_event_support"]       = len(SUCCESS_EVENTS) >= 3

    all_passed = all(v is True for v in results.values() if isinstance(v, bool))
    results["ALL_PASSED"] = all_passed

    return results


@frappe.whitelist()
def list_learned_time_slots():
    """Diagnostic — show discovered time slot mappings."""
    result = {
        "static_map": dict(KNOWN_TIME_SLOT_OPTION_IDS),
        "auto_learned": {},
        "all_option_ids_seen_in_bookings": [],
    }

    all_bookings = frappe.get_all(
        "Interakt Booking",
        fields=["name", "preferred_time_slot"],
        limit=500,
    )

    seen_option_ids = set()
    for b in all_bookings:
        slot = b.get("preferred_time_slot") or ""
        if slot.startswith("option_"):
            seen_option_ids.add(slot)

    result["all_option_ids_seen_in_bookings"] = sorted(seen_option_ids)

    for option_id in sorted(seen_option_ids):
        try:
            learned = frappe.cache().get_value(f"time_slot_resolved:{option_id}")
            if learned:
                result["auto_learned"][option_id] = learned
        except Exception:
            pass

    result["coverage_summary"] = {
        "in_static_map":       len(result["static_map"]),
        "auto_learned":        len(result["auto_learned"]),
        "raw_in_bookings":     len(seen_option_ids),
        "unresolved":          [
            oid for oid in seen_option_ids
            if oid not in result["static_map"] and oid not in result["auto_learned"]
        ],
    }

    return result


def _verify_razorpay_signature(raw_body, signature):
    """HMAC-SHA256 verification."""
    expected = hmac.new(
        RAZORPAY_WEBHOOK_SECRET.encode("utf-8"),
        raw_body,
        hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


# ──────────────────────────────────────────────────────────────────────────────
# Time slot resolver
# ──────────────────────────────────────────────────────────────────────────────

KNOWN_TIME_SLOT_OPTION_IDS = {
    "option_1779967864173_lppvz7yvs": "12:30 PM - 01:00 PM",
    "option_1779967866725_8g7jao7ie": "02:00 PM - 02:30 PM",
    "option_1779967868993_mvtiazkdb": "03:30 PM - 04:00 PM",
    "option_1779968083706_4z0zpowea": "05:30 PM - 06:00 PM",
}


def _normalize_time_slot_label(time_str):
    """Fix weird spacing like '05: 30 PM' → '05:30 PM'."""
    if not time_str:
        return time_str
    cleaned = re.sub(r":\s+", ":", time_str)
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip()


def _resolve_raw_time_slot_if_needed(booking):
    """Resolve raw option_XXX time slots using cache → static map → log warning."""
    current_slot = booking.preferred_time_slot or ""

    if current_slot and not current_slot.startswith("option_"):
        normalized = _normalize_time_slot_label(current_slot)
        if normalized != current_slot:
            frappe.db.set_value(
                "Interakt Booking", booking.name,
                "preferred_time_slot", normalized
            )
            frappe.db.commit()
            booking.reload()
        return

    if not current_slot.startswith("option_"):
        return

    phone = booking.whatsapp_number or ""
    resolved = None
    source = None

    try:
        cached_raw = frappe.cache().get_value(f"interakt_traits:{phone}")
        if cached_raw:
            cached = json.loads(cached_raw)
            cached_time = (cached.get("consultation_time") or "").strip()
            if cached_time and not cached_time.startswith("option_"):
                resolved = _normalize_time_slot_label(cached_time)
                source = "cache_by_phone"
    except Exception:
        pass

    if not resolved:
        try:
            learned = frappe.cache().get_value(f"time_slot_resolved:{current_slot}")
            if learned:
                resolved = _normalize_time_slot_label(learned)
                source = "auto_learned"
        except Exception:
            pass

    if not resolved and current_slot in KNOWN_TIME_SLOT_OPTION_IDS:
        resolved = KNOWN_TIME_SLOT_OPTION_IDS[current_slot]
        source = "static_map"

    if resolved:
        frappe.db.set_value(
            "Interakt Booking", booking.name,
            "preferred_time_slot", resolved
        )
        frappe.db.commit()
        booking.reload()
        frappe.log_error(
            f"Resolved raw time slot: '{current_slot}' → '{resolved}' (via {source}) "
            f"for booking {booking.name}, phone {phone}",
            "Time Slot Resolved"
        )
    else:
        frappe.log_error(
            f"COULD NOT RESOLVE raw time slot '{current_slot}' for booking {booking.name}, "
            f"phone {phone}.",
            "Time Slot Resolution FAILED"
        )


# ──────────────────────────────────────────────────────────────────────────────
# Main Payment Success Handler — flexible extraction + strong idempotency
# ──────────────────────────────────────────────────────────────────────────────

def _handle_payment_success(event):
    """Handles any successful payment event with flexible extraction."""
    try:
        info = _extract_payment_info(event)
        if not info:
            frappe.log_error(
                f"Could not extract payment info from event.\n"
                f"Raw event:\n{json.dumps(event, indent=2, default=str)[:3000]}",
                "Razorpay: Extraction Failed"
            )
            return

        payment_id   = info["payment_id"]
        order_id     = info["order_id"]
        contact_raw  = info["contact"]
        amount_inr   = info["amount_inr"] or 499
        event_type   = info["event_type"]

        customer_phone = _normalize_phone(contact_raw)

        # ── IDEMPOTENCY: skip if already processed under this payment_id or order_id ──
        if payment_id:
            already = frappe.db.get_value(
                "Interakt Booking",
                {"payment_id": payment_id, "payment_status": "Paid"},
                "name"
            )
            if already:
                return

        if order_id:
            already = frappe.db.get_value(
                "Interakt Booking",
                {"razorpay_order_id": order_id, "payment_status": "Paid"},
                "name"
            )
            if already:
                return

        # ── Find matching booking via multiple strategies ──
        booking_name = _find_matching_booking(payment_id, order_id, customer_phone)

        if not booking_name:
            frappe.log_error(
                f"No pending booking match. event={event_type}, payment_id={payment_id}, "
                f"order_id={order_id}, contact={contact_raw} (normalized: {customer_phone})",
                "Razorpay Webhook: Booking Not Found"
            )
            return

        booking = frappe.get_doc("Interakt Booking", booking_name)

        # Resolve raw time slot BEFORE saving payment fields
        _resolve_raw_time_slot_if_needed(booking)

        # Update payment fields
        booking.razorpay_order_id   = order_id or booking.razorpay_order_id
        booking.payment_status      = "Paid"
        booking.payment_id          = payment_id or booking.payment_id
        booking.payment_received_at = now_datetime()
        booking.booking_status      = "Payment Confirmed"
        if not booking.payment_amount_inr:
            booking.payment_amount_inr = amount_inr
        booking.save(ignore_permissions=True)
        frappe.db.commit()

        # Resolve doctor
        speciality = (booking.speciality or "Other").strip()
        doctor_details = None

        try:
            doctor_name = doctor_assignment.resolve_doctor(
                speciality,
                booking.preferred_date,
                booking.preferred_time_slot
            )
            if doctor_name:
                booking.assigned_team_member = doctor_name
                booking.save(ignore_permissions=True)
                frappe.db.commit()
                doctor_details = doctor_assignment.get_doctor_details(doctor_name)
        except Exception:
            frappe.log_error(frappe.get_traceback(), "Doctor Assignment Error")

        # Patient Appointment
        try:
            _create_patient_appointment(booking)
        except Exception:
            frappe.log_error(frappe.get_traceback(), "Patient Appointment Creation Error")

        # Payment Entry
        try:
            _create_payment_entry(booking, amount_inr)
        except Exception:
            frappe.log_error(frappe.get_traceback(), "Payment Entry Creation Error")

        booking.reload()

        # Google Meet
        try:
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
            booking.reload()
        except Exception:
            frappe.log_error(frappe.get_traceback(), "Google Meet Creation Error")

        # Patient WhatsApp
        try:
            _send_patient_whatsapp(booking, doctor_details, speciality)
        except Exception:
            frappe.log_error(frappe.get_traceback(), "Patient WhatsApp Error")

        # Doctor WhatsApp
        try:
            settings = frappe.get_single("Booking API Settings")
            if (
                doctor_details
                and doctor_details.get("exists")
                and getattr(settings, "send_doctor_notification", 1)
            ):
                _send_doctor_whatsapp(booking, doctor_details, speciality)
        except Exception:
            frappe.log_error(frappe.get_traceback(), "Doctor WhatsApp Error")

        # Admin
        try:
            doctor_display = (
                doctor_details["display_name"]
                if (doctor_details and doctor_details.get("exists"))
                else None
            )
            admin_result = admin_notifier.notify_admin(booking, doctor_display)

            if admin_result.get("whatsapp_sent") or admin_result.get("email_sent"):
                frappe.db.set_value(
                    "Interakt Booking", booking.name, "admin_message_sent", 1
                )
                frappe.db.commit()

            if admin_result.get("errors"):
                frappe.log_error(
                    "\n".join(admin_result["errors"]),
                    f"Admin notification errors: {booking.name}"
                )
        except Exception:
            frappe.log_error(frappe.get_traceback(), "Admin Notification Error")

    except Exception:
        # Log full event on any unhandled exception for post-mortem
        try:
            evt_snippet = json.dumps(event, indent=2, default=str)[:3000]
        except Exception:
            evt_snippet = str(event)[:3000]

        frappe.log_error(
            f"Traceback:\n{frappe.get_traceback()}\n\nEvent:\n{evt_snippet}",
            "Razorpay Payment Success Handler Error"
        )


def _find_matching_booking(payment_id, order_id, phone):
    """Multi-strategy booking lookup."""
    if payment_id:
        matched = frappe.db.get_value(
            "Interakt Booking",
            {"payment_id": payment_id},
            "name"
        )
        if matched:
            return matched

    if order_id:
        matched = frappe.db.get_value(
            "Interakt Booking",
            {"razorpay_order_id": order_id},
            "name"
        )
        if matched:
            return matched

    if phone:
        matched = frappe.db.get_value(
            "Interakt Booking",
            filters={
                "whatsapp_number": phone,
                "payment_status": "Pending",
                "booking_status": "Pending Payment"
            },
            fieldname="name",
            order_by="creation DESC"
        )
        if matched:
            return matched

    return None


# ──────────────────────────────────────────────────────────────────────────────
# Patient / Doctor WhatsApp senders
# ──────────────────────────────────────────────────────────────────────────────

def _send_patient_whatsapp(booking, doctor_details, speciality):
    """Send patient_consultation_confirmed template."""
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
    """Send doctor_new_consultation_alert template."""
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
# Patient Appointment + Payment Entry
# ──────────────────────────────────────────────────────────────────────────────

def _create_patient_appointment(booking):
    """Auto-create Patient Appointment."""
    try:
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
    """Resolve company for accounting entries."""
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
    """Find Customer linked to Patient."""
    if not patient_name:
        return None

    customer = frappe.db.get_value("Patient", patient_name, "customer")
    if customer and frappe.db.exists("Customer", customer):
        return customer

    if frappe.db.exists("Customer", patient_name):
        return patient_name

    return None


def _create_payment_entry(booking, amount):
    """Auto-create Payment Entry with Administrator elevation."""
    original_user = frappe.session.user
    frappe.set_user("Administrator")

    try:
        company = _get_default_company()
        if not company:
            raise Exception("No company configured in the system")

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

        customer = _resolve_customer_from_patient(booking.patient)
        if not customer:
            raise Exception(
                f"No Customer linked to Patient '{booking.patient}'. "
                f"Set Customer field on the Patient record."
            )

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
    finally:
        try:
            frappe.set_user(original_user)
        except Exception:
            pass


# ──────────────────────────────────────────────────────────────────────────────
# Payment Failure Handler
# ──────────────────────────────────────────────────────────────────────────────

def _handle_payment_failed(event):
    """Triggered on payment failure/cancellation."""
    try:
        info = _extract_payment_info(event)
        if not info:
            return

        customer_phone = _normalize_phone(info["contact"])
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
    """Normalize to '91XXXXXXXXXX' format."""
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

    return s