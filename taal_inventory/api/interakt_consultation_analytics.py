"""
Interakt Doctor Consultation Analytics — Backend API

Provides data endpoints for:
  • Interakt Doctor Consultation Analytics Script Report (KPIs + Booking + Doctor tables)
  • Interakt Consultation Command Center Frappe Page (Live Ops + Strategic Analytics)

All endpoints are @frappe.whitelist()-ed for client-side consumption.
Location: taal_inventory/api/interakt_consultation_analytics.py
"""

import frappe
from frappe.utils import (
    nowdate, add_days, getdate, get_datetime, now_datetime,
    flt, cint, format_datetime,
)
from collections import defaultdict
from datetime import datetime, timedelta


# ──────────────────────────────────────────────────────────────────────────────
# Shared helpers
# ──────────────────────────────────────────────────────────────────────────────

def _resolve_date_range(from_date, to_date):
    """Default to last 30 days if not provided."""
    if not from_date:
        from_date = add_days(nowdate(), -30)
    if not to_date:
        to_date = nowdate()
    return getdate(from_date), getdate(to_date)


def _get_bookings(from_date, to_date, doctor=None, speciality=None, status=None):
    """Fetch bookings within filters. Returns list of dicts."""
    filters = {
        "creation": ["between", [from_date, to_date]],
    }
    if doctor:
        filters["assigned_team_member"] = doctor
    if speciality:
        filters["speciality"] = speciality
    if status:
        filters["payment_status"] = status

    return frappe.get_all(
        "Interakt Booking",
        filters=filters,
        fields=[
            "name", "patient", "patient_name", "whatsapp_number",
            "city_location", "speciality", "selected_service",
            "preferred_date", "preferred_time_slot",
            "payment_amount_inr", "payment_status", "payment_id",
            "razorpay_order_id", "payment_received_at", "payment_entry",
            "booking_status", "assigned_team_member",
            "patient_appointment", "google_meet_link",
            "meet_creation_status", "meet_creation_error",
            "patient_message_sent", "doctor_message_sent", "admin_message_sent",
            "creation", "modified",
        ],
        order_by="creation desc",
    )


def _time_to_pay_seconds(booking):
    """How many seconds between booking creation and payment received."""
    if not booking.get("payment_received_at") or not booking.get("creation"):
        return None
    created = get_datetime(booking["creation"])
    paid = get_datetime(booking["payment_received_at"])
    return int((paid - created).total_seconds())


def _format_time_diff(seconds):
    """Convert seconds to human-readable like '2m 14s'."""
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
    """Get the display name for a Healthcare Practitioner."""
    if not doctor_id:
        return "Unassigned"
    return frappe.db.get_value("Healthcare Practitioner", doctor_id, "practitioner_name") or doctor_id


# ──────────────────────────────────────────────────────────────────────────────
# REPORT: Doctor Consultation Analytics — main data endpoint
# ──────────────────────────────────────────────────────────────────────────────

@frappe.whitelist()
def get_report_kpis(from_date=None, to_date=None, doctor=None, speciality=None, status=None):
    """
    Returns the 14 KPI values for the Report.
    Split into 'booking_kpis' (8) and 'doctor_kpis' (6).
    """
    from_date, to_date = _resolve_date_range(from_date, to_date)
    bookings = _get_bookings(from_date, to_date, doctor, speciality, status)

    total = len(bookings)
    paid = [b for b in bookings if b["payment_status"] == "Paid"]
    pending = [b for b in bookings if b["payment_status"] == "Pending"]
    paid_count = len(paid)
    conversion = round((paid_count / total) * 100, 1) if total else 0.0
    revenue = sum(flt(b.get("payment_amount_inr") or 0) for b in paid)

    # Time-to-pay
    times = [_time_to_pay_seconds(b) for b in paid]
    times = [t for t in times if t is not None]
    avg_time_to_pay = sum(times) // len(times) if times else None

    # Meet success
    meet_attempted = [b for b in paid if b.get("meet_creation_status")]
    meet_success = [b for b in meet_attempted if b["meet_creation_status"] == "Created"]
    meet_rate = round((len(meet_success) / len(meet_attempted)) * 100, 1) if meet_attempted else 0.0

    # WhatsApp delivery rate (across patient + doctor + admin)
    total_msgs = 0
    sent_msgs = 0
    for b in paid:
        total_msgs += 3
        sent_msgs += cint(b.get("patient_message_sent")) + cint(b.get("doctor_message_sent")) + cint(b.get("admin_message_sent"))
    whatsapp_rate = round((sent_msgs / total_msgs) * 100, 1) if total_msgs else 0.0

    # At-risk
    at_risk_count = len(pending)
    at_risk_value = sum(flt(b.get("payment_amount_inr") or 0) for b in pending)

    booking_kpis = [
        {"label": "Total Bookings", "value": total, "type": "number"},
        {"label": "Paid", "value": paid_count, "type": "number", "indicator": "Green"},
        {"label": "Conversion", "value": f"{conversion}%", "type": "percent"},
        {"label": "Revenue", "value": f"₹{revenue:,.0f}", "type": "currency"},
        {"label": "Avg Time→Pay", "value": _format_time_diff(avg_time_to_pay), "type": "duration"},
        {"label": "Meet Success", "value": f"{meet_rate}%", "type": "percent", "indicator": "Green" if meet_rate > 90 else "Orange"},
        {"label": "WhatsApp Rate", "value": f"{whatsapp_rate}%", "type": "percent"},
        {"label": "At Risk", "value": at_risk_count, "subtitle": f"₹{at_risk_value:,.0f}", "type": "number", "indicator": "Red"},
    ]

    # Doctor KPIs
    doctor_bookings = defaultdict(list)
    for b in bookings:
        if b.get("assigned_team_member"):
            doctor_bookings[b["assigned_team_member"]].append(b)

    active_doctors = len(doctor_bookings)
    total_doctors = frappe.db.count("Healthcare Practitioner", {"status": "Active"})

    # Most booked
    most_booked_id = max(doctor_bookings.keys(), key=lambda k: len(doctor_bookings[k])) if doctor_bookings else None
    most_booked_name = _doctor_display_name(most_booked_id) if most_booked_id else "—"
    most_booked_count = len(doctor_bookings[most_booked_id]) if most_booked_id else 0

    avg_per_doctor = round(total / active_doctors, 1) if active_doctors else 0.0

    # Highest revenue speciality
    spec_rev = defaultdict(float)
    for b in paid:
        spec_rev[b.get("speciality") or "Other"] += flt(b.get("payment_amount_inr") or 0)
    top_spec = max(spec_rev.keys(), key=lambda k: spec_rev[k]) if spec_rev else "—"
    top_spec_rev = spec_rev[top_spec] if top_spec != "—" else 0

    # Top time slot
    slot_counts = defaultdict(int)
    for b in paid:
        if b.get("preferred_time_slot") and not b["preferred_time_slot"].startswith("option_"):
            slot_counts[b["preferred_time_slot"]] += 1
    top_slot = max(slot_counts.keys(), key=lambda k: slot_counts[k]) if slot_counts else "—"

    doctor_kpis = [
        {"label": "Active Doctors", "value": f"{active_doctors}/{total_doctors}", "type": "ratio"},
        {"label": "Most Booked", "value": most_booked_name, "subtitle": f"{most_booked_count} bookings", "type": "text"},
        {"label": "Avg per Doctor", "value": avg_per_doctor, "type": "decimal"},
        {"label": "Top Speciality", "value": top_spec, "subtitle": f"₹{top_spec_rev:,.0f}", "type": "text"},
        {"label": "Top Time Slot", "value": top_slot, "type": "text"},
        {"label": "Period", "value": f"{from_date} to {to_date}", "type": "text"},
    ]

    return {
        "booking_kpis": booking_kpis,
        "doctor_kpis": doctor_kpis,
        "summary": {
            "total": total,
            "paid": paid_count,
            "conversion": conversion,
            "revenue": revenue,
        }
    }


@frappe.whitelist()
def get_doctor_performance(from_date=None, to_date=None):
    """
    Returns per-doctor aggregated performance metrics for the Doctor section
    of the unified report.
    """
    from_date, to_date = _resolve_date_range(from_date, to_date)

    doctors = frappe.get_all(
        "Healthcare Practitioner",
        filters={"status": "Active"},
        fields=["name", "practitioner_name", "mobile_phone"],
    )

    bookings = _get_bookings(from_date, to_date)
    by_doctor = defaultdict(list)
    for b in bookings:
        if b.get("assigned_team_member"):
            by_doctor[b["assigned_team_member"]].append(b)

    # Speciality mapping per doctor
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
        d_paid = [b for b in d_bookings if b["payment_status"] == "Paid"]
        d_cancelled = [b for b in d_bookings if b["payment_status"] in ("Failed", "Cancelled")]

        revenue = sum(flt(b.get("payment_amount_inr") or 0) for b in d_paid)
        cancel_rate = round((len(d_cancelled) / len(d_bookings)) * 100, 1) if d_bookings else 0.0

        # Last 14 days sparkline
        sparkline = []
        for i in range(13, -1, -1):
            day = add_days(nowdate(), -i)
            day_count = sum(1 for b in d_bookings if getdate(b["creation"]) == getdate(day))
            sparkline.append(day_count)

        results.append({
            "doctor_id": d["name"],
            "doctor_name": d["practitioner_name"],
            "specialities": ", ".join(doc_specs.get(d["name"], [])) or "—",
            "total_bookings": len(d_bookings),
            "completed": len(d_paid),
            "revenue": revenue,
            "cancel_rate": cancel_rate,
            "sparkline": sparkline,
        })

    results.sort(key=lambda x: x["revenue"], reverse=True)
    return {"doctors": results, "period": f"{from_date} to {to_date}"}


# ──────────────────────────────────────────────────────────────────────────────
# DASHBOARD: Section A — Live Operations Hub
# ──────────────────────────────────────────────────────────────────────────────

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

    today_paid = [b for b in today_bookings if b["payment_status"] == "Paid"]
    yesterday_paid = [b for b in yesterday_bookings if b["payment_status"] == "Paid"]
    week_paid = [b for b in week_bookings if b["payment_status"] == "Paid"]

    today_revenue = sum(flt(b.get("payment_amount_inr") or 0) for b in today_paid)
    yesterday_revenue = sum(flt(b.get("payment_amount_inr") or 0) for b in yesterday_paid)

    week_conversion = round((len(week_paid) / len(week_bookings)) * 100, 1) if week_bookings else 0.0

    total_doctors = frappe.db.count("Healthcare Practitioner", {"status": "Active"})

    # Funnel: count at each stage in last 7 days
    funnel = {
        "submitted":   len(week_bookings),
        "booking":     len(week_bookings),
        "link_sent":   sum(1 for b in week_bookings if b.get("payment_link_url") or b.get("razorpay_order_id")),
        "paid":        len(week_paid),
        "appointment": sum(1 for b in week_paid if b.get("patient_appointment")),
        "meet":        sum(1 for b in week_paid if b.get("meet_creation_status") == "Created"),
        "notified":    sum(1 for b in week_paid if cint(b.get("patient_message_sent"))),
    }

    # Activity feed: last 10 bookings (any status)
    recent_bookings = _get_bookings(add_days(today_start, -1), today_start)
    recent_bookings.sort(key=lambda b: b["creation"], reverse=True)
    activity = []
    for b in recent_bookings[:10]:
        status_meta = _activity_status(b)
        activity.append({
            "name": b["name"],
            "patient_name": b["patient_name"] or "—",
            "speciality": b["speciality"] or "—",
            "amount": flt(b.get("payment_amount_inr") or 0),
            "status_label": status_meta["label"],
            "status_color": status_meta["color"],
            "icon": status_meta["icon"],
            "time_ago": _time_ago(b["creation"]),
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
        if b["payment_status"] == "Paid" and b.get("preferred_time_slot"):
            slot = b["preferred_time_slot"]
            if not slot.startswith("option_"):
                # Get just the start time, e.g. "12:30 PM"
                slot_start = slot.split(" - ")[0].strip() if " - " in slot else slot
                slot_dist[slot_start] += 1

    # System health checks
    last_razorpay_log = _last_log_minutes("Razorpay Payment Success Handler Error")
    last_interakt_log = _last_log_minutes("INTERAKT DEBUG: Incoming Webhook")
    errors_24h = frappe.db.count("Error Log", {"creation": [">", add_days(now_datetime(), -1)]})

    return {
        "hero_kpis": {
            "today_revenue": today_revenue,
            "today_revenue_change_pct": round(((today_revenue - yesterday_revenue) / yesterday_revenue) * 100, 1) if yesterday_revenue else 0,
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
            "google_meet": "OK",  # Static for now; could ping Meet Settings expiry
            "whatsapp_rate": _calculate_whatsapp_rate(week_paid),
            "errors_24h": errors_24h,
        },
        "refreshed_at": now_datetime().strftime("%H:%M:%S"),
    }


def _activity_status(booking):
    """Map a booking's state to a UI status."""
    if booking["payment_status"] == "Paid":
        if booking.get("meet_creation_status") == "Created":
            return {"label": "Complete", "color": "#1D9E75", "icon": "ti-check"}
        return {"label": "Paid", "color": "#0F6E56", "icon": "ti-currency-rupee"}
    if booking["payment_status"] == "Pending":
        return {"label": "Awaiting Payment", "color": "#BA7517", "icon": "ti-credit-card"}
    if booking["payment_status"] in ("Failed", "Cancelled"):
        return {"label": "Failed", "color": "#A32D2D", "icon": "ti-x"}
    return {"label": "New", "color": "#185FA5", "icon": "ti-plus"}


def _time_ago(ts):
    """Human-readable time difference."""
    if not ts:
        return "—"
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


def _last_log_minutes(method_filter):
    """How many minutes since the last Error Log entry matching the filter."""
    try:
        last = frappe.db.sql(
            """SELECT MAX(creation) FROM `tabError Log` WHERE method LIKE %s""",
            (f"%{method_filter}%",),
        )
        if last and last[0][0]:
            diff = (now_datetime() - get_datetime(last[0][0])).total_seconds() / 60
            return int(diff)
    except Exception:
        pass
    return None


def _calculate_whatsapp_rate(bookings):
    """Calculate WhatsApp delivery rate across all messages."""
    total = 0
    sent = 0
    for b in bookings:
        total += 3
        sent += cint(b.get("patient_message_sent")) + cint(b.get("doctor_message_sent")) + cint(b.get("admin_message_sent"))
    return round((sent / total) * 100, 1) if total else 0.0


# ──────────────────────────────────────────────────────────────────────────────
# DASHBOARD: Section B — Strategic Analytics
# ──────────────────────────────────────────────────────────────────────────────

@frappe.whitelist()
def get_dashboard_analytics_data(period="month"):
    """
    Returns Section B data based on period: 'week', 'month', or 'quarter'.
    """
    if period == "week":
        from_date = add_days(nowdate(), -7)
    elif period == "quarter":
        from_date = add_days(nowdate(), -90)
    else:  # month default
        from_date = add_days(nowdate(), -30)
    to_date = nowdate()

    bookings = _get_bookings(from_date, to_date)
    paid = [b for b in bookings if b["payment_status"] == "Paid"]

    # Acquisition KPIs
    unique_patients = set(b["patient"] for b in bookings if b.get("patient"))
    # Repeat = patients with > 1 booking in this period
    patient_counts = defaultdict(int)
    for b in bookings:
        if b.get("patient"):
            patient_counts[b["patient"]] += 1
    repeat_patients = sum(1 for c in patient_counts.values() if c > 1)

    times = [_time_to_pay_seconds(b) for b in paid]
    times = [t for t in times if t is not None]
    avg_time_to_pay = sum(times) // len(times) if times else 0

    total_revenue = sum(flt(b.get("payment_amount_inr") or 0) for b in paid)
    revenue_per_patient = round(total_revenue / len(unique_patients), 0) if unique_patients else 0

    # Time slot heatmap (slot × day-of-week)
    heatmap = defaultdict(int)
    slots = []
    for b in paid:
        slot = b.get("preferred_time_slot") or ""
        if slot.startswith("option_"):
            continue
        slot_start = slot.split(" - ")[0].strip() if " - " in slot else slot
        if slot_start and b.get("preferred_date"):
            day_name = getdate(b["preferred_date"]).strftime("%a")
            heatmap[(slot_start, day_name)] += 1
            if slot_start not in slots:
                slots.append(slot_start)
    slots.sort()

    # Convert to grid structure
    days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    heatmap_data = []
    for slot in slots:
        row = {"slot": slot, "cells": []}
        for day in days:
            count = heatmap.get((slot, day), 0)
            row["cells"].append({"day": day, "count": count})
        heatmap_data.append(row)

    # Revenue trend (daily for the period)
    daily_revenue = defaultdict(float)
    for b in paid:
        if b.get("payment_received_at"):
            day = getdate(b["payment_received_at"])
            daily_revenue[day] += flt(b.get("payment_amount_inr") or 0)

    # Fill in zero-revenue days
    revenue_trend = []
    cursor = from_date
    while cursor <= to_date:
        d = getdate(cursor)
        revenue_trend.append({
            "date": str(d),
            "revenue": daily_revenue.get(d, 0.0),
        })
        cursor = add_days(cursor, 1)

    # Top cities
    city_counts = defaultdict(int)
    for b in paid:
        city = (b.get("city_location") or "Unknown").strip().title()
        city_counts[city] += 1
    top_cities = sorted(city_counts.items(), key=lambda x: x[1], reverse=True)[:6]

    # Drop-off causes
    no_form = sum(1 for b in bookings if not b.get("preferred_time_slot") or b["preferred_time_slot"].startswith("option_"))
    abandoned_payment = sum(1 for b in bookings if b["payment_status"] == "Pending" and b.get("payment_link_url"))
    meet_failed = sum(1 for b in paid if b.get("meet_creation_status") == "Failed")
    total_for_pct = len(bookings) or 1

    return {
        "period": period,
        "from_date": str(from_date),
        "to_date": str(to_date),
        "acquisition_kpis": {
            "new_patients": len(unique_patients) - repeat_patients,
            "repeat_patients": repeat_patients,
            "avg_time_to_pay": _format_time_diff(avg_time_to_pay),
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