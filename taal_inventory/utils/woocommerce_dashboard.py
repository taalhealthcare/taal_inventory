import frappe
from frappe.utils import today, add_days, get_first_day, get_last_day, nowdate


@frappe.whitelist()
def get_woocommerce_dashboard_data(mode="online", from_date=None, to_date=None, period_label=None):
    """
    mode         : "online"  — custom_offline__online = 'Online'
                   "offline" — custom_offline__online = 'Offline'
    from_date    : filter start date (YYYY-MM-DD), optional
    to_date      : filter end date   (YYYY-MM-DD), optional
    period_label : human-readable period label echoed back to JS
    """
    t   = today()
    fd  = from_date or None
    td  = to_date   or None

    # Current month defaults (used when no preset selected)
    ms  = str(get_first_day(t))
    me  = str(get_last_day(t))
    lms = str(get_first_day(add_days(ms, -1)))
    lme = str(add_days(ms, -1))

    if mode == "offline":
        return {
            "mode":         "offline",
            "period_label": period_label or "All Time",
            "from_date":    from_date or "",
            "to_date":      to_date   or "",
            "kpis":     _offline_kpis(t, ms, me, lms, lme, fd, td),
            "snapshot": _offline_snapshot(ms, me, fd, td),
            "charts":   _offline_charts(t, ms, me, fd, td),
            "top5":     _offline_top5(fd, td),
            "feed":     _offline_recent(fd, td),
            "pipeline": _offline_pipeline(fd, td),
        }
    else:
        return {
            "mode":         "online",
            "period_label": period_label or "All Time",
            "from_date":    from_date or "",
            "to_date":      to_date   or "",
            "kpis":     _online_kpis(t, ms, me, lms, lme, fd, td),
            "snapshot": _online_snapshot(fd, td),
            "charts":   _online_charts(t, fd, td),
            "top5":     _online_top5(fd, td),
            "feed":     _online_recent(fd, td),
            "pipeline": _online_pipeline(fd, td),
        }


# ================================================================
#  SHARED HELPERS
# ================================================================

def _q(sql, args=None):
    r = frappe.db.sql(sql, args or [], as_list=True)
    if not r or r[0][0] is None:
        return 0
    return r[0][0]

def _flt(v): return float(v or 0)
def _int(v): return int(v or 0)

def _trend(cur, prev):
    cur, prev = _flt(cur), _flt(prev)
    if prev == 0: return {"pct": 0.0, "dir": "flat"}
    diff = cur - prev
    pct  = round(abs(diff) / prev * 100, 1)
    return {"pct": pct, "dir": "up" if diff > 0 else ("down" if diff < 0 else "flat")}

# Month label helper — avoids DATE_FORMAT %b %Y in SQL (% escaping issues)
_MONTH_NAMES = ["","Jan","Feb","Mar","Apr","May","Jun",
                "Jul","Aug","Sep","Oct","Nov","Dec"]

def _mo_label(yr, mo):
    """Return e.g. 'Apr 2026' from year=2026, month=4"""
    try:
        return _MONTH_NAMES[int(mo)] + " " + str(yr)
    except Exception:
        return str(yr) + "-" + str(mo)



def _date_clause(fd, td, alias="so"):
    """Return AND clause for date range if provided."""
    if fd and td:
        col = "{}.transaction_date".format(alias) if alias else "transaction_date"
        return " AND {} BETWEEN '{}' AND '{}'".format(col, fd, td)
    return ""


# ================================================================
#  ONLINE CONSTANTS
# ================================================================
# Base filter — Online orders only
_ON    = "docstatus=1 AND custom_offline__online='Online'"
# Revenue/data status filter — Shipped OR Processing
_RSTAT = "woocommerce_status IN ('Shipped','Processing','shipped','processing')"
# Pending — only Pending status
_PEND  = "woocommerce_status IN ('Pending','pending')"
# Cancelled
_CANC  = "woocommerce_status IN ('Cancelled','cancelled','failed')"
# Shipped only
_SHIP  = "woocommerce_status IN ('Shipped','shipped')"


# ================================================================
#  ONLINE KPIs
# ================================================================

def _online_kpis(t, ms, me, lms, lme, fd, td):
    date_c = _date_clause(fd, td, alias="")

    def rev(extra=""):
        w = "WHERE {} AND {}".format(_ON, _RSTAT)
        if extra: w += " AND " + extra
        if date_c: w += date_c
        return _flt(_q("SELECT COALESCE(SUM(base_net_total),0) FROM `tabSales Order` " + w))

    def cnt(extra=""):
        w = "WHERE " + _ON
        if extra: w += " AND " + extra
        if date_c: w += date_c
        return _int(_q("SELECT COUNT(*) FROM `tabSales Order` " + w))

    # When date preset is active, use it; otherwise use current month for trends
    if fd and td:
        total_rev  = rev()
        month_rev  = total_rev
        lmonth_rev = 0.0
    else:
        total_rev  = rev()
        month_rev  = rev("transaction_date BETWEEN '{}' AND '{}'".format(ms, me))
        lmonth_rev = rev("transaction_date BETWEEN '{}' AND '{}'".format(lms, lme))

    ty       = str(add_days(t, -1))
    total_ord = cnt()
    # Orders today — Shipped + Processing only (same filter as all revenue metrics)
    today_ord = cnt("{} AND transaction_date='{}'".format(_RSTAT, str(t)))
    yest_ord  = cnt("{} AND transaction_date='{}'".format(_RSTAT, ty))

    pending   = _int(_q("SELECT COUNT(*) FROM `tabSales Order` WHERE {} AND {}{}".format(_ON, _PEND, date_c)))
    cancelled = _int(_q("SELECT COUNT(*) FROM `tabSales Order` WHERE {} AND {}{}".format(_ON, _CANC, date_c)))
    shipped   = _int(_q("SELECT COUNT(*) FROM `tabSales Order` WHERE {} AND {}{}".format(_ON, _SHIP, date_c)))
    ship_proc = _int(_q("SELECT COUNT(*) FROM `tabSales Order` WHERE {} AND {}{}".format(_ON, _RSTAT, date_c)))
    # AOV = revenue ÷ Shipped+Processing order count (matches report logic)
    aov = round(total_rev / ship_proc, 2) if ship_proc else 0.0

    return {
        "total_revenue":    total_rev,
        "month_revenue":    month_rev,
        "month_rev_trend":  _trend(month_rev, lmonth_rev),
        "avg_order_value":  aov,
        "total_orders":     total_ord,
        "ship_proc_count":  ship_proc,           # absolute count for chip display
        "today_orders":     today_ord,
        "today_trend":      _trend(today_ord, yest_ord),
        "pending":          pending,
        "cancelled":        cancelled,
        "fulfil_shipped":   round(shipped   / total_ord * 100) if total_ord else 0,
        "fulfil_ship_proc": round(ship_proc / total_ord * 100) if total_ord else 0,
    }


# ================================================================
#  ONLINE SNAPSHOT
#  FIX: ALL queries now filter by woocommerce_status IN (Shipped, Processing)
# ================================================================

def _online_snapshot(fd, td):
    date_c = _date_clause(fd, td, alias="so")
    date_c_bare = _date_clause(fd, td, alias="")

    # Top customer — Shipped or Processing only
    tc = frappe.db.sql("""
        SELECT customer, COUNT(*) AS cnt, SUM(base_net_total) AS rev
        FROM `tabSales Order`
        WHERE docstatus=1 AND custom_offline__online='Online'
          AND {rstat} {date}
          AND customer IS NOT NULL AND customer != ''
        GROUP BY customer ORDER BY rev DESC LIMIT 1
    """.format(rstat=_RSTAT, date=date_c_bare), as_dict=True)

    # Top item — Shipped or Processing only
    ti = frappe.db.sql("""
        SELECT soi.item_name, SUM(soi.qty) AS qty, SUM(soi.amount) AS rev
        FROM `tabSales Order Item` soi
        JOIN `tabSales Order` so ON so.name=soi.parent
        WHERE so.docstatus=1 AND so.custom_offline__online='Online'
          AND so.{rstat} {date}
          AND soi.item_name IS NOT NULL AND soi.item_name != ''
        GROUP BY soi.item_name ORDER BY rev DESC LIMIT 1
    """.format(rstat=_RSTAT, date=date_c), as_dict=True)

    unique = _int(_q("""
        SELECT COUNT(DISTINCT customer) FROM `tabSales Order`
        WHERE docstatus=1 AND custom_offline__online='Online'
          AND {rstat} {date}
          AND customer IS NOT NULL AND customer != ''
    """.format(rstat=_RSTAT, date=date_c_bare)))

    repeat = _int(_q("""
        SELECT COUNT(*) FROM (
            SELECT customer FROM `tabSales Order`
            WHERE docstatus=1 AND custom_offline__online='Online'
              AND {rstat} {date}
              AND customer IS NOT NULL AND customer != ''
            GROUP BY customer HAVING COUNT(*) > 1
        ) t
    """.format(rstat=_RSTAT, date=date_c_bare)))

    total = _int(_q("""
        SELECT COUNT(*) FROM `tabSales Order`
        WHERE docstatus=1 AND custom_offline__online='Online'
          AND {rstat} {date}
    """.format(rstat=_RSTAT, date=date_c_bare)))

    # Get fulfillment rates
    shipped   = _int(_q("SELECT COUNT(*) FROM `tabSales Order` WHERE docstatus=1 AND custom_offline__online='Online' AND {}{}".format(_SHIP, date_c_bare)))
    ship_proc = _int(_q("SELECT COUNT(*) FROM `tabSales Order` WHERE docstatus=1 AND custom_offline__online='Online' AND {}{}".format(_RSTAT, date_c_bare)))
    all_online= _int(_q("SELECT COUNT(*) FROM `tabSales Order` WHERE docstatus=1 AND custom_offline__online='Online'{}".format(date_c_bare)))

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
        "fulfil_shipped":   round(shipped   / all_online * 100) if all_online else 0,
        "fulfil_ship_proc": round(ship_proc / all_online * 100) if all_online else 0,
    }


# ================================================================
#  ONLINE CHARTS
#  FIX: order_buckets, top_states, monthly_ord now filter by _RSTAT
# ================================================================

def _online_charts(t, fd, td):
    date_c      = _date_clause(fd, td, alias="")
    date_c_so   = _date_clause(fd, td, alias="so")

    # Status doughnut — all Online (no revenue filter, shows full picture)
    ws = frappe.db.sql("""
        SELECT woocommerce_status, COUNT(*) AS cnt
        FROM `tabSales Order`
        WHERE docstatus=1 AND custom_offline__online='Online' {}
          AND woocommerce_status IS NOT NULL AND woocommerce_status != ''
        GROUP BY woocommerce_status ORDER BY cnt DESC
    """.format(date_c), as_dict=True)

    # Payment method — Shipped or Processing only
    pm = frappe.db.sql("""
        SELECT woocommerce_payment_method, COUNT(*) AS cnt
        FROM `tabSales Order`
        WHERE docstatus=1 AND custom_offline__online='Online'
          AND {} {} AND woocommerce_payment_method IS NOT NULL
          AND woocommerce_payment_method != ''
        GROUP BY woocommerce_payment_method ORDER BY cnt DESC LIMIT 6
    """.format(_RSTAT, date_c), as_dict=True)

    # Order value buckets — Shipped or Processing only (FIX)
    buckets = [
        ("Under ₹500",       0,    500),
        ("₹500 – ₹1,000",  500,   1000),
        ("₹1,000 – ₹2,000", 1000,  2000),
        ("₹2,000 – ₹5,000", 2000,  5000),
        ("Above ₹5,000",    5000, 999999999),
    ]
    bkt_labels, bkt_values = [], []
    for lbl, lo, hi in buckets:
        c = _int(_q("""
            SELECT COUNT(*) FROM `tabSales Order`
            WHERE docstatus=1 AND custom_offline__online='Online'
              AND {} AND base_net_total >= %s AND base_net_total < %s {}
        """.format(_RSTAT, date_c), [lo, hi]))
        bkt_labels.append(lbl)
        bkt_values.append(c)

    # Top states — Shipped or Processing only (FIX)
    addr_rows = frappe.db.sql("""
        SELECT shipping_address_name, COUNT(*) AS cnt
        FROM `tabSales Order`
        WHERE docstatus=1 AND custom_offline__online='Online'
          AND {} {} AND shipping_address_name IS NOT NULL
          AND shipping_address_name != ''
        GROUP BY shipping_address_name ORDER BY cnt DESC LIMIT 20
    """.format(_RSTAT, date_c), as_dict=True)
    state_map = {}
    for r in addr_rows:
        try:
            st = frappe.db.get_value("Address", r.shipping_address_name, "state")
            key = st if st else r.shipping_address_name
        except Exception:
            key = r.shipping_address_name
        state_map[key] = state_map.get(key, 0) + _int(r.cnt)
    sorted_states = sorted(state_map.items(), key=lambda x: x[1], reverse=True)[:8]

    # Monthly revenue — Shipped or Processing only (already correct)
    _mo_date = "AND transaction_date >= DATE_SUB('{}', INTERVAL 6 MONTH)".format(str(t)) if not (fd and td) else "AND transaction_date BETWEEN '{}' AND '{}'".format(fd, td)
    mr = frappe.db.sql("""
        SELECT YEAR(transaction_date) AS yr,
               MONTH(transaction_date) AS mo_num,
               YEAR(transaction_date)*100+MONTH(transaction_date) AS sk,
               COALESCE(SUM(base_net_total),0) AS rev
        FROM `tabSales Order`
        WHERE docstatus=1 AND custom_offline__online='Online'
          AND {} {}
          AND transaction_date IS NOT NULL
        GROUP BY yr, mo_num, sk ORDER BY sk
    """.format(_RSTAT, _mo_date), as_dict=True)

    # Monthly order volume — Shipped or Processing only (FIX)
    mo = frappe.db.sql("""
        SELECT YEAR(transaction_date) AS yr,
               MONTH(transaction_date) AS mo_num,
               YEAR(transaction_date)*100+MONTH(transaction_date) AS sk,
               COUNT(*) AS cnt
        FROM `tabSales Order`
        WHERE docstatus=1 AND custom_offline__online='Online'
          AND {} {}
          AND transaction_date IS NOT NULL
        GROUP BY yr, mo_num, sk ORDER BY sk
    """.format(_RSTAT, _mo_date), as_dict=True)

    return {
        "woo_status":    {"labels": [r.woocommerce_status for r in ws],           "datasets": [{"values": [_int(r.cnt) for r in ws]}]},
        "payment":       {"labels": [r.woocommerce_payment_method for r in pm],   "datasets": [{"values": [_int(r.cnt) for r in pm]}]},
        "order_buckets": {"labels": bkt_labels,                                   "datasets": [{"values": bkt_values}]},
        "top_states":    {"labels": [s[0] for s in sorted_states],                "datasets": [{"values": [s[1] for s in sorted_states]}]},
        "monthly_rev":   {"labels": [_mo_label(r.yr, r.mo_num) for r in mr],                           "datasets": [{"values": [_flt(r.rev) for r in mr]}]},
        "monthly_ord":   {"labels": [_mo_label(r.yr, r.mo_num) for r in mo],                           "datasets": [{"values": [_int(r.cnt) for r in mo]}]},
    }


# ================================================================
#  ONLINE TOP 5
#  FIX: customers query now filters by _RSTAT
# ================================================================

def _online_top5(fd, td):
    date_c    = _date_clause(fd, td, alias="so")
    date_c_b  = _date_clause(fd, td, alias="")

    items = frappe.db.sql("""
        SELECT soi.item_name, SUM(soi.qty) AS tq, SUM(soi.amount) AS tr
        FROM `tabSales Order Item` soi
        JOIN `tabSales Order` so ON so.name=soi.parent
        WHERE so.docstatus=1 AND so.custom_offline__online='Online'
          AND so.{rstat} {date}
          AND soi.item_name IS NOT NULL AND soi.item_name != ''
        GROUP BY soi.item_name ORDER BY tr DESC LIMIT 6
    """.format(rstat=_RSTAT, date=date_c), as_dict=True)

    # Customers — Shipped or Processing only (FIX)
    custs = frappe.db.sql("""
        SELECT customer, COUNT(*) AS oc, SUM(base_net_total) AS tr
        FROM `tabSales Order`
        WHERE docstatus=1 AND custom_offline__online='Online'
          AND {rstat} {date}
          AND customer IS NOT NULL AND customer != ''
        GROUP BY customer ORDER BY tr DESC LIMIT 5
    """.format(rstat=_RSTAT, date=date_c_b), as_dict=True)

    im = _flt(items[0].tr) if items else 1
    cm = _flt(custs[0].tr) if custs else 1
    return {
        "items":     [{"name": r.item_name, "qty": _int(r.tq), "rev": _flt(r.tr), "pct": round(_flt(r.tr)/im*100)} for r in items],
        "customers": [{"name": r.customer,  "cnt": _int(r.oc), "rev": _flt(r.tr), "pct": round(_flt(r.tr)/cm*100)} for r in custs],
    }


def _online_recent(fd, td):
    date_c = _date_clause(fd, td, alias="")
    # Recent feed shows all Online orders (not filtered by status) so staff can see pending/cancelled too
    rows = frappe.db.sql("""
        SELECT name, customer, transaction_date, grand_total, woocommerce_status
        FROM `tabSales Order`
        WHERE docstatus=1 AND custom_offline__online='Online' {}
        ORDER BY creation DESC LIMIT 8
    """.format(date_c), as_dict=True)
    return [{"name": r.name, "customer": r.customer or "—",
             "transaction_date": str(r.transaction_date or ""),
             "grand_total": _flt(r.grand_total),
             "status": r.woocommerce_status or "—"} for r in rows]


def _online_pipeline(fd, td):
    date_c = _date_clause(fd, td, alias="")
    rows = frappe.db.sql("""
        SELECT woocommerce_status, COUNT(*) AS cnt
        FROM `tabSales Order`
        WHERE docstatus=1 AND custom_offline__online='Online' {}
          AND woocommerce_status IS NOT NULL AND woocommerce_status != ''
        GROUP BY woocommerce_status
    """.format(date_c), as_dict=True)
    sm = {r.woocommerce_status: _int(r.cnt) for r in rows}
    def p(*keys): return sum(sm.get(k, 0) for k in keys)
    total     = _int(_q("SELECT COUNT(*) FROM `tabSales Order` WHERE docstatus=1 AND custom_offline__online='Online'{}".format(date_c)))
    shipped   = p("Shipped","shipped")
    proc      = p("Processing","processing")
    return {
        "pending":          p("Pending","pending"),
        "on_hold":          p("on-hold"),
        "processing":       proc,
        "shipped":          shipped,
        "refunded":         p("Refunded","refunded"),
        "cancelled":        p("Cancelled","cancelled","failed"),
        "total":            total,
        "fulfil_shipped":   round(shipped          / total * 100) if total else 0,
        "fulfil_ship_proc": round((shipped + proc) / total * 100) if total else 0,
    }


# ================================================================
#  OFFLINE FUNCTIONS
# ================================================================

_OFF = "docstatus=1 AND custom_offline__online='Offline'"


def _offline_kpis(t, ms, me, lms, lme, fd, td):
    date_c = _date_clause(fd, td, alias="")

    def rev(extra=""):
        w = "WHERE " + _OFF + (" AND " + extra if extra else "") + date_c
        return _flt(_q("SELECT COALESCE(SUM(base_net_total),0) FROM `tabSales Order` " + w))

    def cnt(extra=""):
        w = "WHERE " + _OFF + (" AND " + extra if extra else "") + date_c
        return _int(_q("SELECT COUNT(*) FROM `tabSales Order` " + w))

    if fd and td:
        total_rev  = rev()
        month_rev  = total_rev
        lmonth_rev = 0.0
    else:
        total_rev  = rev()
        month_rev  = rev("transaction_date BETWEEN '{}' AND '{}'".format(ms, me))
        lmonth_rev = rev("transaction_date BETWEEN '{}' AND '{}'".format(lms, lme))

    total_ord = cnt()
    today_ord = cnt("transaction_date='{}' AND {}".format(str(t), _RSTAT))
    yest_ord  = cnt("transaction_date='{}' AND {}".format(str(add_days(t, -1)), _RSTAT))
    pending   = cnt("status='To Deliver and Bill'")
    cancelled = cnt("status='Cancelled'")
    completed = cnt("status='Completed'")
    aov       = round(total_rev / total_ord, 2) if total_ord else 0.0

    return {
        "total_revenue":   total_rev,
        "month_revenue":   month_rev,
        "month_rev_trend": _trend(month_rev, lmonth_rev),
        "avg_order_value": aov,
        "total_orders":    total_ord,
        "today_orders":    today_ord,
        "today_trend":     _trend(today_ord, yest_ord),
        "pending":         pending,
        "cancelled":       cancelled,
        "completion_rate": round(completed / total_ord * 100) if total_ord else 0,
    }


def _offline_snapshot(ms, me, fd, td):
    date_c = _date_clause(fd, td, alias="")
    date_c_so = _date_clause(fd, td, alias="so")
    tax_ms = fd if fd else ms
    tax_me = td if td else me

    tc = frappe.db.sql("""
        SELECT customer, COUNT(*) AS cnt, SUM(base_net_total) AS rev
        FROM `tabSales Order`
        WHERE docstatus=1 AND custom_offline__online='Offline' {}
          AND customer IS NOT NULL AND customer != ''
        GROUP BY customer ORDER BY rev DESC LIMIT 1
    """.format(date_c), as_dict=True)

    ti = frappe.db.sql("""
        SELECT soi.item_name, SUM(soi.qty) AS qty, SUM(soi.amount) AS rev
        FROM `tabSales Order Item` soi
        JOIN `tabSales Order` so ON so.name=soi.parent
        WHERE so.docstatus=1 AND so.custom_offline__online='Offline' {}
          AND soi.item_name IS NOT NULL AND soi.item_name != ''
        GROUP BY soi.item_name ORDER BY rev DESC LIMIT 1
    """.format(date_c_so), as_dict=True)

    # Dynamic tax for selected period
    tax_rows = frappe.db.sql("""
        SELECT stc.account_head, COALESCE(SUM(stc.tax_amount), 0) AS total
        FROM `tabSales Taxes and Charges` stc
        JOIN `tabSales Order` so ON so.name = stc.parent
        WHERE so.docstatus=1 AND so.custom_offline__online='Offline'
          AND so.transaction_date BETWEEN %s AND %s
          AND stc.tax_amount IS NOT NULL
          AND stc.charge_type != 'Actual'
        GROUP BY stc.account_head ORDER BY total DESC
    """, [tax_ms, tax_me], as_dict=True)

    sgst = cgst = igst = tds = 0.0
    for r in tax_rows:
        ah  = (r.account_head or "").upper()
        val = _flt(r.total)
        if "TDS" in ah or "TAX DEDUCT" in ah: tds  += val
        elif "IGST" in ah:                    igst += val
        elif "SGST" in ah:                    sgst += val
        elif "CGST" in ah:                    cgst += val

    gross = sgst + cgst + igst
    net   = gross - tds

    unique = _int(_q("""
        SELECT COUNT(DISTINCT customer) FROM `tabSales Order`
        WHERE docstatus=1 AND custom_offline__online='Offline' {}
          AND customer IS NOT NULL AND customer != ''
    """.format(date_c)))
    repeat = _int(_q("""
        SELECT COUNT(*) FROM (
            SELECT customer FROM `tabSales Order`
            WHERE docstatus=1 AND custom_offline__online='Offline' {}
              AND customer IS NOT NULL AND customer != ''
            GROUP BY customer HAVING COUNT(*) > 1
        ) t
    """.format(date_c)))
    total = _int(_q("SELECT COUNT(*) FROM `tabSales Order` WHERE docstatus=1 AND custom_offline__online='Offline'{}".format(date_c)))

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
        "avg_per_customer": round(total  / unique,  1)  if unique else 0.0,
    }


def _offline_charts(t, ms, me, fd, td):
    date_c = _date_clause(fd, td, alias="")
    date_c_so = _date_clause(fd, td, alias="so")
    tax_ms = fd if fd else ms
    tax_me = td if td else me

    st_rows = frappe.db.sql("""
        SELECT status, COUNT(*) AS cnt
        FROM `tabSales Order`
        WHERE docstatus=1 AND custom_offline__online='Offline' {}
          AND status IS NOT NULL AND status != ''
        GROUP BY status ORDER BY cnt DESC
    """.format(date_c), as_dict=True)

    _mo_date = "AND so.transaction_date >= DATE_SUB('{}', INTERVAL 6 MONTH)".format(str(t)) if not (fd and td) else "AND so.transaction_date BETWEEN '{}' AND '{}'".format(fd, td)
    tax_monthly = frappe.db.sql("""
        SELECT YEAR(so.transaction_date) AS yr,
               MONTH(so.transaction_date) AS mo_num,
               YEAR(so.transaction_date)*100+MONTH(so.transaction_date) AS sk,
               stc.account_head,
               COALESCE(SUM(stc.tax_amount), 0) AS total
        FROM `tabSales Taxes and Charges` stc
        JOIN `tabSales Order` so ON so.name = stc.parent
        WHERE so.docstatus=1 AND so.custom_offline__online='Offline'
          {} AND stc.tax_amount IS NOT NULL
        GROUP BY yr, mo_num, sk, stc.account_head ORDER BY sk
    """.format(_mo_date), as_dict=True)

    month_map = {}
    for r in tax_monthly:
        if r.sk not in month_map:
            month_map[r.sk] = {"mo": _mo_label(r.sk // 100, r.sk % 100), "sk": r.sk, "sgst": 0.0, "cgst": 0.0, "igst": 0.0, "tds": 0.0}
        ah  = (r.account_head or "").upper()
        val = _flt(r.total)
        if "TDS" in ah or "TAX DEDUCT" in ah: month_map[r.sk]["tds"]  += val
        elif "IGST" in ah:                    month_map[r.sk]["igst"] += val
        elif "SGST" in ah:                    month_map[r.sk]["sgst"] += val
        elif "CGST" in ah:                    month_map[r.sk]["cgst"] += val

    if not month_map:
        _ord_date = "AND transaction_date >= DATE_SUB('{}', INTERVAL 6 MONTH)".format(str(t)) if not (fd and td) else "AND transaction_date BETWEEN '{}' AND '{}'".format(fd, td)
        ord_rows = frappe.db.sql("""
            SELECT YEAR(transaction_date) AS yr,
                   MONTH(transaction_date) AS mo_num,
                   YEAR(transaction_date)*100+MONTH(transaction_date) AS sk
            FROM `tabSales Order`
            WHERE docstatus=1 AND custom_offline__online='Offline' {}
            GROUP BY yr, mo_num, sk ORDER BY sk
        """.format(_ord_date), as_dict=True)
        for r in ord_rows:
            month_map[r.sk] = {"mo": _mo_label(r.sk // 100, r.sk % 100), "sk": r.sk, "sgst": 0.0, "cgst": 0.0, "igst": 0.0, "tds": 0.0}

    sorted_months = sorted(month_map.values(), key=lambda x: x["sk"])

    off_buckets = [("Under ₹1,000",0,1000),("₹1,000 – ₹2,000",1000,2000),
                   ("₹2,000 – ₹5,000",2000,5000),("₹5,000 – ₹10,000",5000,10000),
                   ("Above ₹10,000",10000,999999999)]
    bkt_labels, bkt_values = [], []
    for lbl, lo, hi in off_buckets:
        c = _int(_q("""
            SELECT COUNT(*) FROM `tabSales Order`
            WHERE docstatus=1 AND custom_offline__online='Offline'
              AND base_net_total >= %s AND base_net_total < %s {}
        """.format(date_c), [lo, hi]))
        bkt_labels.append(lbl)
        bkt_values.append(c)

    _rev_date = "AND transaction_date >= DATE_SUB('{}', INTERVAL 6 MONTH)".format(str(t)) if not (fd and td) else "AND transaction_date BETWEEN '{}' AND '{}'".format(fd, td)
    rev_rows = frappe.db.sql("""
        SELECT YEAR(transaction_date) AS yr,
               MONTH(transaction_date) AS mo_num,
               YEAR(transaction_date)*100+MONTH(transaction_date) AS sk,
               COALESCE(SUM(base_net_total),0) AS rev
        FROM `tabSales Order`
        WHERE docstatus=1 AND custom_offline__online='Offline' {}
        GROUP BY yr, mo_num, sk ORDER BY sk
    """.format(_rev_date), as_dict=True)

    mo_rows = frappe.db.sql("""
        SELECT YEAR(transaction_date) AS yr,
               MONTH(transaction_date) AS mo_num,
               YEAR(transaction_date)*100+MONTH(transaction_date) AS sk,
               COUNT(*) AS cnt
        FROM `tabSales Order`
        WHERE docstatus=1 AND custom_offline__online='Offline' {}
        GROUP BY yr, mo_num, sk ORDER BY sk
    """.format(_rev_date), as_dict=True)

    return {
        "status":        {"labels": [r.status for r in st_rows], "datasets": [{"values": [_int(r.cnt) for r in st_rows]}]},
        "tax_monthly":   [{"mo": m["mo"],"sgst": round(m["sgst"],2),"cgst": round(m["cgst"],2),"igst": round(m["igst"],2),"tds": round(m["tds"],2)} for m in sorted_months],
        "order_buckets": {"labels": bkt_labels, "datasets": [{"values": bkt_values}]},
        "monthly_rev":   {"labels": [_mo_label(r.yr, r.mo_num) for r in rev_rows], "datasets": [{"values": [_flt(r.rev) for r in rev_rows]}]},
        "monthly_ord":   {"labels": [_mo_label(r.yr, r.mo_num) for r in mo_rows],  "datasets": [{"values": [_int(r.cnt) for r in mo_rows]}]},
    }


def _offline_top5(fd, td):
    date_c    = _date_clause(fd, td, alias="so")
    date_c_b  = _date_clause(fd, td, alias="")

    items = frappe.db.sql("""
        SELECT soi.item_name, SUM(soi.qty) AS tq, SUM(soi.amount) AS tr
        FROM `tabSales Order Item` soi
        JOIN `tabSales Order` so ON so.name=soi.parent
        WHERE so.docstatus=1 AND so.custom_offline__online='Offline' {}
          AND soi.item_name IS NOT NULL AND soi.item_name != ''
        GROUP BY soi.item_name ORDER BY tr DESC LIMIT 6
    """.format(date_c), as_dict=True)

    custs = frappe.db.sql("""
        SELECT customer, COUNT(*) AS oc, SUM(base_net_total) AS tr
        FROM `tabSales Order`
        WHERE docstatus=1 AND custom_offline__online='Offline' {}
          AND customer IS NOT NULL AND customer != ''
        GROUP BY customer ORDER BY tr DESC LIMIT 5
    """.format(date_c_b), as_dict=True)

    im = _flt(items[0].tr) if items else 1
    cm = _flt(custs[0].tr) if custs else 1
    return {
        "items":     [{"name": r.item_name, "qty": _int(r.tq), "rev": _flt(r.tr), "pct": round(_flt(r.tr)/im*100)} for r in items],
        "customers": [{"name": r.customer,  "cnt": _int(r.oc), "rev": _flt(r.tr), "pct": round(_flt(r.tr)/cm*100)} for r in custs],
    }


def _offline_recent(fd, td):
    date_c = _date_clause(fd, td, alias="")
    rows = frappe.db.sql("""
        SELECT name, customer, transaction_date, grand_total, status
        FROM `tabSales Order`
        WHERE docstatus=1 AND custom_offline__online='Offline' {}
        ORDER BY creation DESC LIMIT 8
    """.format(date_c), as_dict=True)
    return [{"name": r.name, "customer": r.customer or "—",
             "transaction_date": str(r.transaction_date or ""),
             "grand_total": _flt(r.grand_total),
             "status": r.status or "—"} for r in rows]


def _offline_pipeline(fd, td):
    date_c = _date_clause(fd, td, alias="")
    rows = frappe.db.sql("""
        SELECT status, COUNT(*) AS cnt
        FROM `tabSales Order`
        WHERE docstatus=1 AND custom_offline__online='Offline' {}
          AND status IS NOT NULL AND status != ''
        GROUP BY status
    """.format(date_c), as_dict=True)
    sm    = {r.status: _int(r.cnt) for r in rows}
    total = _int(_q("SELECT COUNT(*) FROM `tabSales Order` WHERE docstatus=1 AND custom_offline__online='Offline'{}".format(date_c)))
    comp  = sm.get("Completed", 0)
    return {
        "draft":           sm.get("Draft", 0),
        "to_deliver_bill": sm.get("To Deliver and Bill", 0),
        "to_bill":         sm.get("To Bill", 0),
        "completed":       comp,
        "cancelled":       sm.get("Cancelled", 0),
        "total":           total,
        "completion_rate": round(comp / total * 100) if total else 0,
    }