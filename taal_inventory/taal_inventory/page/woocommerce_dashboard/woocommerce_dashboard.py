import frappe
from frappe.utils import today, add_days, get_first_day, get_last_day


@frappe.whitelist()
def get_woocommerce_dashboard_data():
    t   = today()
    ms  = str(get_first_day(t))
    me  = str(get_last_day(t))
    lms = str(get_first_day(add_days(ms, -1)))
    lme = str(add_days(ms, -1))
    return {
        "kpis":     _kpis(t, ms, me, lms, lme),
        "snapshot": _snapshot(),
        "charts":   _charts(t),
        "top5":     _top5(),
        "feed":     _recent_orders(),
        "pipeline": _pipeline(),
    }


# ── Constants ─────────────────────────────────────────────────────
# All dashboard metrics are restricted to Online orders only.
# Revenue metrics additionally restrict to Shipped + Processing.
_ONLINE   = "custom_offline__online = 'Online'"
_REV_STAT = "woocommerce_status IN ('Shipped','Processing','shipped','processing')"
_PENDING  = "woocommerce_status IN ('Pending','pending')"
_CANCEL   = "woocommerce_status IN ('Cancelled','cancelled','failed')"
_SHIPPED  = "woocommerce_status IN ('Shipped','shipped')"
_BASE     = "docstatus = 1 AND custom_offline__online = 'Online'"


def _q(sql, args=None):
    r = frappe.db.sql(sql, args or [], as_list=True)
    if not r:
        return 0
    return r[0][0] if r[0][0] is not None else 0


def _trend(cur, prev):
    cur  = float(cur  or 0)
    prev = float(prev or 0)
    if prev == 0:
        return {"pct": 0.0, "dir": "flat"}
    diff = cur - prev
    pct  = round(abs(diff) / prev * 100, 1)
    return {"pct": pct, "dir": "up" if diff > 0 else ("down" if diff < 0 else "flat")}


# ── KPIs ──────────────────────────────────────────────────────────

def _kpis(t, ms, me, lms, lme):
    ty = str(add_days(t, -1))

    # Revenue = Online + (Shipped OR Processing)
    def rev(extra=""):
        w = "WHERE {} AND {}{}".format(_BASE, _REV_STAT, " AND " + extra if extra else "")
        return float(_q("SELECT COALESCE(SUM(grand_total),0) FROM `tabSales Order` {}".format(w)) or 0)

    # Count = Online only
    def cnt(extra=""):
        w = "WHERE {}{}".format(_BASE, " AND " + extra if extra else "")
        return int(_q("SELECT COUNT(*) FROM `tabSales Order` {}".format(w)) or 0)

    total_rev   = rev()
    month_rev   = rev("transaction_date BETWEEN '{}' AND '{}'".format(ms, me))
    lmonth_rev  = rev("transaction_date BETWEEN '{}' AND '{}'".format(lms, lme))
    total_ord   = cnt()
    today_ord   = cnt("transaction_date = '{}'".format(str(t)))
    yest_ord    = cnt("transaction_date = '{}'".format(ty))
    month_ord   = cnt("transaction_date BETWEEN '{}' AND '{}'".format(ms, me))
    lmonth_ord  = cnt("transaction_date BETWEEN '{}' AND '{}'".format(lms, lme))

    # Pending = Online + status = Pending only
    pending = int(_q(
        "SELECT COUNT(*) FROM `tabSales Order` WHERE {} AND {}".format(_BASE, _PENDING)
    ) or 0)

    # Cancelled = Online
    cancelled = int(_q(
        "SELECT COUNT(*) FROM `tabSales Order` WHERE {} AND {}".format(_BASE, _CANCEL)
    ) or 0)

    aov = round(total_rev / total_ord, 2) if total_ord else 0.0

    # Fulfillment: Shipped only
    shipped_cnt = int(_q(
        "SELECT COUNT(*) FROM `tabSales Order` WHERE {} AND {}".format(_BASE, _SHIPPED)
    ) or 0)
    # Fulfillment: Shipped + Processing
    ship_proc_cnt = int(_q(
        "SELECT COUNT(*) FROM `tabSales Order` WHERE {} AND {}".format(_BASE, _REV_STAT)
    ) or 0)

    fulfil_shipped   = round(shipped_cnt   / total_ord * 100) if total_ord else 0
    fulfil_ship_proc = round(ship_proc_cnt / total_ord * 100) if total_ord else 0

    return {
        "total_revenue":      total_rev,
        "month_revenue":      month_rev,
        "month_rev_trend":    _trend(month_rev,  lmonth_rev),
        "avg_order_value":    aov,
        "total_orders":       total_ord,
        "today_orders":       today_ord,
        "today_trend":        _trend(today_ord,  yest_ord),
        "month_orders":       month_ord,
        "month_ord_trend":    _trend(month_ord,  lmonth_ord),
        "pending":            pending,
        "cancelled":          cancelled,
        "fulfil_shipped":     fulfil_shipped,
        "fulfil_ship_proc":   fulfil_ship_proc,
    }


# ── Snapshot ─────────────────────────────────────────────────────

def _snapshot():
    # Top customer — Online orders only
    tc = frappe.db.sql("""
        SELECT customer, COUNT(*) AS cnt, SUM(grand_total) AS rev
        FROM `tabSales Order`
        WHERE docstatus=1 AND custom_offline__online='Online'
          AND customer IS NOT NULL AND customer != ''
        GROUP BY customer ORDER BY rev DESC LIMIT 1
    """, as_dict=True)

    # Top item — Online + Shipped/Processing
    ti = frappe.db.sql("""
        SELECT soi.item_name,
               SUM(soi.qty)    AS qty,
               SUM(soi.amount) AS rev
        FROM `tabSales Order Item` soi
        JOIN `tabSales Order` so ON so.name = soi.parent
        WHERE so.docstatus=1
          AND so.custom_offline__online = 'Online'
          AND so.woocommerce_status IN ('Shipped','Processing','shipped','processing')
          AND soi.item_name IS NOT NULL AND soi.item_name != ''
        GROUP BY soi.item_name ORDER BY rev DESC LIMIT 1
    """, as_dict=True)

    # Unique / repeat customers — Online
    unique = int(_q("""
        SELECT COUNT(DISTINCT customer)
        FROM `tabSales Order`
        WHERE docstatus=1 AND custom_offline__online='Online'
          AND customer IS NOT NULL AND customer != ''
    """) or 0)

    repeat = int(_q("""
        SELECT COUNT(*) FROM (
            SELECT customer FROM `tabSales Order`
            WHERE docstatus=1 AND custom_offline__online='Online'
              AND customer IS NOT NULL AND customer != ''
            GROUP BY customer HAVING COUNT(*) > 1
        ) t
    """) or 0)

    total_ord = int(_q(
        "SELECT COUNT(*) FROM `tabSales Order` WHERE docstatus=1 AND custom_offline__online='Online'"
    ) or 0)

    return {
        "top_customer":     tc[0].customer if tc else "—",
        "top_cust_cnt":     int(tc[0].cnt) if tc else 0,
        "top_cust_rev":     float(tc[0].rev) if tc else 0.0,
        "top_item":         ti[0].item_name if ti else "—",
        "top_item_qty":     int(ti[0].qty) if ti else 0,
        "top_item_rev":     float(ti[0].rev) if ti else 0.0,
        "unique_customers": unique,
        "repeat_customers": repeat,
        "repeat_rate":      round(repeat / unique * 100) if unique else 0,
        "avg_per_customer": round(total_ord / unique, 1) if unique else 0.0,
    }


# ── Charts ────────────────────────────────────────────────────────

def _woo_status_chart():
    rows = frappe.db.sql("""
        SELECT woocommerce_status, COUNT(*) AS cnt
        FROM `tabSales Order`
        WHERE docstatus=1 AND custom_offline__online='Online'
          AND woocommerce_status IS NOT NULL AND woocommerce_status != ''
        GROUP BY woocommerce_status ORDER BY cnt DESC
    """, as_dict=True)
    return {
        "labels":   [r.woocommerce_status for r in rows],
        "datasets": [{"name": "Orders", "values": [int(r.cnt) for r in rows]}]
    }


def _payment_chart():
    rows = frappe.db.sql("""
        SELECT woocommerce_payment_method, COUNT(*) AS cnt
        FROM `tabSales Order`
        WHERE docstatus=1 AND custom_offline__online='Online'
          AND woocommerce_payment_method IS NOT NULL
          AND woocommerce_payment_method != ''
        GROUP BY woocommerce_payment_method ORDER BY cnt DESC LIMIT 6
    """, as_dict=True)
    return {
        "labels":   [r.woocommerce_payment_method for r in rows],
        "datasets": [{"name": "Orders", "values": [int(r.cnt) for r in rows]}]
    }


def _order_value_buckets():
    """Bucket Online orders by grand_total range."""
    buckets = [
        ("Under ₹500",       0,    500),
        ("₹500 – ₹1,000",  500,   1000),
        ("₹1,000 – ₹2,000", 1000,  2000),
        ("₹2,000 – ₹5,000", 2000,  5000),
        ("Above ₹5,000",    5000, 999999999),
    ]
    labels, values = [], []
    for lbl, lo, hi in buckets:
        cnt = int(_q("""
            SELECT COUNT(*) FROM `tabSales Order`
            WHERE docstatus=1 AND custom_offline__online='Online'
              AND grand_total >= %s AND grand_total < %s
        """, [lo, hi]) or 0)
        labels.append(lbl)
        values.append(cnt)
    return {"labels": labels, "datasets": [{"name": "Orders", "values": values}]}


def _top_states():
    """Top states from shipping address on Online orders."""
    rows = frappe.db.sql("""
        SELECT shipping_address_name,
               COUNT(*) AS cnt
        FROM `tabSales Order`
        WHERE docstatus=1 AND custom_offline__online='Online'
          AND shipping_address_name IS NOT NULL
          AND shipping_address_name != ''
        GROUP BY shipping_address_name ORDER BY cnt DESC LIMIT 8
    """, as_dict=True)

    # Try to get state from Address doctype
    state_counts = {}
    for r in rows:
        try:
            addr = frappe.db.get_value("Address", r.shipping_address_name, "state")
            if addr:
                state_counts[addr] = state_counts.get(addr, 0) + int(r.cnt)
        except Exception:
            pass

    # Fallback: use address name as label if state lookup fails
    if not state_counts:
        for r in rows:
            state_counts[r.shipping_address_name] = int(r.cnt)

    sorted_states = sorted(state_counts.items(), key=lambda x: x[1], reverse=True)[:8]
    return {
        "labels":   [s[0] for s in sorted_states],
        "datasets": [{"name": "Orders", "values": [s[1] for s in sorted_states]}]
    }


def _monthly_revenue(t):
    rows = frappe.db.sql("""
        SELECT DATE_FORMAT(transaction_date,'%%b %%Y') AS mo,
               YEAR(transaction_date)*100+MONTH(transaction_date) AS sk,
               COALESCE(SUM(grand_total),0) AS rev
        FROM `tabSales Order`
        WHERE docstatus=1 AND custom_offline__online='Online'
          AND woocommerce_status IN ('Shipped','Processing','shipped','processing')
          AND transaction_date >= DATE_SUB(%s, INTERVAL 6 MONTH)
          AND transaction_date IS NOT NULL
        GROUP BY mo, sk ORDER BY sk
    """, str(t), as_dict=True)
    return {
        "labels":   [r.mo for r in rows],
        "datasets": [{"name": "Revenue", "values": [float(r.rev or 0) for r in rows]}]
    }


def _monthly_orders(t):
    rows = frappe.db.sql("""
        SELECT DATE_FORMAT(transaction_date,'%%b %%Y') AS mo,
               YEAR(transaction_date)*100+MONTH(transaction_date) AS sk,
               COUNT(*) AS cnt
        FROM `tabSales Order`
        WHERE docstatus=1 AND custom_offline__online='Online'
          AND transaction_date >= DATE_SUB(%s, INTERVAL 6 MONTH)
          AND transaction_date IS NOT NULL
        GROUP BY mo, sk ORDER BY sk
    """, str(t), as_dict=True)
    return {
        "labels":   [r.mo for r in rows],
        "datasets": [{"name": "Orders", "values": [int(r.cnt) for r in rows]}]
    }


def _charts(t):
    return {
        "woo_status":    _woo_status_chart(),
        "payment":       _payment_chart(),
        "order_buckets": _order_value_buckets(),
        "top_states":    _top_states(),
        "monthly_rev":   _monthly_revenue(t),
        "monthly_ord":   _monthly_orders(t),
    }


# ── Top 5 items + top 5 customers ────────────────────────────────

def _top5():
    items = frappe.db.sql("""
        SELECT soi.item_name,
               SUM(soi.qty)    AS total_qty,
               SUM(soi.amount) AS total_rev
        FROM `tabSales Order Item` soi
        JOIN `tabSales Order` so ON so.name = soi.parent
        WHERE so.docstatus=1
          AND so.custom_offline__online = 'Online'
          AND so.woocommerce_status IN ('Shipped','Processing','shipped','processing')
          AND soi.item_name IS NOT NULL AND soi.item_name != ''
        GROUP BY soi.item_name ORDER BY total_rev DESC LIMIT 6
    """, as_dict=True)

    custs = frappe.db.sql("""
        SELECT customer, COUNT(*) AS order_cnt, SUM(grand_total) AS total_rev
        FROM `tabSales Order`
        WHERE docstatus=1 AND custom_offline__online='Online'
          AND customer IS NOT NULL AND customer != ''
        GROUP BY customer ORDER BY total_rev DESC LIMIT 5
    """, as_dict=True)

    item_max = float(items[0].total_rev or 1) if items else 1
    cust_max = float(custs[0].total_rev or 1) if custs else 1

    return {
        "items": [
            {"name": r.item_name, "qty": int(r.total_qty or 0),
             "rev": float(r.total_rev or 0),
             "pct": round(float(r.total_rev or 0) / item_max * 100)}
            for r in items
        ],
        "customers": [
            {"name": r.customer, "cnt": int(r.order_cnt or 0),
             "rev": float(r.total_rev or 0),
             "pct": round(float(r.total_rev or 0) / cust_max * 100)}
            for r in custs
        ],
    }


# ── Recent orders ─────────────────────────────────────────────────

def _recent_orders():
    rows = frappe.db.sql("""
        SELECT name, customer, transaction_date, grand_total, woocommerce_status
        FROM `tabSales Order`
        WHERE docstatus=1 AND custom_offline__online='Online'
        ORDER BY creation DESC LIMIT 8
    """, as_dict=True)
    result = []
    for r in rows:
        result.append({
            "name":               r.name,
            "customer":           r.customer or "—",
            "transaction_date":   str(r.transaction_date or ""),
            "grand_total":        float(r.grand_total or 0),
            "woocommerce_status": r.woocommerce_status or "—",
        })
    return result


# ── Pipeline ─────────────────────────────────────────────────────

def _pipeline():
    rows = frappe.db.sql("""
        SELECT woocommerce_status, COUNT(*) AS cnt
        FROM `tabSales Order`
        WHERE docstatus=1 AND custom_offline__online='Online'
          AND woocommerce_status IS NOT NULL AND woocommerce_status != ''
        GROUP BY woocommerce_status
    """, as_dict=True)
    sm = {}
    for r in rows:
        sm[r.woocommerce_status] = int(r.cnt)

    total = int(_q("""
        SELECT COUNT(*) FROM `tabSales Order`
        WHERE docstatus=1 AND custom_offline__online='Online'
    """) or 0)

    def pick(*keys):
        return sum(sm.get(k, 0) for k in keys)

    shipped   = pick("Shipped",    "shipped")
    proc      = pick("Processing", "processing")
    pending   = pick("Pending",    "pending")
    on_hold   = pick("on-hold")
    refunded  = pick("Refunded",   "refunded")
    cancelled = pick("Cancelled",  "cancelled", "failed")

    return {
        "pending":           pending,
        "on_hold":           on_hold,
        "processing":        proc,
        "shipped":           shipped,
        "refunded":          refunded,
        "cancelled":         cancelled,
        "total":             total,
        "fulfil_shipped":    round(shipped / total * 100) if total else 0,
        "fulfil_ship_proc":  round((shipped + proc) / total * 100) if total else 0,
    }