import frappe
from frappe.utils import nowdate, flt
import re


# ──────────────────────────────────────────────────────────────────
#  CITY → GST PLACE OF SUPPLY
# ──────────────────────────────────────────────────────────────────

_CITY_TO_SUPPLY = {
    # ── Maharashtra (27) ─────────────────────────────────────────
    "pune":               "27-Maharashtra",
    "pimpri":             "27-Maharashtra",
    "chinchwad":          "27-Maharashtra",
    "pimpri chinchwad":   "27-Maharashtra",
    "mumbai":             "27-Maharashtra",
    "thane":              "27-Maharashtra",
    "navi mumbai":        "27-Maharashtra",
    "nagpur":             "27-Maharashtra",
    "nashik":             "27-Maharashtra",
    "aurangabad":         "27-Maharashtra",
    "solapur":            "27-Maharashtra",
    "kolhapur":           "27-Maharashtra",
    "amravati":           "27-Maharashtra",
    "nanded":             "27-Maharashtra",
    "sangli":             "27-Maharashtra",
    "jalgaon":            "27-Maharashtra",
    "akola":              "27-Maharashtra",
    "latur":              "27-Maharashtra",
    "dhule":              "27-Maharashtra",
    "ahmednagar":         "27-Maharashtra",
    "satara":             "27-Maharashtra",
    "ratnagiri":          "27-Maharashtra",
    "vasai":              "27-Maharashtra",
    "virar":              "27-Maharashtra",
    "bhiwandi":           "27-Maharashtra",
    "wardha":             "27-Maharashtra",
    "yavatmal":           "27-Maharashtra",
    "chandrapur":         "27-Maharashtra",
    "gondia":             "27-Maharashtra",
    "bhandara":           "27-Maharashtra",
    "washim":             "27-Maharashtra",
    "hingoli":            "27-Maharashtra",
    "parbhani":           "27-Maharashtra",
    "gadchiroli":         "27-Maharashtra",
    "buldhana":           "27-Maharashtra",
    "osmanabad":          "27-Maharashtra",
    "raigad":             "27-Maharashtra",
    "sindhudurg":         "27-Maharashtra",
    "nandurbar":          "27-Maharashtra",

    # ── Telangana (36) ───────────────────────────────────────────
    "hyderabad":          "36-Telangana",
    "secunderabad":       "36-Telangana",
    "warangal":           "36-Telangana",
    "karimnagar":         "36-Telangana",
    "nizamabad":          "36-Telangana",
    "khammam":            "36-Telangana",
    "nalgonda":           "36-Telangana",
    "mahabubnagar":       "36-Telangana",
    "mahbubnagar":        "36-Telangana",

    # ── Andhra Pradesh (37) ──────────────────────────────────────
    "visakhapatnam":      "37-Andhra Pradesh",
    "vizag":              "37-Andhra Pradesh",
    "vijayawada":         "37-Andhra Pradesh",
    "guntur":             "37-Andhra Pradesh",
    "tirupati":           "37-Andhra Pradesh",
    "nellore":            "37-Andhra Pradesh",
    "rajahmundry":        "37-Andhra Pradesh",
    "kakinada":           "37-Andhra Pradesh",
    "kurnool":            "37-Andhra Pradesh",

    # ── Karnataka (29) ───────────────────────────────────────────
    "bangalore":          "29-Karnataka",
    "bengaluru":          "29-Karnataka",
    "mysore":             "29-Karnataka",
    "mysuru":             "29-Karnataka",
    "hubli":              "29-Karnataka",
    "dharwad":            "29-Karnataka",
    "mangalore":          "29-Karnataka",
    "mangaluru":          "29-Karnataka",
    "belgaum":            "29-Karnataka",
    "belagavi":           "29-Karnataka",
    "bellary":            "29-Karnataka",
    "ballari":            "29-Karnataka",
    "shimoga":            "29-Karnataka",
    "davangere":          "29-Karnataka",
    "tumkur":             "29-Karnataka",
    "udupi":              "29-Karnataka",

    # ── Tamil Nadu (33) ──────────────────────────────────────────
    "chennai":            "33-Tamil Nadu",
    "coimbatore":         "33-Tamil Nadu",
    "madurai":            "33-Tamil Nadu",
    "trichy":             "33-Tamil Nadu",
    "tiruchirappalli":    "33-Tamil Nadu",
    "salem":              "33-Tamil Nadu",
    "tirunelveli":        "33-Tamil Nadu",
    "erode":              "33-Tamil Nadu",
    "vellore":            "33-Tamil Nadu",
    "tiruppur":           "33-Tamil Nadu",
    "thanjavur":          "33-Tamil Nadu",
    "thoothukudi":        "33-Tamil Nadu",

    # ── Kerala (32) ──────────────────────────────────────────────
    "thiruvananthapuram": "32-Kerala",
    "trivandrum":         "32-Kerala",
    "kochi":              "32-Kerala",
    "ernakulam":          "32-Kerala",
    "kozhikode":          "32-Kerala",
    "calicut":            "32-Kerala",
    "thrissur":           "32-Kerala",
    "kollam":             "32-Kerala",
    "kannur":             "32-Kerala",
    "palakkad":           "32-Kerala",
    "malappuram":         "32-Kerala",
    "kottayam":           "32-Kerala",

    # ── Delhi (07) ───────────────────────────────────────────────
    "delhi":              "07-Delhi",
    "new delhi":          "07-Delhi",

    # ── Uttar Pradesh (09) ───────────────────────────────────────
    "lucknow":            "09-Uttar Pradesh",
    "kanpur":             "09-Uttar Pradesh",
    "agra":               "09-Uttar Pradesh",
    "varanasi":           "09-Uttar Pradesh",
    "noida":              "09-Uttar Pradesh",
    "greater noida":      "09-Uttar Pradesh",
    "ghaziabad":          "09-Uttar Pradesh",
    "allahabad":          "09-Uttar Pradesh",
    "prayagraj":          "09-Uttar Pradesh",
    "meerut":             "09-Uttar Pradesh",
    "gorakhpur":          "09-Uttar Pradesh",
    "mathura":            "09-Uttar Pradesh",
    "aligarh":            "09-Uttar Pradesh",
    "bareilly":           "09-Uttar Pradesh",

    # ── Rajasthan (08) ───────────────────────────────────────────
    "jaipur":             "08-Rajasthan",
    "jodhpur":            "08-Rajasthan",
    "udaipur":            "08-Rajasthan",
    "kota":               "08-Rajasthan",
    "bikaner":            "08-Rajasthan",
    "ajmer":              "08-Rajasthan",
    "alwar":              "08-Rajasthan",
    "sikar":              "08-Rajasthan",

    # ── Gujarat (24) ─────────────────────────────────────────────
    "ahmedabad":          "24-Gujarat",
    "surat":              "24-Gujarat",
    "vadodara":           "24-Gujarat",
    "baroda":             "24-Gujarat",
    "rajkot":             "24-Gujarat",
    "gandhinagar":        "24-Gujarat",
    "jamnagar":           "24-Gujarat",
    "bhavnagar":          "24-Gujarat",
    "anand":              "24-Gujarat",
    "junagadh":           "24-Gujarat",

    # ── West Bengal (19) ─────────────────────────────────────────
    "kolkata":            "19-West Bengal",
    "calcutta":           "19-West Bengal",
    "howrah":             "19-West Bengal",
    "durgapur":           "19-West Bengal",
    "siliguri":           "19-West Bengal",
    "asansol":            "19-West Bengal",

    # ── Madhya Pradesh (23) ──────────────────────────────────────
    "bhopal":             "23-Madhya Pradesh",
    "indore":             "23-Madhya Pradesh",
    "gwalior":            "23-Madhya Pradesh",
    "jabalpur":           "23-Madhya Pradesh",
    "ujjain":             "23-Madhya Pradesh",
    "rewa":               "23-Madhya Pradesh",

    # ── Haryana (06) ─────────────────────────────────────────────
    "gurgaon":            "06-Haryana",
    "gurugram":           "06-Haryana",
    "faridabad":          "06-Haryana",
    "panipat":            "06-Haryana",
    "ambala":             "06-Haryana",
    "hisar":              "06-Haryana",
    "rohtak":             "06-Haryana",

    # ── Punjab (03) ──────────────────────────────────────────────
    "ludhiana":           "03-Punjab",
    "amritsar":           "03-Punjab",
    "jalandhar":          "03-Punjab",
    "patiala":            "03-Punjab",

    # ── Chandigarh (04) ──────────────────────────────────────────
    "chandigarh":         "04-Chandigarh",

    # ── Bihar (10) ───────────────────────────────────────────────
    "patna":              "10-Bihar",
    "gaya":               "10-Bihar",
    "muzaffarpur":        "10-Bihar",
    "bhagalpur":          "10-Bihar",

    # ── Odisha (21) ──────────────────────────────────────────────
    "bhubaneswar":        "21-Odisha",
    "cuttack":            "21-Odisha",
    "rourkela":           "21-Odisha",

    # ── Assam (18) ───────────────────────────────────────────────
    "guwahati":           "18-Assam",
    "dibrugarh":          "18-Assam",
    "silchar":            "18-Assam",

    # ── Jharkhand (20) ───────────────────────────────────────────
    "ranchi":             "20-Jharkhand",
    "jamshedpur":         "20-Jharkhand",
    "dhanbad":            "20-Jharkhand",

    # ── Chhattisgarh (22) ────────────────────────────────────────
    "raipur":             "22-Chhattisgarh",
    "bilaspur":           "22-Chhattisgarh",
    "bhilai":             "22-Chhattisgarh",

    # ── Uttarakhand (05) ─────────────────────────────────────────
    "dehradun":           "05-Uttarakhand",
    "haridwar":           "05-Uttarakhand",
    "rishikesh":          "05-Uttarakhand",

    # ── Himachal Pradesh (02) ────────────────────────────────────
    "shimla":             "02-Himachal Pradesh",
    "manali":             "02-Himachal Pradesh",
    "dharamsala":         "02-Himachal Pradesh",

    # ── Goa (30) ─────────────────────────────────────────────────
    "goa":                "30-Goa",
    "panaji":             "30-Goa",
    "margao":             "30-Goa",
    "vasco":              "30-Goa",

    # ── Jammu and Kashmir (01) ───────────────────────────────────
    "srinagar":           "01-Jammu and Kashmir",
    "jammu":              "01-Jammu and Kashmir",

    # ── North-East ───────────────────────────────────────────────
    "imphal":             "14-Manipur",
    "shillong":           "17-Meghalaya",
    "kohima":             "13-Nagaland",
    "aizawl":             "15-Mizoram",
    "agartala":           "16-Tripura",
    "itanagar":           "12-Arunachal Pradesh",
    "gangtok":            "11-Sikkim",
}


def _get_place_of_supply(city):
    """
    Return the ERPNext GST place_of_supply string for a patient city.
    Returns None for blank or unrecognised cities — caller skips setting
    place_of_supply so ERPNext's default runs instead.
    """
    city_clean = (city or "").lower().strip()
    if not city_clean:
        return None
    return _CITY_TO_SUPPLY.get(city_clean)


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
            "Use the 'View Sales Invoice' button.".format(
                booking.custom_sales_invoice
            )
        )

    patient_name = booking.get("patient_name") or booking.get("patient") or ""
    mobile       = booking.get("whatsapp_number") or ""
    city         = booking.get("city_location") or ""

    # Step 1 — Customer
    customer = _get_or_create_customer(patient_name, mobile)

    # Step 2 — Contact (with mobile)
    _get_or_create_contact(
        customer_name  = customer,
        patient_name   = patient_name,
        mobile_number  = mobile,
        email          = None,
    )

    # Step 3 — Address (with city → state for GST)
    _get_or_create_address(customer_name=customer, city=city)

    # Step 4 — Item
    item_code = _resolve_consultation_item(
        booking.get("speciality") or booking.get("selected_service")
    )

    # Step 5 — Build Sales Invoice
    si = frappe.new_doc("Sales Invoice")
    si.customer     = customer
    si.posting_date = nowdate()
    si.due_date     = nowdate()
    si.company      = frappe.db.get_default("company")
    si.cost_center  = "Main - TPC"

    # place_of_supply set directly so SS11 is satisfied even before
    # the user links a billing address through the form
    pos = _get_place_of_supply(city)
    if pos:
        si.place_of_supply = pos

    si.custom_courier_charges_required = "No"
    si.custom_online__offline          = "Online"

    si.custom_interakt_booking = booking_name
    if booking.get("patient_appointment"):
        si.custom_patient_appointment = booking.patient_appointment
    if booking.get("payment_id"):
        si.custom_razorpay_payment_id = booking.payment_id

    speciality_label = (
        booking.get("speciality") or booking.get("selected_service") or ""
    )
    si.append("items", {
        "item_code":  item_code,
        "item_name":  "Doctor Consultation – {}".format(
            speciality_label
        ).strip(" –"),
        "description": (
            "Service: {}\n"
            "Speciality: {}\n"
            "Date: {} | Slot: {}\n"
            "Appointment: {}\n"
            "Google Meet: {}"
        ).format(
            booking.get("selected_service")    or "",
            booking.get("speciality")          or "",
            booking.get("preferred_date")      or "",
            booking.get("preferred_time_slot") or "",
            booking.get("patient_appointment") or "",
            booking.get("google_meet_link")    or "",
        ),
        "qty":  1,
        "rate": flt(booking.get("payment_amount_inr") or 0),
        "uom":  "Nos",
    })

    si.flags.ignore_permissions = True
    si.flags.ignore_mandatory   = True
    si.insert()

    # Remove zero rows auto-injected by server scripts during validate
    frappe.db.sql(
        "DELETE FROM `tabSales Taxes and Charges` WHERE parent = %s",
        si.name
    )
    frappe.db.sql(
        "DELETE FROM `tabCourier Charge Entry` WHERE parent = %s",
        si.name
    )

    frappe.db.set_value(
        "Interakt Booking", booking_name, "custom_sales_invoice", si.name
    )
    frappe.db.commit()

    return {
        "sales_invoice": si.name,
        "message": "Sales Invoice {} created. Opening now.".format(si.name),
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
            "Use the 'View Sales Invoice' button.".format(
                doc.custom_sales_invoice
            )
        )

    # Extract amount from "Consultation Fee 499" → 499
    amount = 499
    if doc.get("mode_of_payment"):
        match = re.search(r'\d+', doc.mode_of_payment)
        if match:
            amount = int(match.group())

    patient_name = doc.patient_name or ""
    mobile       = doc.get("mobile_number") or ""
    email        = doc.get("email_to") or ""
    city         = doc.get("location") or ""

    # Step 1 — Customer
    customer = _get_or_create_customer(patient_name, mobile)

    # Step 2 — Contact (with mobile + email from DC record)
    _get_or_create_contact(
        customer_name  = customer,
        patient_name   = patient_name,
        mobile_number  = mobile,
        email          = email,
    )

    # Step 3 — Address (with city → state for GST)
    _get_or_create_address(customer_name=customer, city=city)

    # Step 4 — Item
    item_code = _resolve_consultation_item(doc.get("book_specialist"))

    # Step 5 — Build Sales Invoice
    si = frappe.new_doc("Sales Invoice")
    si.customer     = customer
    si.posting_date = nowdate()
    si.due_date     = nowdate()
    si.company      = frappe.db.get_default("company")
    si.cost_center  = "Main - TPC"

    # place_of_supply set directly so SS11 is satisfied even before
    # the user links a billing address through the form
    pos = _get_place_of_supply(city)
    if pos:
        si.place_of_supply = pos

    si.custom_courier_charges_required = "No"
    si.custom_online__offline          = "Online"

    # NOTE: custom_doctor_consultation on Sales Invoice is a Section Break.
    # The actual Link → Doctor Consultation field is
    # custom_website_doctor_consultation.
    si.custom_website_doctor_consultation = doc_name

    specialist_label = doc.get("book_specialist") or ""
    si.append("items", {
        "item_code":  item_code,
        "item_name":  "Doctor Consultation – {}".format(
            specialist_label
        ).strip(" –"),
        "description": (
            "Service: Doctor Consultation\n"
            "Specialist: {}\n"
            "Doctor: {}\n"
            "Date: {} | Slot: {}\n"
            "Mode: {}\n"
            "Google Meet: {}"
        ).format(
            doc.get("book_specialist")      or "",
            doc.get("doctor_name")          or "",
            doc.get("appointment_date")     or "",
            doc.get("time")                 or "",
            doc.get("mode_of_consultation") or "",
            doc.get("g_meet")               or "",
        ),
        "qty":  1,
        "rate": flt(amount),
        "uom":  "Nos",
    })

    si.flags.ignore_permissions = True
    si.flags.ignore_mandatory   = True
    si.insert()

    # Remove zero rows auto-injected by server scripts during validate
    frappe.db.sql(
        "DELETE FROM `tabSales Taxes and Charges` WHERE parent = %s",
        si.name
    )
    frappe.db.sql(
        "DELETE FROM `tabCourier Charge Entry` WHERE parent = %s",
        si.name
    )

    frappe.db.set_value(
        "Doctor Consultation", doc_name, "custom_sales_invoice", si.name
    )
    frappe.db.commit()

    return {
        "sales_invoice": si.name,
        "message": "Sales Invoice {} created. Opening now.".format(si.name),
    }


# ──────────────────────────────────────────────────────────────────
#  SHARED HELPERS
# ──────────────────────────────────────────────────────────────────

def _get_or_create_customer(patient_name, mobile_number=None):
    """Resolve Customer — Patient link first, then name match, then create."""

    if not patient_name:
        frappe.throw("Patient name is required to create a Customer.")

    # 1. Check if a Patient doc already links to a Customer
    if frappe.db.exists("Patient", patient_name):
        patient_customer = frappe.db.get_value(
            "Patient", patient_name, "customer"
        )
        if patient_customer and frappe.db.exists("Customer", patient_customer):
            return patient_customer

    # 2. Direct Customer name match
    if frappe.db.exists("Customer", patient_name):
        return patient_name

    # 3. Create a new Customer
    customer = frappe.new_doc("Customer")
    customer.customer_name  = patient_name
    customer.customer_type  = "Individual"
    customer.customer_group = (
        frappe.db.get_single_value("Selling Settings", "customer_group")
        or "Individual"
    )
    customer.territory = (
        frappe.db.get_single_value("Selling Settings", "territory")
        or "India"
    )
    if mobile_number:
        customer.mobile_no = mobile_number
    customer.flags.ignore_permissions = True
    customer.insert()

    # Link back to Patient record if one exists
    if frappe.db.exists("Patient", patient_name):
        frappe.db.set_value("Patient", patient_name, "customer", customer.name)

    return customer.name


def _get_or_create_contact(customer_name, patient_name,
                            mobile_number=None, email=None):
    """
    Create a Contact linked to the Customer if one does not already exist.
    Skips creation silently if something fails — the SI will still be created.
    """
    try:
        # Check for an existing Contact linked to this Customer
        existing = frappe.db.get_value(
            "Dynamic Link",
            {
                "link_doctype": "Customer",
                "link_name":    customer_name,
                "parenttype":   "Contact",
            },
            "parent",
        )
        if existing:
            return existing

        # Split name into first / last
        name_parts = (patient_name or "").strip().split(" ", 1)
        first_name = name_parts[0]
        last_name  = name_parts[1] if len(name_parts) > 1 else ""

        contact            = frappe.new_doc("Contact")
        contact.first_name = first_name
        contact.last_name  = last_name

        if mobile_number:
            contact.append("phone_nos", {
                "phone":                mobile_number,
                "is_primary_mobile_no": 1,
            })

        if email:
            contact.append("email_ids", {
                "email_id":  email,
                "is_primary": 1,
            })

        contact.append("links", {
            "link_doctype": "Customer",
            "link_name":    customer_name,
        })

        contact.flags.ignore_permissions = True
        contact.flags.ignore_mandatory   = True
        contact.insert()

        return contact.name

    except Exception as e:
        frappe.log_error(
            "Contact creation failed for {}: {}".format(customer_name, str(e)),
            "Interakt Billing – Contact"
        )
        return None


def _get_or_create_address(customer_name, city=None):
    """
    Create a Billing Address linked to the Customer if one does not already
    exist. The state is derived from the city using the GST supply map so
    ERPNext's own address-based GST logic (place_of_supply, gst_category)
    works correctly in all future manual SI creation through the UI.
    Skips creation if city is blank or unrecognised.
    """
    if not city:
        return None

    pos = _get_place_of_supply(city)
    if not pos:
        # City not in map — skip address creation to avoid incorrect state
        return None

    try:
        # Check for an existing Address linked to this Customer
        existing = frappe.db.get_value(
            "Dynamic Link",
            {
                "link_doctype": "Customer",
                "link_name":    customer_name,
                "parenttype":   "Address",
            },
            "parent",
        )
        if existing:
            return existing

        # Derive state name from GST code e.g. "36-Telangana" → "Telangana"
        state = pos.split("-", 1)[1] if "-" in pos else ""

        address                   = frappe.new_doc("Address")
        address.address_title     = customer_name
        address.address_type      = "Billing"
        # address_line1 is mandatory in ERPNext — use city as placeholder
        address.address_line1     = city
        address.city              = city
        address.state             = state
        address.country           = "India"
        address.is_primary_address   = 1
        address.is_shipping_address  = 1

        address.append("links", {
            "link_doctype": "Customer",
            "link_name":    customer_name,
        })

        address.flags.ignore_permissions = True
        address.flags.ignore_mandatory   = True
        address.insert()

        return address.name

    except Exception as e:
        frappe.log_error(
            "Address creation failed for {}: {}".format(customer_name, str(e)),
            "Interakt Billing – Address"
        )
        return None


def _resolve_consultation_item(speciality=None):
    """Find the best-fit service Item, falling back to a generic one."""

    if speciality:
        specific = frappe.db.get_value(
            "Item",
            {
                "item_name":     ["like", "%{}%".format(speciality)],
                "disabled":      0,
                "is_stock_item": 0,
            },
            "name",
        )
        if specific:
            return specific

    generic = frappe.db.get_value(
        "Item",
        {
            "item_name":     ["like", "%Doctor Consultation%"],
            "disabled":      0,
            "is_stock_item": 0,
        },
        "name",
    )
    if generic:
        return generic

    # Auto-create a minimal service item as last resort
    if not frappe.db.exists("Item", "DOCTOR-CONSULTATION"):
        item = frappe.new_doc("Item")
        item.item_code     = "DOCTOR-CONSULTATION"
        item.item_name     = "Doctor Consultation"
        item.item_group    = "Services"
        item.is_stock_item = 0
        item.stock_uom     = "Nos"
        item.flags.ignore_permissions = True
        item.insert()

    return "DOCTOR-CONSULTATION"