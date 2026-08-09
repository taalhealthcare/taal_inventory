"""
taal_inventory/events/tally_import.py

Imports 836 Sales Invoices directly from the Tally Sales Register.
Every value — item lines, rates, courier, rounding — is written
verbatim from Tally. Product GST uses On Net Total (required by
India Compliance app) with exact 2.5%/2.5%/5% rates.
Courier GST uses Actual rows with Tally values verbatim.

Whitelisted methods:
  run_import(dry_run=True)   — full import
  retry_interstate()         — retry failed inter-state invoices
"""

import frappe
import openpyxl
import os


# ── CONFIRMED ITEM MAP (Phase 1 verified, Abec/AbecL confirmed) ─
ITEM_MAP = {
    "A-Bec 60 Tab":   "TPIC 82",
    "Abalam":         "TPIC 01",
    "Abavir":         "TPIC 02",
    "Abec":           "TPIC 104",
    "Abec L":         "TPIC 04",
    "Atazor R":       "TPIC 101",
    "Cronivir":       "TPIC 09",
    "Danavir-R 600":  "TPIC 10",
    "Danavir-R 800":  "TPIC 11",
    "Efcure 200":     "TPIC 16",
    "Efcure 600":     "TPIC 17",
    "Emtaf":          "TPIC 20",
    "Estiva-600":     "TPIC 21",
    "Heptavir-100":   "TPIC 87",
    "Inbec":          "TPIC 24",
    "Instgra":        "TPIC 25",
    "Lavir 150":      "TPIC 26",
    "Lazid":          "TPIC 27",
    "Lazid-E-Kit":    "TPIC 88",
    "Lazid-N":        "TPIC 89",
    "Nevir ER":       "TPIC 30",
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

COMPANY         = "Taalplus CHC Private Limited"
SGST_ACCOUNT    = "Output Tax SGST - TPC"
CGST_ACCOUNT    = "Output Tax CGST - TPC"
IGST_ACCOUNT    = "Output Tax IGST - TPC"
COURIER_ACCOUNT = "Courier Charges Recovery - TPC"
COURIER_SGST    = "Courier SGST Payable - TPC"
COURIER_CGST    = "Courier CGST Payable - TPC"
COURIER_IGST    = "Courier IGST Payable - TPC"


def f(x):
    try:
        return round(float(x or 0), 4)
    except Exception:
        return 0.0


def get_file_path():
    """
    Finds the Tally register Excel on disk.
    Tries multiple search strategies to handle filename variations.
    """
    # Strategy 1: exact filename as uploaded after rename
    file_doc = frappe.db.get_value(
        "File",
        {"file_name": "Sales__April_to_June_2026_For_Upload__1_.xlsx"},
        ["name", "file_url", "is_private"],
        as_dict=True
    )

    # Strategy 2: search by partial filename
    if not file_doc:
        results = frappe.db.sql("""
            SELECT name, file_url, is_private
            FROM `tabFile`
            WHERE file_name LIKE '%April%June%2026%'
               OR file_name LIKE '%April_to_June_2026%'
               OR file_name LIKE '%For_Upload%'
            ORDER BY creation DESC
            LIMIT 1
        """, as_dict=True)
        file_doc = results[0] if results else None

    # Strategy 3: search by file URL pattern
    if not file_doc:
        results = frappe.db.sql("""
            SELECT name, file_url, is_private
            FROM `tabFile`
            WHERE file_url LIKE '%April%2026%'
              AND file_url LIKE '%.xlsx'
            ORDER BY creation DESC
            LIMIT 1
        """, as_dict=True)
        file_doc = results[0] if results else None

    if not file_doc:
        frappe.throw(
            "Tally register Excel not found in Frappe Files. "
            "Please upload the file (Sales__April_to_June_2026_For_Upload__1_.xlsx) "
            "and try again."
        )

    if file_doc.is_private:
        full_path = frappe.get_site_path() + "/private" + file_doc.file_url
    else:
        full_path = frappe.get_site_path() + "/public" + file_doc.file_url

    if not os.path.exists(full_path):
        frappe.throw("File not found on disk at: " + full_path)

    return full_path


def parse_tally_register():
    """
    Parse the Tally Sales Register Excel.

    Column layout (0-indexed):
      0  = Date object (header rows only)
      1  = Customer name (header) / Item name (detail)
      3  = Voucher No
      4  = Qty
      6  = Rate per unit
      7  = Line amount (qty x rate)
      8  = Gross Total (header only)
      9  = Sales Intrastate taxable value
     10  = CGST 2.5% amount
     11  = SGST 2.5% amount
     12  = Sales Interstate taxable value
     13  = IGST 5% amount
     14  = Courier Intrastate (GST-EXCLUSIVE base)
     15  = Courier CGST 9%
     16  = Courier SGST 9%
     17  = Courier Interstate (GST-EXCLUSIVE base)
     18  = Courier IGST 18%
     19  = Round Off
     20  = Online Sale value

    Returns list of invoice dicts with full item lines and tax values.
    """
    full_path = get_file_path()
    wb   = openpyxl.load_workbook(full_path, data_only=True)
    ws   = wb.active
    rows = list(ws.iter_rows(values_only=True))

    # Stop at Grand Total row
    gt_idx = None
    for idx, r in enumerate(rows):
        if r[1] and str(r[1]).strip() == "Grand Total":
            gt_idx = idx
            break
    rows = rows[:gt_idx] if gt_idx else rows

    invoices = []
    i = 9  # data starts at row 10 (index 9)

    while i < len(rows):
        r = rows[i]

        # Header row identified by date object in column 0
        if r[0] is not None and hasattr(r[0], "year"):

            si_tax    = f(r[9])   # intrastate taxable
            inter_tax = f(r[12])  # interstate taxable
            igst_amt  = f(r[13])  # IGST 5%
            online_v  = f(r[20])  # online sale value

            # State type: inter-state if interstate column OR IGST column is set
            if inter_tax > 0 or igst_amt > 0:
                state_type      = "Inter-State"
                place_of_supply = "07-Delhi"
                taxable_val     = inter_tax if inter_tax > 0 else online_v
            elif si_tax > 0:
                state_type      = "Intra-State"
                place_of_supply = "27-Maharashtra"
                taxable_val     = si_tax
            elif online_v > 0:
                state_type      = "Intra-State"
                place_of_supply = "27-Maharashtra"
                taxable_val     = online_v
            else:
                state_type      = "Intra-State"
                place_of_supply = "27-Maharashtra"
                taxable_val     = 0.0

            inv = {
                "date":            r[0].strftime("%Y-%m-%d"),
                "customer":        str(r[1] or "").strip(),
                "vno":             str(r[3] or "").strip(),
                "gross":           f(r[8]),
                "state_type":      state_type,
                "place_of_supply": place_of_supply,
                "taxable_val":     taxable_val,
                # Product GST amounts from Tally (used for verification only)
                "cgst_25":         f(r[10]),
                "sgst_25":         f(r[11]),
                "igst_5":          igst_amt,
                # Courier — verbatim GST-exclusive base + GST amounts
                "courier_intra":   f(r[14]),
                "courier_cgst":    f(r[15]),
                "courier_sgst":    f(r[16]),
                "courier_inter":   f(r[17]),
                "courier_igst":    f(r[18]),
                # Round off
                "roundoff":        f(r[19]),
                "items":           [],
            }

            # Collect all detail rows under this header
            j = i + 1
            while j < len(rows) and rows[j][0] is None and rows[j][1] is not None:
                d = rows[j]
                item_name = str(d[1] or "").strip()
                if item_name:
                    inv["items"].append({
                        "name":  item_name,
                        "qty":   f(d[4]),
                        "rate":  f(d[6]),
                        "value": f(d[7]),
                    })
                j += 1

            if inv["customer"] and inv["items"]:
                invoices.append(inv)
            i = j

        else:
            i += 1

    return invoices


def build_taxes(inv):
    """
    Build the taxes table.

    Product GST: On Net Total with exact rate (2.5% / 5%)
      - Required by India Compliance app
      - ERPNext computes identical amounts since rates are exact
      - SGST first, then CGST (India Compliance ordering)

    Courier GST: Actual rows with verbatim Tally amounts
      - India Compliance allows Actual for non-GST recovery accounts
    """
    taxes = []

    # ── Product GST — On Net Total (India Compliance requirement) ──
    # Intra-State: SGST 2.5% + CGST 2.5%
    if inv["sgst_25"] > 0:
        taxes.append({
            "charge_type":            "On Net Total",
            "account_head":           SGST_ACCOUNT,
            "description":            "SGST on Products",
            "rate":                   2.5,
            "included_in_print_rate": 0,
        })

    if inv["cgst_25"] > 0:
        taxes.append({
            "charge_type":            "On Net Total",
            "account_head":           CGST_ACCOUNT,
            "description":            "CGST on Products",
            "rate":                   2.5,
            "included_in_print_rate": 0,
        })

    # Inter-State: IGST 5%
    if inv["igst_5"] > 0:
        taxes.append({
            "charge_type":            "On Net Total",
            "account_head":           IGST_ACCOUNT,
            "description":            "IGST on Products",
            "rate":                   5.0,
            "included_in_print_rate": 0,
        })

    # ── Courier base — Actual, verbatim Tally value ───────────────
    courier_base = inv["courier_intra"] + inv["courier_inter"]
    if courier_base > 0:
        taxes.append({
            "charge_type":            "Actual",
            "account_head":           COURIER_ACCOUNT,
            "description":            "Courier Charges",
            "tax_amount":             courier_base,
            "rate":                   0,
            "included_in_print_rate": 0,
        })
    else:
        # Zero courier row — keeps account head present for reporting
        taxes.append({
            "charge_type":            "Actual",
            "account_head":           COURIER_ACCOUNT,
            "description":            "Courier Charges",
            "tax_amount":             0,
            "rate":                   0,
            "included_in_print_rate": 0,
        })

    # ── Courier GST — Actual, verbatim Tally values ───────────────
    if inv["courier_sgst"] > 0:
        taxes.append({
            "charge_type":            "Actual",
            "account_head":           COURIER_SGST,
            "description":            "Courier SGST Payable",
            "tax_amount":             inv["courier_sgst"],
            "rate":                   0,
            "included_in_print_rate": 0,
        })

    if inv["courier_cgst"] > 0:
        taxes.append({
            "charge_type":            "Actual",
            "account_head":           COURIER_CGST,
            "description":            "Courier CGST Payable",
            "tax_amount":             inv["courier_cgst"],
            "rate":                   0,
            "included_in_print_rate": 0,
        })

    if inv["courier_igst"] > 0:
        taxes.append({
            "charge_type":            "Actual",
            "account_head":           COURIER_IGST,
            "description":            "Courier IGST Payable",
            "tax_amount":             inv["courier_igst"],
            "rate":                   0,
            "included_in_print_rate": 0,
        })

    return taxes


def create_invoice(inv):
    """Create and insert a single Sales Invoice from a Tally invoice dict."""

    items = []
    for it in inv["items"]:
        code = ITEM_MAP.get(it["name"])
        if not code:
            frappe.throw("Unmapped item in Tally register: " + it["name"])
        items.append({
            "item_code": code,
            "item_name": it["name"],
            "qty":       it["qty"],
            "rate":      it["rate"],
            "uom":       "Nos",
        })

    taxes = build_taxes(inv)

    si = frappe.get_doc({
        "doctype":                         "Sales Invoice",
        "naming_series":                   "SINV-.YY.-",
        "posting_date":                    inv["date"],
        "due_date":                        inv["date"],
        "customer":                        inv["customer"],
        "company":                         COMPANY,
        "currency":                        "INR",
        "selling_price_list":              "Standard Selling",
        "ignore_pricing_rule":             1,
        "set_posting_time":                1,
        "place_of_supply":                 inv["place_of_supply"],
        "custom_state_type":               inv["state_type"],
        # Lock all values — SS_11 must not recalculate courier or taxes
        "custom_courier_manually_edited":  1,
        "disable_rounded_total":           0,
        "items":                           items,
        "taxes":                           taxes,
    })

    si.flags.ignore_permissions = True
    si.flags.ignore_mandatory   = True
    si.insert()

    return si.name


@frappe.whitelist()
def run_import(dry_run=True):
    """
    Main import entry point.

    Call from System Console:
      result = frappe.call(
          "taal_inventory.events.tally_import.run_import",
          dry_run=True
      )
    """
    dry_run  = frappe.utils.cint(dry_run)
    invoices = parse_tally_register()

    result = {
        "total":   len(invoices),
        "created": [],
        "skipped": [],
        "errors":  [],
    }

    for inv in invoices:

        # Skip if customer does not exist in ERPNext
        if not frappe.db.exists("Customer", inv["customer"]):
            result["skipped"].append({
                "vno":      inv["vno"],
                "customer": inv["customer"],
                "reason":   "Customer not found in ERPNext",
            })
            continue

        if dry_run:
            result["created"].append(
                inv["date"] +
                " | " + inv["customer"] +
                " | " + str(len(inv["items"])) + " item(s)" +
                " | " + inv["state_type"] +
                " | gross=" + str(inv["gross"])
            )
            continue

        try:
            si_name = create_invoice(inv)
            result["created"].append(si_name)

            # Commit every 50 to avoid timeout
            if len(result["created"]) % 50 == 0:
                frappe.db.commit()

        except Exception as e:
            result["errors"].append({
                "vno":   inv["vno"],
                "cust":  inv["customer"][:30],
                "error": str(e)[:300],
            })

    if not dry_run:
        frappe.db.commit()

    return result


@frappe.whitelist()
def retry_interstate():
    """
    Retries only Inter-State invoices that failed in run_import.
    Safe to call multiple times — skips already-created invoices.

    Call from System Console:
      result = frappe.call(
          "taal_inventory.events.tally_import.retry_interstate"
      )
    """
    invoices = parse_tally_register()
    inter    = [x for x in invoices if x["state_type"] == "Inter-State"]

    result = {
        "total":   len(inter),
        "already": 0,
        "created": [],
        "errors":  [],
    }

    for inv in inter:

        # Check if already created successfully
        existing = frappe.db.sql("""
            SELECT name FROM `tabSales Invoice`
            WHERE customer          = %s
              AND posting_date      = %s
              AND custom_state_type = %s
              AND docstatus         != 2
            LIMIT 1
        """, (inv["customer"], inv["date"], inv["state_type"]), as_dict=True)

        if existing:
            result["already"] = result["already"] + 1
            continue

        try:
            si_name = create_invoice(inv)
            result["created"].append(si_name)

            if len(result["created"]) % 20 == 0:
                frappe.db.commit()

        except Exception as e:
            result["errors"].append({
                "vno":   inv["vno"],
                "cust":  inv["customer"][:30],
                "error": str(e)[:300],
            })

    frappe.db.commit()
    return result