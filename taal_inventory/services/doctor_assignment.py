"""
Doctor Assignment Service

Resolves which doctor handles a booking based on:
1. Speciality from the booking (must match a Speciality Doctor Mapping row)
2. Load-balancing across multi-doctor specialities
   (counts confirmed bookings per (doctor, date, time_slot))

Reads from the `Speciality Doctor Mapping` DocType — no hardcoded mappings.
Returns None when speciality has no active mapping (e.g. "Other") — caller
should treat that as "no doctor assigned" and skip the doctor WhatsApp.
"""

import frappe


CONFIRMED_STATUSES = ["Payment Confirmed", "Appointment Scheduled", "Completed"]


def resolve_doctor(speciality, preferred_date, preferred_time_slot):
    """
    Returns the Healthcare Practitioner record name to assign for this booking.

    Args:
        speciality (str): Speciality string from Interakt Booking
                          (e.g. "HIV Care (ART)")
        preferred_date (str | date): Booking date
        preferred_time_slot (str): Time slot label (e.g. "11:00 AM - 1:00 PM")

    Returns:
        str: Healthcare Practitioner record name (e.g. "HLC-PRAC-2026-00004"),
             or None when speciality has no active mapping.
    """
    if not speciality:
        return None

    mappings = frappe.get_all(
        "Speciality Doctor Mapping",
        filters={
            "speciality": speciality,
            "is_active": 1
        },
        fields=["doctor"],
        order_by="creation ASC"  # Stable order — first-created wins ties
    )

    candidates = [m.doctor for m in mappings if m.doctor]

    if not candidates:
        return None

    if len(candidates) == 1:
        return candidates[0]

    # Multi-doctor — load-balance per slot
    return _load_balance_per_slot(candidates, preferred_date, preferred_time_slot)


def _load_balance_per_slot(candidates, preferred_date, preferred_time_slot):
    """
    Picks the doctor with fewest confirmed bookings at this exact slot.
    Ties → first candidate (stable order from Speciality Doctor Mapping).

    Example for HIV Care on 2026-06-01 at 11:00 AM - 1:00 PM:
      Booking 1 → Sunil:0, Pujari:0 → tie → Sunil
      Booking 2 → Sunil:1, Pujari:0 → Pujari
      Booking 3 → Sunil:1, Pujari:1 → tie → Sunil
      Booking 4 → Sunil:2, Pujari:1 → Pujari
    """
    min_count = None
    chosen = candidates[0]

    for doctor in candidates:
        count = frappe.db.count(
            "Interakt Booking",
            filters={
                "assigned_team_member": doctor,
                "preferred_date": preferred_date,
                "preferred_time_slot": preferred_time_slot,
                "booking_status": ["in", CONFIRMED_STATUSES]
            }
        )
        if min_count is None or count < min_count:
            min_count = count
            chosen = doctor

    return chosen


def get_doctor_details(doctor_record_name):
    """
    Fetches the doctor's display name and WhatsApp phone for notifications.

    Returns:
        dict with keys: exists, name, display_name, mobile_phone
    """
    if not doctor_record_name:
        return {"exists": False}

    doc = frappe.db.get_value(
        "Healthcare Practitioner",
        doctor_record_name,
        ["practitioner_name", "first_name", "mobile_phone"],
        as_dict=True
    )

    if not doc:
        return {"exists": False}

    display_name = doc.practitioner_name or doc.first_name or doctor_record_name

    return {
        "exists": True,
        "name": doctor_record_name,
        "display_name": display_name,
        "mobile_phone": _normalize_phone(doc.mobile_phone)
    }


def _normalize_phone(phone):
    """Strips +, spaces, dashes. Returns '91XXXXXXXXXX' format (12 digits)."""
    if not phone:
        return ""

    s = str(phone).replace("+", "").replace(" ", "").replace("-", "").strip()

    if not s.isdigit():
        return ""

    if len(s) == 10:
        return "91" + s
    if len(s) == 12 and s.startswith("91"):
        return s
    if len(s) == 11 and s.startswith("0"):
        return "91" + s[1:]

    return s