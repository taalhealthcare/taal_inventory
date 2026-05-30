"""
Google Meet Creator (standalone)

Creates a Google Calendar event with a Meet link for a given Interakt Booking.

Reads OAuth credentials from the EXISTING `Doctor Consultation Meet Settings`
singleton, but does NOT import any code from the `doctor_consultation_meet`
app. We only read a DocType record from the database, same as any other doc.

If you ever uninstall `doctor_consultation_meet`, just create a new singleton
with the same field names (client_id, client_secret, refresh_token, calendar_id)
and this module continues to work.
"""

import re
import json
import requests
import frappe
from datetime import datetime, timedelta


GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_CAL_API   = "https://www.googleapis.com/calendar/v3"
TIMEZONE         = "Asia/Kolkata"
HTTP_TIMEOUT     = 20  # seconds


def create_meet_for_booking(booking):
    """
    Creates a Google Calendar event with Meet for the booking.

    Args:
        booking: Interakt Booking doc (must have preferred_date,
                 preferred_time_slot, patient_name, whatsapp_number)

    Returns:
        dict: {
            "success": bool,
            "meet_link": str,         # only when success=True
            "event_id": str,          # only when success=True
            "error": str              # only when success=False
        }
    """
    try:
        # --- 1. Load OAuth + Booking settings ---
        meet_settings = frappe.get_single("Doctor Consultation Meet Settings")
        booking_settings = frappe.get_single("Booking API Settings")

        calendar_id = meet_settings.calendar_id or "primary"
        duration_minutes = booking_settings.default_meet_duration_minutes or 30

        # --- 2. Parse the time slot string into start/end datetimes ---
        try:
            start_dt, end_dt = _parse_time_slot(
                booking.preferred_time_slot,
                booking.preferred_date,
                fallback_duration_minutes=duration_minutes
            )
        except Exception as parse_err:
            return {
                "success": False,
                "error": f"Time slot parse error: {parse_err}"
            }

        # --- 3. Refresh Google access token ---
        access_token = _refresh_access_token(meet_settings)
        if not access_token:
            return {
                "success": False,
                "error": "Failed to obtain Google access token"
            }

        # --- 4. Build Calendar event payload ---
        event_body = {
            "summary": f"TAAL+ Consultation: {booking.patient_name or 'Patient'}",
            "description": _build_event_description(booking),
            "start": {
                "dateTime": start_dt.strftime("%Y-%m-%dT%H:%M:%S"),
                "timeZone": TIMEZONE
            },
            "end": {
                "dateTime": end_dt.strftime("%Y-%m-%dT%H:%M:%S"),
                "timeZone": TIMEZONE
            },
            "conferenceData": {
                "createRequest": {
                    "requestId": f"taal-{booking.name}",
                    "conferenceSolutionKey": {"type": "hangoutsMeet"}
                }
            }
        }

        # --- 5. POST to Calendar API with conferenceDataVersion=1 ---
        url = f"{GOOGLE_CAL_API}/calendars/{calendar_id}/events?conferenceDataVersion=1"
        response = requests.post(
            url,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json"
            },
            data=json.dumps(event_body),
            timeout=HTTP_TIMEOUT
        )

        if response.status_code not in (200, 201):
            return {
                "success": False,
                "error": f"Calendar API HTTP {response.status_code}: {response.text[:400]}"
            }

        event = response.json()
        event_id = event.get("id", "")

        # --- 6. Extract the Meet link ---
        meet_link = event.get("hangoutLink", "")
        if not meet_link:
            for ep in event.get("conferenceData", {}).get("entryPoints", []):
                if ep.get("entryPointType") == "video":
                    meet_link = ep.get("uri", "")
                    break

        if not meet_link:
            return {
                "success": False,
                "error": "Event created but no Meet link returned by Google",
                "event_id": event_id
            }

        return {
            "success": True,
            "meet_link": meet_link,
            "event_id": event_id
        }

    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "Google Meet Creator Error")
        return {
            "success": False,
            "error": str(e)
        }


# ──────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ──────────────────────────────────────────────────────────────────────────────

def _refresh_access_token(meet_settings):
    """
    Refreshes the Google access token using the stored refresh_token.
    Returns the new access_token string, or None on failure.
    """
    client_id = meet_settings.client_id
    client_secret = meet_settings.get_password("client_secret") \
        if hasattr(meet_settings, "get_password") else meet_settings.client_secret
    refresh_token = meet_settings.get_password("refresh_token") \
        if hasattr(meet_settings, "get_password") else meet_settings.refresh_token

    if not (client_id and client_secret and refresh_token):
        frappe.log_error(
            "Doctor Consultation Meet Settings missing client_id/secret/refresh_token",
            "Google Meet OAuth Config Error"
        )
        return None

    try:
        response = requests.post(
            GOOGLE_TOKEN_URL,
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token"
            },
            timeout=HTTP_TIMEOUT
        )

        if response.status_code != 200:
            frappe.log_error(
                f"Token refresh HTTP {response.status_code}: {response.text[:400]}",
                "Google Meet Token Refresh Failed"
            )
            return None

        return response.json().get("access_token")

    except Exception as e:
        frappe.log_error(
            f"Token refresh exception: {e}",
            "Google Meet Token Refresh Exception"
        )
        return None


def _parse_time_slot(time_slot_str, preferred_date, fallback_duration_minutes=30):
    """
    Parses Interakt time slot strings into start/end datetimes.

    Supported formats:
      "11:00 AM - 1:00 PM"
      "4 PM - 6 PM"
      "11:00am - 1:00pm"
      "11 AM to 1 PM"

    Args:
        time_slot_str: The slot label
        preferred_date: date object or "YYYY-MM-DD" string
        fallback_duration_minutes: if only one time given, use this duration

    Returns:
        (start_dt, end_dt) tuple of naive datetimes (in Asia/Kolkata clock time)
    """
    if not time_slot_str:
        raise ValueError("Empty time slot")

    # Normalize date
    if isinstance(preferred_date, str):
        date_obj = datetime.strptime(preferred_date, "%Y-%m-%d").date()
    else:
        date_obj = preferred_date

    # Split start/end on -, –, —, or " to "
    parts = re.split(r"\s*[-–—]\s*|\s+to\s+", time_slot_str.strip(), flags=re.IGNORECASE)

    if len(parts) == 1:
        start_time = _parse_one_time(parts[0])
        start_dt = datetime.combine(date_obj, start_time)
        end_dt = start_dt + timedelta(minutes=fallback_duration_minutes)
        return start_dt, end_dt

    if len(parts) == 2:
        start_time = _parse_one_time(parts[0])
        end_time = _parse_one_time(parts[1])
        start_dt = datetime.combine(date_obj, start_time)
        end_dt = datetime.combine(date_obj, end_time)
        if end_dt <= start_dt:
            # Defensive: end before start (shouldn't happen, fix to +30 min)
            end_dt = start_dt + timedelta(minutes=fallback_duration_minutes)
        return start_dt, end_dt

    raise ValueError(f"Unparseable time slot: {time_slot_str}")


def _parse_one_time(time_str):
    """Tries multiple time formats: '11:00 AM', '11 AM', '11:00am', '11am', '11:00', etc."""
    s = time_str.strip().upper().replace(".", "")
    # Insert a space before AM/PM if attached (e.g. "11AM" → "11 AM")
    s = re.sub(r"(\d)(AM|PM)", r"\1 \2", s)

    formats = ["%I:%M %p", "%I %p", "%H:%M", "%H"]
    for fmt in formats:
        try:
            return datetime.strptime(s, fmt).time()
        except ValueError:
            continue

    raise ValueError(f"Cannot parse time: {time_str}")


def _build_event_description(booking):
    lines = [
        f"Patient: {booking.patient_name or '-'}",
        f"Phone: +{booking.whatsapp_number or '-'}",
        f"City: {booking.city_location or '-'}",
        f"Speciality: {getattr(booking, 'speciality', None) or 'Other'}",
        f"Reason: {(booking.consultation_reason or '-')[:500]}",
        "",
        f"Booking ID: {booking.name}",
        "Source: Interakt WhatsApp → TAAL+ Healthcare ERPNext"
    ]
    return "\n".join(lines)