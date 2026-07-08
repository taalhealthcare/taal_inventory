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
@frappe.whitelist()
def get_kpi_detail(key):
    key = (key or "").strip()

    if key == "low_stock":
        return frappe.db.sql("""
            SELECT item_name,
                   ROUND(current_stock, 0)  AS current_stock,
                   ROUND(reorder_value, 0)  AS target
            FROM `tabPharmacy Item`
            WHERE is_active=1 AND reorder_value>0 AND current_stock<=reorder_value
            ORDER BY (reorder_value - current_stock) DESC LIMIT 10
        """, as_dict=True)

    if key == "critical":
        return frappe.db.sql("""
            SELECT item_name,
                   ROUND(current_stock, 0) AS current_stock,
                   ROUND(min_stock_qty, 0) AS target
            FROM `tabPharmacy Item`
            WHERE is_active=1 AND min_stock_qty>0 AND current_stock<=min_stock_qty
            ORDER BY (min_stock_qty - current_stock) DESC LIMIT 10
        """, as_dict=True)

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

    if key == "overstock":
        return frappe.db.sql("""
            SELECT item_name,
                   ROUND(current_stock, 0) AS current_stock,
                   ROUND(max_stock_qty, 0) AS target
            FROM `tabPharmacy Item`
            WHERE is_active=1 AND max_stock_qty>0 AND current_stock>max_stock_qty
            ORDER BY (current_stock - max_stock_qty) DESC LIMIT 10
        """, as_dict=True)

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


# ── Item list for the Reorder Gap dropdown (Point 6) ─────────────
@frappe.whitelist()
def get_pharmacy_items_for_reorder():
    """Return active pharmacy items for the reorder-gap dropdown."""
    return frappe.db.sql("""
        SELECT name, item_name
        FROM `tabPharmacy Item`
        WHERE is_active = 1
        ORDER BY item_name ASC
    """, as_dict=True)


# ── Reorder Gap — ALL configured items, largest gap first (Point 6) ──
@frappe.whitelist()
def get_reorder_gap_ranked():
    """
    All active items that HAVE max_stock_qty configured and are below it,
    ranked by largest gap first (descending). Powers the default
    'All items (ranked)' view of the Reorder Gap chart.
    """
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


# ── Reorder Gap for ONE selected item (Point 6) ──────────────────
@frappe.whitelist()
def get_reorder_gap_for_item(item=None):
    """
    Reorder-gap detail for a single selected Pharmacy Item.

    Returns a dict:
      - has_max: 1/0 whether max_stock_qty is configured
      - if has_max: item_name, current_stock, max_stock_qty, gap
      - if not:     item_name, item (name) so the UI can link to the record
                    and prompt the user to set the Max Stock Quantity.
    """
    if not item:
        return {"has_max": 0, "item": None, "item_name": None}

    row = frappe.db.get_value(
        "Pharmacy Item", item,
        ["name", "item_name", "current_stock", "max_stock_qty"],
        as_dict=True
    )
    if not row:
        return {"has_max": 0, "item": None, "item_name": None}

    max_qty = float(row.max_stock_qty or 0)
    cur     = float(row.current_stock or 0)

    if max_qty <= 0:
        # No max configured — UI will prompt user to set it (with a link).
        return {
            "has_max":   0,
            "item":      row.name,
            "item_name": row.item_name,
        }

    return {
        "has_max":       1,
        "item":          row.name,
        "item_name":     row.item_name,
        "current_stock": round(cur, 2),
        "max_stock_qty": round(max_qty, 2),
        "gap":           round(max(0, max_qty - cur), 2),
    }


# ── KPIs ─────────────────────────────────────────────────────────
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
        # Inventory valued at COST (matches Balance Sheet / Current Stock Report).
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
        # POINT 4: Stock Out = SALES ONLY (reference_doctype = 'Sales Invoice').
        "stock_out": float(q(
            "SELECT COALESCE(SUM(qty_deducted),0)"
            " FROM `tabDeduction Log`"
            " WHERE reference_doctype='Sales Invoice'"
            " AND DATE(deduction_datetime) BETWEEN %s AND %s",
            [ms, me]
        )),
        "unique_patients": q(
            "SELECT COUNT(DISTINCT patient_name)"
            " FROM `tabDeduction Log`"
            " WHERE patient_name!='' AND reference_doctype='Sales Invoice'"
            " AND DATE(deduction_datetime) BETWEEN %s AND %s",
            [ms, me]
        ),
        "total_profit": float(q(
            "SELECT COALESCE(SUM(dl.qty_deducted*(pb.selling_price-pb.cost_price)),0)"
            " FROM `tabDeduction Log` dl"
            " LEFT JOIN `tabPharmacy Batch` pb ON pb.name=dl.batch_id"
            " WHERE dl.reference_doctype='Sales Invoice'"
            " AND DATE(dl.deduction_datetime) BETWEEN %s AND %s",
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
        "weekly_movement":    _weekly_movement(t, ms, me),   # POINT 3: current month
        "daily_dispensing":   _daily_dispensing(t),
        "batch_status":       _batch_status(),               # live snapshot
        "category_value":     _category_value(),             # live snapshot, cost basis
        "writeoff_breakdown": _writeoff_breakdown(ms, me),   # POINT 3: current month
        "top_fast_moving":    _top_fast_moving(ms, me),      # POINT 3: current month, sales only
        "top_stock_value":    _top_stock_value(),            # cost basis
        "top_patients":       _top_patients(t),
        "monthly_profit":     _monthly_profit(t),            # last 6 months (unchanged)
        "supplier_purchase":  _supplier_purchase(t),
        "margin_category":    _margin_category(),
        "expiry_timeline":    _expiry_timeline(t),
        "batch_age":          _batch_age(t),
        # reorder_gap chart is now driven by the selected item via
        # get_reorder_gap_for_item(); we send an empty stub here so the
        # initial page load is fast and the JS populates it on selection.
        "reorder_gap":        {"labels": [], "datasets": []},
    }


# POINT 3: current-month weeks only
def _weekly_movement(t, ms, me):
    rows_out = frappe.db.sql("""
        SELECT YEARWEEK(deduction_datetime,1) AS wk,
               SUM(qty_deducted) AS qty
        FROM `tabDeduction Log`
        WHERE reference_doctype='Sales Invoice'
          AND DATE(deduction_datetime) BETWEEN %s AND %s
        GROUP BY wk ORDER BY wk
    """, [ms, me], as_dict=True)

    rows_in = frappe.db.sql("""
        SELECT YEARWEEK(sr.receipt_date,1) AS wk,
               SUM(sri.qty_received) AS qty
        FROM `tabStock Receipt Item` sri
        JOIN `tabStock Receipt` sr ON sr.name=sri.parent
        WHERE sr.docstatus=1
          AND sr.receipt_date BETWEEN %s AND %s
        GROUP BY wk ORDER BY wk
    """, [ms, me], as_dict=True)

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
        WHERE reference_doctype='Sales Invoice'
          AND deduction_datetime >= DATE_SUB(%s, INTERVAL 30 DAY)
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


# Live snapshot, cost basis
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


# POINT 3: current-month write-offs only
def _writeoff_breakdown(ms, me):
    rows = frappe.db.sql("""
        SELECT adjustment_type, COUNT(*) AS cnt
        FROM `tabStock Adjustment`
        WHERE docstatus=1
          AND adjustment_date BETWEEN %s AND %s
        GROUP BY adjustment_type
    """, [ms, me], as_dict=True)
    return {
        "labels":   [r.adjustment_type for r in rows],
        "datasets": [{"name": "Write-offs",
                      "values": [int(r.cnt) for r in rows]}]
    }


# POINT 3: current-month, sales only
def _top_fast_moving(ms, me):
    rows = frappe.db.sql("""
        SELECT pi.item_name,
               COALESCE(SUM(dl.qty_deducted), 0) AS qty
        FROM `tabDeduction Log` dl
        JOIN `tabPharmacy Item` pi ON pi.name = dl.parent
        WHERE dl.reference_doctype='Sales Invoice'
          AND DATE(dl.deduction_datetime) BETWEEN %s AND %s
        GROUP BY dl.parent
        ORDER BY qty DESC LIMIT 10
    """, [ms, me], as_dict=True)
    return {
        "labels":   [r.item_name for r in rows],
        "datasets": [{"name": "Qty Sold",
                      "values": [float(r.qty or 0) for r in rows]}]
    }


# Cost basis
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


def _top_patients(t):
    rows = frappe.db.sql("""
        SELECT patient_name,
               COALESCE(SUM(qty_deducted), 0) AS qty
        FROM `tabDeduction Log`
        WHERE patient_name != ''
          AND reference_doctype='Sales Invoice'
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
        WHERE dl.reference_doctype='Sales Invoice'
          AND dl.deduction_datetime >= DATE_SUB(%s, INTERVAL 6 MONTH)
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