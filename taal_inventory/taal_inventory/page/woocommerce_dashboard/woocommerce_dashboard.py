import frappe
from frappe.utils import today, add_days, get_first_day, get_last_day


@frappe.whitelist()
def get_woocommerce_dashboard_data(mode="online"):
    """
    mode: "online"  — custom_offline__online = 'Online'
          "offline" — custom_offline__online = 'Offline'
    """
    t   = today()
    ms  = str(get_first_day(t))
    me  = str(get_last_day(t))
    lms = str(get_first_day(add_days(ms, -1)))
    lme = str(add_days(ms, -1))

    if mode == "offline":
        return {
            "mode":     "offline",
            "kpis":     _offline_kpis(t, ms, me, lms, lme),
            "snapshot": _offline_snapshot(ms, me),
            "charts":   _offline_charts(t, ms, me),
            "top5":     _offline_top5(),
            "feed":     _offline_recent(),
            "pipeline": _offline_pipeline(),
        }
    else:
        return {
            "mode":     "online",
            "kpis":     _online_kpis(t, ms, me, lms, lme),
            "snapshot": _online_snapshot(),
            "charts":   _online_charts(t),
            "top5":     _online_top5(),
            "feed":     _online_recent(),
            "pipeline": _online_pipeline(),
        }


# ================================================================
#  SHARED HELPERS
# ================================================================

def _q(sql, args=None):
    r = frappe.db.sql(sql, args or [], as_list=True)
    if not r or r[0][0] is None:
        return 0
    return r[0][0]


def _flt(v):
    return float(v or 0)


def _int(v):
    return int(v or 0)


def _trend(cur, prev):
    cur  = _flt(cur)
    prev = _flt(prev)
    if prev == 0:
        return {"pct": 0.0, "dir": "flat"}
    diff = cur - prev
    pct  = round(abs(diff) / prev * 100, 1)
    return {"pct": pct, "dir": "up" if diff > 0 else ("down" if diff < 0 else "flat")}


# ================================================================
#  ONLINE — all metrics filter: custom_offline__online = 'Online'
#  Revenue metrics additionally: woocommerce_status IN (Shipped, Processing)
#  Pending card: woocommerce_status = Pending only
# ================================================================

_ON   = "docstatus=1 AND custom_offline__online='Online'"
_RSTAT = "woocommerce_status IN ('Shipped','Processing','shipped','processing')"
_PEND  = "woocommerce_status IN ('Pending','pending')"
_CANC  = "woocommerce_status IN ('Cancelled','cancelled','failed')"
_SHIP  = "woocommerce_status IN ('Shipped','shipped')"


def _online_kpis(t, ms, me, lms, lme):
    ty = str(add_days(t, -1))

    def rev(extra=""):
        clause = "WHERE {} AND {}".format(_ON, _RSTAT)
        if extra:
            clause += " AND " + extra
        return _flt(_q("SELECT COALESCE(SUM(grand_total),0) FROM `tabSales Order` " + clause))

    def cnt(extra=""):
        clause = "WHERE " + _ON
        if extra:
            clause += " AND " + extra
        return _int(_q("SELECT COUNT(*) FROM `tabSales Order` " + clause))

    total_rev  = rev()
    month_rev  = rev("transaction_date BETWEEN '{}' AND '{}'".format(ms, me))
    lmonth_rev = rev("transaction_date BETWEEN '{}' AND '{}'".format(lms, lme))
    total_ord  = cnt()
    today_ord  = cnt("transaction_date='{}'".format(str(t)))
    yest_ord   = cnt("transaction_date='{}'".format(str(add_days(t, -1))))
    pending    = _int(_q("SELECT COUNT(*) FROM `tabSales Order` WHERE {} AND {}".format(_ON, _PEND)))
    cancelled  = _int(_q("SELECT COUNT(*) FROM `tabSales Order` WHERE {} AND {}".format(_ON, _CANC)))
    shipped    = _int(_q("SELECT COUNT(*) FROM `tabSales Order` WHERE {} AND {}".format(_ON, _SHIP)))
    ship_proc  = _int(_q("SELECT COUNT(*) FROM `tabSales Order` WHERE {} AND {}".format(_ON, _RSTAT)))
    aov        = round(total_rev / total_ord, 2) if total_ord else 0.0

    return {
        "total_revenue":     total_rev,
        "month_revenue":     month_rev,
        "month_rev_trend":   _trend(month_rev, lmonth_rev),
        "avg_order_value":   aov,
        "total_orders":      total_ord,
        "today_orders":      today_ord,
        "today_trend":       _trend(today_ord, yest_ord),
        "pending":           pending,
        "cancelled":         cancelled,
        "fulfil_shipped":    round(shipped   / total_ord * 100) if total_ord else 0,
        "fulfil_ship_proc":  round(ship_proc / total_ord * 100) if total_ord else 0,
    }


def _online_snapshot():
    tc = frappe.db.sql("""
        SELECT customer, COUNT(*) AS cnt, SUM(grand_total) AS rev
        FROM `tabSales Order`
        WHERE docstatus=1 AND custom_offline__online='Online'
          AND customer IS NOT NULL AND customer != ''
        GROUP BY customer ORDER BY rev DESC LIMIT 1
    """, as_dict=True)

    ti = frappe.db.sql("""
        SELECT soi.item_name, SUM(soi.qty) AS qty, SUM(soi.amount) AS rev
        FROM `tabSales Order Item` soi
        JOIN `tabSales Order` so ON so.name=soi.parent
        WHERE so.docstatus=1 AND so.custom_offline__online='Online'
          AND so.woocommerce_status IN ('Shipped','Processing','shipped','processing')
          AND soi.item_name IS NOT NULL AND soi.item_name != ''
        GROUP BY soi.item_name ORDER BY rev DESC LIMIT 1
    """, as_dict=True)

    unique = _int(_q("""
        SELECT COUNT(DISTINCT customer) FROM `tabSales Order`
        WHERE docstatus=1 AND custom_offline__online='Online'
          AND customer IS NOT NULL AND customer != ''
    """))
    repeat = _int(_q("""
        SELECT COUNT(*) FROM (
            SELECT customer FROM `tabSales Order`
            WHERE docstatus=1 AND custom_offline__online='Online'
              AND customer IS NOT NULL AND customer != ''
            GROUP BY customer HAVING COUNT(*) > 1
        ) t
    """))
    total = _int(_q("SELECT COUNT(*) FROM `tabSales Order` WHERE docstatus=1 AND custom_offline__online='Online'"))

    return {
        "top_customer":     tc[0].customer if tc else "—",
        "top_cust_cnt":     _int(tc[0].cnt) if tc else 0,
        "top_cust_rev":     _flt(tc[0].rev) if tc else 0.0,
        "top_item":         ti[0].item_name if ti else "—",
        "top_item_qty":     _int(ti[0].qty) if ti else 0,
        "top_item_rev":     _flt(ti[0].rev) if ti else 0.0,
        "unique_customers": unique,
        "repeat_customers": repeat,
        "repeat_rate":      round(repeat / unique * 100) if unique else 0,
        "avg_per_customer": round(total / unique, 1) if unique else 0.0,
    }


def _online_charts(t):
    # Status doughnut
    ws = frappe.db.sql("""
        SELECT woocommerce_status, COUNT(*) AS cnt
        FROM `tabSales Order`
        WHERE docstatus=1 AND custom_offline__online='Online'
          AND woocommerce_status IS NOT NULL AND woocommerce_status != ''
        GROUP BY woocommerce_status ORDER BY cnt DESC
    """, as_dict=True)

    # Payment doughnut
    pm = frappe.db.sql("""
        SELECT woocommerce_payment_method, COUNT(*) AS cnt
        FROM `tabSales Order`
        WHERE docstatus=1 AND custom_offline__online='Online'
          AND woocommerce_payment_method IS NOT NULL AND woocommerce_payment_method != ''
        GROUP BY woocommerce_payment_method ORDER BY cnt DESC LIMIT 6
    """, as_dict=True)

    # Order value buckets
    buckets = [("Under ₹500",0,500),("₹500 – ₹1,000",500,1000),
               ("₹1,000 – ₹2,000",1000,2000),("₹2,000 – ₹5,000",2000,5000),
               ("Above ₹5,000",5000,999999999)]
    bkt_labels, bkt_values = [], []
    for lbl, lo, hi in buckets:
        c = _int(_q("""
            SELECT COUNT(*) FROM `tabSales Order`
            WHERE docstatus=1 AND custom_offline__online='Online'
              AND grand_total >= %s AND grand_total < %s
        """, [lo, hi]))
        bkt_labels.append(lbl)
        bkt_values.append(c)

    # Top states
    addr_rows = frappe.db.sql("""
        SELECT shipping_address_name, COUNT(*) AS cnt
        FROM `tabSales Order`
        WHERE docstatus=1 AND custom_offline__online='Online'
          AND shipping_address_name IS NOT NULL AND shipping_address_name != ''
        GROUP BY shipping_address_name ORDER BY cnt DESC LIMIT 20
    """, as_dict=True)
    state_map = {}
    for r in addr_rows:
        try:
            st = frappe.db.get_value("Address", r.shipping_address_name, "state")
            if st:
                state_map[st] = state_map.get(st, 0) + _int(r.cnt)
        except Exception:
            state_map[r.shipping_address_name] = state_map.get(r.shipping_address_name, 0) + _int(r.cnt)
    sorted_states = sorted(state_map.items(), key=lambda x: x[1], reverse=True)[:8]

    # Monthly revenue (Online + Shipped/Processing)
    mr = frappe.db.sql("""
        SELECT DATE_FORMAT(transaction_date,'%%b %%Y') AS mo,
               YEAR(transaction_date)*100+MONTH(transaction_date) AS sk,
               COALESCE(SUM(grand_total),0) AS rev
        FROM `tabSales Order`
        WHERE docstatus=1 AND custom_offline__online='Online'
          AND woocommerce_status IN ('Shipped','Processing','shipped','processing')
          AND transaction_date >= DATE_SUB(%s, INTERVAL 6 MONTH)
        GROUP BY mo, sk ORDER BY sk
    """, str(t), as_dict=True)

    # Monthly orders (Online only)
    mo = frappe.db.sql("""
        SELECT DATE_FORMAT(transaction_date,'%%b %%Y') AS mo,
               YEAR(transaction_date)*100+MONTH(transaction_date) AS sk,
               COUNT(*) AS cnt
        FROM `tabSales Order`
        WHERE docstatus=1 AND custom_offline__online='Online'
          AND transaction_date >= DATE_SUB(%s, INTERVAL 6 MONTH)
        GROUP BY mo, sk ORDER BY sk
    """, str(t), as_dict=True)

    return {
        "woo_status":    {"labels":[r.woocommerce_status for r in ws],  "datasets":[{"values":[_int(r.cnt) for r in ws]}]},
        "payment":       {"labels":[r.woocommerce_payment_method for r in pm], "datasets":[{"values":[_int(r.cnt) for r in pm]}]},
        "order_buckets": {"labels":bkt_labels,  "datasets":[{"values":bkt_values}]},
        "top_states":    {"labels":[s[0] for s in sorted_states], "datasets":[{"values":[s[1] for s in sorted_states]}]},
        "monthly_rev":   {"labels":[r.mo for r in mr], "datasets":[{"values":[_flt(r.rev) for r in mr]}]},
        "monthly_ord":   {"labels":[r.mo for r in mo], "datasets":[{"values":[_int(r.cnt) for r in mo]}]},
    }


def _online_top5():
    items = frappe.db.sql("""
        SELECT soi.item_name, SUM(soi.qty) AS tq, SUM(soi.amount) AS tr
        FROM `tabSales Order Item` soi
        JOIN `tabSales Order` so ON so.name=soi.parent
        WHERE so.docstatus=1 AND so.custom_offline__online='Online'
          AND so.woocommerce_status IN ('Shipped','Processing','shipped','processing')
          AND soi.item_name IS NOT NULL AND soi.item_name != ''
        GROUP BY soi.item_name ORDER BY tr DESC LIMIT 6
    """, as_dict=True)
    custs = frappe.db.sql("""
        SELECT customer, COUNT(*) AS oc, SUM(grand_total) AS tr
        FROM `tabSales Order`
        WHERE docstatus=1 AND custom_offline__online='Online'
          AND customer IS NOT NULL AND customer != ''
        GROUP BY customer ORDER BY tr DESC LIMIT 5
    """, as_dict=True)
    im = _flt(items[0].tr) if items else 1
    cm = _flt(custs[0].tr) if custs else 1
    return {
        "items":     [{"name":r.item_name,"qty":_int(r.tq),"rev":_flt(r.tr),"pct":round(_flt(r.tr)/im*100)} for r in items],
        "customers": [{"name":r.customer, "cnt":_int(r.oc),"rev":_flt(r.tr),"pct":round(_flt(r.tr)/cm*100)} for r in custs],
    }


def _online_recent():
    rows = frappe.db.sql("""
        SELECT name, customer, transaction_date, grand_total, woocommerce_status
        FROM `tabSales Order`
        WHERE docstatus=1 AND custom_offline__online='Online'
        ORDER BY creation DESC LIMIT 8
    """, as_dict=True)
    return [{"name":r.name,"customer":r.customer or "—",
             "transaction_date":str(r.transaction_date or ""),
             "grand_total":_flt(r.grand_total),
             "status":r.woocommerce_status or "—"} for r in rows]


def _online_pipeline():
    rows = frappe.db.sql("""
        SELECT woocommerce_status, COUNT(*) AS cnt
        FROM `tabSales Order`
        WHERE docstatus=1 AND custom_offline__online='Online'
          AND woocommerce_status IS NOT NULL AND woocommerce_status != ''
        GROUP BY woocommerce_status
    """, as_dict=True)
    sm = {r.woocommerce_status: _int(r.cnt) for r in rows}
    def p(*keys): return sum(sm.get(k,0) for k in keys)
    total   = _int(_q("SELECT COUNT(*) FROM `tabSales Order` WHERE docstatus=1 AND custom_offline__online='Online'"))
    shipped = p("Shipped","shipped")
    proc    = p("Processing","processing")
    return {
        "pending":          p("Pending","pending"),
        "on_hold":          p("on-hold"),
        "processing":       proc,
        "shipped":          shipped,
        "refunded":         p("Refunded","refunded"),
        "cancelled":        p("Cancelled","cancelled","failed"),
        "total":            total,
        "fulfil_shipped":   round(shipped/total*100) if total else 0,
        "fulfil_ship_proc": round((shipped+proc)/total*100) if total else 0,
    }


# ================================================================
#  OFFLINE — all metrics filter: custom_offline__online = 'Offline'
#  Uses ERPNext native `status` field (not woocommerce_status)
#  Tax: reads dynamically from tabSales Taxes and Charges
# ================================================================

_OFF = "docstatus=1 AND custom_offline__online='Offline'"


def _offline_kpis(t, ms, me, lms, lme):
    ty = str(add_days(t, -1))

    def rev(extra=""):
        clause = "WHERE " + _OFF + (" AND " + extra if extra else "")
        return _flt(_q("SELECT COALESCE(SUM(grand_total),0) FROM `tabSales Order` " + clause))

    def cnt(extra=""):
        clause = "WHERE " + _OFF + (" AND " + extra if extra else "")
        return _int(_q("SELECT COUNT(*) FROM `tabSales Order` " + clause))

    total_rev  = rev()
    month_rev  = rev("transaction_date BETWEEN '{}' AND '{}'".format(ms, me))
    lmonth_rev = rev("transaction_date BETWEEN '{}' AND '{}'".format(lms, lme))
    total_ord  = cnt()
    today_ord  = cnt("transaction_date='{}'".format(str(t)))
    yest_ord   = cnt("transaction_date='{}'".format(str(add_days(t, -1))))
    pending    = cnt("status='To Deliver and Bill'")
    cancelled  = cnt("status='Cancelled'")
    completed  = cnt("status='Completed'")
    aov        = round(total_rev / total_ord, 2) if total_ord else 0.0

    return {
        "total_revenue":    total_rev,
        "month_revenue":    month_rev,
        "month_rev_trend":  _trend(month_rev, lmonth_rev),
        "avg_order_value":  aov,
        "total_orders":     total_ord,
        "today_orders":     today_ord,
        "today_trend":      _trend(today_ord, yest_ord),
        "pending":          pending,
        "cancelled":        cancelled,
        "completion_rate":  round(completed / total_ord * 100) if total_ord else 0,
    }


def _offline_snapshot(ms, me):
    # Top customer
    tc = frappe.db.sql("""
        SELECT customer, COUNT(*) AS cnt, SUM(grand_total) AS rev
        FROM `tabSales Order`
        WHERE docstatus=1 AND custom_offline__online='Offline'
          AND customer IS NOT NULL AND customer != ''
        GROUP BY customer ORDER BY rev DESC LIMIT 1
    """, as_dict=True)

    # Top item
    ti = frappe.db.sql("""
        SELECT soi.item_name, SUM(soi.qty) AS qty, SUM(soi.amount) AS rev
        FROM `tabSales Order Item` soi
        JOIN `tabSales Order` so ON so.name=soi.parent
        WHERE so.docstatus=1 AND so.custom_offline__online='Offline'
          AND soi.item_name IS NOT NULL AND soi.item_name != ''
        GROUP BY soi.item_name ORDER BY rev DESC LIMIT 1
    """, as_dict=True)

    # Dynamic tax breakdown for current month
    tax_rows = frappe.db.sql("""
        SELECT stc.account_head, COALESCE(SUM(stc.tax_amount), 0) AS total
        FROM `tabSales Taxes and Charges` stc
        JOIN `tabSales Order` so ON so.name = stc.parent
        WHERE so.docstatus=1
          AND so.custom_offline__online='Offline'
          AND so.transaction_date BETWEEN %s AND %s
          AND stc.tax_amount IS NOT NULL
          AND stc.charge_type != 'Actual'
        GROUP BY stc.account_head
        ORDER BY total DESC
    """, [ms, me], as_dict=True)

    sgst = cgst = igst = tds = 0.0
    for r in tax_rows:
        ah  = (r.account_head or "").upper()
        val = _flt(r.total)
        if "TDS" in ah or "TAX DEDUCT" in ah:
            tds  += val
        elif "IGST" in ah:
            igst += val
        elif "SGST" in ah:
            sgst += val
        elif "CGST" in ah:
            cgst += val

    gross = sgst + cgst + igst
    net   = gross - tds

    # Customer stats
    unique = _int(_q("""
        SELECT COUNT(DISTINCT customer) FROM `tabSales Order`
        WHERE docstatus=1 AND custom_offline__online='Offline'
          AND customer IS NOT NULL AND customer != ''
    """))
    repeat = _int(_q("""
        SELECT COUNT(*) FROM (
            SELECT customer FROM `tabSales Order`
            WHERE docstatus=1 AND custom_offline__online='Offline'
              AND customer IS NOT NULL AND customer != ''
            GROUP BY customer HAVING COUNT(*) > 1
        ) t
    """))
    total = _int(_q("SELECT COUNT(*) FROM `tabSales Order` WHERE docstatus=1 AND custom_offline__online='Offline'"))

    return {
        "top_customer":     tc[0].customer if tc else "—",
        "top_cust_cnt":     _int(tc[0].cnt) if tc else 0,
        "top_cust_rev":     _flt(tc[0].rev) if tc else 0.0,
        "top_item":         ti[0].item_name if ti else "—",
        "top_item_qty":     _int(ti[0].qty) if ti else 0,
        "top_item_rev":     _flt(ti[0].rev) if ti else 0.0,
        "tax_sgst":         round(sgst, 2),
        "tax_cgst":         round(cgst, 2),
        "tax_igst":         round(igst, 2),
        "tax_tds":          round(tds,  2),
        "tax_gross":        round(gross, 2),
        "tax_net":          round(net,   2),
        "unique_customers": unique,
        "repeat_customers": repeat,
        "repeat_rate":      round(repeat / unique * 100) if unique else 0,
        "avg_per_customer": round(total / unique, 1) if unique else 0.0,
    }


def _offline_charts(t, ms, me):
    # Status doughnut (ERPNext status)
    st_rows = frappe.db.sql("""
        SELECT status, COUNT(*) AS cnt
        FROM `tabSales Order`
        WHERE docstatus=1 AND custom_offline__online='Offline'
          AND status IS NOT NULL AND status != ''
        GROUP BY status ORDER BY cnt DESC
    """, as_dict=True)

    # Dynamic tax by month — all segments
    tax_monthly = frappe.db.sql("""
        SELECT
            DATE_FORMAT(so.transaction_date,'%%b %%Y')          AS mo,
            YEAR(so.transaction_date)*100+MONTH(so.transaction_date) AS sk,
            stc.account_head,
            COALESCE(SUM(stc.tax_amount), 0)                    AS total
        FROM `tabSales Taxes and Charges` stc
        JOIN `tabSales Order` so ON so.name = stc.parent
        WHERE so.docstatus=1
          AND so.custom_offline__online='Offline'
          AND so.transaction_date >= DATE_SUB(%s, INTERVAL 6 MONTH)
          AND stc.tax_amount IS NOT NULL
        GROUP BY mo, sk, stc.account_head
        ORDER BY sk
    """, str(t), as_dict=True)

    # Aggregate by month
    month_map = {}
    for r in tax_monthly:
        mo  = r.mo
        ah  = (r.account_head or "").upper()
        val = _flt(r.total)
        if mo not in month_map:
            month_map[r.sk] = {"mo": mo, "sk": r.sk, "sgst": 0.0, "cgst": 0.0, "igst": 0.0, "tds": 0.0}
        if "TDS" in ah or "TAX DEDUCT" in ah:
            month_map[r.sk]["tds"]  += val
        elif "IGST" in ah:
            month_map[r.sk]["igst"] += val
        elif "SGST" in ah:
            month_map[r.sk]["sgst"] += val
        elif "CGST" in ah:
            month_map[r.sk]["cgst"] += val

    # If no tax data, build months from order data for chart continuity
    if not month_map:
        ord_rows = frappe.db.sql("""
            SELECT DATE_FORMAT(transaction_date,'%%b %%Y') AS mo,
                   YEAR(transaction_date)*100+MONTH(transaction_date) AS sk
            FROM `tabSales Order`
            WHERE docstatus=1 AND custom_offline__online='Offline'
              AND transaction_date >= DATE_SUB(%s, INTERVAL 6 MONTH)
            GROUP BY mo, sk ORDER BY sk
        """, str(t), as_dict=True)
        for r in ord_rows:
            month_map[r.sk] = {"mo": r.mo, "sk": r.sk, "sgst": 0.0, "cgst": 0.0, "igst": 0.0, "tds": 0.0}

    sorted_months = sorted(month_map.values(), key=lambda x: x["sk"])

    # Order value buckets (different ranges for Offline — higher ticket)
    off_buckets = [
        ("Under ₹1,000",  0,    1000),
        ("₹1,000 – ₹2,000", 1000, 2000),
        ("₹2,000 – ₹5,000",  2000, 5000),
        ("₹5,000 – ₹10,000", 5000, 10000),
        ("Above ₹10,000",  10000, 999999999),
    ]
    bkt_labels, bkt_values = [], []
    for lbl, lo, hi in off_buckets:
        c = _int(_q("""
            SELECT COUNT(*) FROM `tabSales Order`
            WHERE docstatus=1 AND custom_offline__online='Offline'
              AND grand_total >= %s AND grand_total < %s
        """, [lo, hi]))
        bkt_labels.append(lbl)
        bkt_values.append(c)

    # Monthly orders
    mo_rows = frappe.db.sql("""
        SELECT DATE_FORMAT(transaction_date,'%%b %%Y') AS mo,
               YEAR(transaction_date)*100+MONTH(transaction_date) AS sk,
               COUNT(*) AS cnt
        FROM `tabSales Order`
        WHERE docstatus=1 AND custom_offline__online='Offline'
          AND transaction_date >= DATE_SUB(%s, INTERVAL 6 MONTH)
        GROUP BY mo, sk ORDER BY sk
    """, str(t), as_dict=True)

    # Monthly revenue
    rev_rows = frappe.db.sql("""
        SELECT DATE_FORMAT(transaction_date,'%%b %%Y') AS mo,
               YEAR(transaction_date)*100+MONTH(transaction_date) AS sk,
               COALESCE(SUM(grand_total),0) AS rev
        FROM `tabSales Order`
        WHERE docstatus=1 AND custom_offline__online='Offline'
          AND transaction_date >= DATE_SUB(%s, INTERVAL 6 MONTH)
        GROUP BY mo, sk ORDER BY sk
    """, str(t), as_dict=True)

    return {
        "status": {
            "labels":   [r.status for r in st_rows],
            "datasets": [{"values": [_int(r.cnt) for r in st_rows]}]
        },
        "tax_monthly": [
            {"mo":   m["mo"],
             "sgst": round(m["sgst"], 2),
             "cgst": round(m["cgst"], 2),
             "igst": round(m["igst"], 2),
             "tds":  round(m["tds"],  2)}
            for m in sorted_months
        ],
        "order_buckets": {"labels": bkt_labels, "datasets": [{"values": bkt_values}]},
        "monthly_rev":   {"labels": [r.mo for r in rev_rows], "datasets": [{"values": [_flt(r.rev) for r in rev_rows]}]},
        "monthly_ord":   {"labels": [r.mo for r in mo_rows],  "datasets": [{"values": [_int(r.cnt) for r in mo_rows]}]},
    }


def _offline_top5():
    items = frappe.db.sql("""
        SELECT soi.item_name, SUM(soi.qty) AS tq, SUM(soi.amount) AS tr
        FROM `tabSales Order Item` soi
        JOIN `tabSales Order` so ON so.name=soi.parent
        WHERE so.docstatus=1 AND so.custom_offline__online='Offline'
          AND soi.item_name IS NOT NULL AND soi.item_name != ''
        GROUP BY soi.item_name ORDER BY tr DESC LIMIT 6
    """, as_dict=True)
    custs = frappe.db.sql("""
        SELECT customer, COUNT(*) AS oc, SUM(grand_total) AS tr
        FROM `tabSales Order`
        WHERE docstatus=1 AND custom_offline__online='Offline'
          AND customer IS NOT NULL AND customer != ''
        GROUP BY customer ORDER BY tr DESC LIMIT 5
    """, as_dict=True)
    im = _flt(items[0].tr) if items else 1
    cm = _flt(custs[0].tr) if custs else 1
    return {
        "items":     [{"name":r.item_name,"qty":_int(r.tq),"rev":_flt(r.tr),"pct":round(_flt(r.tr)/im*100)} for r in items],
        "customers": [{"name":r.customer, "cnt":_int(r.oc),"rev":_flt(r.tr),"pct":round(_flt(r.tr)/cm*100)} for r in custs],
    }


def _offline_recent():
    rows = frappe.db.sql("""
        SELECT name, customer, transaction_date, grand_total, status
        FROM `tabSales Order`
        WHERE docstatus=1 AND custom_offline__online='Offline'
        ORDER BY creation DESC LIMIT 8
    """, as_dict=True)
    return [{"name":r.name,"customer":r.customer or "—",
             "transaction_date":str(r.transaction_date or ""),
             "grand_total":_flt(r.grand_total),
             "status":r.status or "—"} for r in rows]


def _offline_pipeline():
    rows = frappe.db.sql("""
        SELECT status, COUNT(*) AS cnt
        FROM `tabSales Order`
        WHERE docstatus=1 AND custom_offline__online='Offline'
          AND status IS NOT NULL AND status != ''
        GROUP BY status
    """, as_dict=True)
    sm    = {r.status: _int(r.cnt) for r in rows}
    total = _int(_q("SELECT COUNT(*) FROM `tabSales Order` WHERE docstatus=1 AND custom_offline__online='Offline'"))
    comp  = sm.get("Completed", 0)
    return {
        "draft":            sm.get("Draft", 0),
        "to_deliver_bill":  sm.get("To Deliver and Bill", 0),
        "to_bill":          sm.get("To Bill", 0),
        "completed":        comp,
        "cancelled":        sm.get("Cancelled", 0),
        "total":            total,
        "completion_rate":  round(comp / total * 100) if total else 0,
    }