"""
taal_inventory/events/bulk_import.py
Bulk Sales Invoice import from Excel.
Called once from ERPNext System Console.
"""

import frappe
import openpyxl
import os


ITEM_MAP = {
    "A-Bec 60 Tab":   "TPIC 82",
    "Abalam":         "TPIC 01",
    "Abavir":         "TPIC 02",
    "Abec":           "TPIC 04",
    "Abec L":         "TPIC 04",
    "Atazor R":       "TPIC 101",
    "Cronivir":       "TPIC 09",
    "Danavir-R 600":  "TPIC 10",
    "Danavir-R 800":  "TPIC 11",
    "Efcure 600":     "TPIC 17",
    "Emtaf":          "TPIC 20",
    "Estiva-600":     "TPIC 21",
    "Heptavir-100":   "TPIC 87",
    "Inbec":          "TPIC 24",
    "Lavir 150":      "TPIC 26",
    "Lazid":          "TPIC 27",
    "Lazid-E-Kit":    "TPIC 88",
    "Lazid-N":        "TPIC 89",
    "Nevivir":        "TPIC 31",
    "Reatix Kit":     "TPIC 32",
    "Spegra":         "TPIC 67",
    "Spegra-90":      "TPIC 34",
    "Tafero-EM-10":   "TPIC 91",
    "Tafero-EM-25":   "TPIC 92",
    "Taffero":        "TPIC 93",
    "Taffic":         "TPIC 38",
    "Taftrio":        "TPIC 40",
    "Tavin":          "TPIC 41",
    "Tavin-EM":       "TPIC 64",
    "Tavin-L":        "TPIC 43",
    "Tenof-EM":       "TPIC 65",
    "Tenolam":        "TPIC 66",
    "Trustiva":       "TPIC 47",
    "Twinaqt":        "TPIC 48",
    "VM 21":          "TPIC 102",
    "Vianca":         "TPIC 50",
    "Virataz-R":      "TPIC 95",
    "Virem-R":        "TPIC 96",
    "Viropil":        "TPIC 69",
    "Viropil-90":     "TPIC 56",
    "Vonaday":        "TPIC 57",
    "Vonavir":        "TPIC 58",
    "Zidine-300":     "TPIC 98",
    "Zidolam":        "TPIC 60",
    "Zidolam-N":      "TPIC 61",
}


def to_f(val):
    try:
        return float(val or 0)
    except Exception:
        return 0.0


@frappe.whitelist()
def run_import(dry_run=True):
    """
    Main entry point. Call from System Console:
    frappe.call("taal_inventory.events.bulk_import.run_import", dry_run=True)
    """
    dry_run = frappe.utils.cint(dry_run)

    result = {
        "dry_run":             bool(dry_run),
        "total_rows":          0,
        "total_invoices":      0,
        "created":             [],
        "skipped":             [],
        "errors":              [],
        "missing_customers":   [],
        "unmapped_items":      [],
    }

    # ── Find uploaded Excel file ──────────────────────────────
    file_doc = frappe.db.get_value(
        "File",
        {"file_name": "Sales_Apr_Jun_2026_FIXED_Import.xlsx"},
        ["name", "file_url", "is_private"],
        as_dict=True
    )

    if not file_doc:
        frappe.throw(
            "Excel file not found in Frappe Files. "
            "Upload Sales_Apr_Jun_2026_FIXED_Import.xlsx first."
        )

    if file_doc.is_private:
        full_path = frappe.get_site_path() + "/private" + file_doc.file_url
    else:
        full_path = frappe.get_site_path() + "/public" + file_doc.file_url

    if not os.path.exists(full_path):
        frappe.throw("File not found on disk: " + full_path)

    # ── Read Excel ────────────────────────────────────────────
    wb   = openpyxl.load_workbook(full_path, data_only=True)
    ws   = wb.active
    rows = list(ws.iter_rows(values_only=True))
    data = rows[1:]

    result["total_rows"] = len(data)

    # ── Group rows into invoices ──────────────────────────────
    invoices = {}

    for i, row in enumerate(data):
        if not row or not row[0]:
            continue

        posting_date   = str(row[1])[:10] if row[1] else ""
        due_date       = str(row[2])[:10] if row[2] else posting_date
        customer       = str(row[3]  or "").strip()
        online_offline = str(row[12] or "").strip()
        state_type     = str(row[13] or "").strip()
        location       = str(row[14] or "").strip()
        courier_req    = str(row[15] or "No").strip()
        item_name      = str(row[17] or "").strip()
        qty            = to_f(row[18])
        rate           = to_f(row[19])

        if not customer or not posting_date or not item_name:
            continue

        item_code = ITEM_MAP.get(item_name)
        if not item_code:
            result["unmapped_items"].append(
                "Row " + str(i + 2) + ": " + item_name
            )
            continue

        inv_key = (
            posting_date + "||" +
            customer     + "||" +
            state_type   + "||" +
            location
        )

        if inv_key not in invoices:
            invoices[inv_key] = {
                "posting_date":   posting_date,
                "due_date":       due_date,
                "customer":       customer,
                "online_offline": online_offline,
                "state_type":     state_type,
                "location":       location,
                "courier_req":    courier_req,
                "items":          []
            }

        invoices[inv_key]["items"].append({
            "item_code": item_code,
            "item_name": item_name,
            "qty":       qty,
            "rate":      rate,
        })

    result["total_invoices"] = len(invoices)

    # ── Check customers ───────────────────────────────────────
    all_customers = list(set(
        v["customer"] for v in invoices.values()
    ))
    for cust in all_customers:
        if not frappe.db.exists("Customer", cust):
            result["missing_customers"].append(cust)

    # ── Create invoices ───────────────────────────────────────
    for inv_key, inv_data in invoices.items():

        if inv_data["customer"] in result["missing_customers"]:
            result["skipped"].append({
                "customer": inv_data["customer"],
                "date":     inv_data["posting_date"],
                "reason":   "Customer not in ERPNext"
            })
            continue

        if dry_run:
            result["created"].append(
                inv_data["posting_date"] +
                " | " + inv_data["customer"] +
                " | " + str(len(inv_data["items"])) + " item(s)"
            )
            continue

        try:
            si = frappe.get_doc({
                "doctype":                         "Sales Invoice",
                "naming_series":                   "SINV-.YY.-",
                "posting_date":                    inv_data["posting_date"],
                "due_date":                        inv_data["due_date"],
                "customer":                        inv_data["customer"],
                "company":                         "Taalplus CHC Private Limited",
                "currency":                        "INR",
                "selling_price_list":              "Standard Selling",
                "ignore_pricing_rule":             1,
                "set_posting_time":                1,
                "custom_online__offline":          inv_data["online_offline"],
                "custom_state_type":               inv_data["state_type"],
                "custom_location":                 inv_data["location"],
                "custom_courier_charges_required": inv_data["courier_req"],
                "custom_courier_manually_edited":  1,
                "items": [
                    {
                        "item_code": item["item_code"],
                        "item_name": item["item_name"],
                        "qty":       item["qty"],
                        "rate":      item["rate"],
                        "uom":       "Nos",
                    }
                    for item in inv_data["items"]
                ]
            })
            si.flags.ignore_permissions = True
            si.flags.ignore_mandatory   = True
            si.insert()
            result["created"].append(si.name)

            if len(result["created"]) % 50 == 0:
                frappe.db.commit()

        except Exception as e:
            result["errors"].append({
                "key":   inv_key[:80],
                "error": str(e)[:300]
            })

    if not dry_run:
        frappe.db.commit()

    return result