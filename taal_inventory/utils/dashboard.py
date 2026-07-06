import frappe
from frappe.utils import today, add_days, get_first_day, get_last_day


@frappe.whitelist()
def get_dashboard_data():
    t  = today()
    ms = str(get_first_day(t))
    me = str(get_last_day(t))
    return {
        "kpis":   _kpis(t, ms, me),
        "charts": _charts(t, ms, me),
    }


# ── KPI Drill-down for Hover Popup ───────────────────────────────
# Called from inventory_dashboard.js when user hovers a KPI card.
# Returns up to 10 rows of detail data for the given KPI key.

@frappe.whitelist()
def get_kpi_detail(key):
    key = (key or "").strip()

    # Low Stock — items at or below their reorder point
    if key == "low_stock":
        return frappe.db.sql("""
            SELECT item_name,
                   ROUND(current_stock, 0)  AS current_stock,
                   ROUND(reorder_value, 0)  AS target
            FROM `tabPharmacy Item`
            WHERE is_active=1 AND reorder_value>0 AND current_stock<=reorder_value
            ORDER BY (reorder_value - current_stock) DESC LIMIT 10
        """, as_dict=True)

    # Critical — items at or below minimum stock
    if key == "critical":
        return frappe.db.sql("""
            SELECT item_name,
                   ROUND(current_stock, 0) AS current_stock,
                   ROUND(min_stock_qty, 0) AS target
            FROM `tabPharmacy Item`
            WHERE is_active=1 AND min_stock_qty>0 AND current_stock<=min_stock_qty
            ORDER BY (min_stock_qty - current_stock) DESC LIMIT 10
        """, as_dict=True)

    # Expiring in 30 days
    if key == "exp_30":
        return frappe.db.sql("""
            SELECT pi.item_name,
                   pb.expiry_date,
                   ROUND(pb.remaining_qty, 0) AS remaining_qty
            FROM `tabPharmacy Batch` pb
            JOIN `tabPharmacy Item` pi ON pi.name=pb.item
            WHERE pb.status='Active' AND pb.remaining_qty>0
              AND pb.expiry_date BETWEEN CURDATE()
                  AND DATE_ADD(CURDATE(), INTERVAL 30 DAY)
            ORDER BY pb.expiry_date ASC LIMIT 10
        """, as_dict=True)

    # Expiring in 7 days — urgent
    if key == "exp_7":
        return frappe.db.sql("""
            SELECT pi.item_name,
                   pb.expiry_date,
                   ROUND(pb.remaining_qty, 0) AS remaining_qty
            FROM `tabPharmacy Batch` pb
            JOIN `tabPharmacy Item` pi ON pi.name=pb.item
            WHERE pb.status='Active' AND pb.remaining_qty>0
              AND pb.expiry_date BETWEEN CURDATE()
                  AND DATE_ADD(CURDATE(), INTERVAL 7 DAY)
            ORDER BY pb.expiry_date ASC LIMIT 10
        """, as_dict=True)

    # Overstock — items above maximum stock
    if key == "overstock":
        return frappe.db.sql("""
            SELECT item_name,
                   ROUND(current_stock, 0) AS current_stock,
                   ROUND(max_stock_qty, 0) AS target
            FROM `tabPharmacy Item`
            WHERE is_active=1 AND max_stock_qty>0 AND current_stock>max_stock_qty
            ORDER BY (current_stock - max_stock_qty) DESC LIMIT 10
        """, as_dict=True)

    # Never sold — active items with no deduction history
    if key == "never_sold":
        return frappe.db.sql("""
            SELECT item_name,
                   ROUND(current_stock, 0) AS current_stock
            FROM `tabPharmacy Item` pi
            WHERE is_active=1
              AND NOT EXISTS (
                SELECT 1 FROM `tabDeduction Log` dl WHERE dl.parent=pi.name
              )
            LIMIT 10
        """, as_dict=True)

    # Slow moving — no deductions in last 90 days
    if key == "slow_moving":
        d90 = str(add_days(today(), -90))
        return frappe.db.sql("""
            SELECT item_name,
                   ROUND(current_stock, 0) AS current_stock
            FROM `tabPharmacy Item` pi
            WHERE is_active=1
              AND NOT EXISTS (
                SELECT 1 FROM `tabDeduction Log` dl
                WHERE dl.parent=pi.name AND dl.deduction_datetime>=%s
              )
            LIMIT 10
        """, d90, as_dict=True)

    return []


# ── KPIs ─────────────────────────────────────────────────────────
# Unchanged from original — all field names preserved exactly
# EXCEPT: stock_value now uses cost_price (was selling_price) so it
# matches the Balance Sheet and Current Stock Report (inventory at cost).

def _kpis(t, ms, me):
    d7  = str(add_days(t, 7))
    d30 = str(add_days(t, 30))
    d90 = str(add_days(t, -90))

    def q(sql, args=None):
        r = frappe.db.sql(sql, args or [], as_list=True)
        return r[0][0] if r else 0

    return {
        "total_active": q(
            "SELECT COUNT(*) FROM `tabPharmacy Item` WHERE is_active=1"
        ),
        "low_stock": q(
            "SELECT COUNT(*) FROM `tabPharmacy Item`"
            " WHERE is_active=1 AND reorder_value>0"
            " AND current_stock<=reorder_value"
        ),
        "critical": q(
            "SELECT COUNT(*) FROM `tabPharmacy Item`"
            " WHERE is_active=1 AND min_stock_qty>0"
            " AND current_stock<=min_stock_qty"
        ),
        # FIX: value inventory at COST (was selling_price) to match
        # Balance Sheet / Current Stock Report / accounting basis.
        "stock_value": float(q(
            "SELECT COALESCE(SUM(remaining_qty*cost_price),0)"
            " FROM `tabPharmacy Batch` WHERE status='Active'"
        )),
        "exp_30": q(
            "SELECT COUNT(*) FROM `tabPharmacy Batch`"
            " WHERE status='Active' AND remaining_qty>0"
            " AND expiry_date BETWEEN %s AND %s",
            [t, d30]
        ),
        "active_batches": q(
            "SELECT COUNT(*) FROM `tabPharmacy Batch` WHERE status='Active'"
        ),
        "slow_moving": q(
            "SELECT COUNT(*) FROM `tabPharmacy Item` pi WHERE is_active=1"
            " AND NOT EXISTS ("
            "   SELECT 1 FROM `tabDeduction Log` dl"
            "   WHERE dl.parent=pi.name AND dl.deduction_datetime>=%s"
            ")",
            [d90]
        ),
        "exp_7": q(
            "SELECT COUNT(*) FROM `tabPharmacy Batch`"
            " WHERE status='Active' AND remaining_qty>0"
            " AND expiry_date BETWEEN %s AND %s",
            [t, d7]
        ),
        "overstock": q(
            "SELECT COUNT(*) FROM `tabPharmacy Item`"
            " WHERE is_active=1 AND max_stock_qty>0"
            " AND current_stock>max_stock_qty"
        ),
        "never_sold": q(
            "SELECT COUNT(*) FROM `tabPharmacy Item` pi WHERE is_active=1"
            " AND NOT EXISTS ("
            "   SELECT 1 FROM `tabDeduction Log` dl WHERE dl.parent=pi.name"
            ")"
        ),
        "writeoffs": q(
            "SELECT COUNT(*) FROM `tabStock Adjustment`"
            " WHERE docstatus=1 AND adjustment_date BETWEEN %s AND %s",
            [ms, me]
        ),
        "writeoff_value": float(q(
            "SELECT COALESCE(SUM(sai.qty*pb.cost_price),0)"
            " FROM `tabStock Adjustment Item` sai"
            " JOIN `tabStock Adjustment` sa ON sa.name=sai.parent"
            " LEFT JOIN `tabPharmacy Batch` pb ON pb.name=sai.batch_id"
            " WHERE sa.docstatus=1 AND sa.adjustment_date BETWEEN %s AND %s",
            [ms, me]
        )),
        "stock_in": float(q(
            "SELECT COALESCE(SUM(sri.qty_received),0)"
            " FROM `tabStock Receipt Item` sri"
            " JOIN `tabStock Receipt` sr ON sr.name=sri.parent"
            " WHERE sr.docstatus=1 AND sr.receipt_date BETWEEN %s AND %s",
            [ms, me]
        )),
        "stock_out": float(q(
            "SELECT COALESCE(SUM(qty_deducted),0)"
            " FROM `tabDeduction Log`"
            " WHERE DATE(deduction_datetime) BETWEEN %s AND %s",
            [ms, me]
        )),
        "unique_patients": q(
            "SELECT COUNT(DISTINCT patient_name)"
            " FROM `tabDeduction Log`"
            " WHERE patient_name!='' AND DATE(deduction_datetime) BETWEEN %s AND %s",
            [ms, me]
        ),
        "total_profit": float(q(
            "SELECT COALESCE(SUM(dl.qty_deducted*(pb.selling_price-pb.cost_price)),0)"
            " FROM `tabDeduction Log` dl"
            " LEFT JOIN `tabPharmacy Batch` pb ON pb.name=dl.batch_id"
            " WHERE DATE(dl.deduction_datetime) BETWEEN %s AND %s",
            [ms, me]
        )),
        "avg_margin": round(float(q(
            "SELECT COALESCE(AVG(CASE WHEN selling_price>0"
            "  THEN (selling_price-cost_price)/selling_price*100 ELSE 0 END),0)"
            " FROM `tabPharmacy Batch` WHERE status='Active' AND selling_price>0"
        )), 1),
        "purchase_spend": float(q(
            "SELECT COALESCE(SUM(grand_total),0)"
            " FROM `tabStock Receipt`"
            " WHERE docstatus=1 AND receipt_date BETWEEN %s AND %s",
            [ms, me]
        )),
    }


# ── Charts ────────────────────────────────────────────────────────

def _charts(t, ms, me):
    return {
        "weekly_movement":    _weekly_movement(t),
        "daily_dispensing":   _daily_dispensing(t),
        "batch_status":       _batch_status(),
        "category_value":     _category_value(),       # FIX: category_name via JOIN + cost basis
        "writeoff_breakdown": _writeoff_breakdown(),
        "top_fast_moving":    _top_fast_moving(t),     # FIX: rolling 30 days
        "top_stock_value":    _top_stock_value(),      # FIX: cost basis
        "top_patients":       _top_patients(t),        # FIX: rolling 30 days
        "monthly_profit":     _monthly_profit(t),
        "supplier_purchase":  _supplier_purchase(t),
        "margin_category":    _margin_category(),      # FIX: category_name via JOIN
        "expiry_timeline":    _expiry_timeline(t),
        "batch_age":          _batch_age(t),
        "reorder_gap":        _reorder_gap(),
    }


# ── Individual chart queries ──────────────────────────────────────

def _weekly_movement(t):
    rows_out = frappe.db.sql("""
        SELECT YEARWEEK(deduction_datetime,1) AS wk,
               SUM(qty_deducted) AS qty
        FROM `tabDeduction Log`
        WHERE deduction_datetime >= DATE_SUB(%s, INTERVAL 8 WEEK)
        GROUP BY wk ORDER BY wk
    """, t, as_dict=True)

    rows_in = frappe.db.sql("""
        SELECT YEARWEEK(sr.receipt_date,1) AS wk,
               SUM(sri.qty_received) AS qty
        FROM `tabStock Receipt Item` sri
        JOIN `tabStock Receipt` sr ON sr.name=sri.parent
        WHERE sr.docstatus=1
          AND sr.receipt_date >= DATE_SUB(%s, INTERVAL 8 WEEK)
        GROUP BY wk ORDER BY wk
    """, t, as_dict=True)

    wk_map = {}
    for r in rows_in:
        wk_map[str(r.wk)] = {"in": float(r.qty or 0), "out": 0.0}
    for r in rows_out:
        k = str(r.wk)
        if k not in wk_map:
            wk_map[k] = {"in": 0.0, "out": 0.0}
        wk_map[k]["out"] = float(r.qty or 0)

    weeks  = sorted(wk_map.keys())
    labels = ["Wk {}".format(i + 1) for i in range(len(weeks))]
    return {
        "labels": labels,
        "datasets": [
            {"name": "Stock In",  "values": [wk_map[w]["in"]  for w in weeks]},
            {"name": "Stock Out", "values": [wk_map[w]["out"] for w in weeks]},
        ]
    }


def _daily_dispensing(t):
    rows = frappe.db.sql("""
        SELECT DATE(deduction_datetime) AS dt, SUM(qty_deducted) AS qty
        FROM `tabDeduction Log`
        WHERE deduction_datetime >= DATE_SUB(%s, INTERVAL 30 DAY)
        GROUP BY dt ORDER BY dt
    """, t, as_dict=True)
    return {
        "labels":   [str(r.dt) for r in rows],
        "datasets": [{"name": "Qty Dispensed",
                      "values": [float(r.qty or 0) for r in rows]}]
    }


def _batch_status():
    rows = frappe.db.sql("""
        SELECT status, COUNT(*) AS cnt
        FROM `tabPharmacy Batch`
        GROUP BY status
    """, as_dict=True)
    return {
        "labels":   [r.status for r in rows],
        "datasets": [{"name": "Batches",
                      "values": [int(r.cnt) for r in rows]}]
    }


# FIX: JOIN tabItem Category to get human-readable category_name
# instead of the auto-generated hash stored in pi.category.
# FIX: value at COST (was selling_price) to match accounting basis.
def _category_value():
    rows = frappe.db.sql("""
        SELECT COALESCE(ic.category_name, pi.category, 'Uncategorized') AS cat,
               COALESCE(SUM(pb.remaining_qty * pb.cost_price), 0)    AS val
        FROM `tabPharmacy Batch` pb
        JOIN `tabPharmacy Item` pi ON pi.name = pb.item
        LEFT JOIN `tabItem Category` ic ON ic.name = pi.category
        WHERE pb.status = 'Active'
          AND pi.category IS NOT NULL
          AND pi.category != ''
        GROUP BY pi.category
        ORDER BY val DESC LIMIT 8
    """, as_dict=True)
    return {
        "labels":   [r.cat or "Uncategorized" for r in rows],
        "datasets": [{"name": "Stock Value",
                      "values": [float(r.val or 0) for r in rows]}]
    }


def _writeoff_breakdown():
    rows = frappe.db.sql("""
        SELECT adjustment_type, COUNT(*) AS cnt
        FROM `tabStock Adjustment`
        WHERE docstatus=1
        GROUP BY adjustment_type
    """, as_dict=True)
    return {
        "labels":   [r.adjustment_type for r in rows],
        "datasets": [{"name": "Write-offs",
                      "values": [int(r.cnt) for r in rows]}]
    }


# FIX: rolling 30-day window so data shows even mid-month
def _top_fast_moving(t):
    rows = frappe.db.sql("""
        SELECT pi.item_name,
               COALESCE(SUM(dl.qty_deducted), 0) AS qty
        FROM `tabDeduction Log` dl
        JOIN `tabPharmacy Item` pi ON pi.name = dl.parent
        WHERE dl.deduction_datetime >= DATE_SUB(%s, INTERVAL 30 DAY)
        GROUP BY dl.parent
        ORDER BY qty DESC LIMIT 10
    """, t, as_dict=True)
    return {
        "labels":   [r.item_name for r in rows],
        "datasets": [{"name": "Qty Sold",
                      "values": [float(r.qty or 0) for r in rows]}]
    }


# FIX: value at COST (was selling_price) to match accounting basis.
def _top_stock_value():
    rows = frappe.db.sql("""
        SELECT pi.item_name,
               COALESCE(SUM(pb.remaining_qty * pb.cost_price), 0) AS val
        FROM `tabPharmacy Batch` pb
        JOIN `tabPharmacy Item` pi ON pi.name = pb.item
        WHERE pb.status = 'Active'
        GROUP BY pb.item ORDER BY val DESC LIMIT 10
    """, as_dict=True)
    return {
        "labels":   [r.item_name for r in rows],
        "datasets": [{"name": "Stock Value",
                      "values": [float(r.val or 0) for r in rows]}]
    }


# FIX: rolling 30-day window so data shows even mid-month
def _top_patients(t):
    rows = frappe.db.sql("""
        SELECT patient_name,
               COALESCE(SUM(qty_deducted), 0) AS qty
        FROM `tabDeduction Log`
        WHERE patient_name != ''
          AND deduction_datetime >= DATE_SUB(%s, INTERVAL 30 DAY)
        GROUP BY patient_name
        ORDER BY qty DESC LIMIT 10
    """, t, as_dict=True)
    return {
        "labels":   [r.patient_name for r in rows],
        "datasets": [{"name": "Qty Received",
                      "values": [float(r.qty or 0) for r in rows]}]
    }


def _monthly_profit(t):
    rows = frappe.db.sql("""
        SELECT DATE_FORMAT(dl.deduction_datetime,'%%b %%Y') AS mo,
               YEAR(dl.deduction_datetime)*100+MONTH(dl.deduction_datetime) AS sk,
               COALESCE(SUM(dl.qty_deducted*(pb.selling_price-pb.cost_price)),0) AS profit
        FROM `tabDeduction Log` dl
        LEFT JOIN `tabPharmacy Batch` pb ON pb.name=dl.batch_id
        WHERE dl.deduction_datetime >= DATE_SUB(%s, INTERVAL 6 MONTH)
        GROUP BY mo, sk ORDER BY sk
    """, t, as_dict=True)
    return {
        "labels":   [r.mo for r in rows],
        "datasets": [{"name": "Profit (₹)",
                      "values": [float(r.profit or 0) for r in rows]}]
    }


def _supplier_purchase(t):
    rows = frappe.db.sql("""
        SELECT supplier,
               COALESCE(SUM(grand_total), 0) AS total
        FROM `tabStock Receipt`
        WHERE docstatus=1
          AND receipt_date >= DATE_SUB(%s, INTERVAL 12 MONTH)
        GROUP BY supplier ORDER BY total DESC LIMIT 8
    """, t, as_dict=True)
    return {
        "labels":   [r.supplier for r in rows],
        "datasets": [{"name": "Purchase Value",
                      "values": [float(r.total or 0) for r in rows]}]
    }


# FIX: JOIN tabItem Category to get human-readable category_name
def _margin_category():
    rows = frappe.db.sql("""
        SELECT COALESCE(ic.category_name, pi.category, 'Uncategorized') AS cat,
               COALESCE(AVG(CASE WHEN pb.selling_price > 0
                   THEN (pb.selling_price - pb.cost_price) / pb.selling_price * 100
                   ELSE 0 END), 0) AS margin
        FROM `tabPharmacy Batch` pb
        JOIN `tabPharmacy Item` pi ON pi.name = pb.item
        LEFT JOIN `tabItem Category` ic ON ic.name = pi.category
        WHERE pb.status = 'Active'
          AND pi.category IS NOT NULL
          AND pi.category != ''
        GROUP BY pi.category
        ORDER BY margin DESC LIMIT 8
    """, as_dict=True)
    return {
        "labels":   [r.cat for r in rows],
        "datasets": [{"name": "Avg Margin %",
                      "values": [round(float(r.margin or 0), 1) for r in rows]}]
    }


def _expiry_timeline(t):
    rows = frappe.db.sql("""
        SELECT DATE_FORMAT(expiry_date,'%%b %%Y') AS mo,
               YEAR(expiry_date)*100+MONTH(expiry_date) AS sk,
               COUNT(*) AS cnt
        FROM `tabPharmacy Batch`
        WHERE status='Active' AND remaining_qty>0
          AND expiry_date BETWEEN %s AND DATE_ADD(%s, INTERVAL 3 MONTH)
        GROUP BY mo, sk ORDER BY sk
    """, (t, t), as_dict=True)
    return {
        "labels":   [r.mo for r in rows],
        "datasets": [{"name": "Batches Expiring",
                      "values": [int(r.cnt) for r in rows]}]
    }


def _batch_age(t):
    rows = frappe.db.sql("""
        SELECT
          CASE
            WHEN DATEDIFF(%s, creation_date) <= 30  THEN '< 1 month'
            WHEN DATEDIFF(%s, creation_date) <= 90  THEN '1-3 months'
            WHEN DATEDIFF(%s, creation_date) <= 180 THEN '3-6 months'
            WHEN DATEDIFF(%s, creation_date) <= 365 THEN '6-12 months'
            ELSE '> 12 months'
          END AS age_group,
          COUNT(*) AS cnt
        FROM `tabPharmacy Batch`
        WHERE status='Active'
        GROUP BY age_group
    """, (t, t, t, t), as_dict=True)

    order   = ['< 1 month', '1-3 months', '3-6 months', '6-12 months', '> 12 months']
    row_map = {r.age_group: int(r.cnt) for r in rows}
    return {
        "labels":   order,
        "datasets": [{"name": "Active Batches",
                      "values": [row_map.get(o, 0) for o in order]}]
    }


def _reorder_gap():
    rows = frappe.db.sql("""
        SELECT item_name,
               current_stock,
               GREATEST(0, max_stock_qty - current_stock) AS gap
        FROM `tabPharmacy Item`
        WHERE is_active=1 AND max_stock_qty>0 AND current_stock < max_stock_qty
        ORDER BY gap DESC LIMIT 10
    """, as_dict=True)
    return {
        "labels": [r.item_name for r in rows],
        "datasets": [
            {"name": "Current Stock",
             "values": [float(r.current_stock or 0) for r in rows]},
            {"name": "Reorder Needed",
             "values": [float(r.gap or 0) for r in rows]},
        ]
    }