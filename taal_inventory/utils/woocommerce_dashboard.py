import frappe
from frappe.utils import today, add_days, get_first_day, get_last_day, getdate


# ================================================================
#  Orders Dashboard - backend (v4)
#  Alignment rules (matched to the "Basic Report"):
#    - docstatus IN (0,1)  -> drafts included, Online and Offline
#    - Online also requires woocommerce_server to be set
#    - Active statuses: shipped, processing, processing lp,
#      delivered, picked up (case-insensitive)
#    - Revenue = SUM(Sales Order Item.base_amount) of active orders
#    - Pending = "Pending Payment"
#    - from_date / to_date validated via getdate before SQL use
# ================================================================


@frappe.whitelist()
def get_woocommerce_dashboard_data(mode="online", from_date=None, to_date=None, period_label=None):
    t  = today()
    fd = _san_date(from_date)
    td = _san_date(to_date)

    ms  = str(get_first_day(t))
    me  = str(get_last_day(t))
    lms = str(get_first_day(add_days(ms, -1)))
    lme = str(add_days(ms, -1))

    if mode == "offline":
        return {
            "mode":         "offline",
            "period_label": period_label or "All Time",
            "from_date":    fd or "",
            "to_date":      td or "",
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
            "from_date":    fd or "",
            "to_date":      td or "",
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

def _san_date(val):
    if not val:
        return None
    try:
        return str(getdate(val))
    except Exception:
        return None

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

_MONTH_NAMES = ["","Jan","Feb","Mar","Apr","May","Jun",
                "Jul","Aug","Sep","Oct","Nov","Dec"]

def _mo_label(yr, mo):
    try:
        return _MONTH_NAMES[int(mo)] + " " + str(yr)
    except Exception:
        return str(yr) + "-" + str(mo)

def _date_clause(fd, td, alias=""):
    # alias is "" or "so."
    if fd and td:
        return " AND {}transaction_date BETWEEN '{}' AND '{}'".format(alias, fd, td)
    return ""


# ================================================================
#  CONDITION BUILDERS  (alias is "" or "so.")
# ================================================================

def _on(alias=""):
    return ("{a}docstatus IN (0,1) AND {a}custom_offline__online='Online' "
            "AND IFNULL({a}woocommerce_server,'') != ''").format(a=alias)

def _off(alias=""):
    return "{a}docstatus IN (0,1) AND {a}custom_offline__online='Offline'".format(a=alias)

def _rstat(alias=""):
    # Active statuses, matched to the Basic Report rule
    return ("LOWER({a}woocommerce_status) IN "
            "('shipped','processing','processing lp','delivered','picked up')").format(a=alias)

def _wstat(alias, *vals):
    quoted = ", ".join(["'" + v + "'" for v in vals])
    return "LOWER({a}woocommerce_status) IN ({v})".format(a=alias, v=quoted)


# ================================================================
#  ONLINE KPIs
# ================================================================

def _online_kpis(t, ms, me, lms, lme, fd, td):
    dc    = _date_clause(fd, td, alias="")
    dc_so = _date_clause(fd, td, alias="so.")

    def rev(extra=""):
        w = "WHERE {} AND {}".format(_on("so."), _rstat("so."))
        if extra:
            w += " AND " + extra
        if dc_so:
            w += dc_so
        return _flt(_q(
            "SELECT COALESCE(SUM(soi.base_amount),0) "
            "FROM `tabSales Order Item` soi "
            "JOIN `tabSales Order` so ON so.name = soi.parent " + w))

    def cnt(extra=""):
        w = "WHERE " + _on("")
        if extra:
            w += " AND " + extra
        if dc:
            w += dc
        return _int(_q("SELECT COUNT(*) FROM `tabSales Order` " + w))

    if fd and td:
        total_rev  = rev()
        month_rev  = total_rev
        lmonth_rev = 0.0
    else:
        total_rev  = rev()
        month_rev  = rev("so.transaction_date BETWEEN '{}' AND '{}'".format(ms, me))
        lmonth_rev = rev("so.transaction_date BETWEEN '{}' AND '{}'".format(lms, lme))

    ty         = str(add_days(t, -1))
    total_ord  = cnt()
    active_cnt = cnt(_rstat(""))
    today_ord  = cnt("{} AND transaction_date='{}'".format(_rstat(""), str(t)))
    yest_ord   = cnt("{} AND transaction_date='{}'".format(_rstat(""), ty))

    pending   = cnt(_wstat("", "pending payment", "pending"))
    cancelled = cnt(_wstat("", "cancelled"))
    fulfilled = cnt(_wstat("", "shipped", "delivered", "picked up"))

    aov = round(total_rev / active_cnt, 2) if active_cnt else 0.0

    return {
        "total_revenue":    total_rev,
        "month_revenue":    month_rev,
        "month_rev_trend":  _trend(month_rev, lmonth_rev),
        "avg_order_value":  aov,
        "total_orders":     total_ord,
        "ship_proc_count":  active_cnt,
        "today_orders":     today_ord,
        "today_trend":      _trend(today_ord, yest_ord),
        "pending":          pending,
        "cancelled":        cancelled,
        "fulfil_shipped":   round(fulfilled  / total_ord * 100) if total_ord else 0,
        "fulfil_ship_proc": round(active_cnt / total_ord * 100) if total_ord else 0,
    }


# ================================================================
#  ONLINE SNAPSHOT  (tie-aware top customer, item-based revenue)
# ================================================================

def _online_snapshot(fd, td):
    dc    = _date_clause(fd, td, alias="")
    dc_so = _date_clause(fd, td, alias="so.")

    tc_all = frappe.db.sql("""
        SELECT so.customer, COUNT(DISTINCT so.name) AS cnt, SUM(soi.base_amount) AS rev
        FROM `tabSales Order Item` soi
        JOIN `tabSales Order` so ON so.name = soi.parent
        WHERE {on} AND {rstat} {date}
          AND so.customer IS NOT NULL AND so.customer != ''
        GROUP BY so.customer ORDER BY rev DESC LIMIT 5
    """.format(on=_on("so."), rstat=_rstat("so."), date=dc_so), as_dict=True)

    tc = []
    if tc_all:
        top_rev = _flt(tc_all[0].rev)
        for r in tc_all:
            if _flt(r.rev) >= top_rev:
                tc.append(r)
            else:
                break

    ti = frappe.db.sql("""
        SELECT soi.item_name, SUM(soi.qty) AS qty, SUM(soi.base_amount) AS rev
        FROM `tabSales Order Item` soi
        JOIN `tabSales Order` so ON so.name = soi.parent
        WHERE {on} AND {rstat} {date}
          AND soi.item_name IS NOT NULL AND soi.item_name != ''
        GROUP BY soi.item_name ORDER BY rev DESC LIMIT 1
    """.format(on=_on("so."), rstat=_rstat("so."), date=dc_so), as_dict=True)

    unique = _int(_q("""
        SELECT COUNT(DISTINCT customer) FROM `tabSales Order`
        WHERE {on} AND {rstat} {date}
          AND customer IS NOT NULL AND customer != ''
    """.format(on=_on(""), rstat=_rstat(""), date=dc)))

    repeat = _int(_q("""
        SELECT COUNT(*) FROM (
            SELECT customer FROM `tabSales Order`
            WHERE {on} AND {rstat} {date}
              AND customer IS NOT NULL AND customer != ''
            GROUP BY customer HAVING COUNT(*) > 1
        ) t
    """.format(on=_on(""), rstat=_rstat(""), date=dc)))

    total = _int(_q("SELECT COUNT(*) FROM `tabSales Order` WHERE {} AND {}{}".format(
        _on(""), _rstat(""), dc)))

    fulfilled  = _int(_q("SELECT COUNT(*) FROM `tabSales Order` WHERE {} AND {}{}".format(
        _on(""), _wstat("", "shipped", "delivered", "picked up"), dc)))
    active     = _int(_q("SELECT COUNT(*) FROM `tabSales Order` WHERE {} AND {}{}".format(
        _on(""), _rstat(""), dc)))
    all_online = _int(_q("SELECT COUNT(*) FROM `tabSales Order` WHERE {}{}".format(
        _on(""), dc)))

    top_cust_display = " & ".join([r.customer for r in tc]) if tc else "—"
    top_cust_cnt     = _int(tc[0].cnt) if tc else 0
    top_cust_rev     = _flt(tc[0].rev) if tc else 0.0
    if len(tc) > 1:
        top_cust_cnt = sum(_int(r.cnt) for r in tc)

    return {
        "top_customer":     top_cust_display,
        "top_cust_cnt":     top_cust_cnt,
        "top_cust_rev":     top_cust_rev,
        "top_item":         ti[0].item_name if ti else "—",
        "top_item_qty":     _int(ti[0].qty) if ti else 0,
        "top_item_rev":     _flt(ti[0].rev) if ti else 0.0,
        "unique_customers": unique,
        "repeat_customers": repeat,
        "repeat_rate":      round(repeat / unique * 100) if unique else 0,
        "avg_per_customer": round(total / unique, 1) if unique else 0.0,
        "fulfil_shipped":   round(fulfilled / all_online * 100) if all_online else 0,
        "fulfil_ship_proc": round(active    / all_online * 100) if all_online else 0,
    }


# ================================================================
#  ONLINE CHARTS
# ================================================================

def _online_charts(t, fd, td):
    dc    = _date_clause(fd, td, alias="")
    dc_so = _date_clause(fd, td, alias="so.")

    # Status donut: order count + item revenue per status (all statuses)
    ws = frappe.db.sql("""
        SELECT so.woocommerce_status AS st,
               COUNT(DISTINCT so.name) AS cnt,
               COALESCE(SUM(soi.base_amount),0) AS rev
        FROM `tabSales Order` so
        LEFT JOIN `tabSales Order Item` soi ON soi.parent = so.name
        WHERE {on} {date}
          AND so.woocommerce_status IS NOT NULL AND so.woocommerce_status != ''
        GROUP BY so.woocommerce_status ORDER BY cnt DESC
    """.format(on=_on("so."), date=dc_so), as_dict=True)

    # Payment donut: order count + item revenue per method (active only)
    pm = frappe.db.sql("""
        SELECT so.woocommerce_payment_method AS pmname,
               COUNT(DISTINCT so.name) AS cnt,
               COALESCE(SUM(soi.base_amount),0) AS rev
        FROM `tabSales Order` so
        LEFT JOIN `tabSales Order Item` soi ON soi.parent = so.name
        WHERE {on} AND {rstat} {date}
          AND so.woocommerce_payment_method IS NOT NULL
          AND so.woocommerce_payment_method != ''
        GROUP BY so.woocommerce_payment_method ORDER BY cnt DESC LIMIT 6
    """.format(on=_on("so."), rstat=_rstat("so."), date=dc_so), as_dict=True)

    buckets = [
        ("Under ₹500",       0,    500),
        ("₹500 – ₹1,000",  500,   1000),
        ("₹1,000 – ₹2,000", 1000,  2000),
        ("₹2,000 – ₹5,000", 2000,  5000),
        ("Above ₹5,000",    5000, 999999999),
    ]
    bkt_labels, bkt_values, bkt_rev = [], [], []
    for lbl, lo, hi in buckets:
        row = frappe.db.sql("""
            SELECT COUNT(*) AS cnt, COALESCE(SUM(base_net_total),0) AS rev
            FROM `tabSales Order`
            WHERE {on} AND {rstat}
              AND base_net_total >= %s AND base_net_total < %s {date}
        """.format(on=_on(""), rstat=_rstat(""), date=dc), [lo, hi], as_dict=True)
        bkt_labels.append(lbl)
        bkt_values.append(_int(row[0].cnt) if row else 0)
        bkt_rev.append(_flt(row[0].rev) if row else 0.0)

    state_rows = frappe.db.sql("""
        SELECT COALESCE(NULLIF(TRIM(addr.state), ''), 'Unknown') AS state,
               COUNT(*) AS cnt,
               COALESCE(SUM(so.base_net_total),0) AS rev
        FROM `tabSales Order` so
        LEFT JOIN `tabAddress` addr ON addr.name = so.shipping_address_name
        WHERE {on} AND {rstat} {date}
          AND IFNULL(so.shipping_address_name,'') != ''
        GROUP BY state ORDER BY cnt DESC LIMIT 8
    """.format(on=_on("so."), rstat=_rstat("so."), date=dc_so), as_dict=True)

    if fd and td:
        win_so = " AND so.transaction_date BETWEEN '{}' AND '{}'".format(fd, td)
        win    = " AND transaction_date BETWEEN '{}' AND '{}'".format(fd, td)
    else:
        win_so = " AND so.transaction_date >= DATE_SUB('{}', INTERVAL 6 MONTH)".format(str(t))
        win    = " AND transaction_date >= DATE_SUB('{}', INTERVAL 6 MONTH)".format(str(t))

    mr = frappe.db.sql("""
        SELECT YEAR(so.transaction_date) AS yr,
               MONTH(so.transaction_date) AS mo_num,
               YEAR(so.transaction_date)*100+MONTH(so.transaction_date) AS sk,
               COALESCE(SUM(soi.base_amount),0) AS rev
        FROM `tabSales Order Item` soi
        JOIN `tabSales Order` so ON so.name = soi.parent
        WHERE {on} AND {rstat} {win}
          AND so.transaction_date IS NOT NULL
        GROUP BY yr, mo_num, sk ORDER BY sk
    """.format(on=_on("so."), rstat=_rstat("so."), win=win_so), as_dict=True)

    mo = frappe.db.sql("""
        SELECT YEAR(transaction_date) AS yr,
               MONTH(transaction_date) AS mo_num,
               YEAR(transaction_date)*100+MONTH(transaction_date) AS sk,
               COUNT(*) AS cnt
        FROM `tabSales Order`
        WHERE {on} AND {rstat} {win}
          AND transaction_date IS NOT NULL
        GROUP BY yr, mo_num, sk ORDER BY sk
    """.format(on=_on(""), rstat=_rstat(""), win=win), as_dict=True)

    # ── Per-month full detail for the chart tooltips ──────────
    detail = {}

    def _slot(sk):
        if sk not in detail:
            detail[sk] = {
                "label": _mo_label(sk // 100, sk % 100),
                "orders": 0, "qty": 0.0, "revenue": 0.0, "aov": 0.0,
                "grand": 0.0, "rounded": 0.0, "courier": 0.0,
                "all_orders": 0, "shipped": 0, "delivered": 0,
                "processing": 0, "pending": 0, "on_hold": 0,
                "cancelled": 0, "trash": 0, "failed_refunded": 0, "other": 0
            }
        return detail[sk]

    # Header sums per month: active orders only
    hdr = frappe.db.sql("""
        SELECT YEAR(transaction_date)*100+MONTH(transaction_date) AS sk,
               COUNT(*) AS cnt,
               COALESCE(SUM(base_grand_total),0) AS grand,
               COALESCE(SUM(CASE WHEN IFNULL(base_rounded_total,0) != 0
                                 THEN base_rounded_total
                                 ELSE base_grand_total END),0) AS rounded,
               COALESCE(SUM(base_total_taxes_and_charges),0) AS courier
        FROM `tabSales Order`
        WHERE {on} AND {rstat} {win}
          AND transaction_date IS NOT NULL
        GROUP BY sk
    """.format(on=_on(""), rstat=_rstat(""), win=win), as_dict=True)
    for r in hdr:
        s = _slot(r.sk)
        s["orders"]  = _int(r.cnt)
        s["grand"]   = _flt(r.grand)
        s["rounded"] = _flt(r.rounded)
        s["courier"] = _flt(r.courier)

    # Item sums per month: revenue and qty, active orders only
    itm = frappe.db.sql("""
        SELECT YEAR(so.transaction_date)*100+MONTH(so.transaction_date) AS sk,
               COALESCE(SUM(soi.base_amount),0) AS rev,
               COALESCE(SUM(soi.qty),0) AS qty
        FROM `tabSales Order Item` soi
        JOIN `tabSales Order` so ON so.name = soi.parent
        WHERE {on} AND {rstat} {win}
          AND so.transaction_date IS NOT NULL
        GROUP BY sk
    """.format(on=_on("so."), rstat=_rstat("so."), win=win_so), as_dict=True)
    for r in itm:
        s = _slot(r.sk)
        s["revenue"] = _flt(r.rev)
        s["qty"]     = _flt(r.qty)

    # Status counts per month: every Online order
    stm = frappe.db.sql("""
        SELECT YEAR(transaction_date)*100+MONTH(transaction_date) AS sk,
               LOWER(IFNULL(woocommerce_status,'')) AS st,
               COUNT(*) AS cnt
        FROM `tabSales Order`
        WHERE {on} {win}
          AND transaction_date IS NOT NULL
        GROUP BY sk, st
    """.format(on=_on(""), win=win), as_dict=True)
    for r in stm:
        s  = _slot(r.sk)
        st = (r.st or "").strip()
        c  = _int(r.cnt)
        s["all_orders"] += c
        if st == "shipped":                           s["shipped"]         += c
        elif st in ("delivered", "picked up"):        s["delivered"]       += c
        elif st in ("processing", "processing lp"):   s["processing"]      += c
        elif st in ("pending payment", "pending"):    s["pending"]         += c
        elif st in ("on hold", "on-hold"):            s["on_hold"]         += c
        elif st == "cancelled":                       s["cancelled"]       += c
        elif st == "trash":                           s["trash"]           += c
        elif st in ("failed", "refunded"):            s["failed_refunded"] += c
        else:                                         s["other"]           += c

    monthly_detail = {}
    for s in detail.values():
        s["aov"] = round(s["revenue"] / s["orders"], 2) if s["orders"] else 0.0
        monthly_detail[s["label"]] = s

    return {
        "woo_status":    {"labels": [r.st for r in ws],          "datasets": [{"values": [_int(r.cnt) for r in ws]}], "revenue": [_flt(r.rev) for r in ws]},
        "payment":       {"labels": [r.pmname for r in pm],      "datasets": [{"values": [_int(r.cnt) for r in pm]}], "revenue": [_flt(r.rev) for r in pm]},
        "order_buckets": {"labels": bkt_labels,                  "datasets": [{"values": bkt_values}],                "revenue": bkt_rev},
        "top_states":    {"labels": [r.state for r in state_rows], "datasets": [{"values": [_int(r.cnt) for r in state_rows]}], "revenue": [_flt(r.rev) for r in state_rows]},
        "monthly_rev":   {"labels": [_mo_label(r.yr, r.mo_num) for r in mr], "datasets": [{"values": [_flt(r.rev) for r in mr]}]},
        "monthly_ord":   {"labels": [_mo_label(r.yr, r.mo_num) for r in mo], "datasets": [{"values": [_int(r.cnt) for r in mo]}]},
        "monthly_detail": monthly_detail,
    }


# ================================================================
#  ONLINE TOP 5
# ================================================================

def _online_top5(fd, td):
    dc_so = _date_clause(fd, td, alias="so.")

    items = frappe.db.sql("""
        SELECT soi.item_name, SUM(soi.qty) AS tq, SUM(soi.base_amount) AS tr
        FROM `tabSales Order Item` soi
        JOIN `tabSales Order` so ON so.name = soi.parent
        WHERE {on} AND {rstat} {date}
          AND soi.item_name IS NOT NULL AND soi.item_name != ''
        GROUP BY soi.item_name ORDER BY tr DESC LIMIT 6
    """.format(on=_on("so."), rstat=_rstat("so."), date=dc_so), as_dict=True)

    custs = frappe.db.sql("""
        SELECT so.customer, COUNT(DISTINCT so.name) AS oc, SUM(soi.base_amount) AS tr
        FROM `tabSales Order Item` soi
        JOIN `tabSales Order` so ON so.name = soi.parent
        WHERE {on} AND {rstat} {date}
          AND so.customer IS NOT NULL AND so.customer != ''
        GROUP BY so.customer ORDER BY tr DESC LIMIT 5
    """.format(on=_on("so."), rstat=_rstat("so."), date=dc_so), as_dict=True)

    im = _flt(items[0].tr) if items else 1
    cm = _flt(custs[0].tr) if custs else 1
    return {
        "items":     [{"name": r.item_name, "qty": _int(r.tq), "rev": _flt(r.tr), "pct": round(_flt(r.tr)/im*100)} for r in items],
        "customers": [{"name": r.customer,  "cnt": _int(r.oc), "rev": _flt(r.tr), "pct": round(_flt(r.tr)/cm*100)} for r in custs],
    }


def _online_recent(fd, td):
    dc = _date_clause(fd, td, alias="")
    rows = frappe.db.sql("""
        SELECT name, customer, transaction_date, grand_total, woocommerce_status
        FROM `tabSales Order`
        WHERE {on} {date}
        ORDER BY creation DESC LIMIT 8
    """.format(on=_on(""), date=dc), as_dict=True)
    return [{"name": r.name, "customer": r.customer or "—",
             "transaction_date": str(r.transaction_date or ""),
             "grand_total": _flt(r.grand_total),
             "status": r.woocommerce_status or "—"} for r in rows]


def _online_pipeline(fd, td):
    dc = _date_clause(fd, td, alias="")
    rows = frappe.db.sql("""
        SELECT woocommerce_status, COUNT(*) AS cnt
        FROM `tabSales Order`
        WHERE {on} {date}
          AND woocommerce_status IS NOT NULL AND woocommerce_status != ''
        GROUP BY woocommerce_status
    """.format(on=_on(""), date=dc), as_dict=True)

    sm = {}
    for r in rows:
        key = (r.woocommerce_status or "").strip().lower()
        sm[key] = sm.get(key, 0) + _int(r.cnt)

    total      = _int(_q("SELECT COUNT(*) FROM `tabSales Order` WHERE {}{}".format(_on(""), dc)))
    shipped    = sm.get("shipped", 0)
    delivered  = sm.get("delivered", 0) + sm.get("picked up", 0)
    processing = sm.get("processing", 0) + sm.get("processing lp", 0)
    fulfilled  = shipped + delivered
    active     = fulfilled + processing

    return {
        "pending":          sm.get("pending payment", 0) + sm.get("pending", 0),
        "on_hold":          sm.get("on hold", 0) + sm.get("on-hold", 0),
        "processing":       processing,
        "shipped":          shipped,
        "delivered":        delivered,
        "refunded":         sm.get("refunded", 0) + sm.get("failed", 0),
        "cancelled":        sm.get("cancelled", 0) + sm.get("trash", 0),
        "total":            total,
        "fulfil_shipped":   round(fulfilled / total * 100) if total else 0,
        "fulfil_ship_proc": round(active    / total * 100) if total else 0,
    }


# ================================================================
#  OFFLINE FUNCTIONS  (drafts included: docstatus IN (0,1))
# ================================================================

def _offline_kpis(t, ms, me, lms, lme, fd, td):
    dc = _date_clause(fd, td, alias="")

    def rev(extra=""):
        w = "WHERE " + _off("") + (" AND " + extra if extra else "") + dc
        return _flt(_q("SELECT COALESCE(SUM(base_net_total),0) FROM `tabSales Order` " + w))

    def cnt(extra=""):
        w = "WHERE " + _off("") + (" AND " + extra if extra else "") + dc
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
    today_ord = cnt("transaction_date='{}'".format(str(t)))
    yest_ord  = cnt("transaction_date='{}'".format(str(add_days(t, -1))))
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
    dc     = _date_clause(fd, td, alias="")
    dc_so  = _date_clause(fd, td, alias="so.")
    tax_ms = fd if fd else ms
    tax_me = td if td else me

    tc_all = frappe.db.sql("""
        SELECT customer, COUNT(*) AS cnt, SUM(base_net_total) AS rev
        FROM `tabSales Order`
        WHERE {off} {date}
          AND customer IS NOT NULL AND customer != ''
        GROUP BY customer ORDER BY rev DESC LIMIT 5
    """.format(off=_off(""), date=dc), as_dict=True)

    tc = []
    if tc_all:
        top_rev = _flt(tc_all[0].rev)
        for r in tc_all:
            if _flt(r.rev) >= top_rev:
                tc.append(r)
            else:
                break

    ti = frappe.db.sql("""
        SELECT soi.item_name, SUM(soi.qty) AS qty, SUM(soi.amount) AS rev
        FROM `tabSales Order Item` soi
        JOIN `tabSales Order` so ON so.name = soi.parent
        WHERE {off} {date}
          AND soi.item_name IS NOT NULL AND soi.item_name != ''
        GROUP BY soi.item_name ORDER BY rev DESC LIMIT 1
    """.format(off=_off("so."), date=dc_so), as_dict=True)

    tax_rows = frappe.db.sql("""
        SELECT stc.account_head, COALESCE(SUM(stc.tax_amount), 0) AS total
        FROM `tabSales Taxes and Charges` stc
        JOIN `tabSales Order` so ON so.name = stc.parent
        WHERE {off}
          AND so.transaction_date BETWEEN %s AND %s
          AND stc.tax_amount IS NOT NULL
          AND stc.charge_type != 'Actual'
        GROUP BY stc.account_head ORDER BY total DESC
    """.format(off=_off("so.")), [tax_ms, tax_me], as_dict=True)

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
        WHERE {off} {date}
          AND customer IS NOT NULL AND customer != ''
    """.format(off=_off(""), date=dc)))
    repeat = _int(_q("""
        SELECT COUNT(*) FROM (
            SELECT customer FROM `tabSales Order`
            WHERE {off} {date}
              AND customer IS NOT NULL AND customer != ''
            GROUP BY customer HAVING COUNT(*) > 1
        ) t
    """.format(off=_off(""), date=dc)))
    total = _int(_q("SELECT COUNT(*) FROM `tabSales Order` WHERE {}{}".format(_off(""), dc)))

    top_cust_display = " & ".join([r.customer for r in tc]) if tc else "—"
    top_cust_cnt     = sum(_int(r.cnt) for r in tc) if len(tc) > 1 else (_int(tc[0].cnt) if tc else 0)
    top_cust_rev     = _flt(tc[0].rev) if tc else 0.0

    return {
        "top_customer":     top_cust_display,
        "top_cust_cnt":     top_cust_cnt,
        "top_cust_rev":     top_cust_rev,
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
    dc = _date_clause(fd, td, alias="")

    st_rows = frappe.db.sql("""
        SELECT status, COUNT(*) AS cnt, COALESCE(SUM(base_net_total),0) AS rev
        FROM `tabSales Order`
        WHERE {off} {date}
          AND status IS NOT NULL AND status != ''
        GROUP BY status ORDER BY cnt DESC
    """.format(off=_off(""), date=dc), as_dict=True)

    if fd and td:
        win_so = " AND so.transaction_date BETWEEN '{}' AND '{}'".format(fd, td)
        win    = " AND transaction_date BETWEEN '{}' AND '{}'".format(fd, td)
    else:
        win_so = " AND so.transaction_date >= DATE_SUB('{}', INTERVAL 6 MONTH)".format(str(t))
        win    = " AND transaction_date >= DATE_SUB('{}', INTERVAL 6 MONTH)".format(str(t))

    tax_monthly = frappe.db.sql("""
        SELECT YEAR(so.transaction_date) AS yr,
               MONTH(so.transaction_date) AS mo_num,
               YEAR(so.transaction_date)*100+MONTH(so.transaction_date) AS sk,
               stc.account_head,
               COALESCE(SUM(stc.tax_amount), 0) AS total
        FROM `tabSales Taxes and Charges` stc
        JOIN `tabSales Order` so ON so.name = stc.parent
        WHERE {off}
          {win} AND stc.tax_amount IS NOT NULL
        GROUP BY yr, mo_num, sk, stc.account_head ORDER BY sk
    """.format(off=_off("so."), win=win_so), as_dict=True)

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
        ord_rows = frappe.db.sql("""
            SELECT YEAR(transaction_date) AS yr,
                   MONTH(transaction_date) AS mo_num,
                   YEAR(transaction_date)*100+MONTH(transaction_date) AS sk
            FROM `tabSales Order`
            WHERE {off} {win}
            GROUP BY yr, mo_num, sk ORDER BY sk
        """.format(off=_off(""), win=win), as_dict=True)
        for r in ord_rows:
            month_map[r.sk] = {"mo": _mo_label(r.sk // 100, r.sk % 100), "sk": r.sk, "sgst": 0.0, "cgst": 0.0, "igst": 0.0, "tds": 0.0}

    sorted_months = sorted(month_map.values(), key=lambda x: x["sk"])

    off_buckets = [("Under ₹1,000",0,1000),("₹1,000 – ₹2,000",1000,2000),
                   ("₹2,000 – ₹5,000",2000,5000),("₹5,000 – ₹10,000",5000,10000),
                   ("Above ₹10,000",10000,999999999)]
    bkt_labels, bkt_values, bkt_rev = [], [], []
    for lbl, lo, hi in off_buckets:
        row = frappe.db.sql("""
            SELECT COUNT(*) AS cnt, COALESCE(SUM(base_net_total),0) AS rev
            FROM `tabSales Order`
            WHERE {off}
              AND base_net_total >= %s AND base_net_total < %s {date}
        """.format(off=_off(""), date=dc), [lo, hi], as_dict=True)
        bkt_labels.append(lbl)
        bkt_values.append(_int(row[0].cnt) if row else 0)
        bkt_rev.append(_flt(row[0].rev) if row else 0.0)

    rev_rows = frappe.db.sql("""
        SELECT YEAR(transaction_date) AS yr,
               MONTH(transaction_date) AS mo_num,
               YEAR(transaction_date)*100+MONTH(transaction_date) AS sk,
               COALESCE(SUM(base_net_total),0) AS rev
        FROM `tabSales Order`
        WHERE {off} {win}
        GROUP BY yr, mo_num, sk ORDER BY sk
    """.format(off=_off(""), win=win), as_dict=True)

    mo_rows = frappe.db.sql("""
        SELECT YEAR(transaction_date) AS yr,
               MONTH(transaction_date) AS mo_num,
               YEAR(transaction_date)*100+MONTH(transaction_date) AS sk,
               COUNT(*) AS cnt
        FROM `tabSales Order`
        WHERE {off} {win}
        GROUP BY yr, mo_num, sk ORDER BY sk
    """.format(off=_off(""), win=win), as_dict=True)

    # ── Per-month full detail for the chart tooltips ──────────
    detail = {}

    def _slot(sk):
        if sk not in detail:
            detail[sk] = {
                "label": _mo_label(sk // 100, sk % 100),
                "orders": 0, "qty": 0.0, "revenue": 0.0, "aov": 0.0,
                "grand": 0.0, "rounded": 0.0, "tax": 0.0, "tds": 0.0,
                "completed": 0, "to_deliver": 0, "to_bill": 0,
                "cancelled": 0, "draft": 0, "other": 0
            }
        return detail[sk]

    hdr = frappe.db.sql("""
        SELECT YEAR(transaction_date)*100+MONTH(transaction_date) AS sk,
               COUNT(*) AS cnt,
               COALESCE(SUM(base_net_total),0) AS rev,
               COALESCE(SUM(base_grand_total),0) AS grand,
               COALESCE(SUM(CASE WHEN IFNULL(base_rounded_total,0) != 0
                                 THEN base_rounded_total
                                 ELSE base_grand_total END),0) AS rounded
        FROM `tabSales Order`
        WHERE {off} {win}
          AND transaction_date IS NOT NULL
        GROUP BY sk
    """.format(off=_off(""), win=win), as_dict=True)
    for r in hdr:
        s = _slot(r.sk)
        s["orders"]  = _int(r.cnt)
        s["revenue"] = _flt(r.rev)
        s["grand"]   = _flt(r.grand)
        s["rounded"] = _flt(r.rounded)

    itm = frappe.db.sql("""
        SELECT YEAR(so.transaction_date)*100+MONTH(so.transaction_date) AS sk,
               COALESCE(SUM(soi.qty),0) AS qty
        FROM `tabSales Order Item` soi
        JOIN `tabSales Order` so ON so.name = soi.parent
        WHERE {off} {win}
          AND so.transaction_date IS NOT NULL
        GROUP BY sk
    """.format(off=_off("so."), win=win_so), as_dict=True)
    for r in itm:
        _slot(r.sk)["qty"] = _flt(r.qty)

    stm = frappe.db.sql("""
        SELECT YEAR(transaction_date)*100+MONTH(transaction_date) AS sk,
               status, COUNT(*) AS cnt
        FROM `tabSales Order`
        WHERE {off} {win}
          AND transaction_date IS NOT NULL
        GROUP BY sk, status
    """.format(off=_off(""), win=win), as_dict=True)
    for r in stm:
        s  = _slot(r.sk)
        st = (r.status or "").strip()
        c  = _int(r.cnt)
        if st == "Completed":                                  s["completed"]  += c
        elif st in ("To Deliver and Bill", "To Deliver"):      s["to_deliver"] += c
        elif st == "To Bill":                                  s["to_bill"]    += c
        elif st == "Cancelled":                                s["cancelled"]  += c
        elif st == "Draft":                                    s["draft"]      += c
        else:                                                  s["other"]      += c

    for sk, m in month_map.items():
        s = _slot(sk)
        s["tax"] = round(m["sgst"] + m["cgst"] + m["igst"], 2)
        s["tds"] = round(m["tds"], 2)

    monthly_detail = {}
    for s in detail.values():
        s["aov"] = round(s["revenue"] / s["orders"], 2) if s["orders"] else 0.0
        monthly_detail[s["label"]] = s

    return {
        "status":        {"labels": [r.status for r in st_rows], "datasets": [{"values": [_int(r.cnt) for r in st_rows]}], "revenue": [_flt(r.rev) for r in st_rows]},
        "tax_monthly":   [{"mo": m["mo"],"sgst": round(m["sgst"],2),"cgst": round(m["cgst"],2),"igst": round(m["igst"],2),"tds": round(m["tds"],2)} for m in sorted_months],
        "order_buckets": {"labels": bkt_labels, "datasets": [{"values": bkt_values}], "revenue": bkt_rev},
        "monthly_rev":   {"labels": [_mo_label(r.yr, r.mo_num) for r in rev_rows], "datasets": [{"values": [_flt(r.rev) for r in rev_rows]}]},
        "monthly_ord":   {"labels": [_mo_label(r.yr, r.mo_num) for r in mo_rows],  "datasets": [{"values": [_int(r.cnt) for r in mo_rows]}]},
        "monthly_detail": monthly_detail,
    }


def _offline_top5(fd, td):
    dc    = _date_clause(fd, td, alias="")
    dc_so = _date_clause(fd, td, alias="so.")

    items = frappe.db.sql("""
        SELECT soi.item_name, SUM(soi.qty) AS tq, SUM(soi.amount) AS tr
        FROM `tabSales Order Item` soi
        JOIN `tabSales Order` so ON so.name = soi.parent
        WHERE {off} {date}
          AND soi.item_name IS NOT NULL AND soi.item_name != ''
        GROUP BY soi.item_name ORDER BY tr DESC LIMIT 6
    """.format(off=_off("so."), date=dc_so), as_dict=True)

    custs = frappe.db.sql("""
        SELECT customer, COUNT(*) AS oc, SUM(base_net_total) AS tr
        FROM `tabSales Order`
        WHERE {off} {date}
          AND customer IS NOT NULL AND customer != ''
        GROUP BY customer ORDER BY tr DESC LIMIT 5
    """.format(off=_off(""), date=dc), as_dict=True)

    im = _flt(items[0].tr) if items else 1
    cm = _flt(custs[0].tr) if custs else 1
    return {
        "items":     [{"name": r.item_name, "qty": _int(r.tq), "rev": _flt(r.tr), "pct": round(_flt(r.tr)/im*100)} for r in items],
        "customers": [{"name": r.customer,  "cnt": _int(r.oc), "rev": _flt(r.tr), "pct": round(_flt(r.tr)/cm*100)} for r in custs],
    }


def _offline_recent(fd, td):
    dc = _date_clause(fd, td, alias="")
    rows = frappe.db.sql("""
        SELECT name, customer, transaction_date, grand_total, status
        FROM `tabSales Order`
        WHERE {off} {date}
        ORDER BY creation DESC LIMIT 8
    """.format(off=_off(""), date=dc), as_dict=True)
    return [{"name": r.name, "customer": r.customer or "—",
             "transaction_date": str(r.transaction_date or ""),
             "grand_total": _flt(r.grand_total),
             "status": r.status or "—"} for r in rows]


def _offline_pipeline(fd, td):
    dc = _date_clause(fd, td, alias="")
    rows = frappe.db.sql("""
        SELECT status, COUNT(*) AS cnt
        FROM `tabSales Order`
        WHERE {off} {date}
          AND status IS NOT NULL AND status != ''
        GROUP BY status
    """.format(off=_off(""), date=dc), as_dict=True)
    sm    = {r.status: _int(r.cnt) for r in rows}
    total = _int(_q("SELECT COUNT(*) FROM `tabSales Order` WHERE {}{}".format(_off(""), dc)))
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