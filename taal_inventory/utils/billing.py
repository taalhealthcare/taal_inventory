import frappe
from frappe.utils import nowdate, flt
import re


# ──────────────────────────────────────────────────────────────────
#  INTERAKT BOOKING → SALES INVOICE
# ──────────────────────────────────────────────────────────────────

@frappe.whitelist()
def create_si_from_interakt_booking(booking_name):
    """Creates a Sales Invoice (Draft) from an Interakt Booking record."""

    booking = frappe.get_doc("Interakt Booking", booking_name)

    if booking.payment_status != "Paid":
        frappe.throw("Cannot create Sales Invoice — payment not yet received.")

    if booking.get("custom_sales_invoice"):
        frappe.throw(
            "Sales Invoice {} already exists for this booking. "
            "Use the 'View Sales Invoice' button.".format(booking.custom_sales_invoice)
        )

    patient_name = booking.get("patient_name") or booking.get("patient") or ""
    customer = _get_or_create_customer(patient_name, booking.get("whatsapp_number"))
    item_code = _resolve_consultation_item(
        booking.get("speciality") or booking.get("selected_service")
    )

    si = frappe.new_doc("Sales Invoice")
    si.customer = customer
    si.posting_date = nowdate()
    si.due_date = nowdate()
    si.company = frappe.db.get_default("company")
    si.cost_center = "Main - TPC"

    si.custom_interakt_booking = booking_name
    if booking.get("patient_appointment"):
        si.custom_patient_appointment = booking.patient_appointment
    if booking.get("payment_id"):
        si.custom_razorpay_payment_id = booking.payment_id

    speciality_label = (
        booking.get("speciality") or booking.get("selected_service") or ""
    )
    si.append("items", {
        "item_code": item_code,
        "item_name": "Doctor Consultation – {}".format(speciality_label).strip(" –"),
        "description": (
            "Service: {}\n"
            "Speciality: {}\n"
            "Date: {} | Slot: {}\n"
            "Appointment: {}\n"
            "Google Meet: {}"
        ).format(
            booking.get("selected_service") or "",
            booking.get("speciality") or "",
            booking.get("preferred_date") or "",
            booking.get("preferred_time_slot") or "",
            booking.get("patient_appointment") or "",
            booking.get("google_meet_link") or "",
        ),
        "qty": 1,
        "rate": flt(booking.get("payment_amount_inr") or 0),
        "uom": "Nos",
    })

    si.flags.ignore_permissions = True
    si.insert()  # Draft — NOT submitted

    frappe.db.set_value(
        "Interakt Booking", booking_name, "custom_sales_invoice", si.name
    )
    frappe.db.commit()

    return {
        "sales_invoice": si.name,
        "message": "Sales Invoice {} created in Draft mode.".format(si.name),
    }


# ──────────────────────────────────────────────────────────────────
#  DOCTOR CONSULTATION → SALES INVOICE
# ──────────────────────────────────────────────────────────────────

@frappe.whitelist()
def create_si_from_doctor_consultation(doc_name):
    """Creates a Sales Invoice (Draft) from a Doctor Consultation record."""

    doc = frappe.get_doc("Doctor Consultation", doc_name)

    if doc.payment_status != "Paid":
        frappe.throw("Cannot create Sales Invoice — payment status is not Paid.")

    if doc.get("custom_sales_invoice"):
        frappe.throw(
            "Sales Invoice {} already exists for this record. "
            "Use the 'View Sales Invoice' button.".format(doc.custom_sales_invoice)
        )

    # Parse amount from "Consultation Fee 499" → 499
    amount = 499
    if doc.get("mode_of_payment"):
        match = re.search(r'\d+', doc.mode_of_payment)
        if match:
            amount = int(match.group())

    customer = _get_or_create_customer(
        doc.patient_name, doc.get("mobile_number")
    )
    item_code = _resolve_consultation_item(doc.get("book_specialist"))

    si = frappe.new_doc("Sales Invoice")
    si.customer = customer
    si.posting_date = nowdate()
    si.due_date = nowdate()
    si.company = frappe.db.get_default("company")
    si.cost_center = "Main - TPC"

    si.custom_doctor_consultation = doc_name

    specialist_label = doc.get("book_specialist") or ""
    si.append("items", {
        "item_code": item_code,
        "item_name": "Doctor Consultation – {}".format(specialist_label).strip(" –"),
        "description": (
            "Service: Doctor Consultation\n"
            "Specialist: {}\n"
            "Doctor: {}\n"
            "Date: {} | Slot: {}\n"
            "Mode: {}\n"
            "Google Meet: {}"
        ).format(
            doc.get("book_specialist") or "",
            doc.get("doctor_name") or "",
            doc.get("appointment_date") or "",
            doc.get("time") or "",
            doc.get("mode_of_consultation") or "",
            doc.get("g_meet") or "",
        ),
        "qty": 1,
        "rate": flt(amount),
        "uom": "Nos",
    })

    si.flags.ignore_permissions = True
    si.insert()  # Draft — NOT submitted

    frappe.db.set_value(
        "Doctor Consultation", doc_name, "custom_sales_invoice", si.name
    )
    frappe.db.commit()

    return {
        "sales_invoice": si.name,
        "message": "Sales Invoice {} created in Draft mode.".format(si.name),
    }


# ──────────────────────────────────────────────────────────────────
#  SHARED HELPERS
# ──────────────────────────────────────────────────────────────────

def _get_or_create_customer(patient_name, mobile_number=None):
    """Resolve Customer — Patient link first, then name match, then create."""

    if not patient_name:
        frappe.throw("Patient name is required to create a Customer.")

    # Check if a Patient doc links to a Customer
    if frappe.db.exists("Patient", patient_name):
        patient_customer = frappe.db.get_value("Patient", patient_name, "customer")
        if patient_customer and frappe.db.exists("Customer", patient_customer):
            return patient_customer

    # Direct Customer name match
    if frappe.db.exists("Customer", patient_name):
        return patient_name

    # Create a new Customer
    customer = frappe.new_doc("Customer")
    customer.customer_name = patient_name
    customer.customer_type = "Individual"
    customer.customer_group = (
        frappe.db.get_single_value("Selling Settings", "customer_group") or "Individual"
    )
    customer.territory = (
        frappe.db.get_single_value("Selling Settings", "territory") or "India"
    )
    if mobile_number:
        customer.mobile_no = mobile_number
    customer.flags.ignore_permissions = True
    customer.insert()

    # Link back to Patient record if one exists
    if frappe.db.exists("Patient", patient_name):
        frappe.db.set_value("Patient", patient_name, "customer", customer.name)

    return customer.name


def _resolve_consultation_item(speciality=None):
    """Find the best-fit service Item, falling back to a generic one."""

    if speciality:
        specific = frappe.db.get_value(
            "Item",
            {"item_name": ["like", "%{}%".format(speciality)], "disabled": 0, "is_stock_item": 0},
            "name",
        )
        if specific:
            return specific

    generic = frappe.db.get_value(
        "Item",
        {"item_name": ["like", "%Doctor Consultation%"], "disabled": 0, "is_stock_item": 0},
        "name",
    )
    if generic:
        return generic

    # Auto-create a minimal service item as last resort
    if not frappe.db.exists("Item", "DOCTOR-CONSULTATION"):
        item = frappe.new_doc("Item")
        item.item_code = "DOCTOR-CONSULTATION"
        item.item_name = "Doctor Consultation"
        item.item_group = "Services"
        item.is_stock_item = 0
        item.stock_uom = "Nos"
        item.flags.ignore_permissions = True
        item.insert()

    return "DOCTOR-CONSULTATION"