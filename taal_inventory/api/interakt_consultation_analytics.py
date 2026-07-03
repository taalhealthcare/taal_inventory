"""
Interakt Consultation Analytics — Backend API
Location: taal_inventory/api/interakt_consultation_analytics.py

Endpoints powering the "Interakt — Consultation Command Center" dashboard:
  • get_dashboard_live_data()      — Section A (real-time ops, polled every 30s)
  • get_dashboard_analytics_data() — Section B (strategic analytics)

Defensive: auto-detects available fields on Interakt Booking so this file
works on any bench regardless of whether all Custom Fields have been added.
"""

import frappe
from frappe.utils import (
    nowdate, add_days, getdate, get_datetime, now_datetime,
    flt, cint,
)
from collections import defaultdict


# ─── Defensive field detection ────────────────────────────────────────────────

def _get_available_fields(doctype):
    """Return the set of field names that exist on this DocType (incl. Custom Fields)."""
    field_set = set()
    try:
        meta = frappe.get_meta(doctype)
        for f in meta.fields:
            field_set.add(f.fieldname)
        field_set.update(["name", "creation", "modified", "owner"])
    except Exception:
        pass
    return field_set


def _filter_fields(desired, available):
    """Filter a list of desired fields to only those that exist."""
    return [f for f in desired if f in available]


# ─── Shared helpers ───────────────────────────────────────────────────────────

def _resolve_date_range(from_date, to_date):
    if not from_date:
        from_date = add_days(nowdate(), -30)
    if not to_date:
        to_date = nowdate()
    return getdate(from_date), getdate(to_date)


def _get_bookings(from_date, to_date, doctor=None, speciality=None, status=None):
    """Fetch bookings with defensive field selection."""
    filters = {"creation": ["between", [from_date, to_date]]}
    if doctor:
        filters["assigned_team_member"] = doctor
    if speciality:
        filters["speciality"] = speciality
    if status:
        filters["payment_status"] = status

    available = _get_available_fields("Interakt Booking")
    desired = [
        "name", "patient", "patient_name", "whatsapp_number",
        "city_location", "speciality", "selected_service",
        "preferred_date", "preferred_time_slot",
        "payment_amount_inr", "payment_status", "payment_id",
        "razorpay_order_id", "payment_received_at", "payment_entry",
        "booking_status", "assigned_team_member",
        "patient_appointment", "google_meet_link",
        "meet_creation_status", "meet_creation_error",
        "patient_message_sent", "doctor_message_sent", "admin_message_sent",
        "payment_link_url",
        "creation", "modified",
    ]
    fields = _filter_fields(desired, available)

    return frappe.get_all(
        "Interakt Booking",
        filters=filters,
        fields=fields,
        order_by="creation desc",
    )


def _time_to_pay_seconds(booking):
    if not booking.get("payment_received_at") or not booking.get("creation"):
        return None
    try:
        created = get_datetime(booking["creation"])
        paid = get_datetime(booking["payment_received_at"])
        return int((paid - created).total_seconds())
    except Exception:
        return None


def _format_time_diff(seconds):
    if seconds is None:
        return "—"
    if seconds < 60:
        return f"{seconds}s"
    mins = seconds // 60
    secs = seconds % 60
    if mins < 60:
        return f"{mins}m {secs}s"
    hours = mins // 60
    mins = mins % 60
    return f"{hours}h {mins}m"


def _doctor_display_name(doctor_id):
    if not doctor_id:
        return "Unassigned"
    try:
        name = frappe.db.get_value("Healthcare Practitioner", doctor_id, "practitioner_name")
        return name or doctor_id
    except Exception:
        return doctor_id


def _activity_status(booking):
    if booking.get("payment_status") == "Paid":
        if booking.get("meet_creation_status") == "Created":
            return {"label": "Complete", "color": "#1D9E75", "icon": "ti-check"}
        return {"label": "Paid", "color": "#0F6E56", "icon": "ti-currency-rupee"}
    if booking.get("payment_status") == "Pending":
        return {"label": "Awaiting Payment", "color": "#BA7517", "icon": "ti-credit-card"}
    if booking.get("payment_status") in ("Failed", "Cancelled"):
        return {"label": "Failed", "color": "#A32D2D", "icon": "ti-x"}
    return {"label": "New", "color": "#185FA5", "icon": "ti-plus"}


def _time_ago(ts):
    if not ts:
        return "—"
    try:
        now = now_datetime()
        diff = now - get_datetime(ts)
        seconds = int(diff.total_seconds())
        if seconds < 60:
            return f"{seconds}s ago"
        if seconds < 3600:
            return f"{seconds // 60} min ago"
        if seconds < 86400:
            return f"{seconds // 3600} hr ago"
        return f"{seconds // 86400} d ago"
    except Exception:
        return "—"


def _last_log_minutes(method_filter):
    """How many minutes since the last Error Log entry matching the method filter."""
    try:
        result = frappe.db.sql(
            """SELECT MAX(creation) FROM `tabError Log` WHERE method LIKE %s""",
            (f"%{method_filter}%",),
        )
        if result and result[0][0]:
            diff = (now_datetime() - get_datetime(result[0][0])).total_seconds() / 60
            return int(diff)
    except Exception:
        pass
    return None


def _calculate_whatsapp_rate(bookings):
    total = 0
    sent = 0
    for b in bookings:
        total += 3
        sent += cint(b.get("patient_message_sent")) + cint(b.get("doctor_message_sent")) + cint(b.get("admin_message_sent"))
    return round((sent / total) * 100, 1) if total else 0.0


# ─── DASHBOARD: Section A — Live Operations Hub ──────────────────────────────

@frappe.whitelist()
def get_dashboard_live_data():
    """
    Returns everything Section A of the dashboard needs. Polled every 30s.
    """
    today_start = getdate(nowdate())
    yesterday_start = add_days(today_start, -1)
    week_start = add_days(today_start, -7)

    today_bookings = _get_bookings(today_start, today_start)
    yesterday_bookings = _get_bookings(yesterday_start, yesterday_start)
    week_bookings = _get_bookings(week_start, today_start)

    today_paid = [b for b in today_bookings if b.get("payment_status") == "Paid"]
    yesterday_paid = [b for b in yesterday_bookings if b.get("payment_status") == "Paid"]
    week_paid = [b for b in week_bookings if b.get("payment_status") == "Paid"]

    today_revenue = sum(flt(b.get("payment_amount_inr") or 0) for b in today_paid)
    yesterday_revenue = sum(flt(b.get("payment_amount_inr") or 0) for b in yesterday_paid)

    if yesterday_revenue:
        today_revenue_change_pct = round(((today_revenue - yesterday_revenue) / yesterday_revenue) * 100, 1)
    else:
        today_revenue_change_pct = 0

    week_conversion = round((len(week_paid) / len(week_bookings)) * 100, 1) if week_bookings else 0.0

    try:
        total_doctors = frappe.db.count("Healthcare Practitioner", {"status": "Active"})
    except Exception:
        total_doctors = 0

    # Funnel (last 7 days)
    funnel = {
        "submitted":   len(week_bookings),
        "booking":     len(week_bookings),
        "link_sent":   sum(1 for b in week_bookings if b.get("payment_link_url") or b.get("razorpay_order_id")),
        "paid":        len(week_paid),
        "appointment": sum(1 for b in week_paid if b.get("patient_appointment")),
        "meet":        sum(1 for b in week_paid if b.get("meet_creation_status") == "Created"),
        "notified":    sum(1 for b in week_paid if cint(b.get("patient_message_sent"))),
    }

    # Activity feed (last 24h)
    recent_bookings = _get_bookings(add_days(today_start, -1), today_start)
    recent_bookings.sort(key=lambda b: b.get("creation") or "", reverse=True)
    activity = []
    for b in recent_bookings[:10]:
        status_meta = _activity_status(b)
        activity.append({
            "name": b["name"],
            "patient_name": b.get("patient_name") or "—",
            "speciality": b.get("speciality") or "—",
            "amount": flt(b.get("payment_amount_inr") or 0),
            "status_label": status_meta["label"],
            "status_color": status_meta["color"],
            "icon": status_meta["icon"],
            "time_ago": _time_ago(b.get("creation")),
        })

    # Doctor distribution (last 7 days, paid only)
    doctor_dist = defaultdict(int)
    for b in week_paid:
        if b.get("assigned_team_member"):
            doctor_dist[_doctor_display_name(b["assigned_team_member"])] += 1
    doctor_dist_sorted = sorted(doctor_dist.items(), key=lambda x: x[1], reverse=True)

    # Speciality distribution (last 7 days, paid only)
    spec_dist = defaultdict(int)
    for b in week_paid:
        spec_dist[b.get("speciality") or "Other"] += 1
    spec_dist_sorted = sorted(spec_dist.items(), key=lambda x: x[1], reverse=True)

    # Time slot demand (last 30 days, paid only)
    slot_dist = defaultdict(int)
    month_bookings = _get_bookings(add_days(today_start, -30), today_start)
    for b in month_bookings:
        if b.get("payment_status") == "Paid" and b.get("preferred_time_slot"):
            slot = b["preferred_time_slot"]
            if not slot.startswith("option_"):
                slot_start = slot.split(" - ")[0].strip() if " - " in slot else slot
                slot_dist[slot_start] += 1

    # System health
    last_razorpay_log = _last_log_minutes("Razorpay")
    last_interakt_log = _last_log_minutes("INTERAKT")
    try:
        errors_24h = frappe.db.count("Error Log", {"creation": [">", add_days(now_datetime(), -1)]})
    except Exception:
        errors_24h = 0

    return {
        "hero_kpis": {
            "today_revenue": today_revenue,
            "today_revenue_change_pct": today_revenue_change_pct,
            "today_bookings": len(today_bookings),
            "today_bookings_change": len(today_bookings) - len(yesterday_bookings),
            "week_conversion": week_conversion,
            "active_doctors": total_doctors,
        },
        "funnel": funnel,
        "activity": activity,
        "doctor_distribution": [{"label": k, "value": v} for k, v in doctor_dist_sorted],
        "speciality_distribution": [{"label": k, "value": v} for k, v in spec_dist_sorted],
        "timeslot_demand": [{"label": k, "value": v} for k, v in sorted(slot_dist.items())],
        "system_health": {
            "razorpay_last_min": last_razorpay_log,
            "interakt_last_min": last_interakt_log,
            "google_meet": "OK",
            "whatsapp_rate": _calculate_whatsapp_rate(week_paid),
            "errors_24h": errors_24h,
        },
        "refreshed_at": now_datetime().strftime("%H:%M:%S"),
    }


# ─── DASHBOARD: Section B — Strategic Analytics ──────────────────────────────

@frappe.whitelist()
def get_dashboard_analytics_data(period="month"):
    """
    Returns Section B data based on period: 'week', 'month', or 'quarter'.
    """
    if period == "week":
        from_date = add_days(nowdate(), -7)
    elif period == "quarter":
        from_date = add_days(nowdate(), -90)
    else:
        from_date = add_days(nowdate(), -30)
    to_date = nowdate()

    bookings = _get_bookings(from_date, to_date)
    paid = [b for b in bookings if b.get("payment_status") == "Paid"]

    # Acquisition KPIs
    unique_patients = set(b["patient"] for b in bookings if b.get("patient"))
    patient_counts = defaultdict(int)
    for b in bookings:
        if b.get("patient"):
            patient_counts[b["patient"]] += 1
    repeat_patients = sum(1 for c in patient_counts.values() if c > 1)

    times = [_time_to_pay_seconds(b) for b in paid]
    times = [t for t in times if t is not None]
    avg_time_to_pay_sec = sum(times) // len(times) if times else 0

    total_revenue = sum(flt(b.get("payment_amount_inr") or 0) for b in paid)
    revenue_per_patient = round(total_revenue / len(unique_patients), 0) if unique_patients else 0

    # Time slot heatmap
    heatmap = defaultdict(int)
    slots = []
    for b in paid:
        slot = b.get("preferred_time_slot") or ""
        if slot.startswith("option_"):
            continue
        slot_start = slot.split(" - ")[0].strip() if " - " in slot else slot
        if slot_start and b.get("preferred_date"):
            try:
                day_name = getdate(b["preferred_date"]).strftime("%a")
                heatmap[(slot_start, day_name)] += 1
                if slot_start not in slots:
                    slots.append(slot_start)
            except Exception:
                continue
    slots.sort()

    days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    heatmap_data = []
    for slot in slots:
        row = {"slot": slot, "cells": []}
        for day in days:
            count = heatmap.get((slot, day), 0)
            row["cells"].append({"day": day, "count": count})
        heatmap_data.append(row)

    # Revenue trend
    daily_revenue = defaultdict(float)
    for b in paid:
        if b.get("payment_received_at"):
            try:
                day = getdate(b["payment_received_at"])
                daily_revenue[day] += flt(b.get("payment_amount_inr") or 0)
            except Exception:
                continue

    revenue_trend = []
    cursor = getdate(from_date)
    end = getdate(to_date)
    while cursor <= end:
        revenue_trend.append({
            "date": str(cursor),
            "revenue": daily_revenue.get(cursor, 0.0),
        })
        cursor = add_days(cursor, 1)

    # Top cities
    city_counts = defaultdict(int)
    for b in paid:
        city = (b.get("city_location") or "Unknown").strip().title()
        city_counts[city] += 1
    top_cities = sorted(city_counts.items(), key=lambda x: x[1], reverse=True)[:6]

    # Drop-off causes
    no_form = sum(1 for b in bookings if not b.get("preferred_time_slot") or (b.get("preferred_time_slot") or "").startswith("option_"))
    abandoned_payment = sum(1 for b in bookings if b.get("payment_status") == "Pending" and b.get("payment_link_url"))
    meet_failed = sum(1 for b in paid if b.get("meet_creation_status") == "Failed")
    total_for_pct = len(bookings) or 1

    return {
        "period": period,
        "from_date": str(from_date),
        "to_date": str(to_date),
        "acquisition_kpis": {
            "new_patients": len(unique_patients) - repeat_patients,
            "repeat_patients": repeat_patients,
            "avg_time_to_pay": _format_time_diff(avg_time_to_pay_sec) if avg_time_to_pay_sec else "—",
            "revenue_per_patient": revenue_per_patient,
        },
        "heatmap": {
            "slots": slots,
            "days": days,
            "data": heatmap_data,
        },
        "revenue_trend": revenue_trend,
        "top_cities": [{"city": c, "count": n} for c, n in top_cities],
        "dropoff": {
            "no_form_pct":           round((no_form / total_for_pct) * 100, 1),
            "abandoned_payment_pct": round((abandoned_payment / total_for_pct) * 100, 1),
            "meet_failed_pct":       round((meet_failed / total_for_pct) * 100, 1),
        }
    }


# ─── LEGACY endpoints (kept for compatibility) ───────────────────────────────
# The Report is now self-contained and no longer calls these, but they're
# left here in case anything else references them.

@frappe.whitelist()
def get_report_kpis(from_date=None, to_date=None, doctor=None, speciality=None, status=None):
    return {"deprecated": True, "message": "Report is now self-contained."}


@frappe.whitelist()
def get_doctor_performance(from_date=None, to_date=None):
    from_date, to_date = _resolve_date_range(from_date, to_date)
    bookings = _get_bookings(from_date, to_date)

    doctors = frappe.get_all(
        "Healthcare Practitioner",
        filters={"status": "Active"},
        fields=["name", "practitioner_name"],
    )
    by_doctor = defaultdict(list)
    for b in bookings:
        if b.get("assigned_team_member"):
            by_doctor[b["assigned_team_member"]].append(b)

    spec_map = frappe.get_all(
        "Speciality Doctor Mapping",
        filters={"is_active": 1},
        fields=["doctor", "speciality"],
    )
    doc_specs = defaultdict(list)
    for m in spec_map:
        doc_specs[m["doctor"]].append(m["speciality"])

    results = []
    for d in doctors:
        d_bookings = by_doctor.get(d["name"], [])
        d_paid = [b for b in d_bookings if b.get("payment_status") == "Paid"]
        d_cancelled = [b for b in d_bookings if b.get("payment_status") in ("Failed", "Cancelled")]

        revenue = sum(flt(b.get("payment_amount_inr") or 0) for b in d_paid)
        cancel_rate = round((len(d_cancelled) / len(d_bookings)) * 100, 1) if d_bookings else 0.0

        sparkline = []
        for i in range(13, -1, -1):
            day = add_days(nowdate(), -i)
            day_count = sum(1 for b in d_bookings if b.get("creation") and getdate(b["creation"]) == getdate(day))
            sparkline.append(day_count)

        results.append({
            "doctor_id": d["name"],
            "doctor_name": d["practitioner_name"] or d["name"],
            "specialities": ", ".join(doc_specs.get(d["name"], [])) or "—",
            "total_bookings": len(d_bookings),
            "completed": len(d_paid),
            "revenue": revenue,
            "cancel_rate": cancel_rate,
            "sparkline": sparkline,
        })

    results.sort(key=lambda x: x["revenue"], reverse=True)
    return {"doctors": results, "period": f"{from_date} to {to_date}"}