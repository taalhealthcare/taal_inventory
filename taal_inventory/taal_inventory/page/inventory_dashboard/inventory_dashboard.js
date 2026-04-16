// ================================================================
//  Inventory Dashboard — Taal Inventory
//  Visual design: same as Doctor Consultation Dashboard
//  Charts       : Chart.js 4.4.1 (replaces frappe.Chart)
//  Data         : taal_inventory.utils.dashboard.get_dashboard_data
//  Python       : ZERO changes — all field names / doctypes unchanged
// ================================================================

frappe.pages["inventory-dashboard"].on_page_load = function (wrapper) {
    var page = frappe.ui.make_app_page({
        parent:        wrapper,
        title:         "Inventory Dashboard",
        single_column: true
    });
    page.taal_db = new TaalInventoryDashboard(page);
};

frappe.pages["inventory-dashboard"].on_page_show = function (wrapper) {
    if (wrapper.page && wrapper.page.taal_db) {
        wrapper.page.taal_db.refresh();
    }
};


// ================================================================
//  Dashboard Class
// ================================================================
class TaalInventoryDashboard {

    constructor(page) {
        this.page    = page;
        this._cjs    = {};     // Chart.js instances keyed by chart id
        this._timer  = null;
        this._setup();
    }

    // ── Setup ────────────────────────────────────────────────────
    _setup() {
        this._inject_styles();

        this.page.add_inner_button("↺  Refresh", () => this.refresh());

        this.$last = $('<span style="font-size:11px;color:#94a3b8;margin-left:12px;"></span>');
        $(this.page.main).find(".page-actions").append(this.$last);

        $(this.page.body).html(this._skeleton());

        // Auto-refresh every 5 minutes
        this._timer = setInterval(() => this.refresh(), 300000);
    }

    // ── Data fetch ───────────────────────────────────────────────
    refresh() {
        this._loading(true);
        frappe.call({
            method:  "taal_inventory.utils.dashboard.get_dashboard_data",
            freeze:  false,
            callback: (r) => {
                this._loading(false);
                if (r.message) {
                    this._render_kpis(r.message.kpis);
                    this._load_chartjs(() => {
                        this._render_charts(r.message.charts);
                    });
                    this.$last.text(
                        "Updated " + frappe.datetime.str_to_user(frappe.datetime.now_datetime())
                    );
                }
            }
        });
    }

    // ── Page skeleton ────────────────────────────────────────────
    _skeleton() {
        const sh = (lbl) =>
            `<div class="ti-sechd"><span class="ti-seclbl">${lbl}</span><div class="ti-secline"></div></div>`;

        const krow = (id) => `<div id="${id}" class="ti-kg"></div>`;

        const cg = (cls, ids) => {
            const cards = ids.map(id =>
                `<div class="ti-card" id="ti-card-${id}">
                    <div class="ti-ct" id="ti-t-${id}"></div>
                    <div id="ti-i-${id}"></div>
                 </div>`
            ).join("");
            return `<div class="${cls}">${cards}</div>`;
        };

        return `
        <div id="ti-wrap">
            ${sh("Core KPIs")}
            ${krow("ti-kr1")}

            ${sh("Stock Alerts")}
            ${krow("ti-kr2")}

            ${sh("This Month Activity")}
            ${krow("ti-kr3")}

            ${sh("Stock Movement")}
            ${cg("ti-g2", ["weekly_movement", "daily_dispensing"])}

            ${sh("Inventory Distribution")}
            ${cg("ti-g3", ["batch_status", "category_value", "writeoff_breakdown"])}

            ${sh("Top Items")}
            ${cg("ti-g3", ["top_fast_moving", "top_stock_value", "top_patients"])}

            ${sh("Financial & Supplier")}
            ${cg("ti-g3", ["monthly_profit", "supplier_purchase", "margin_category"])}

            ${sh("Expiry & Age")}
            ${cg("ti-g2", ["expiry_timeline", "batch_age"])}

            ${sh("Reorder Analysis")}
            ${cg("ti-g1", ["reorder_gap"])}
        </div>`;
    }

    // ── KPI Cards ────────────────────────────────────────────────
    // Exact same field names as before — Python untouched
    _render_kpis(d) {
        const rows = [
            {
                id: "ti-kr1",
                cards: [
                    { label: "Total Active Items",  value: d.total_active,   accent: "#2980b9", fmt: "int"      },
                    { label: "Low Stock Items",      value: d.low_stock,      accent: "#e67e22", fmt: "int"      },
                    { label: "Critical Below Min",   value: d.critical,       accent: "#c0392b", fmt: "int"      },
                    { label: "Total Stock Value",    value: d.stock_value,    accent: "#1e8449", fmt: "currency" },
                    { label: "Expiring in 30 Days",  value: d.exp_30,         accent: "#e74c3c", fmt: "int"      },
                    { label: "Active Batches",       value: d.active_batches, accent: "#2980b9", fmt: "int"      },
                ]
            },
            {
                id: "ti-kr2",
                cards: [
                    { label: "Slow Moving Items",    value: d.slow_moving,    accent: "#7f8c8d", fmt: "int"      },
                    { label: "Expiring in 7 Days",   value: d.exp_7,          accent: "#c0392b", fmt: "int"      },
                    { label: "Overstock Items",      value: d.overstock,      accent: "#8e44ad", fmt: "int"      },
                    { label: "Never Sold Items",     value: d.never_sold,     accent: "#7f8c8d", fmt: "int"      },
                    { label: "Write-offs This Month",value: d.writeoffs,      accent: "#e74c3c", fmt: "int"      },
                    { label: "Write-off Value",      value: d.writeoff_value, accent: "#c0392b", fmt: "currency" },
                ]
            },
            {
                id: "ti-kr3",
                cards: [
                    { label: "This Month Stock In",  value: d.stock_in,        accent: "#1e8449", fmt: "float"    },
                    { label: "This Month Stock Out", value: d.stock_out,       accent: "#e74c3c", fmt: "float"    },
                    { label: "Unique Patients",      value: d.unique_patients, accent: "#2980b9", fmt: "int"      },
                    { label: "Total Profit",         value: d.total_profit,    accent: "#1e8449", fmt: "currency" },
                    { label: "Avg Profit Margin",    value: d.avg_margin,      accent: "#16a085", fmt: "percent"  },
                    { label: "Purchase Spend",       value: d.purchase_spend,  accent: "#e67e22", fmt: "currency" },
                ]
            }
        ];

        rows.forEach((row, ri) => {
            const html = row.cards.map((c, ci) => {
                const delay = (ri * 6 + ci) * 0.04;
                return `<div class="ti-kpi" style="--tc:${c.accent};animation-delay:${delay}s">
                    <div class="ti-kl">${c.label}</div>
                    <div class="ti-kv" data-target="${parseFloat(c.value) || 0}"
                         data-fmt="${c.fmt}">${this._fmt(c.value, c.fmt)}</div>
                </div>`;
            }).join("");
            $(`#${row.id}`).html(html);
        });

        this._count_up();
    }

    // ── Number formatter ─────────────────────────────────────────
    _fmt(v, f) {
        const n = parseFloat(v) || 0;
        if (f === "currency") return "₹ " + Math.round(n).toLocaleString("en-IN");
        if (f === "percent")  return n.toFixed(1) + "%";
        return Math.round(n).toLocaleString("en-IN");
    }

    // ── Count-up animation ───────────────────────────────────────
    _count_up() {
        document.querySelectorAll(".ti-kv[data-target]").forEach(el => {
            const target = parseFloat(el.dataset.target) || 0;
            const fmt    = el.dataset.fmt;
            const steps  = 40;
            const dur    = 900;
            let   step   = 0;

            const fn = (v) => {
                if (fmt === "currency") return "₹ " + Math.round(v).toLocaleString("en-IN");
                if (fmt === "percent")  return v.toFixed(1) + "%";
                return Math.round(v).toLocaleString("en-IN");
            };

            const iv = setInterval(() => {
                step++;
                const ease = 1 - Math.pow(1 - step / steps, 3);
                el.textContent = fn(target * ease);
                if (step >= steps) {
                    el.textContent = fn(target);
                    clearInterval(iv);
                }
            }, dur / steps);
        });
    }

    // ── Chart.js CDN loader ──────────────────────────────────────
    _load_chartjs(cb) {
        if (window.Chart && typeof window.Chart.register === "function") {
            cb();
            return;
        }
        const s   = document.createElement("script");
        s.src     = "https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js";
        s.onload  = cb;
        s.onerror = () => frappe.show_alert(
            { message: "Chart.js failed to load from CDN.", indicator: "red" }, 5
        );
        document.head.appendChild(s);
    }

    // ── Chart definitions & rendering ────────────────────────────
    // ── FIX: switched from frappe.Chart to Chart.js 4.4.1
    // ── All 5 previously broken charts now use Chart.js which
    //    handles large floats, empty data, and NULL JOINs correctly
    _render_charts(charts) {
        const P = [
            "#2980b9","#8e44ad","#16a085","#e67e22","#c0392b",
            "#1e8449","#d4ac0d","#7f8c8d","#e74c3c","#2ecc71"
        ];

        // cfg: id, title, type, colors, height, horizontal bar?
        const cfg = [
            // ── Stock Movement ──────────────────────────────────
            {
                id:    "weekly_movement",
                title: "Weekly Stock In vs Out",
                type:  "bar",
                colors: ["#1e8449", "#e74c3c"],
                height: 240,
                hbar:   false
            },
            {
                id:    "daily_dispensing",
                title: "Daily Dispensing — Last 30 Days",
                type:  "line",
                colors: ["#2980b9"],
                height: 240,
                hbar:   false
            },

            // ── Inventory Distribution ─────────────────────────
            {
                id:    "batch_status",
                title: "Batch Status Distribution",
                type:  "doughnut",
                colors: P,
                height: 240,
                hbar:   false
            },
            // FIX 1: category_value — was pie, now doughnut (Chart.js more stable)
            // Large float values caused frappe.Chart pie to silently fail
            {
                id:    "category_value",
                title: "Stock Value by Category",
                type:  "doughnut",
                colors: P,
                height: 240,
                hbar:   false
            },
            // FIX 2: writeoff_breakdown — donut with possible single-segment
            // frappe.Chart donut fails with 1 segment; Chart.js handles it fine
            {
                id:    "writeoff_breakdown",
                title: "Write-off Breakdown",
                type:  "doughnut",
                colors: ["#c0392b","#e67e22","#7f8c8d","#e74c3c"],
                height: 240,
                hbar:   false
            },

            // ── Top Items ───────────────────────────────────────
            // FIX 3: top_fast_moving — horizontal bar, long item names
            // frappe.Chart bar failed when SQL returned empty for month range
            // Chart.js shows "No data" state gracefully
            {
                id:    "top_fast_moving",
                title: "Top 10 Fast Moving Items",
                type:  "bar",
                colors: ["#2980b9"],
                height: 400,
                hbar:   true
            },
            {
                id:    "top_stock_value",
                title: "Top 10 Items by Stock Value",
                type:  "bar",
                colors: ["#8e44ad"],
                height: 400,
                hbar:   true
            },
            // FIX 4: top_patients — horizontal bar
            // Same issue as top_fast_moving — empty month range → silent fail
            {
                id:    "top_patients",
                title: "Top 10 Patients by Qty",
                type:  "bar",
                colors: ["#16a085"],
                height: 400,
                hbar:   true
            },

            // ── Financial & Supplier ────────────────────────────
            // FIX 5: monthly_profit — line chart
            // LEFT JOIN on batch_id produces NULL cost values → all-zero profits
            // frappe.Chart line crashes on zero-only data; Chart.js renders it correctly
            {
                id:    "monthly_profit",
                title: "Monthly Profit Trend",
                type:  "line",
                colors: ["#1e8449"],
                height: 240,
                hbar:   false
            },
            {
                id:    "supplier_purchase",
                title: "Supplier Purchase Value",
                type:  "bar",
                colors: ["#e67e22"],
                height: 340,
                hbar:   true
            },
            {
                id:    "margin_category",
                title: "Avg Profit Margin % by Category",
                type:  "bar",
                colors: ["#16a085"],
                height: 240,
                hbar:   false
            },

            // ── Expiry & Age ────────────────────────────────────
            {
                id:    "expiry_timeline",
                title: "Expiry Timeline — Next 3 Months",
                type:  "bar",
                colors: ["#e74c3c"],
                height: 240,
                hbar:   false
            },
            {
                id:    "batch_age",
                title: "Batch Age Distribution",
                type:  "bar",
                colors: ["#2980b9"],
                height: 240,
                hbar:   false
            },

            // ── Reorder Analysis ────────────────────────────────
            {
                id:    "reorder_gap",
                title: "Reorder Gap — Current Stock vs Target",
                type:  "bar",
                colors: ["#2980b9", "#dce8f5"],
                height: 340,
                hbar:   true
            },
        ];

        cfg.forEach(c => {
            $(`#ti-t-${c.id}`).text(c.title);
            this._draw_chart(c.id, c.type, charts[c.id], c.colors, c.height, c.hbar);
        });
    }

    // ── Draw single chart using Chart.js ─────────────────────────
    _draw_chart(id, type, frData, colors, height, hbar) {
        const $el = $(`#ti-i-${id}`);
        if (!$el.length) return;

        // Destroy any existing Chart.js instance to prevent canvas reuse error
        if (this._cjs[id]) {
            try { this._cjs[id].destroy(); } catch (e) {}
            delete this._cjs[id];
        }

        // Guard: no data from Python
        const labels = (frData && frData.labels) || [];
        if (!labels.length) {
            $el.html('<div class="ti-nodata">No data for this period</div>');
            return;
        }

        $el.html(
            `<div style="position:relative;height:${height}px">`
            + `<canvas id="ti-cv-${id}" role="img" aria-label="${id} chart">`
            + `</canvas></div>`
        );

        const canvas = document.getElementById(`ti-cv-${id}`);
        if (!canvas) return;

        const dark   = window.matchMedia("(prefers-color-scheme: dark)").matches;
        const gc     = dark ? "rgba(255,255,255,0.07)" : "rgba(0,0,0,0.06)";
        const lc     = dark ? "#94a3b8" : "#888780";
        const bdr    = dark ? "#1a1a1a" : "#ffffff";

        const raw_ds = (frData && frData.datasets) || [];

        // ── Convert frappe.Chart format → Chart.js format ────────
        // Python returns: {labels: [...], datasets: [{name: "X", values: [...]}]}
        // Chart.js needs: {labels: [...], datasets: [{label: "X", data: [...]}]}
        let cjsData;

        if (type === "doughnut" || type === "pie") {
            const vals = raw_ds.length > 0 ? (raw_ds[0].values || []) : [];
            cjsData = {
                labels: labels,
                datasets: [{
                    data:            vals,
                    backgroundColor: colors,
                    borderWidth:     3,
                    borderColor:     bdr,
                    hoverOffset:     8
                }]
            };
        } else if (type === "line") {
            cjsData = {
                labels: labels,
                datasets: raw_ds.map((ds, i) => ({
                    label:               ds.name || "",
                    data:                ds.values || [],
                    borderColor:         colors[i] || colors[0],
                    backgroundColor:     this._rgba(colors[i] || colors[0], dark ? 0.12 : 0.08),
                    borderWidth:         2,
                    pointBackgroundColor: colors[i] || colors[0],
                    pointBorderColor:    bdr,
                    pointBorderWidth:    2,
                    pointRadius:         3,
                    pointHoverRadius:    5,
                    tension:             0.4,
                    fill:                true
                }))
            };
        } else {
            // bar (vertical or horizontal)
            cjsData = {
                labels: labels,
                datasets: raw_ds.map((ds, i) => ({
                    label:           ds.name || "",
                    data:            ds.values || [],
                    backgroundColor: colors[i] || colors[0],
                    borderRadius:    hbar ? 4 : 6,
                    borderSkipped:   false
                }))
            };
        }

        // ── Chart.js options ─────────────────────────────────────
        const multiSeries  = raw_ds.length > 1;
        const showLegend   = (type === "doughnut" || type === "pie" || multiSeries);

        const opts = {
            responsive:          true,
            maintainAspectRatio: false,
            animation:           { duration: 800 },
            plugins: {
                legend: {
                    display:  showLegend,
                    position: "bottom",
                    labels: {
                        font:           { size: 11 },
                        padding:        12,
                        usePointStyle:  true
                    }
                },
                tooltip: {
                    callbacks: {
                        label: (ctx) => {
                            if (type === "doughnut" || type === "pie") {
                                const tot = ctx.dataset.data.reduce((a, b) => a + b, 0);
                                const pct = tot > 0 ? Math.round(ctx.parsed / tot * 100) : 0;
                                return ` ${ctx.label}: ${(ctx.raw || 0).toLocaleString("en-IN")} (${pct}%)`;
                            }
                            const v = ctx.raw !== undefined ? parseFloat(ctx.raw) : 0;
                            return ` ${ctx.dataset.label || ""}: ${v.toLocaleString("en-IN")}`;
                        }
                    }
                }
            }
        };

        if (type === "doughnut") {
            opts.cutout = "62%";
        } else if (type === "pie") {
            opts.cutout = 0;
        } else {
            // bar / line scales
            opts.scales = {
                x: {
                    ticks:  {
                        color:       lc,
                        font:        { size: 10 },
                        maxRotation: hbar ? 0 : 45,
                        autoSkip:    true
                    },
                    grid:   { color: hbar ? gc : "transparent" },
                    border: { display: false }
                },
                y: {
                    beginAtZero: true,
                    ticks:       { color: lc, font: { size: 10 } },
                    grid:        { color: hbar ? "transparent" : gc },
                    border:      { display: false }
                }
            };
            if (hbar) {
                opts.indexAxis = "y";
            }
        }

        try {
            this._cjs[id] = new Chart(canvas, {
                type:    type,
                data:    cjsData,
                options: opts
            });
        } catch (e) {
            $el.html('<div class="ti-nodata">Chart unavailable</div>');
            console.warn("[TaalInventoryDashboard] Chart error on " + id + ":", e);
        }
    }

    // ── Hex → rgba helper ────────────────────────────────────────
    _rgba(hex, alpha) {
        const r = parseInt(hex.slice(1, 3), 16);
        const g = parseInt(hex.slice(3, 5), 16);
        const b = parseInt(hex.slice(5, 7), 16);
        return `rgba(${r},${g},${b},${alpha})`;
    }

    // ── Loading bar ──────────────────────────────────────────────
    _loading(show) {
        if (show) {
            if (!$("#ti-loadbar").length) {
                $(this.page.body).append(
                    '<div id="ti-loadbar" style="position:fixed;top:0;left:0;'
                    + 'width:100%;height:3px;'
                    + 'background:linear-gradient(90deg,#2980b9,#1e8449,#e74c3c,#2980b9);'
                    + 'background-size:200%;'
                    + 'animation:ti-loading-anim 1.2s linear infinite;z-index:9999;"></div>'
                );
            }
        } else {
            $("#ti-loadbar").remove();
        }
    }

    // ── Styles — same visual language as Doctor Consultation Dashboard ──
    _inject_styles() {
        if (document.getElementById("ti-dash-css")) return;
        const s = document.createElement("style");
        s.id    = "ti-dash-css";
        s.textContent = ""
            // ── Page wrapper ──────────────────────────────────
            + "#ti-wrap{padding:16px 0 48px;max-width:1400px;}"

            // ── Section headers ───────────────────────────────
            + ".ti-sechd{display:flex;align-items:center;gap:8px;margin:22px 0 10px}"
            + ".ti-seclbl{font-size:10px;font-weight:500;text-transform:uppercase;"
            +   "letter-spacing:.08em;color:#94a3b8;white-space:nowrap}"
            + ".ti-secline{flex:1;height:0.5px;background:#e2e8f0}"

            // ── KPI grid — 6 columns per row ──────────────────
            + ".ti-kg{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));"
            +   "gap:10px;margin-bottom:14px}"

            // ── KPI card — DC Dashboard style ─────────────────
            + ".ti-kpi{background:#fff;border:0.5px solid #e2e8f0;border-radius:12px;"
            +   "padding:14px 16px;position:relative;overflow:hidden;cursor:default;"
            +   "transition:transform .18s ease,box-shadow .18s ease,border-color .2s ease;"
            +   "animation:ti-kpi-in .4s ease both}"
            + ".ti-kpi:hover{transform:translateY(-4px);"
            +   "box-shadow:0 8px 22px rgba(0,0,0,.09);border-color:#bbb}"
            // Colored top border accent (same pattern as DC Dashboard ::before)
            + ".ti-kpi::before{content:'';position:absolute;top:0;left:0;right:0;"
            +   "height:3px;border-radius:12px 12px 0 0;background:var(--tc,#2980b9)}"
            // Shimmer on hover (kept from original — adds premium feel)
            + ".ti-kpi::after{content:'';position:absolute;top:-60%;left:-60%;"
            +   "width:55%;height:220%;"
            +   "background:linear-gradient(105deg,transparent 40%,"
            +   "rgba(255,255,255,.25) 50%,transparent 60%);"
            +   "transform:skewX(-20deg);transition:left .55s ease;pointer-events:none}"
            + ".ti-kpi:hover::after{left:150%}"
            + ".ti-kl{font-size:10px;font-weight:500;color:#64748b;margin-bottom:4px;"
            +   "text-transform:uppercase;letter-spacing:.04em;"
            +   "white-space:nowrap;overflow:hidden;text-overflow:ellipsis}"
            + ".ti-kv{font-size:22px;font-weight:500;line-height:1;"
            +   "font-variant-numeric:tabular-nums;color:var(--tc,#2980b9)}"

            // ── Chart grids ───────────────────────────────────
            + ".ti-g2{display:grid;grid-template-columns:1fr 1fr;"
            +   "gap:12px;margin-bottom:14px}"
            + ".ti-g3{display:grid;grid-template-columns:1fr 1fr 1fr;"
            +   "gap:12px;margin-bottom:14px}"
            + ".ti-g1{display:grid;grid-template-columns:1fr;"
            +   "gap:12px;margin-bottom:14px}"

            // ── Chart card — DC Dashboard style ───────────────
            + ".ti-card{background:#fff;border:0.5px solid #e2e8f0;border-radius:12px;"
            +   "padding:16px 18px;transition:box-shadow .18s ease}"
            + ".ti-card:hover{box-shadow:0 4px 14px rgba(0,0,0,.07)}"
            + ".ti-ct{font-size:13px;font-weight:500;color:#111;margin-bottom:12px}"

            // ── No-data state ─────────────────────────────────
            + ".ti-nodata{display:flex;align-items:center;justify-content:center;"
            +   "height:180px;color:#94a3b8;font-size:12px;font-style:italic}"

            // ── Animations ────────────────────────────────────
            + "@keyframes ti-kpi-in{"
            +   "from{opacity:0;transform:translateY(10px)}"
            +   "to{opacity:1;transform:translateY(0)}"
            + "}"
            + "@keyframes ti-loading-anim{"
            +   "0%{background-position:0% 50%}"
            +   "100%{background-position:200% 50%}"
            + "}";

        document.head.appendChild(s);
    }
}