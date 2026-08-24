# ================================================================
#  Sales & GST Dashboard  —  data layer
#  App    : taal_inventory
#  Path   : taal_inventory/utils/gst_sales_dashboard.py
#  Method : taal_inventory.utils.gst_sales_dashboard.get_gst_dashboard_data
#
#  Segments (mutually exclusive, they sum to Total):
#      online_intra   Online  + Intra-State
#      online_inter   Online  + Inter-State
#      offline_intra  Offline + Intra-State
#      offline_inter  Offline + Inter-State
#      international  custom_state_type = International   (conditional)
#      unclassified   anything that matches none of the above (conditional)
#
#  Grain rules:
#      item-level     net_amount, cgst/sgst/igst_amount   -> sum every item row
#      invoice-level  rounded_total, rounding_adjustment,
#                     outstanding_amount, grand_total     -> one row per invoice
#      courier-level  Courier Charge Entry                -> may be several rows
#                                                            per invoice, sum them
#  Because each grain is queried separately and merged on (segment, month),
#  there is no row-explosion and no dedup pass is needed.
# ================================================================

import frappe
from frappe.utils import (
    today, add_days, add_months, getdate, flt, cint,
    get_first_day, get_last_day, formatdate,
)


# ── Segment precedence.  Reorder / edit these two lists to change the model. ──
_SEG_ORDER = [
    "online_intra",
    "online_inter",
    "offline_intra",
    "offline_inter",
    "international",
    "unclassified",
]

_SEG_LABEL = {
    "online_intra":  "Online — Intra State",
    "online_inter":  "Online — Inter State",
    "offline_intra": "Offline — Intra State",
    "offline_inter": "Offline — Inter State",
    "international": "International",
    "unclassified":  "Unclassified",
}

# Segments that are only rendered when they actually contain invoices.
_SEG_CONDITIONAL = ("international", "unclassified")

# The CASE expression is written once and reused by all three grain queries so
# an invoice can never land in one segment for sales and another for courier.
_SEG_CASE = """
CASE
    WHEN si.custom_state_type = 'International' THEN 'international'
    WHEN si.custom_online__offline = 'Online'
         AND si.custom_state_type = 'Intra-State' THEN 'online_intra'
    WHEN si.custom_online__offline = 'Online'
         AND si.custom_state_type = 'Inter-State' THEN 'online_inter'
    WHEN si.custom_state_type = 'Intra-State' THEN 'offline_intra'
    WHEN si.custom_state_type = 'Inter-State' THEN 'offline_inter'
    ELSE 'unclassified'
END
"""

_SK = "YEAR(si.posting_date) * 100 + MONTH(si.posting_date)"

_MONTHS = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

# Recipients of the scheduled monthly mail.  Edit this list, or leave it empty
# and the job falls back to every enabled user holding "Accounts Manager".
_EMAIL_RECIPIENTS = []


# ================================================================
#  Small helpers
# ================================================================

def _f(v):
    return flt(v or 0, 2)


def _i(v):
    return cint(v or 0)


def _mo_label(sk):
    sk = _i(sk)
    try:
        return _MONTHS[sk % 100] + " " + str(sk // 100)
    except Exception:
        return str(sk)


def _blank_cell():
    return {
        "sales": 0.0, "cgst": 0.0, "sgst": 0.0, "igst": 0.0,
        "courier": 0.0, "c_cgst": 0.0, "c_sgst": 0.0, "c_igst": 0.0,
        "roff": 0.0, "total": 0.0, "grand": 0.0,
        "outstanding": 0.0, "inv_count": 0,
    }


def _add_cell(a, b):
    out = {}
    for k in a:
        out[k] = a[k] + b.get(k, 0)
    return out


def _base_conditions(company, from_date, to_date, include_drafts):
    """Returns (sql_fragment, params).  Applies to every query in this module."""
    conds = ["si.posting_date BETWEEN %s AND %s"]
    params = [from_date, to_date]

    if include_drafts:
        conds.append("si.docstatus IN (0, 1)")
    else:
        conds.append("si.docstatus = 1")

    if company and company != "__all__":
        conds.append("si.company = %s")
        params.append(company)

    return " AND ".join(conds), params


def _gross_only():
    """Excludes credit notes and debit notes from the gross figures."""
    return " AND si.is_return = 0 AND si.is_debit_note = 0"


# ================================================================
#  Matrix builder — reused for the live period and both comparisons
# ================================================================

def _build_matrix(company, from_date, to_date, include_drafts):
    where, params = _base_conditions(company, from_date, to_date, include_drafts)
    where += _gross_only()

    cells = {}          # (segment, sk) -> cell dict
    seen_sk = set()
    seen_seg = set()

    def cell(seg, sk):
        key = (seg, _i(sk))
        if key not in cells:
            cells[key] = _blank_cell()
            seen_sk.add(_i(sk))
            seen_seg.add(seg)
        return cells[key]

    # ── Grain 1 — item level ─────────────────────────────────────
    item_rows = frappe.db.sql("""
        SELECT {seg} AS segment,
               {sk}  AS sk,
               COALESCE(SUM(sii.net_amount),   0) AS sales,
               COALESCE(SUM(sii.cgst_amount),  0) AS cgst,
               COALESCE(SUM(sii.sgst_amount),  0) AS sgst,
               COALESCE(SUM(sii.igst_amount),  0) AS igst
        FROM `tabSales Invoice Item` sii
        JOIN `tabSales Invoice` si ON si.name = sii.parent
        WHERE {where}
        GROUP BY segment, sk
    """.format(seg=_SEG_CASE, sk=_SK, where=where), params, as_dict=True)

    for r in item_rows:
        c = cell(r.segment, r.sk)
        c["sales"] += _f(r.sales)
        c["cgst"]  += _f(r.cgst)
        c["sgst"]  += _f(r.sgst)
        c["igst"]  += _f(r.igst)

    # ── Grain 2 — invoice level ──────────────────────────────────
    inv_rows = frappe.db.sql("""
        SELECT {seg} AS segment,
               {sk}  AS sk,
               COUNT(*)                                     AS inv_count,
               COALESCE(SUM(si.rounding_adjustment), 0)     AS roff,
               COALESCE(SUM(si.rounded_total),       0)     AS rounded,
               COALESCE(SUM(si.grand_total),         0)     AS grand,
               COALESCE(SUM(si.outstanding_amount),  0)     AS outstanding
        FROM `tabSales Invoice` si
        WHERE {where}
        GROUP BY segment, sk
    """.format(seg=_SEG_CASE, sk=_SK, where=where), params, as_dict=True)

    for r in inv_rows:
        c = cell(r.segment, r.sk)
        c["roff"]        += _f(r.roff)
        c["grand"]       += _f(r.grand)
        c["outstanding"] += _f(r.outstanding)
        c["inv_count"]   += _i(r.inv_count)
        # rounded_total is 0 when the invoice has rounding disabled
        c["total"]       += _f(r.rounded) or _f(r.grand)

    # ── Grain 3 — courier level ──────────────────────────────────
    cc_rows = frappe.db.sql("""
        SELECT {seg} AS segment,
               {sk}  AS sk,
               COALESCE(SUM(cce.courier_charges), 0) AS courier,
               COALESCE(SUM(cce.cgst),            0) AS c_cgst,
               COALESCE(SUM(cce.sgst),            0) AS c_sgst,
               COALESCE(SUM(cce.igst),            0) AS c_igst
        FROM `tabCourier Charge Entry` cce
        JOIN `tabSales Invoice` si ON si.name = cce.parent
        WHERE cce.parenttype = 'Sales Invoice' AND {where}
        GROUP BY segment, sk
    """.format(seg=_SEG_CASE, sk=_SK, where=where), params, as_dict=True)

    for r in cc_rows:
        c = cell(r.segment, r.sk)
        c["courier"] += _f(r.courier)
        c["c_cgst"]  += _f(r.c_cgst)
        c["c_sgst"]  += _f(r.c_sgst)
        c["c_igst"]  += _f(r.c_igst)

    # ── Shape it ─────────────────────────────────────────────────
    months = sorted(seen_sk)          # sk is chronological, which is FY order
    segments = []

    for seg in _SEG_ORDER:
        if seg in _SEG_CONDITIONAL and seg not in seen_seg:
            continue
        if seg not in seen_seg:
            continue

        rows, subtotal = [], _blank_cell()
        for sk in months:
            c = cells.get((seg, sk))
            if not c:
                continue
            row = dict(c)
            row["sk"] = sk
            row["month"] = _mo_label(sk)
            rows.append(row)
            subtotal = _add_cell(subtotal, c)

        if not rows:
            continue

        segments.append({
            "key":      seg,
            "label":    _SEG_LABEL.get(seg, seg),
            "months":   rows,
            "subtotal": subtotal,
        })

    # Total block — every segment added together, month by month
    total_rows, grand = [], _blank_cell()
    for sk in months:
        acc = _blank_cell()
        for seg in seen_seg:
            c = cells.get((seg, sk))
            if c:
                acc = _add_cell(acc, c)
        row = dict(acc)
        row["sk"] = sk
        row["month"] = _mo_label(sk)
        total_rows.append(row)
        grand = _add_cell(grand, acc)

    return {
        "segments":     segments,
        "total_months": total_rows,
        "grand":        grand,
        "months":       [{"sk": sk, "label": _mo_label(sk)} for sk in months],
    }


# ================================================================
#  KPI cards  (value + the hover breakdown behind it)
# ================================================================

def _kpis(company, from_date, to_date, include_drafts, matrix):
    g = matrix["grand"]

    product_gst = g["cgst"] + g["sgst"] + g["igst"]
    courier_gst = g["c_cgst"] + g["c_sgst"] + g["c_igst"]

    where, params = _base_conditions(company, from_date, to_date, include_drafts)

    doc_mix = frappe.db.sql("""
        SELECT
            SUM(CASE WHEN si.is_return = 0 AND si.is_debit_note = 0 THEN 1 ELSE 0 END) AS tax_invoice,
            SUM(CASE WHEN si.is_return = 1 THEN 1 ELSE 0 END)          AS credit_note,
            SUM(CASE WHEN si.is_debit_note = 1 THEN 1 ELSE 0 END)      AS debit_note,
            SUM(CASE WHEN si.is_pos = 1 THEN 1 ELSE 0 END)             AS pos,
            SUM(CASE WHEN si.amended_from IS NOT NULL
                      AND si.amended_from != '' THEN 1 ELSE 0 END)     AS amended,
            SUM(CASE WHEN si.docstatus = 0 THEN 1 ELSE 0 END)          AS draft
        FROM `tabSales Invoice` si
        WHERE {where}
    """.format(where=where), params, as_dict=True)
    dm = doc_mix[0] if doc_mix else {}

    # Credit / debit notes carry negative amounts natively.
    notes = frappe.db.sql("""
        SELECT
            SUM(CASE WHEN si.is_return = 1 THEN 1 ELSE 0 END)                       AS cn_count,
            COALESCE(SUM(CASE WHEN si.is_return = 1
                         THEN si.rounded_total ELSE 0 END), 0)                      AS cn_value,
            SUM(CASE WHEN si.is_debit_note = 1 THEN 1 ELSE 0 END)                   AS dn_count,
            COALESCE(SUM(CASE WHEN si.is_debit_note = 1
                         THEN si.rounded_total ELSE 0 END), 0)                      AS dn_value
        FROM `tabSales Invoice` si
        WHERE {where}
    """.format(where=where), params, as_dict=True)
    nt = notes[0] if notes else {}

    cn_gst = frappe.db.sql("""
        SELECT COALESCE(SUM(sii.cgst_amount + sii.sgst_amount + sii.igst_amount), 0) AS gst
        FROM `tabSales Invoice Item` sii
        JOIN `tabSales Invoice` si ON si.name = sii.parent
        WHERE {where} AND si.is_return = 1
    """.format(where=where), params, as_dict=True)

    roff_split = frappe.db.sql("""
        SELECT COALESCE(SUM(CASE WHEN si.rounding_adjustment > 0
                     THEN si.rounding_adjustment ELSE 0 END), 0) AS pos_drift,
               COALESCE(SUM(CASE WHEN si.rounding_adjustment < 0
                     THEN si.rounding_adjustment ELSE 0 END), 0) AS neg_drift
        FROM `tabSales Invoice` si
        WHERE {where}
    """.format(where=where), params, as_dict=True)
    rs = roff_split[0] if roff_split else {}

    aging = _aging(company, from_date, to_date, include_drafts)

    courier_inv = _i(frappe.db.sql("""
        SELECT COUNT(DISTINCT cce.parent)
        FROM `tabCourier Charge Entry` cce
        JOIN `tabSales Invoice` si ON si.name = cce.parent
        WHERE cce.parenttype = 'Sales Invoice'
          AND cce.courier_charges > 0 AND {where}
    """.format(where=where), params)[0][0])

    seg_sales = {}
    for s in matrix["segments"]:
        seg_sales[s["key"]] = {"label": s["label"], "value": s["subtotal"]["sales"]}

    return {
        "inv_count": {
            "value": g["inv_count"],
            "breakdown": [
                {"label": "Tax invoices", "value": _i(dm.get("tax_invoice"))},
                {"label": "Credit notes", "value": _i(dm.get("credit_note"))},
                {"label": "Debit notes",  "value": _i(dm.get("debit_note"))},
                {"label": "POS invoices", "value": _i(dm.get("pos"))},
                {"label": "Amended",      "value": _i(dm.get("amended"))},
            ],
            "note": ("Drafts included" if include_drafts
                     else "%d drafts excluded" % _i(dm.get("draft"))),
            "is_count": True,
        },
        "net_sales": {
            "value": g["sales"],
            "breakdown": [{"label": v["label"], "value": v["value"]}
                          for v in seg_sales.values()],
            "note": "Item-level net_amount",
        },
        "product_gst": {
            "value": product_gst,
            "breakdown": [
                {"label": "CGST", "value": g["cgst"]},
                {"label": "SGST", "value": g["sgst"]},
                {"label": "IGST", "value": g["igst"]},
            ],
            "note": "Item-level, excludes courier GST",
        },
        "courier_all": {
            "value": g["courier"] + courier_gst,
            "breakdown": [
                {"label": "Courier charges", "value": g["courier"]},
                {"label": "Courier CGST",    "value": g["c_cgst"]},
                {"label": "Courier SGST",    "value": g["c_sgst"]},
                {"label": "Courier IGST",    "value": g["c_igst"]},
            ],
            "note": "%s invoices carry courier" % courier_inv,
        },
        "credit_notes": {
            "value": _f(nt.get("cn_value")),
            "breakdown": [
                {"label": "Credit notes",  "value": _i(nt.get("cn_count")), "is_count": True},
                {"label": "GST reversed",  "value": _f(cn_gst[0].gst) if cn_gst else 0},
                {"label": "Debit notes",   "value": _i(nt.get("dn_count")), "is_count": True},
                {"label": "Debit value",   "value": _f(nt.get("dn_value"))},
            ],
            "note": "Excluded from the matrix above",
        },
        "round_off": {
            "value": g["roff"],
            "breakdown": [
                {"label": "Positive drift", "value": _f(rs.get("pos_drift"))},
                {"label": "Negative drift", "value": _f(rs.get("neg_drift"))},
            ],
            "note": "rounding_adjustment",
        },
        "grand_total": {
            "value": g["total"],
            "breakdown": [
                {"label": "Net sales",       "value": g["sales"]},
                {"label": "Product GST",     "value": product_gst},
                {"label": "Courier charges", "value": g["courier"]},
                {"label": "Courier GST",     "value": courier_gst},
                {"label": "Round off",       "value": g["roff"]},
                {"label": "Less outstanding", "value": -g["outstanding"]},
                {"label": "Collected",       "value": g["total"] - g["outstanding"]},
            ],
            "note": "Sum of rounded_total",
        },
        "outstanding": {
            "value": g["outstanding"],
            "breakdown": [{"label": b["label"], "value": b["value"]} for b in aging],
            "note": "Aged on due date",
        },
    }


def _aging(company, from_date, to_date, include_drafts):
    where, params = _base_conditions(company, from_date, to_date, include_drafts)
    rows = frappe.db.sql("""
        SELECT
            COALESCE(SUM(CASE WHEN DATEDIFF(CURDATE(), si.due_date) <= 0
                    THEN si.outstanding_amount ELSE 0 END), 0) AS not_due,
            COALESCE(SUM(CASE WHEN DATEDIFF(CURDATE(), si.due_date) BETWEEN 1 AND 30
                    THEN si.outstanding_amount ELSE 0 END), 0) AS d30,
            COALESCE(SUM(CASE WHEN DATEDIFF(CURDATE(), si.due_date) BETWEEN 31 AND 60
                    THEN si.outstanding_amount ELSE 0 END), 0) AS d60,
            COALESCE(SUM(CASE WHEN DATEDIFF(CURDATE(), si.due_date) BETWEEN 61 AND 90
                    THEN si.outstanding_amount ELSE 0 END), 0) AS d90,
            COALESCE(SUM(CASE WHEN DATEDIFF(CURDATE(), si.due_date) > 90
                    THEN si.outstanding_amount ELSE 0 END), 0) AS d90p
        FROM `tabSales Invoice` si
        WHERE {where} AND si.outstanding_amount > 0
    """.format(where=where), params, as_dict=True)
    r = rows[0] if rows else {}
    return [
        {"label": "Not yet due", "value": _f(r.get("not_due"))},
        {"label": "1-30 days",   "value": _f(r.get("d30"))},
        {"label": "31-60 days",  "value": _f(r.get("d60"))},
        {"label": "61-90 days",  "value": _f(r.get("d90"))},
        {"label": "90+ days",    "value": _f(r.get("d90p"))},
    ]


# ================================================================
#  Rate-wise taxable value  (GSTR-1 shape)
# ================================================================

def _rate_wise(company, from_date, to_date, include_drafts):
    where, params = _base_conditions(company, from_date, to_date, include_drafts)
    where += _gross_only()

    rows = frappe.db.sql("""
        SELECT
            (COALESCE(sii.cgst_rate, 0) + COALESCE(sii.sgst_rate, 0)
             + COALESCE(sii.igst_rate, 0))              AS rate,
            COALESCE(SUM(sii.net_amount),   0)          AS taxable,
            COALESCE(SUM(sii.cgst_amount),  0)          AS cgst,
            COALESCE(SUM(sii.sgst_amount),  0)          AS sgst,
            COALESCE(SUM(sii.igst_amount),  0)          AS igst,
            COUNT(DISTINCT sii.parent)                  AS inv_count
        FROM `tabSales Invoice Item` sii
        JOIN `tabSales Invoice` si ON si.name = sii.parent
        WHERE {where}
        GROUP BY rate
        ORDER BY rate
    """.format(where=where), params, as_dict=True)

    out = []
    for r in rows:
        tax = _f(r.cgst) + _f(r.sgst) + _f(r.igst)
        out.append({
            "rate":      _f(r.rate),
            "label":     ("Exempt / nil" if _f(r.rate) == 0
                          else ("%g%%" % _f(r.rate))),
            "taxable":   _f(r.taxable),
            "cgst":      _f(r.cgst),
            "sgst":      _f(r.sgst),
            "igst":      _f(r.igst),
            "tax":       tax,
            "total":     _f(r.taxable) + tax,
            "inv_count": _i(r.inv_count),
        })

    # Courier sits in a child table, not in the item table, so it needs its own
    # line — otherwise the 18% service rate never appears in a GSTR-1 pivot.
    cwhere, cparams = _base_conditions(company, from_date, to_date, include_drafts)
    cwhere += _gross_only()
    crow = frappe.db.sql("""
        SELECT COALESCE(SUM(cce.courier_charges), 0) AS taxable,
               COALESCE(SUM(cce.cgst), 0)            AS cgst,
               COALESCE(SUM(cce.sgst), 0)            AS sgst,
               COALESCE(SUM(cce.igst), 0)            AS igst,
               COUNT(DISTINCT cce.parent)            AS inv_count
        FROM `tabCourier Charge Entry` cce
        JOIN `tabSales Invoice` si ON si.name = cce.parent
        WHERE cce.parenttype = 'Sales Invoice' AND {where}
    """.format(where=cwhere), cparams, as_dict=True)

    if crow and _f(crow[0].taxable):
        c = crow[0]
        tax = _f(c.cgst) + _f(c.sgst) + _f(c.igst)
        out.append({
            "rate":      18.0,
            "label":     "Courier service 18%",
            "taxable":   _f(c.taxable),
            "cgst":      _f(c.cgst),
            "sgst":      _f(c.sgst),
            "igst":      _f(c.igst),
            "tax":       tax,
            "total":     _f(c.taxable) + tax,
            "inv_count": _i(c.inv_count),
            "is_service": True,
        })

    return out


# ================================================================
#  Document mix, status, courier, trends, channel, feed
# ================================================================

def _status_monthly(company, from_date, to_date, include_drafts):
    where, params = _base_conditions(company, from_date, to_date, include_drafts)
    rows = frappe.db.sql("""
        SELECT {sk} AS sk, si.status, COUNT(*) AS cnt
        FROM `tabSales Invoice` si
        WHERE {where}
        GROUP BY sk, si.status
        ORDER BY sk
    """.format(sk=_SK, where=where), params, as_dict=True)

    by_month, all_status = {}, set()
    for r in rows:
        sk = _i(r.sk)
        by_month.setdefault(sk, {})
        by_month[sk][r.status or "Unknown"] = _i(r.cnt)
        all_status.add(r.status or "Unknown")

    return {
        "months":   [{"sk": sk, "label": _mo_label(sk), "counts": by_month[sk]}
                     for sk in sorted(by_month)],
        "statuses": sorted(all_status),
    }


def _doc_mix(company, from_date, to_date, include_drafts):
    where, params = _base_conditions(company, from_date, to_date, include_drafts)
    r = frappe.db.sql("""
        SELECT
            SUM(CASE WHEN si.is_return = 0 AND si.is_debit_note = 0
                      AND si.is_pos = 0 THEN 1 ELSE 0 END)            AS tax_invoice,
            SUM(CASE WHEN si.is_return = 1 THEN 1 ELSE 0 END)         AS credit_note,
            SUM(CASE WHEN si.is_debit_note = 1 THEN 1 ELSE 0 END)     AS debit_note,
            SUM(CASE WHEN si.is_pos = 1 THEN 1 ELSE 0 END)            AS pos,
            SUM(CASE WHEN si.amended_from IS NOT NULL
                      AND si.amended_from != '' THEN 1 ELSE 0 END)    AS amended
        FROM `tabSales Invoice` si
        WHERE {where}
    """.format(where=where), params, as_dict=True)
    d = r[0] if r else {}
    return [
        {"label": "Tax invoice", "value": _i(d.get("tax_invoice")), "color": "#639922"},
        {"label": "Credit note", "value": _i(d.get("credit_note")), "color": "#E24B4A"},
        {"label": "Debit note",  "value": _i(d.get("debit_note")),  "color": "#BA7517"},
        {"label": "POS",         "value": _i(d.get("pos")),         "color": "#378ADD"},
        {"label": "Amended",     "value": _i(d.get("amended")),     "color": "#888780"},
    ]


def _notes_monthly(company, from_date, to_date, include_drafts):
    where, params = _base_conditions(company, from_date, to_date, include_drafts)
    rows = frappe.db.sql("""
        SELECT {sk} AS sk,
               COALESCE(SUM(CASE WHEN si.is_return = 1
                       THEN si.rounded_total ELSE 0 END), 0) AS cn_value,
               SUM(CASE WHEN si.is_return = 1 THEN 1 ELSE 0 END) AS cn_count,
               COALESCE(SUM(CASE WHEN si.is_debit_note = 1
                       THEN si.rounded_total ELSE 0 END), 0) AS dn_value
        FROM `tabSales Invoice` si
        WHERE {where}
        GROUP BY sk ORDER BY sk
    """.format(sk=_SK, where=where), params, as_dict=True)

    return [{"sk": _i(r.sk), "label": _mo_label(r.sk),
             "cn_value": abs(_f(r.cn_value)), "cn_count": _i(r.cn_count),
             "dn_value": _f(r.dn_value)} for r in rows]


def _recent_notes(company, from_date, to_date, include_drafts):
    where, params = _base_conditions(company, from_date, to_date, include_drafts)
    rows = frappe.db.sql("""
        SELECT si.name, si.customer, si.posting_date, si.rounded_total,
               si.return_against, si.is_return, si.is_debit_note, si.status
        FROM `tabSales Invoice` si
        WHERE {where} AND (si.is_return = 1 OR si.is_debit_note = 1)
        ORDER BY si.posting_date DESC, si.creation DESC
        LIMIT 10
    """.format(where=where), params, as_dict=True)

    return [{"name": r.name, "customer": r.customer or "-",
             "date": str(r.posting_date or ""), "amount": _f(r.rounded_total),
             "against": r.return_against or "-",
             "kind": "Credit note" if _i(r.is_return) else "Debit note",
             "status": r.status or "-"} for r in rows]


def _courier_monthly(company, from_date, to_date, include_drafts):
    where, params = _base_conditions(company, from_date, to_date, include_drafts)
    where += _gross_only()

    monthly = frappe.db.sql("""
        SELECT {sk} AS sk,
               COALESCE(SUM(cce.courier_charges), 0) AS charges,
               COALESCE(SUM(cce.cgst + cce.sgst + cce.igst), 0) AS gst
        FROM `tabCourier Charge Entry` cce
        JOIN `tabSales Invoice` si ON si.name = cce.parent
        WHERE cce.parenttype = 'Sales Invoice' AND {where}
        GROUP BY sk ORDER BY sk
    """.format(sk=_SK, where=where), params, as_dict=True)

    by_location = frappe.db.sql("""
        SELECT COALESCE(NULLIF(cce.location, ''), 'Not set') AS location,
               COALESCE(SUM(cce.courier_charges), 0) AS charges,
               COUNT(DISTINCT cce.parent) AS inv_count
        FROM `tabCourier Charge Entry` cce
        JOIN `tabSales Invoice` si ON si.name = cce.parent
        WHERE cce.parenttype = 'Sales Invoice' AND {where}
        GROUP BY location ORDER BY charges DESC
    """.format(where=where), params, as_dict=True)

    return {
        "monthly": [{"sk": _i(r.sk), "label": _mo_label(r.sk),
                     "charges": _f(r.charges), "gst": _f(r.gst)} for r in monthly],
        "by_location": [{"label": r.location, "value": _f(r.charges),
                         "count": _i(r.inv_count)} for r in by_location],
    }


def _channel_monthly(company, from_date, to_date, include_drafts):
    where, params = _base_conditions(company, from_date, to_date, include_drafts)
    where += _gross_only()
    rows = frappe.db.sql("""
        SELECT {sk} AS sk,
               COALESCE(si.custom_online__offline, 'Not set') AS channel,
               COALESCE(SUM(sii.net_amount), 0) AS sales,
               COUNT(DISTINCT si.name) AS inv_count
        FROM `tabSales Invoice Item` sii
        JOIN `tabSales Invoice` si ON si.name = sii.parent
        WHERE {where}
        GROUP BY sk, channel ORDER BY sk
    """.format(sk=_SK, where=where), params, as_dict=True)

    by_month = {}
    for r in rows:
        sk = _i(r.sk)
        by_month.setdefault(sk, {"sk": sk, "label": _mo_label(sk),
                                 "Online": 0.0, "Offline": 0.0, "Not set": 0.0})
        by_month[sk][r.channel] = _f(r.sales)
    return [by_month[sk] for sk in sorted(by_month)]


def _feed(company, from_date, to_date, include_drafts):
    where, params = _base_conditions(company, from_date, to_date, include_drafts)
    rows = frappe.db.sql("""
        SELECT si.name, si.customer, si.posting_date, si.rounded_total,
               si.grand_total, si.status, si.is_return, si.is_debit_note,
               si.custom_online__offline AS channel, si.custom_state_type AS state_type,
               (SELECT sii.gst_hsn_code FROM `tabSales Invoice Item` sii
                 WHERE sii.parent = si.name LIMIT 1) AS hsn
        FROM `tabSales Invoice` si
        WHERE {where}
        ORDER BY si.posting_date DESC, si.creation DESC
        LIMIT 15
    """.format(where=where), params, as_dict=True)

    out = []
    for r in rows:
        kind = "Credit note" if _i(r.is_return) else (
            "Debit note" if _i(r.is_debit_note) else "Invoice")
        sk = 0
        if r.posting_date:
            d = getdate(r.posting_date)
            sk = d.year * 100 + d.month
        out.append({
            "name": r.name, "customer": r.customer or "-",
            "date": str(r.posting_date or ""), "month": _mo_label(sk),
            "amount": _f(r.rounded_total) or _f(r.grand_total),
            "status": r.status or "-", "kind": kind, "hsn": r.hsn or "-",
            "channel": r.channel or "-", "state_type": r.state_type or "-",
        })
    return out


# ================================================================
#  Pre-filing exceptions
# ================================================================

def _exceptions(company, from_date, to_date, include_drafts):
    where, params = _base_conditions(company, from_date, to_date, include_drafts)
    out = []

    def count(sql, extra_params=None):
        res = frappe.db.sql(sql, params + (extra_params or []))
        return _i(res[0][0]) if res else 0

    # 1 — registered customers with no GSTIN
    n = count("""
        SELECT COUNT(*) FROM `tabSales Invoice` si
        WHERE {where} AND si.gst_category = 'Registered Regular'
          AND (si.billing_address_gstin IS NULL OR si.billing_address_gstin = '')
    """.format(where=where))
    out.append({
        "key": "no_gstin", "count": n, "level": "high",
        "label": "Registered customers with no GSTIN",
        "detail": "gst_category is Registered Regular but billing_address_gstin is empty",
        "action": "Fix now",
        "route": {"gst_category": "Registered Regular"},
    })

    # 2 — drafts sitting inside the period
    n = count("""
        SELECT COUNT(*) FROM `tabSales Invoice` si
        WHERE si.posting_date BETWEEN %s AND %s AND si.docstatus = 0
          {comp}
    """.format(comp=("AND si.company = %s" if company and company != "__all__" else "")),
        [])
    out.append({
        "key": "drafts", "count": n, "level": "medium",
        "label": "Draft invoices in this period",
        "detail": "Submit them or they never reach the return",
        "action": "Review",
        "route": {"docstatus": 0},
    })

    # 3 — courier expected but no entry
    n = count("""
        SELECT COUNT(*) FROM `tabSales Invoice` si
        WHERE {where} AND si.custom_courier_charges_required = 'Yes'
          AND NOT EXISTS (
              SELECT 1 FROM `tabCourier Charge Entry` cce
              WHERE cce.parent = si.name AND cce.courier_charges > 0)
    """.format(where=where))
    out.append({
        "key": "courier_missing", "count": n, "level": "medium",
        "label": "Courier required but not charged",
        "detail": "custom_courier_charges_required is Yes, Courier Charge Entry is empty",
        "action": "Review",
        "route": {"custom_courier_charges_required": "Yes"},
    })

    # 4 — place of supply vs tax type mismatch
    n = count("""
        SELECT COUNT(DISTINCT si.name)
        FROM `tabSales Invoice` si
        JOIN `tabSales Invoice Item` sii ON sii.parent = si.name
        WHERE {where} AND (
            (si.custom_state_type = 'Intra-State' AND sii.igst_amount > 0) OR
            (si.custom_state_type = 'Inter-State' AND
                (sii.cgst_amount > 0 OR sii.sgst_amount > 0)))
    """.format(where=where))
    out.append({
        "key": "tax_mismatch", "count": n, "level": "high",
        "label": "State type and tax type disagree",
        "detail": "Intra-State invoice charging IGST, or Inter-State charging CGST/SGST",
        "action": "Investigate",
        "route": {},
    })

    # 5 — items with no HSN
    n = count("""
        SELECT COUNT(DISTINCT si.name)
        FROM `tabSales Invoice` si
        JOIN `tabSales Invoice Item` sii ON sii.parent = si.name
        WHERE {where} AND (sii.gst_hsn_code IS NULL OR sii.gst_hsn_code = '')
    """.format(where=where))
    out.append({
        "key": "no_hsn", "count": n, "level": "medium",
        "label": "Items with no HSN code",
        "detail": "HSN is mandatory on the invoice and in GSTR-1",
        "action": "Review",
        "route": {},
    })

    # 6 — cancelled invoices
    n = _i(frappe.db.sql("""
        SELECT COUNT(*) FROM `tabSales Invoice` si
        WHERE si.posting_date BETWEEN %s AND %s AND si.docstatus = 2
          {comp}
    """.format(comp=("AND si.company = %s" if company and company != "__all__" else "")),
        [from_date, to_date] + ([company] if company and company != "__all__" else []))[0][0])
    out.append({
        "key": "cancelled", "count": n, "level": "low",
        "label": "Cancelled invoices in this period",
        "detail": "Excluded from every figure above, listed here for the audit trail",
        "action": "View",
        "route": {"docstatus": 2},
    })

    # 7 — gaps in the invoice number series
    gaps = _series_gaps(company, from_date, to_date)
    out.append({
        "key": "series_gaps", "count": len(gaps), "level": "high",
        "label": "Gaps in the invoice number series",
        "detail": (", ".join(gaps[:6]) + ("  and %d more" % (len(gaps) - 6) if len(gaps) > 6 else ""))
                  if gaps else "No missing numbers found",
        "action": "Investigate",
        "route": {},
        "items": gaps[:50],
    })

    return [o for o in out if o["count"] > 0]


def _series_gaps(company, from_date, to_date):
    """Finds missing numbers inside each naming series present in the period.

    Only reports a gap when the number sits between two numbers that both
    exist, so an unused tail of the series is never flagged.
    """
    conds = ["si.posting_date BETWEEN %s AND %s", "si.docstatus IN (0, 1, 2)"]
    params = [from_date, to_date]
    if company and company != "__all__":
        conds.append("si.company = %s")
        params.append(company)

    rows = frappe.db.sql("""
        SELECT si.name FROM `tabSales Invoice` si
        WHERE {where} ORDER BY si.name
    """.format(where=" AND ".join(conds)), params, as_dict=True)

    buckets = {}
    for r in rows:
        name = r.name or ""
        idx = len(name)
        while idx > 0 and name[idx - 1].isdigit():
            idx -= 1
        if idx == len(name):
            continue
        prefix, num_str = name[:idx], name[idx:]
        buckets.setdefault(prefix, {"width": len(num_str), "nums": set()})
        buckets[prefix]["nums"].add(int(num_str))

    gaps = []
    for prefix, b in buckets.items():
        nums = sorted(b["nums"])
        if len(nums) < 2:
            continue
        present = set(nums)
        for n in range(nums[0], nums[-1] + 1):
            if n not in present:
                gaps.append(prefix + str(n).zfill(b["width"]))
    return sorted(gaps)


# ================================================================
#  Public entry point
# ================================================================

@frappe.whitelist()
def get_gst_dashboard_data(company=None, from_date=None, to_date=None,
                           include_drafts=0, period_label=None, compare_mode="none"):
    include_drafts = cint(include_drafts)

    if not from_date or not to_date:
        from_date = str(get_first_day(today()))
        to_date = str(get_last_day(today()))
        period_label = period_label or "This Month"

    if not company:
        company = frappe.defaults.get_user_default("Company") or \
            (frappe.db.get_value("Company", {}, "name") or None)

    matrix = _build_matrix(company, from_date, to_date, include_drafts)

    compare = None
    if compare_mode == "prev_period":
        span = (getdate(to_date) - getdate(from_date)).days + 1
        c_to = add_days(from_date, -1)
        c_from = add_days(c_to, -(span - 1))
        compare = {"label": "vs previous period",
                   "from_date": str(c_from), "to_date": str(c_to),
                   "matrix": _build_matrix(company, c_from, c_to, include_drafts)}
    elif compare_mode == "prev_year":
        c_from = add_months(from_date, -12)
        c_to = add_months(to_date, -12)
        compare = {"label": "vs same period last year",
                   "from_date": str(c_from), "to_date": str(c_to),
                   "matrix": _build_matrix(company, c_from, c_to, include_drafts)}

    companies = [c.name for c in frappe.get_all("Company", fields=["name"],
                                                order_by="name")]

    return {
        "meta": {
            "company": company,
            "companies": companies,
            "from_date": str(from_date),
            "to_date": str(to_date),
            "period_label": period_label or "Custom",
            "include_drafts": include_drafts,
            "compare_mode": compare_mode,
            "generated_on": frappe.utils.now_datetime().strftime("%d %b %Y, %I:%M %p"),
        },
        "kpis":       _kpis(company, from_date, to_date, include_drafts, matrix),
        "matrix":     matrix,
        "compare":    compare,
        "rate_wise":  _rate_wise(company, from_date, to_date, include_drafts),
        "doc_mix":    _doc_mix(company, from_date, to_date, include_drafts),
        "status":     _status_monthly(company, from_date, to_date, include_drafts),
        "notes":      _notes_monthly(company, from_date, to_date, include_drafts),
        "recent_notes": _recent_notes(company, from_date, to_date, include_drafts),
        "courier":    _courier_monthly(company, from_date, to_date, include_drafts),
        "channel":    _channel_monthly(company, from_date, to_date, include_drafts),
        "aging":      _aging(company, from_date, to_date, include_drafts),
        "exceptions": _exceptions(company, from_date, to_date, include_drafts),
        "feed":       _feed(company, from_date, to_date, include_drafts),
    }


# ================================================================
#  Scheduled monthly email
#  hooks.py:
#      scheduler_events = {
#          "cron": {
#              "0 7 1 * *": [
#                  "taal_inventory.utils.gst_sales_dashboard.send_monthly_gst_email"
#              ]
#          }
#      }
# ================================================================

def _recipients():
    if _EMAIL_RECIPIENTS:
        return _EMAIL_RECIPIENTS
    users = frappe.get_all(
        "Has Role", filters={"role": "Accounts Manager", "parenttype": "User"},
        fields=["parent"])
    emails = []
    for u in users:
        enabled = frappe.db.get_value("User", u.parent, "enabled")
        if enabled and "@" in (u.parent or ""):
            emails.append(u.parent)
    return emails


def _matrix_html(matrix, title):
    cols = [("month", "Month"), ("sales", "Sales"), ("cgst", "CGST"),
            ("sgst", "SGST"), ("igst", "IGST"), ("courier", "Courier Charges"),
            ("c_cgst", "Courier CGST"), ("c_sgst", "Courier SGST"),
            ("c_igst", "Courier IGST"), ("roff", "R/off"), ("total", "Total")]

    def fmt(v):
        if isinstance(v, str):
            return v
        return "{:,.2f}".format(flt(v))

    h = ['<div style="font-family:Arial,sans-serif;font-size:12px;color:#1e293b">']
    h.append('<h2 style="font-size:15px;color:#1e3a5f;margin:0 0 10px">%s</h2>' % title)
    h.append('<table cellpadding="0" cellspacing="0" '
             'style="width:100%;border-collapse:collapse">')
    h.append('<tr style="background:#1e3a5f">')
    for _, label in cols:
        h.append('<th style="padding:7px 9px;color:#fff;font-size:11px;'
                 'font-weight:600;border:1px solid #2563eb;white-space:nowrap;'
                 'text-align:right">%s</th>' % label)
    h.append("</tr>")

    def block(label, rows, subtotal, bg):
        h.append('<tr><td colspan="%d" style="padding:6px 9px;background:%s;'
                 'font-weight:600;font-size:11px;border:1px solid #e2e8f0">%s</td></tr>'
                 % (len(cols), bg, label))
        for r in rows:
            h.append("<tr>")
            for key, _ in cols:
                h.append('<td style="padding:5px 9px;border:1px solid #e2e8f0;'
                         'font-size:11px;text-align:right">%s</td>' % fmt(r.get(key, 0)))
            h.append("</tr>")
        h.append('<tr style="background:#EAF3DE">')
        h.append('<td style="padding:5px 9px;border:1px solid #e2e8f0;'
                 'font-size:11px;font-weight:600">Subtotal</td>')
        for key, _ in cols[1:]:
            h.append('<td style="padding:5px 9px;border:1px solid #e2e8f0;'
                     'font-size:11px;font-weight:600;text-align:right">%s</td>'
                     % fmt(subtotal.get(key, 0)))
        h.append("</tr>")

    for seg in matrix["segments"]:
        block(seg["label"], seg["months"], seg["subtotal"], "#E6F1FB")
    block("Total — all segments", matrix["total_months"], matrix["grand"], "#E1F5EE")

    h.append("</table></div>")
    return "".join(h)


def _matrix_xlsx(matrix):
    from frappe.utils.xlsxutils import make_xlsx

    header = ["Segment", "Month", "Sales", "CGST", "SGST", "IGST",
              "Courier Charges", "Courier CGST", "Courier SGST", "Courier IGST",
              "R/off", "Total"]
    data = [header]

    def push(seg_label, r):
        data.append([seg_label, r.get("month", ""),
                     flt(r.get("sales")), flt(r.get("cgst")), flt(r.get("sgst")),
                     flt(r.get("igst")), flt(r.get("courier")), flt(r.get("c_cgst")),
                     flt(r.get("c_sgst")), flt(r.get("c_igst")), flt(r.get("roff")),
                     flt(r.get("total"))])

    for seg in matrix["segments"]:
        for r in seg["months"]:
            push(seg["label"], r)
        sub = dict(seg["subtotal"])
        sub["month"] = "Subtotal"
        push(seg["label"], sub)
        data.append([""] * len(header))

    for r in matrix["total_months"]:
        push("Total", r)
    grand = dict(matrix["grand"])
    grand["month"] = "Grand total"
    push("Total", grand)

    return make_xlsx(data, "GST Summary")


def send_monthly_gst_email(company=None, month_offset=-1):
    """Emails last month's GST matrix. Scheduled for the 1st of every month."""
    anchor = add_months(today(), month_offset)
    from_date = str(get_first_day(anchor))
    to_date = str(get_last_day(anchor))

    if not company:
        company = frappe.defaults.get_global_default("company") or \
            frappe.db.get_value("Company", {}, "name")

    matrix = _build_matrix(company, from_date, to_date, 0)
    if not matrix["segments"]:
        return

    recipients = _recipients()
    if not recipients:
        frappe.log_error("No recipients configured", "GST dashboard monthly email")
        return

    title = "%s — GST summary for %s" % (company, formatdate(from_date, "MMMM yyyy"))
    body = _matrix_html(matrix, title)
    body += ('<p style="font-family:Arial,sans-serif;font-size:11px;color:#94a3b8">'
             'Submitted invoices only. Credit and debit notes excluded. '
             'Generated automatically from ERPNext.</p>')

    xlsx = _matrix_xlsx(matrix)

    frappe.sendmail(
        recipients=recipients,
        subject=title,
        message=body,
        attachments=[{
            "fname": "GST_Summary_%s.xlsx" % from_date[:7],
            "fcontent": xlsx.getvalue(),
        }],
        reference_doctype="Sales Invoice",
    )