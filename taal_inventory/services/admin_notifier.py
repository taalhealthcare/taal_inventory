"""
Admin Notifier

Sends booking notifications to the administrator via:
  1. WhatsApp template (Interakt) — using "admin_consultation_booked" template
  2. Email — using frappe.sendmail with an HTML body

Settings come from `Booking API Settings`:
  - admin_whatsapp_number    — if set, WhatsApp is sent
  - admin_notification_email — if set, email is sent
  - admin_template_name      — optional override; defaults to "admin_consultation_booked"

The notifier is "always-on": no master toggle. If either channel's destination
field is empty, that channel is silently skipped. Both empty → noop.
"""

import frappe
from frappe.utils import flt
from taal_inventory.services import interakt_outbound


def notify_admin(booking, doctor_display_name=None):
    """
    Notify the admin via WhatsApp + email about a confirmed booking.

    Args:
        booking: Interakt Booking doc (already loaded with all fields populated)
        doctor_display_name: Resolved doctor's display name, or None when
                             speciality is "Other"/unmapped.

    Returns:
        dict: {
            "whatsapp_sent": bool,
            "email_sent": bool,
            "errors": list[str]
        }
    """
    settings = frappe.get_single("Booking API Settings")

    result = {
        "whatsapp_sent": False,
        "email_sent": False,
        "errors": []
    }

    doctor_label = doctor_display_name or "Unassigned (Other)"
    patient_phone_no_prefix = _strip_country_code(booking.whatsapp_number)
    speciality = getattr(booking, "speciality", None) or "Other"

    # Defensive field reads (Booking API Settings doesn't have these
    # fields directly defined, so we use getattr with sensible defaults)
    admin_whatsapp_number    = getattr(settings, "admin_whatsapp_number", None)
    admin_notification_email = getattr(settings, "admin_notification_email", None)
    admin_template_name      = getattr(settings, "admin_template_name", None) \
                               or "admin_consultation_booked"

    # ──────────────────────────────────────────────────────────────────────
    # 1. WhatsApp via Interakt
    # ──────────────────────────────────────────────────────────────────────
    if admin_whatsapp_number:
        body_values = [
            booking.patient_name or "-",
            patient_phone_no_prefix or "-",
            booking.city_location or "-",
            speciality,
            doctor_label,
            str(booking.preferred_date or "-"),
            booking.preferred_time_slot or "-",
            str(flt(booking.payment_amount_inr or 0)),
            booking.name or "-",
            (booking.consultation_reason or "-")[:200],
            getattr(booking, "google_meet_link", None) or "Not generated"
        ]

        try:
            wa_result = interakt_outbound.send_template_message(
                phone_number=admin_whatsapp_number,
                template_name=admin_template_name,
                body_values=body_values
            )

            if wa_result.get("success"):
                result["whatsapp_sent"] = True
            else:
                result["errors"].append(f"Admin WhatsApp: {wa_result.get('error', 'unknown')}")
        except Exception as e:
            result["errors"].append(f"Admin WhatsApp exception: {str(e)}")
    else:
        result["errors"].append("Admin WhatsApp skipped: admin_whatsapp_number not set")

    # ──────────────────────────────────────────────────────────────────────
    # 2. Email via frappe.sendmail
    # ──────────────────────────────────────────────────────────────────────
    if admin_notification_email:
        try:
            subject = f"[TAAL+] New Consultation Booked — {booking.patient_name or 'Patient'}"
            html_body = _build_admin_email_html(
                booking, doctor_label, patient_phone_no_prefix, speciality
            )

            frappe.sendmail(
                recipients=[admin_notification_email],
                subject=subject,
                message=html_body,
                now=True  # send synchronously so we know if it failed
            )
            result["email_sent"] = True

        except Exception as e:
            frappe.log_error(frappe.get_traceback(), "Admin Email Send Error")
            result["errors"].append(f"Admin Email: {str(e)}")
    else:
        result["errors"].append("Admin email skipped: admin_notification_email not set")

    return result


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _strip_country_code(phone):
    """Returns the 10-digit local part for display (drops '91' prefix)."""
    if not phone:
        return ""
    s = str(phone).strip().replace("+", "").replace(" ", "").replace("-", "")
    if len(s) == 12 and s.startswith("91"):
        return s[2:]
    if len(s) == 10:
        return s
    return s


def _build_admin_email_html(booking, doctor_label, patient_phone_local, speciality):
    """Builds the HTML body for the admin notification email."""
    meet_link = booking.google_meet_link or ""
    meet_cell = (
        f'<a href="{meet_link}">{meet_link}</a>'
        if meet_link else
        '<span style="color:#c33">Not generated</span>'
    )

    amount = flt(booking.payment_amount_inr or 0)

    return f"""
    <div style="font-family:Arial,sans-serif; max-width:640px">
      <h2 style="color:#10b981; margin-bottom:4px">🔔 New Consultation Booked</h2>
      <p style="color:#555; margin-top:0">
        A patient has booked and paid for a doctor consultation.
      </p>

      <table cellpadding="8" cellspacing="0"
             style="border-collapse:collapse; width:100%; border:1px solid #e5e7eb">
        <tr style="background:#f9fafb">
          <td style="border:1px solid #e5e7eb; width:35%"><b>Patient</b></td>
          <td style="border:1px solid #e5e7eb">{booking.patient_name or '-'}</td>
        </tr>
        <tr>
          <td style="border:1px solid #e5e7eb"><b>Phone</b></td>
          <td style="border:1px solid #e5e7eb">+91 {patient_phone_local or '-'}</td>
        </tr>
        <tr style="background:#f9fafb">
          <td style="border:1px solid #e5e7eb"><b>Location</b></td>
          <td style="border:1px solid #e5e7eb">{booking.city_location or '-'}</td>
        </tr>
        <tr>
          <td style="border:1px solid #e5e7eb"><b>Speciality</b></td>
          <td style="border:1px solid #e5e7eb">{speciality}</td>
        </tr>
        <tr style="background:#f9fafb">
          <td style="border:1px solid #e5e7eb"><b>Assigned Doctor</b></td>
          <td style="border:1px solid #e5e7eb">{doctor_label}</td>
        </tr>
        <tr>
          <td style="border:1px solid #e5e7eb"><b>Date</b></td>
          <td style="border:1px solid #e5e7eb">{booking.preferred_date or '-'}</td>
        </tr>
        <tr style="background:#f9fafb">
          <td style="border:1px solid #e5e7eb"><b>Time Slot</b></td>
          <td style="border:1px solid #e5e7eb">{booking.preferred_time_slot or '-'}</td>
        </tr>
        <tr>
          <td style="border:1px solid #e5e7eb"><b>Payment</b></td>
          <td style="border:1px solid #e5e7eb"><b>₹{amount:,.2f}</b> (Paid)</td>
        </tr>
        <tr style="background:#f9fafb">
          <td style="border:1px solid #e5e7eb"><b>Booking ID</b></td>
          <td style="border:1px solid #e5e7eb"><code>{booking.name}</code></td>
        </tr>
        <tr>
          <td style="border:1px solid #e5e7eb"><b>Reason</b></td>
          <td style="border:1px solid #e5e7eb">{booking.consultation_reason or '-'}</td>
        </tr>
        <tr style="background:#f9fafb">
          <td style="border:1px solid #e5e7eb"><b>Google Meet</b></td>
          <td style="border:1px solid #e5e7eb">{meet_cell}</td>
        </tr>
      </table>

      <p style="color:#888; font-size:12px; margin-top:24px">
        — TAAL+ Healthcare System (auto-generated)
      </p>
    </div>
    """