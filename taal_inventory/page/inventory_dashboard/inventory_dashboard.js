frappe.pages['inventory-dashboard'].on_page_load = function(wrapper) {
    var page = frappe.ui.make_app_page({
        parent:        wrapper,
        title:         'Inventory Dashboard',
        single_column: true
    });

    page.taal_db = new TaalInventoryDashboard(page);
};

frappe.pages['inventory-dashboard'].on_page_show = function(wrapper) {
    if (wrapper.page && wrapper.page.taal_db) {
        wrapper.page.taal_db.refresh();
    }
};

// ─────────────────────────────────────────────────────────────────
// Dashboard Class
// ─────────────────────────────────────────────────────────────────
class TaalInventoryDashboard {

    constructor(page) {
        this.page          = page;
        this.chart_objects = {};
        this.timer         = null;
        this._setup();
    }

    _setup() {
        this._inject_styles();

        // Refresh button
        this.page.add_inner_button('↺  Refresh', () => this.refresh());

        // Last updated label
        this.$last = $('<span style="font-size:11px;color:#9aa5b1;margin-left:12px;"></span>');
        $(this.page.main).find('.page-actions').append(this.$last);

        // Build skeleton
        $(this.page.body).html(this._skeleton());

        // Auto-refresh every 5 minutes
        this.timer = setInterval(() => this.refresh(), 300000);
    }

    refresh() {
        this._show_loading(true);
        frappe.call({
            method:  'taal_inventory.page.inventory_dashboard.inventory_dashboard.get_dashboard_data',
            freeze:  false,
            callback: (r) => {
                this._show_loading(false);
                if (r.message) {
                    this._render_kpis(r.message.kpis);
                    this._render_charts(r.message.charts);
                    this.$last.text('Updated ' + frappe.datetime.str_to_user(frappe.datetime.now_datetime()));
                }
            }
        });
    }

    // ── Skeleton HTML ───────────────────────────────────────────
    _skeleton() {
        const section = (id, label) =>
            `<div class="ti-section-label">${label}</div><div id="${id}"></div>`;

        const chart_grid = (id, cols, ids) => {
            const cls = cols === 3 ? 'ti-grid-3' : cols === 1 ? 'ti-grid-1' : 'ti-grid-2';
            const cards = ids.map(cid =>
                `<div class="ti-chart-card" id="chart-${cid}">
                    <div class="ti-chart-title" id="title-${cid}"></div>
                    <div class="ti-chart-inner" id="inner-${cid}"></div>
                 </div>`
            ).join('');
            return `<div class="${cls}" id="${id}">${cards}</div>`;
        };

        return `
        <div id="ti-wrap" style="padding:16px 0;">
            ${section('kpi-row-1', 'Core KPIs')}
            ${section('kpi-row-2', 'Stock Alerts')}
            ${section('kpi-row-3', 'This Month Activity')}

            <div class="ti-section-label">Stock Movement</div>
            ${chart_grid('cg-movement', 2, ['weekly_movement', 'daily_dispensing'])}

            <div class="ti-section-label">Inventory Distribution</div>
            ${chart_grid('cg-dist', 3, ['batch_status', 'category_value', 'writeoff_breakdown'])}

            <div class="ti-section-label">Top Items</div>
            ${chart_grid('cg-top', 3, ['top_fast_moving', 'top_stock_value', 'top_patients'])}

            <div class="ti-section-label">Financial & Supplier</div>
            ${chart_grid('cg-fin', 3, ['monthly_profit', 'supplier_purchase', 'margin_category'])}

            <div class="ti-section-label">Expiry & Age</div>
            ${chart_grid('cg-expiry', 2, ['expiry_timeline', 'batch_age'])}

            <div class="ti-section-label">Reorder Analysis</div>
            ${chart_grid('cg-reorder', 1, ['reorder_gap'])}
        </div>`;
    }

    // ── KPI Cards ───────────────────────────────────────────────
    _render_kpis(d) {
        const fmt = (v, f) => {
            const n = parseFloat(v) || 0;
            if (f === 'currency') return '₹ ' + Math.round(n).toLocaleString('en-IN');
            if (f === 'percent')  return n.toFixed(1) + '%';
            if (f === 'float')    return Math.round(n).toLocaleString('en-IN');
            return Math.round(n).toLocaleString('en-IN');
        };

        const rows = [
            {
                id:    'kpi-row-1',
                cards: [
                    { label: 'Total Active Items',  value: d.total_active,   icon: '📦', accent: '#2980b9', format: 'int' },
                    { label: 'Low Stock Items',      value: d.low_stock,      icon: '⚠',  accent: '#e67e22', format: 'int' },
                    { label: 'Critical Below Min',   value: d.critical,       icon: '🔴', accent: '#c0392b', format: 'int' },
                    { label: 'Total Stock Value',    value: d.stock_value,    icon: '💰', accent: '#1e8449', format: 'currency' },
                    { label: 'Expiring in 30 Days',  value: d.exp_30,         icon: '⏰', accent: '#e74c3c', format: 'int' },
                    { label: 'Active Batches',        value: d.active_batches, icon: '🗂', accent: '#2980b9', format: 'int' },
                ]
            },
            {
                id:    'kpi-row-2',
                cards: [
                    { label: 'Slow Moving Items',    value: d.slow_moving,    icon: '🐢', accent: '#7f8c8d', format: 'int' },
                    { label: 'Expiring in 7 Days',   value: d.exp_7,          icon: '🔥', accent: '#c0392b', format: 'int' },
                    { label: 'Overstock Items',       value: d.overstock,      icon: '📈', accent: '#8e44ad', format: 'int' },
                    { label: 'Never Sold Items',      value: d.never_sold,     icon: '⛔', accent: '#7f8c8d', format: 'int' },
                    { label: 'Write-offs This Month', value: d.writeoffs,      icon: '📋', accent: '#e74c3c', format: 'int' },
                    { label: 'Write-off Value',       value: d.writeoff_value, icon: '💸', accent: '#c0392b', format: 'currency' },
                ]
            },
            {
                id:    'kpi-row-3',
                cards: [
                    { label: 'This Month Stock In',  value: d.stock_in,        icon: '📥', accent: '#1e8449', format: 'float' },
                    { label: 'This Month Stock Out', value: d.stock_out,       icon: '📤', accent: '#e74c3c', format: 'float' },
                    { label: 'Unique Patients',       value: d.unique_patients, icon: '👥', accent: '#2980b9', format: 'int' },
                    { label: 'Total Profit',          value: d.total_profit,    icon: '💹', accent: '#1e8449', format: 'currency' },
                    { label: 'Avg Profit Margin',     value: d.avg_margin,      icon: '📊', accent: '#16a085', format: 'percent' },
                    { label: 'Purchase Spend',        value: d.purchase_spend,  icon: '🏭', accent: '#e67e22', format: 'currency' },
                ]
            }
        ];

        rows.forEach(row => {
            const html = '<div class="ti-kpi-flex">' +
                row.cards.map(c => `
                    <div class="ti-kpi-card" style="--tc:${c.accent};"
                         data-value="${c.value}" data-format="${c.format}">
                        <span class="ti-kpi-icon">${c.icon}</span>
                        <div class="ti-kpi-label">${c.label}</div>
                        <div class="ti-kpi-value" data-target="${parseFloat(c.value)||0}"
                             data-format="${c.format}">
                            ${fmt(c.value, c.format)}
                        </div>
                    </div>`
                ).join('') + '</div>';

            $(`#${row.id}`).html(html);
        });

        this._countup_animate();
    }

    _countup_animate() {
        document.querySelectorAll('.ti-kpi-value[data-target]').forEach(el => {
            const target   = parseFloat(el.dataset.target)  || 0;
            const format   = el.dataset.format;
            const steps    = 40;
            const duration = 900;
            let   step     = 0;

            const fmt = (v) => {
                if (format === 'currency') return '₹ ' + Math.round(v).toLocaleString('en-IN');
                if (format === 'percent')  return v.toFixed(1) + '%';
                return Math.round(v).toLocaleString('en-IN');
            };

            const iv = setInterval(() => {
                step++;
                const progress = step / steps;
                const ease     = 1 - Math.pow(1 - progress, 3);
                el.textContent = fmt(target * ease);
                if (step >= steps) {
                    el.textContent = fmt(target);
                    clearInterval(iv);
                }
            }, duration / steps);
        });
    }

    // ── Charts ──────────────────────────────────────────────────
    _render_charts(charts) {
        const cfg = [
            {
                id:     'weekly_movement',
                title:  'Weekly Stock In vs Out',
                type:   'bar',
                colors: ['#1e8449', '#e74c3c'],
                data:   charts.weekly_movement
            },
            {
                id:     'daily_dispensing',
                title:  'Daily Dispensing — Last 30 Days',
                type:   'line',
                colors: ['#2980b9'],
                data:   charts.daily_dispensing
            },
            {
                id:     'batch_status',
                title:  'Batch Status Distribution',
                type:   'donut',
                colors: ['#1e8449', '#7f8c8d', '#c0392b', '#e67e22', '#2980b9'],
                data:   charts.batch_status
            },
            {
                id:     'category_value',
                title:  'Stock Value by Category',
                type:   'pie',
                colors: ['#2980b9', '#8e44ad', '#16a085', '#e67e22', '#c0392b', '#1e8449', '#d4ac0d', '#7f8c8d'],
                data:   charts.category_value
            },
            {
                id:     'writeoff_breakdown',
                title:  'Write-off Breakdown',
                type:   'donut',
                colors: ['#c0392b', '#e67e22', '#7f8c8d', '#e74c3c'],
                data:   charts.writeoff_breakdown
            },
            {
                id:     'top_fast_moving',
                title:  'Top 10 Fast Moving Items',
                type:   'bar',
                colors: ['#2980b9'],
                data:   charts.top_fast_moving
            },
            {
                id:     'top_stock_value',
                title:  'Top 10 Items by Stock Value',
                type:   'bar',
                colors: ['#8e44ad'],
                data:   charts.top_stock_value
            },
            {
                id:     'top_patients',
                title:  'Top 10 Patients by Qty',
                type:   'bar',
                colors: ['#16a085'],
                data:   charts.top_patients
            },
            {
                id:     'monthly_profit',
                title:  'Monthly Profit Trend',
                type:   'line',
                colors: ['#1e8449'],
                data:   charts.monthly_profit
            },
            {
                id:     'supplier_purchase',
                title:  'Supplier Purchase Value',
                type:   'bar',
                colors: ['#e67e22'],
                data:   charts.supplier_purchase
            },
            {
                id:     'margin_category',
                title:  'Avg Profit Margin % by Category',
                type:   'bar',
                colors: ['#16a085'],
                data:   charts.margin_category
            },
            {
                id:     'expiry_timeline',
                title:  'Expiry Timeline — Next 3 Months',
                type:   'bar',
                colors: ['#e74c3c'],
                data:   charts.expiry_timeline
            },
            {
                id:     'batch_age',
                title:  'Batch Age Distribution',
                type:   'bar',
                colors: ['#2980b9'],
                data:   charts.batch_age
            },
            {
                id:     'reorder_gap',
                title:  'Reorder Gap — Current Stock vs Target',
                type:   'bar',
                colors: ['#2980b9', '#dce8f5'],
                data:   charts.reorder_gap
            },
        ];

        cfg.forEach(c => this._draw_chart(c));
    }

    _draw_chart(cfg) {
        const $title = $(`#title-${cfg.id}`);
        const $inner = $(`#inner-${cfg.id}`);

        if (!$inner.length) return;

        $title.text(cfg.title);

        // Destroy existing
        if (this.chart_objects[cfg.id]) {
            try { this.chart_objects[cfg.id].destroy(); } catch(e) {}
            delete this.chart_objects[cfg.id];
        }

        // Guard: no data
        const labels   = (cfg.data && cfg.data.labels)   || [];
        const datasets = (cfg.data && cfg.data.datasets) || [];
        if (!labels.length) {
            $inner.html('<div class="ti-no-data">No data for this period</div>');
            return;
        }

        $inner.html('');

        try {
            this.chart_objects[cfg.id] = new frappe.Chart(`#inner-${cfg.id}`, {
                type:    cfg.type,
                height:  220,
                animate: 1,
                colors:  cfg.colors || ['#2980b9'],
                data:    { labels: labels, datasets: datasets },
                tooltipOptions: { formatTooltipX: d => d, formatTooltipY: d => String(d) }
            });
        } catch(e) {
            $inner.html('<div class="ti-no-data">Chart unavailable</div>');
        }
    }

    // ── Loading overlay ─────────────────────────────────────────
    _show_loading(show) {
        if (show) {
            if (!$('#ti-loading').length) {
                $(this.page.body).append(
                    '<div id="ti-loading" style="position:fixed;top:0;left:0;'
                    + 'width:100%;height:3px;background:linear-gradient(90deg,'
                    + '#2980b9,#1e8449,#e74c3c,#2980b9);background-size:200%;'
                    + 'animation:ti-bar 1.2s linear infinite;z-index:9999;"></div>'
                );
            }
        } else {
            $('#ti-loading').remove();
        }
    }

    // ── Styles ──────────────────────────────────────────────────
    _inject_styles() {
        if (document.getElementById('ti-css')) return;
        const s    = document.createElement('style');
        s.id       = 'ti-css';
        s.innerHTML = `
            .ti-section-label {
                font-size: 10px; font-weight: 600; letter-spacing: 0.8px;
                text-transform: uppercase; color: #9aa5b1;
                border-bottom: 1px solid #eee;
                padding-bottom: 6px; margin: 20px 0 12px;
            }
            .ti-kpi-flex {
                display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 6px;
            }
            .ti-kpi-card {
                flex: 1; min-width: 155px;
                background: #fff;
                border: 2px solid #dce8f5;
                border-left: 6px solid var(--tc, #2980b9);
                border-radius: 12px;
                padding: 16px 18px 14px 16px;
                box-shadow: 0 3px 10px rgba(0,0,0,0.06);
                transition: transform .22s ease, box-shadow .22s ease,
                            background .22s ease, border-color .22s ease;
                cursor: default; position: relative; overflow: hidden;
            }
            .ti-kpi-card:hover {
                transform: translateY(-5px) scale(1.025);
                box-shadow: 0 10px 26px rgba(0,0,0,0.13);
                background: #f3f9ff;
                border-color: var(--tc, #2980b9);
            }
            .ti-kpi-card::after {
                content: ''; position: absolute;
                top: -60%; left: -60%; width: 55%; height: 220%;
                background: linear-gradient(105deg,
                    transparent 40%, rgba(255,255,255,.30) 50%, transparent 60%);
                transform: skewX(-20deg);
                transition: left .55s ease; pointer-events: none;
            }
            .ti-kpi-card:hover::after { left: 150%; }
            .ti-kpi-icon  { font-size: 18px; margin-bottom: 7px; display: block; }
            .ti-kpi-label {
                font-size: 9px; color: #9aa5b1;
                text-transform: uppercase; letter-spacing: .6px;
                font-weight: 600; margin-bottom: 6px;
            }
            .ti-kpi-value {
                font-size: 24px; font-weight: 700;
                color: var(--tc, #2980b9); line-height: 1;
            }
            .ti-grid-2 {
                display: grid; grid-template-columns: 1fr 1fr;
                gap: 12px; margin-bottom: 12px;
            }
            .ti-grid-3 {
                display: grid; grid-template-columns: 1fr 1fr 1fr;
                gap: 12px; margin-bottom: 12px;
            }
            .ti-grid-1 {
                display: grid; grid-template-columns: 1fr;
                gap: 12px; margin-bottom: 12px;
            }
            .ti-chart-card {
                background: #fff;
                border: 1.5px solid #dce8f5;
                border-radius: 12px;
                padding: 14px 16px;
                min-height: 270px;
            }
            .ti-chart-title {
                font-size: 12px; font-weight: 600;
                color: #2c3e50; margin-bottom: 10px;
            }
            .ti-chart-inner { min-height: 220px; }
            .ti-no-data {
                display: flex; align-items: center; justify-content: center;
                height: 180px; color: #bbb; font-size: 12px; font-style: italic;
            }
            @keyframes ti-bar {
                0%   { background-position: 0% 50%; }
                100% { background-position: 200% 50%; }
            }
        `;
        document.head.appendChild(s);
    }
}