// ================================================================
//  Inventory Dashboard — Taal Inventory
//  v2 — Fixes: category names, rolling 30-day charts, KPI hover popup
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


class TaalInventoryDashboard {

    constructor(page) {
        this.page           = page;
        this._cjs           = {};    // Chart.js instances
        this._timer         = null;
        this._popup_cache   = {};    // keyed by popup key, cleared on refresh
        this._popup_timeout = null;  // delay before hiding popup
        this._setup();
    }

    // ── Setup ─────────────────────────────────────────────────────
    _setup() {
        this._inject_styles();

        this.page.add_inner_button("↺  Refresh", () => this.refresh());

        this.$last = $('<span style="font-size:11px;color:#94a3b8;margin-left:12px;"></span>');
        $(this.page.main).find(".page-actions").append(this.$last);

        $(this.page.body).html(this._skeleton());

        // ── Popup DOM element — appended once to body ────────────
        if (!$("#ti-popup").length) {
            $("body").append('<div id="ti-popup"></div>');
        }

        // ── Popup event delegation ────────────────────────────────
        $(this.page.body)
            .on("mouseenter", ".ti-kpi[data-popup]", (e) => {
                clearTimeout(this._popup_timeout);
                const key  = $(e.currentTarget).data("popup");
                const rect = e.currentTarget.getBoundingClientRect();
                this._show_popup(key, rect);
            })
            .on("mouseleave", ".ti-kpi[data-popup]", () => {
                this._popup_timeout = setTimeout(() => this._hide_popup(), 160);
            });

        // Keep popup alive when cursor moves onto it
        $(document)
            .on("mouseenter", "#ti-popup", () => clearTimeout(this._popup_timeout))
            .on("mouseleave", "#ti-popup", () => { this._hide_popup(); });

        // Auto-refresh every 5 minutes
        this._timer = setInterval(() => this.refresh(), 300000);
    }

    // ── Data fetch ────────────────────────────────────────────────
    refresh() {
        this._popup_cache = {};   // clear stale drill-down data
        this._hide_popup();
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
                        "Updated " + frappe.datetime.str_to_user(
                            frappe.datetime.now_datetime()
                        )
                    );
                }
            }
        });
    }

    // ── Page skeleton ─────────────────────────────────────────────
    _skeleton() {
        const sh = (lbl) =>
            `<div class="ti-sechd"><span class="ti-seclbl">${lbl}</span>`
            + `<div class="ti-secline"></div></div>`;

        const krow = (id) => `<div id="${id}" class="ti-kg"></div>`;

        const cg = (cls, ids) => {
            const cards = ids.map(id =>
                `<div class="ti-card" id="ti-card-${id}">`
                + `<div class="ti-ct" id="ti-t-${id}"></div>`
                + `<div id="ti-i-${id}"></div>`
                + `</div>`
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

    // ── KPI Cards ─────────────────────────────────────────────────
    _render_kpis(d) {
        const rows = [
            {
                id: "ti-kr1",
                cards: [
                    { label: "Total Active Items",   value: d.total_active,   accent: "#2980b9", fmt: "int",      popup: null          },
                    { label: "Low Stock Items",       value: d.low_stock,      accent: "#e67e22", fmt: "int",      popup: "low_stock"   },
                    { label: "Critical Below Min",    value: d.critical,       accent: "#c0392b", fmt: "int",      popup: "critical"    },
                    { label: "Total Stock Value",     value: d.stock_value,    accent: "#1e8449", fmt: "currency", popup: null          },
                    { label: "Expiring in 30 Days",   value: d.exp_30,         accent: "#e74c3c", fmt: "int",      popup: "exp_30"      },
                    { label: "Active Batches",        value: d.active_batches, accent: "#2980b9", fmt: "int",      popup: null          },
                ]
            },
            {
                id: "ti-kr2",
                cards: [
                    { label: "Slow Moving Items",     value: d.slow_moving,    accent: "#7f8c8d", fmt: "int",      popup: "slow_moving" },
                    { label: "Expiring in 7 Days",    value: d.exp_7,          accent: "#c0392b", fmt: "int",      popup: "exp_7"       },
                    { label: "Overstock Items",        value: d.overstock,      accent: "#8e44ad", fmt: "int",      popup: "overstock"   },
                    { label: "Never Sold Items",       value: d.never_sold,     accent: "#7f8c8d", fmt: "int",      popup: "never_sold"  },
                    { label: "Write-offs This Month",  value: d.writeoffs,      accent: "#e74c3c", fmt: "int",      popup: null          },
                    { label: "Write-off Value",        value: d.writeoff_value, accent: "#c0392b", fmt: "currency", popup: null          },
                ]
            },
            {
                id: "ti-kr3",
                cards: [
                    { label: "This Month Stock In",   value: d.stock_in,        accent: "#1e8449", fmt: "float",    popup: null },
                    { label: "This Month Stock Out",  value: d.stock_out,       accent: "#e74c3c", fmt: "float",    popup: null },
                    { label: "Unique Patients",        value: d.unique_patients, accent: "#2980b9", fmt: "int",      popup: null },
                    { label: "Total Profit",           value: d.total_profit,    accent: "#1e8449", fmt: "currency", popup: null },
                    { label: "Avg Profit Margin",      value: d.avg_margin,      accent: "#16a085", fmt: "percent",  popup: null },
                    { label: "Purchase Spend",         value: d.purchase_spend,  accent: "#e67e22", fmt: "currency", popup: null },
                ]
            }
        ];

        rows.forEach((row, ri) => {
            const html = row.cards.map((c, ci) => {
                const delay     = (ri * 6 + ci) * 0.04;
                const popupAttr = c.popup ? ` data-popup="${c.popup}"` : "";
                const extraCls  = c.popup ? " ti-kpi-has-popup" : "";
                return `<div class="ti-kpi${extraCls}" style="--tc:${c.accent};`
                    + `animation-delay:${delay}s"${popupAttr}>`
                    + `<div class="ti-kl">${c.label}</div>`
                    + `<div class="ti-kv" data-target="${parseFloat(c.value) || 0}"`
                    + ` data-fmt="${c.fmt}">${this._fmt(c.value, c.fmt)}</div>`
                    + (c.popup ? `<div class="ti-kpi-hint">hover for details</div>` : "")
                    + `</div>`;
            }).join("");
            $(`#${row.id}`).html(html);
        });

        this._count_up();
    }

    // ── Number formatter ──────────────────────────────────────────
    _fmt(v, f) {
        const n = parseFloat(v) || 0;
        if (f === "currency") return "₹ " + Math.round(n).toLocaleString("en-IN");
        if (f === "percent")  return n.toFixed(1) + "%";
        return Math.round(n).toLocaleString("en-IN");
    }

    // ── Count-up animation ────────────────────────────────────────
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

    // ── Chart.js CDN loader ───────────────────────────────────────
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

    // ── Chart definitions ─────────────────────────────────────────
    _render_charts(charts) {
        const P = ["#2980b9","#8e44ad","#16a085","#e67e22","#c0392b",
                   "#1e8449","#d4ac0d","#7f8c8d","#e74c3c","#2ecc71"];

        const cfg = [
            { id: "weekly_movement",    title: "Weekly Stock In vs Out",                type: "bar",      colors: ["#1e8449","#e74c3c"], height: 240, hbar: false },
            { id: "daily_dispensing",   title: "Daily Dispensing — Last 30 Days",        type: "line",     colors: ["#2980b9"],           height: 240, hbar: false },
            { id: "batch_status",       title: "Batch Status Distribution",               type: "doughnut", colors: P,                     height: 240, hbar: false },
            { id: "category_value",     title: "Stock Value by Category",                type: "doughnut", colors: P,                     height: 240, hbar: false },
            { id: "writeoff_breakdown", title: "Write-off Breakdown",                    type: "doughnut", colors: ["#c0392b","#e67e22","#7f8c8d","#e74c3c"], height: 240, hbar: false },
            { id: "top_fast_moving",    title: "Top 10 Fast Moving Items — Last 30 Days",type: "bar",      colors: ["#2980b9"],           height: 400, hbar: true  },
            { id: "top_stock_value",    title: "Top 10 Items by Stock Value",            type: "bar",      colors: ["#8e44ad"],           height: 400, hbar: true  },
            { id: "top_patients",       title: "Top 10 Patients by Qty — Last 30 Days",  type: "bar",      colors: ["#16a085"],           height: 400, hbar: true  },
            { id: "monthly_profit",     title: "Monthly Profit Trend",                   type: "line",     colors: ["#1e8449"],           height: 240, hbar: false },
            { id: "supplier_purchase",  title: "Supplier Purchase Value",                type: "bar",      colors: ["#e67e22"],           height: 340, hbar: true  },
            { id: "margin_category",    title: "Avg Profit Margin % by Category",        type: "bar",      colors: ["#16a085"],           height: 240, hbar: false },
            { id: "expiry_timeline",    title: "Expiry Timeline — Next 3 Months",        type: "bar",      colors: ["#e74c3c"],           height: 240, hbar: false },
            { id: "batch_age",          title: "Batch Age Distribution",                 type: "bar",      colors: ["#2980b9"],           height: 240, hbar: false },
            { id: "reorder_gap",        title: "Reorder Gap — Current Stock vs Target",  type: "bar",      colors: ["#2980b9","#dce8f5"], height: 340, hbar: true  },
        ];

        cfg.forEach(c => {
            $(`#ti-t-${c.id}`).text(c.title);
            this._draw_chart(c.id, c.type, charts[c.id], c.colors, c.height, c.hbar);
        });
    }

    // ── Draw single chart ─────────────────────────────────────────
    _draw_chart(id, type, frData, colors, height, hbar) {
        const $el = $(`#ti-i-${id}`);
        if (!$el.length) return;

        if (this._cjs[id]) {
            try { this._cjs[id].destroy(); } catch (e) {}
            delete this._cjs[id];
        }

        const labels = (frData && frData.labels) || [];
        if (!labels.length) {
            $el.html('<div class="ti-nodata">No data for this period</div>');
            return;
        }

        $el.html(
            `<div style="position:relative;height:${height}px">`
            + `<canvas id="ti-cv-${id}" role="img" aria-label="${id} chart"></canvas></div>`
        );

        const canvas = document.getElementById(`ti-cv-${id}`);
        if (!canvas) return;

        const dark = window.matchMedia("(prefers-color-scheme: dark)").matches;
        const gc   = dark ? "rgba(255,255,255,0.07)" : "rgba(0,0,0,0.06)";
        const lc   = dark ? "#94a3b8" : "#888780";
        const bdr  = dark ? "#1a1a1a" : "#ffffff";
        const raw  = (frData && frData.datasets) || [];

        let cjsData;

        if (type === "doughnut" || type === "pie") {
            const vals = raw.length > 0 ? (raw[0].values || []) : [];
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
                datasets: raw.map((ds, i) => ({
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
            cjsData = {
                labels: labels,
                datasets: raw.map((ds, i) => ({
                    label:           ds.name || "",
                    data:            ds.values || [],
                    backgroundColor: colors[i] || colors[0],
                    borderRadius:    hbar ? 4 : 6,
                    borderSkipped:   false
                }))
            };
        }

        const showLegend = (type === "doughnut" || type === "pie" || raw.length > 1);

        const opts = {
            responsive:          true,
            maintainAspectRatio: false,
            animation:           { duration: 800 },
            plugins: {
                legend: {
                    display:  showLegend,
                    position: "bottom",
                    labels:   { font: { size: 11 }, padding: 12, usePointStyle: true }
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
        } else {
            opts.scales = {
                x: {
                    ticks:  { color: lc, font: { size: 10 }, maxRotation: hbar ? 0 : 45 },
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
            if (hbar) opts.indexAxis = "y";
        }

        try {
            this._cjs[id] = new Chart(canvas, { type, data: cjsData, options: opts });
        } catch (e) {
            $el.html('<div class="ti-nodata">Chart unavailable</div>');
        }
    }

    _rgba(hex, alpha) {
        const r = parseInt(hex.slice(1, 3), 16);
        const g = parseInt(hex.slice(3, 5), 16);
        const b = parseInt(hex.slice(5, 7), 16);
        return `rgba(${r},${g},${b},${alpha})`;
    }

    // ═══════════════════════════════════════════════════════════════
    //  HOVER POPUP
    //  Shows a floating drill-down panel when hovering 7 KPI cards.
    //  Stays visible while cursor is over the card OR the popup itself.
    //  Results are cached per session; cleared on Refresh.
    //
    //  Cards with popups and what they show:
    //    low_stock   → items at/below reorder level
    //    critical    → items at/below minimum stock
    //    exp_30      → batches expiring within 30 days
    //    exp_7       → batches expiring within 7 days (urgent)
    //    overstock   → items above maximum stock
    //    never_sold  → items with zero sales history
    //    slow_moving → items with no sales in 90 days
    // ═══════════════════════════════════════════════════════════════

    _show_popup(key, rect) {
        const $pop = $("#ti-popup");
        if (!$pop.length) return;

        // Always show BELOW the hovered card. Never flip above.
        // position:fixed so rect coords are viewport-relative — no scroll offset needed.
        // Popup body max-height is calculated from available space below the card
        // so the popup always fits and always appears directly below every row.
        let top  = rect.bottom + 10;
        let left = rect.left;

        // Clamp to right viewport edge
        if (left + 290 > window.innerWidth) left = window.innerWidth - 298;
        // Clamp to left viewport edge
        if (left < 8) left = 8;

        // popup chrome = header(~44px) + footer(~30px) = 74px
        // body gets the remaining available space, minimum 80px
        const availBelow = window.innerHeight - rect.bottom - 26;
        this._popup_body_h = Math.max(80, availBelow - 74);

        $pop.css({ top: top + "px", left: left + "px" }).show();

        // ── Return cached data immediately ────────────────────────
        if (this._popup_cache[key]) {
            $pop.html(this._build_popup_html(key, this._popup_cache[key]));
            return;
        }

        // ── Show loading header while fetching ────────────────────
        $pop.html(
            `<div class="ti-pop-head" style="border-top-color:${this._popup_accent(key)}">`
            + `<span class="ti-pop-title">${this._popup_title(key)}</span></div>`
            + `<div class="ti-pop-loading">Loading…</div>`
        );

        // ── Fetch drill-down rows from Python ─────────────────────
        frappe.call({
            method:  "taal_inventory.utils.dashboard.get_kpi_detail",
            args:    { key: key },
            callback: (r) => {
                const rows = r.message || [];
                this._popup_cache[key] = rows;
                // Only update if the popup is still visible
                if ($("#ti-popup").is(":visible")) {
                    $("#ti-popup").html(this._build_popup_html(key, rows));
                }
            }
        });
    }

    _hide_popup() {
        $("#ti-popup").hide().html("");
    }

    _popup_title(key) {
        return {
            low_stock:   "Low Stock Items",
            critical:    "Critical — Below Min",
            exp_30:      "Expiring in 30 Days",
            exp_7:       "Expiring in 7 Days",
            overstock:   "Overstock Items",
            never_sold:  "Never Sold Items",
            slow_moving: "Slow Moving Items"
        }[key] || key;
    }

    _popup_accent(key) {
        return {
            low_stock:   "#e67e22",
            critical:    "#c0392b",
            exp_30:      "#e74c3c",
            exp_7:       "#c0392b",
            overstock:   "#8e44ad",
            never_sold:  "#7f8c8d",
            slow_moving: "#7f8c8d"
        }[key] || "#2980b9";
    }

    _build_popup_html(key, rows) {
        const accent   = this._popup_accent(key);
        const title    = this._popup_title(key);
        const subtitle = {
            low_stock:   "Sorted by largest deficit",
            critical:    "Sorted by largest deficit",
            exp_30:      "Sorted by earliest expiry",
            exp_7:       "Urgent — expiring very soon",
            overstock:   "Sorted by largest excess",
            never_sold:  "Zero sales since creation",
            slow_moving: "No sales in past 90 days"
        }[key] || "";

        const head =
            `<div class="ti-pop-head" style="border-top-color:${accent}">`
            + `<span class="ti-pop-title">${title}</span>`
            + (rows.length
                ? `<span class="ti-pop-count" style="background:${accent}22;color:${accent}">`
                  + rows.length + `</span>`
                : "")
            + `</div>`;

        if (!rows.length) {
            return head + `<div class="ti-pop-empty">No items found</div>`;
        }

        const items = rows.map(r => {
            const name = r.item_name || "—";
            return `<div class="ti-pop-item">`
                + `<div class="ti-pop-name">${name}</div>`
                + `<div class="ti-pop-meta">${this._popup_meta(key, r)}</div>`
                + `</div>`;
        }).join("");

        const bodyH = this._popup_body_h || 256;
        return head
            + `<div class="ti-pop-body" style="max-height:${bodyH}px">${items}</div>`
            + `<div class="ti-pop-foot">${subtitle || "Top " + rows.length + " shown"}</div>`;
    }

    _popup_meta(key, r) {
        const n = (v) => parseFloat(v || 0).toLocaleString("en-IN");
        if (key === "low_stock")
            return `Stock: <b>${n(r.current_stock)}</b>&nbsp;·&nbsp;Reorder at: <b>${n(r.target)}</b>`;
        if (key === "critical")
            return `Stock: <b>${n(r.current_stock)}</b>&nbsp;·&nbsp;Min: <b>${n(r.target)}</b>`;
        if (key === "exp_30" || key === "exp_7")
            return `Expires: <b>${r.expiry_date}</b>&nbsp;·&nbsp;Qty: <b>${n(r.remaining_qty)}</b>`;
        if (key === "overstock")
            return `Stock: <b>${n(r.current_stock)}</b>&nbsp;·&nbsp;Max: <b>${n(r.target)}</b>`;
        return `Stock: <b>${n(r.current_stock)}</b>`;
    }

    // ── Loading bar ───────────────────────────────────────────────
    _loading(show) {
        if (show) {
            if (!$("#ti-loadbar").length) {
                $(this.page.body).append(
                    '<div id="ti-loadbar" style="position:fixed;top:0;left:0;'
                    + 'width:100%;height:3px;'
                    + 'background:linear-gradient(90deg,#2980b9,#1e8449,#e74c3c,#2980b9);'
                    + 'background-size:200%;'
                    + 'animation:ti-loadanim 1.2s linear infinite;z-index:9999;"></div>'
                );
            }
        } else {
            $("#ti-loadbar").remove();
        }
    }

    // ── Styles ────────────────────────────────────────────────────
    _inject_styles() {
        if (document.getElementById("ti-dash-css")) return;
        const s = document.createElement("style");
        s.id    = "ti-dash-css";
        s.textContent = ""
            + "#ti-wrap{padding:16px 0 48px;max-width:1400px;}"
            + ".ti-sechd{display:flex;align-items:center;gap:8px;margin:22px 0 10px}"
            + ".ti-seclbl{font-size:10px;font-weight:500;text-transform:uppercase;"
            +   "letter-spacing:.08em;color:#94a3b8;white-space:nowrap}"
            + ".ti-secline{flex:1;height:0.5px;background:#e2e8f0}"
            + ".ti-kg{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));"
            +   "gap:10px;margin-bottom:14px}"
            + ".ti-kpi{background:#fff;border:0.5px solid #e2e8f0;border-radius:12px;"
            +   "padding:14px 16px;position:relative;overflow:hidden;cursor:default;"
            +   "transition:transform .18s ease,box-shadow .18s ease,border-color .2s ease;"
            +   "animation:ti-kpi-in .4s ease both}"
            + ".ti-kpi:hover{transform:translateY(-4px);"
            +   "box-shadow:0 8px 22px rgba(0,0,0,.09);border-color:#bbb}"
            + ".ti-kpi::before{content:'';position:absolute;top:0;left:0;right:0;"
            +   "height:3px;border-radius:12px 12px 0 0;background:var(--tc,#2980b9)}"
            + ".ti-kpi::after{content:'';position:absolute;top:-60%;left:-60%;"
            +   "width:55%;height:220%;"
            +   "background:linear-gradient(105deg,transparent 40%,"
            +   "rgba(255,255,255,.22) 50%,transparent 60%);"
            +   "transform:skewX(-20deg);transition:left .55s ease;pointer-events:none}"
            + ".ti-kpi:hover::after{left:150%}"
            + ".ti-kpi-has-popup{cursor:pointer}"
            + ".ti-kl{font-size:10px;font-weight:500;color:#64748b;margin-bottom:4px;"
            +   "text-transform:uppercase;letter-spacing:.04em;"
            +   "white-space:nowrap;overflow:hidden;text-overflow:ellipsis}"
            + ".ti-kv{font-size:22px;font-weight:500;line-height:1;"
            +   "font-variant-numeric:tabular-nums;color:var(--tc,#2980b9)}"
            + ".ti-kpi-hint{font-size:9px;color:#d0d0d0;margin-top:5px;"
            +   "transition:color .18s ease;letter-spacing:.02em}"
            + ".ti-kpi:hover .ti-kpi-hint{color:var(--tc,#2980b9)}"
            + ".ti-g2{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:14px}"
            + ".ti-g3{display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px;margin-bottom:14px}"
            + ".ti-g1{display:grid;grid-template-columns:1fr;gap:12px;margin-bottom:14px}"
            + ".ti-card{background:#fff;border:0.5px solid #e2e8f0;border-radius:12px;"
            +   "padding:16px 18px;transition:box-shadow .18s ease}"
            + ".ti-card:hover{box-shadow:0 4px 14px rgba(0,0,0,.07)}"
            + ".ti-ct{font-size:13px;font-weight:500;color:#111;margin-bottom:12px}"
            + ".ti-nodata{display:flex;align-items:center;justify-content:center;"
            +   "height:180px;color:#94a3b8;font-size:12px;font-style:italic}"
            // ── Hover popup ───────────────────────────────────
            + "#ti-popup{position:fixed;width:280px;background:#fff;"
            +   "border:0.5px solid #e2e8f0;border-radius:12px;"
            +   "box-shadow:0 8px 28px rgba(0,0,0,.13);"
            +   "z-index:99999;overflow:hidden;pointer-events:auto}"
            + ".ti-pop-head{display:flex;align-items:center;justify-content:space-between;"
            +   "padding:10px 14px 9px;border-top:3px solid #e67e22;"
            +   "border-bottom:0.5px solid #e2e8f0}"
            + ".ti-pop-title{font-size:10px;font-weight:500;text-transform:uppercase;"
            +   "letter-spacing:.06em;color:#64748b}"
            + ".ti-pop-count{font-size:11px;font-weight:500;padding:1px 8px;"
            +   "border-radius:999px;flex-shrink:0}"
            + ".ti-pop-body{overflow-y:auto}"
            + ".ti-pop-item{padding:8px 14px;border-bottom:0.5px solid #f1f5f9;"
            +   "transition:background .12s ease}"
            + ".ti-pop-item:last-child{border-bottom:none}"
            + ".ti-pop-item:hover{background:#f8fafc}"
            + ".ti-pop-name{font-size:12px;font-weight:500;color:#111;margin-bottom:2px;"
            +   "white-space:nowrap;overflow:hidden;text-overflow:ellipsis}"
            + ".ti-pop-meta{font-size:11px;color:#94a3b8}"
            + ".ti-pop-meta b{color:#64748b;font-weight:500}"
            + ".ti-pop-foot{padding:6px 14px;font-size:10px;color:#94a3b8;"
            +   "text-align:center;border-top:0.5px solid #e2e8f0;background:#f8fafc}"
            + ".ti-pop-empty{padding:20px 14px;font-size:12px;color:#94a3b8;"
            +   "text-align:center;font-style:italic}"
            + ".ti-pop-loading{padding:16px 14px;font-size:12px;color:#94a3b8;"
            +   "text-align:center}"
            // ── Animations ───────────────────────────────────
            + "@keyframes ti-kpi-in{"
            +   "from{opacity:0;transform:translateY(10px)}"
            +   "to{opacity:1;transform:translateY(0)}}"
            + "@keyframes ti-loadanim{"
            +   "0%{background-position:0% 50%}"
            +   "100%{background-position:200% 50%}}";

        document.head.appendChild(s);
    }
}