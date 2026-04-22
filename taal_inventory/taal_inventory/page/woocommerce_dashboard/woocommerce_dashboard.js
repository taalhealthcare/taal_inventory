// ================================================================
//  WooCommerce Orders Dashboard  —  FINAL VERSION
//  App  : taal_inventory
//  Page : woocommerce-dashboard
//  URL  : /app/woocommerce-dashboard
//
//  Confirmed filters applied throughout:
//    1. All metrics  : custom_offline__online = 'Online'
//    2. Revenue KPIs : woocommerce_status IN ('Shipped','Processing')
//    3. Pending card : woocommerce_status = 'Pending' only
//    4. Fulfillment  : two bars — Shipped ÷ Total AND (Shipped+Processing) ÷ Total
//    5. Section 4    : Order Value Distribution + Top States (both side by side)
// ================================================================

frappe.pages["woocommerce-dashboard"].on_page_load = function (wrapper) {
    var page = frappe.ui.make_app_page({
        parent:        wrapper,
        title:         "WooCommerce Orders Dashboard",
        single_column: true
    });

    page.add_inner_button(__("Refresh Dashboard"), function () {
        _woo_load(page);
    });

    _woo_styles();
    _woo_load(page);
};


// ================================================================
//  STYLES
// ================================================================
function _woo_styles() {
    if (document.getElementById("_woo_css")) return;
    var s = document.createElement("style");
    s.id  = "_woo_css";
    s.textContent = ""
        + ".ww{padding:14px 20px 56px;max-width:1400px;margin:0 auto}"
        + ".ww-topbar{display:flex;align-items:center;justify-content:space-between;"
        +   "padding:14px 18px;background:#fff;border:0.5px solid #e2e8f0;"
        +   "border-radius:12px;margin-bottom:12px;flex-wrap:wrap;gap:10px}"
        + ".ww-ttl{font-size:16px;font-weight:500;color:#111;margin-bottom:2px}"
        + ".ww-sub{font-size:11px;color:#94a3b8}"
        + ".ww-chips{display:flex;gap:8px;flex-wrap:wrap}"
        + ".ww-chip{font-size:11px;font-weight:500;padding:3px 10px;border-radius:999px;border:0.5px solid}"
        + ".wc-g{background:#EAF3DE;color:#27500A;border-color:#639922}"
        + ".wc-b{background:#E6F1FB;color:#0C447C;border-color:#378ADD}"
        + ".wc-a{background:#FAEEDA;color:#633806;border-color:#BA7517}"
        + ".ww-notice{background:#E6F1FB;border:0.5px solid #378ADD;border-radius:8px;"
        +   "padding:7px 14px;margin-bottom:18px;font-size:11px;color:#0C447C;font-weight:500}"
        + ".ww-sh{display:flex;align-items:center;gap:8px;margin:22px 0 10px}"
        + ".ww-sl{font-size:10px;font-weight:500;text-transform:uppercase;"
        +   "letter-spacing:.08em;color:#94a3b8;white-space:nowrap}"
        + ".ww-sl-line{flex:1;height:0.5px;background:#e2e8f0}"
        + ".ww-g3{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px;margin-bottom:14px}"
        + ".ww-g2{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px;margin-bottom:14px}"
        // KPI cards
        + ".ww-kpi{background:#fff;border:0.5px solid #e2e8f0;border-radius:12px;"
        +   "padding:15px 17px;position:relative;overflow:hidden;cursor:default;"
        +   "transition:transform .18s,box-shadow .18s,border-color .2s}"
        + ".ww-kpi:hover{transform:translateY(-4px);"
        +   "box-shadow:0 8px 22px rgba(0,0,0,.09);border-color:#bbb}"
        + ".ww-kpi::before{content:'';position:absolute;top:0;left:0;right:0;"
        +   "height:3px;border-radius:12px 12px 0 0}"
        + ".ww-kpi.kg::before{background:#639922} .ww-kpi.kg .ww-kv{color:#27500A}"
        + ".ww-kpi.kb::before{background:#378ADD} .ww-kpi.kb .ww-kv{color:#0C447C}"
        + ".ww-kpi.kt::before{background:#1D9E75} .ww-kpi.kt .ww-kv{color:#085041}"
        + ".ww-kpi.ka::before{background:#BA7517} .ww-kpi.ka .ww-kv{color:#633806}"
        + ".ww-kpi.kp::before{background:#7F77DD} .ww-kpi.kp .ww-kv{color:#3C3489}"
        + ".ww-kpi.kc::before{background:#D85A30} .ww-kpi.kc .ww-kv{color:#712B13}"
        + ".ww-kl{font-size:11px;font-weight:500;color:#64748b;margin-bottom:4px}"
        + ".ww-kv{font-size:26px;font-weight:500;line-height:1;"
        +   "font-variant-numeric:tabular-nums;margin-bottom:4px}"
        + ".ww-ktr{font-size:10px;font-weight:500;margin-bottom:4px}"
        + ".ww-t-up{color:#27500A} .ww-t-dn{color:#791F1F} .ww-t-fl{color:#94a3b8}"
        + ".ww-t-inv-up{color:#791F1F} .ww-t-inv-dn{color:#27500A}"
        + ".ww-knote{font-size:9px;font-weight:500;color:#BA7517;background:#FAEEDA;"
        +   "border-radius:4px;padding:1px 6px;display:inline-block;margin-bottom:5px}"
        + ".ww-ks{font-size:10px;color:#94a3b8;margin-bottom:8px}"
        + ".ww-bar{height:3px;border-radius:999px;background:#e2e8f0}"
        + ".ww-barf{height:100%;border-radius:999px;transition:width 1.1s ease;width:0}"
        // Cards
        + ".ww-card{background:#fff;border:0.5px solid #e2e8f0;border-radius:12px;"
        +   "padding:16px 18px;transition:box-shadow .18s}"
        + ".ww-card:hover{box-shadow:0 4px 14px rgba(0,0,0,.07)}"
        + ".ww-ct{font-size:13px;font-weight:500;color:#111;margin-bottom:2px}"
        + ".ww-cs{font-size:11px;color:#94a3b8;margin-bottom:12px}"
        // Snapshot stat tiles
        + ".ww-stc{background:#f8fafc;border:0.5px solid #e2e8f0;border-radius:10px;"
        +   "padding:12px 14px;transition:transform .15s}"
        + ".ww-stc:hover{transform:translateY(-2px)}"
        + ".ww-stl{font-size:10px;font-weight:500;text-transform:uppercase;"
        +   "letter-spacing:.05em;color:#94a3b8;margin-bottom:5px}"
        + ".ww-stv{font-size:13px;font-weight:500;color:#111;margin-bottom:2px}"
        + ".ww-sts{font-size:10px;color:#94a3b8;margin-bottom:6px}"
        // Legend inline
        + ".ww-leg-row{display:flex;align-items:center;gap:6px;margin-bottom:6px}"
        + ".ww-leg-dot{width:9px;height:9px;border-radius:2px;flex-shrink:0}"
        + ".ww-leg-lbl{font-size:11px;color:#64748b;flex:1}"
        + ".ww-leg-val{font-size:11px;font-weight:500;color:#111}"
        // Horizontal bar rows
        + ".ww-hbar-row{display:flex;align-items:center;gap:8px;margin-bottom:8px}"
        + ".ww-hbar-lbl{font-size:10px;color:#94a3b8;width:100px;flex-shrink:0;"
        +   "text-align:right;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}"
        + ".ww-hbar-track{flex:1;height:16px;border-radius:4px;background:#f1f5f9;overflow:hidden}"
        + ".ww-hbar-fill{height:100%;border-radius:4px;transition:width 1s ease;width:0;"
        +   "display:flex;align-items:center;justify-content:flex-end;padding-right:5px}"
        + ".ww-hbar-inner-val{font-size:9px;font-weight:500;color:#fff}"
        + ".ww-hbar-outer-val{font-size:10px;font-weight:500;color:#555;min-width:20px}"
        // Ranked rows
        + ".ww-srow{display:flex;align-items:center;gap:8px;padding:7px 0;"
        +   "border-bottom:0.5px solid #e2e8f0}"
        + ".ww-srow:last-child{border-bottom:none}"
        + ".ww-sn{font-size:11px;color:#111;flex:1;min-width:0;overflow:hidden;"
        +   "text-overflow:ellipsis;white-space:nowrap}"
        + ".ww-sc{font-size:10px;font-weight:500;color:#555;min-width:18px;text-align:right}"
        + ".ww-sv{font-size:11px;color:#27500A;min-width:64px;text-align:right}"
        + ".ww-sbw{flex:1;min-width:32px;height:4px;border-radius:999px;background:#e2e8f0}"
        + ".ww-sbf{height:100%;border-radius:999px;transition:width 1.1s ease;width:0}"
        // Order feed table
        + ".ww-feed-hd{display:grid;grid-template-columns:1.4fr 1.6fr 1fr 1fr 1fr;"
        +   "gap:8px;padding:6px 0;border-bottom:0.5px solid #e2e8f0;margin-bottom:4px}"
        + ".ww-feed-hd span{font-size:10px;font-weight:500;color:#94a3b8;"
        +   "text-transform:uppercase;letter-spacing:.04em}"
        + ".ww-frow{display:grid;grid-template-columns:1.4fr 1.6fr 1fr 1fr 1fr;"
        +   "gap:8px;padding:7px 0;border-bottom:0.5px solid #e2e8f0;"
        +   "align-items:center;transition:background .12s;border-radius:4px}"
        + ".ww-frow:last-child{border-bottom:none}"
        + ".ww-frow:hover{background:#f8fafc}"
        + ".ww-oid{font-size:12px;font-weight:500;color:#0C447C;"
        +   "overflow:hidden;text-overflow:ellipsis;white-space:nowrap}"
        + ".ww-fcust{font-size:12px;color:#111;overflow:hidden;"
        +   "text-overflow:ellipsis;white-space:nowrap}"
        + ".ww-fdate{font-size:11px;color:#94a3b8}"
        + ".ww-famt{font-size:12px;font-weight:500;color:#27500A}"
        // Status badges
        + ".ww-badge{font-size:10px;font-weight:500;padding:1px 7px;border-radius:999px}"
        + ".wb-sh{background:#E1F5EE;color:#085041}"
        + ".wb-pr{background:#E6F1FB;color:#0C447C}"
        + ".wb-pe{background:#FAEEDA;color:#633806}"
        + ".wb-oh{background:#F1EFE8;color:#444441}"
        + ".wb-rf{background:#EEEDFE;color:#3C3489}"
        + ".wb-cn{background:#FCEBEB;color:#791F1F}"
        // Pipeline
        + ".ww-pipe{display:flex;align-items:stretch;margin:12px 0 14px}"
        + ".ww-ps{flex:1;text-align:center}"
        + ".ww-pb{border-radius:10px;padding:11px 4px;"
        +   "transition:transform .15s,box-shadow .15s;cursor:default}"
        + ".ww-pb:hover{transform:translateY(-3px);box-shadow:0 5px 14px rgba(0,0,0,.09)}"
        + ".ww-pn{font-size:20px;font-weight:500;line-height:1;margin-bottom:2px;"
        +   "font-variant-numeric:tabular-nums}"
        + ".ww-pl{font-size:8px;font-weight:500;text-transform:uppercase;letter-spacing:.04em}"
        + ".ww-pa{width:14px;flex-shrink:0;display:flex;align-items:center;justify-content:center}"
        // Fulfilment bars in pipeline
        + ".ww-fbar-wrap{background:#f8fafc;border-radius:8px;padding:10px 14px;margin-bottom:8px}"
        + ".ww-fbar-wrap:last-child{margin-bottom:0}"
        + ".ww-fbar-hd{display:flex;justify-content:space-between;align-items:center;margin-bottom:4px}"
        + ".ww-fbar-lbl{font-size:11px;font-weight:500;color:#64748b}"
        + ".ww-fbar-pct{font-size:13px;font-weight:500}"
        + ".ww-fbar-track{height:6px;border-radius:999px;background:#e2e8f0}"
        + ".ww-fbar-fill{height:100%;border-radius:999px;transition:width 1.2s ease;width:0}"
        // Customer stat mini tiles
        + ".ww-cstat-g{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-bottom:14px}"
        + ".ww-cstat{background:#f8fafc;border-radius:10px;padding:12px 10px;"
        +   "text-align:center;transition:transform .15s}"
        + ".ww-cstat:hover{transform:translateY(-2px)}"
        + ".ww-csv{font-size:22px;font-weight:500;color:#0C447C;margin-bottom:3px;"
        +   "font-variant-numeric:tabular-nums}"
        + ".ww-csl{font-size:10px;font-weight:500;color:#94a3b8;"
        +   "text-transform:uppercase;letter-spacing:.04em}"
        // Bucket bars (order value distribution)
        + ".ww-bkt{margin-bottom:10px}"
        + ".ww-bkt-hd{display:flex;justify-content:space-between;margin-bottom:3px}"
        + ".ww-bkt-lbl{font-size:11px;color:#64748b}"
        + ".ww-bkt-val{font-size:11px;font-weight:500;color:#111}"
        + ".ww-bkt-track{height:8px;border-radius:4px;background:#e2e8f0}"
        + ".ww-bkt-fill{height:100%;border-radius:4px;transition:width 1.1s ease;width:0}"
        // Loading
        + ".ww-loading{text-align:center;padding:80px 20px;font-size:14px;color:#94a3b8}"
        + ".ww-empty{text-align:center;padding:24px 0;font-size:13px;color:#94a3b8;font-style:italic}"
        // Animations
        + "@keyframes _wwIn{from{opacity:0;transform:translateY(10px)}to{opacity:1;transform:translateY(0)}}"
        + ".ww-kpi:nth-child(1){animation:_wwIn .4s .04s ease both}"
        + ".ww-kpi:nth-child(2){animation:_wwIn .4s .08s ease both}"
        + ".ww-kpi:nth-child(3){animation:_wwIn .4s .12s ease both}"
        + ".ww-kpi:nth-child(4){animation:_wwIn .4s .16s ease both}"
        + ".ww-kpi:nth-child(5){animation:_wwIn .4s .20s ease both}"
        + ".ww-kpi:nth-child(6){animation:_wwIn .4s .24s ease both}"
        + ".ww-card{animation:_wwIn .4s .28s ease both}"
        + ".ww-stc{animation:_wwIn .4s .30s ease both}";
    document.head.appendChild(s);
}


// ================================================================
//  LOAD
// ================================================================
function _woo_load(page) {
    $(page.main).find(".ww,.ww-loading").remove();
    $(page.main).append('<div class="ww-loading">Loading dashboard…</div>');
    frappe.call({
        method:  "taal_inventory.page.woocommerce_dashboard.woocommerce_dashboard.get_woocommerce_dashboard_data",
        freeze:  false,
        callback: function (r) {
            $(page.main).find(".ww-loading").remove();
            if (r.message) {
                _woo_cjs(function () { _woo_build(page, r.message); });
            } else {
                $(page.main).append('<div class="ww-loading">No data found.</div>');
            }
        },
        error: function () {
            $(page.main).find(".ww-loading").remove();
            $(page.main).append('<div class="ww-loading">Failed to load. Please refresh.</div>');
        }
    });
}

function _woo_cjs(cb) {
    if (window.Chart && typeof window.Chart.register === "function") { cb(); return; }
    var s = document.createElement("script");
    s.src   = "https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js";
    s.onload = cb;
    document.head.appendChild(s);
}

function _woo_build(page, data) {
    $(page.main).append(_woo_html(data));
    setTimeout(function () { _woo_animate(data); }, 120);
    setTimeout(function () { _woo_fill_bars();   }, 540);
    setTimeout(function () { _woo_charts(data); }, 360);
}


// ================================================================
//  HELPERS
// ================================================================
function _woo_inr(n) {
    return "₹ " + Math.round(n || 0).toLocaleString("en-IN");
}

function _woo_badge(status) {
    var s   = (status || "").toLowerCase();
    var cls = s === "shipped"     ? "wb-sh"
            : s === "processing"  ? "wb-pr"
            : s === "pending"     ? "wb-pe"
            : s === "on-hold"     ? "wb-oh"
            : s === "refunded"    ? "wb-rf"
            : "wb-cn";
    return '<span class="ww-badge ' + cls + '">' + (status || "—") + "</span>";
}

function _woo_trend(t, invert) {
    if (!t || t.dir === "flat" || t.pct === 0)
        return '<div class="ww-ktr ww-t-fl">— stable</div>';
    var up    = t.dir === "up";
    var arrow = up ? "↑" : "↓";
    var good  = invert ? !up : up;
    var cls   = invert ? (up ? "ww-t-inv-up" : "ww-t-inv-dn") : (good ? "ww-t-up" : "ww-t-dn");
    return '<div class="ww-ktr ' + cls + '">' + arrow + " " + t.pct + "% vs last month</div>";
}

function _woo_sechd(lbl) {
    return '<div class="ww-sh"><span class="ww-sl">' + lbl + '</span>'
         + '<div class="ww-sl-line"></div></div>';
}

function _woo_kpi(cls, lbl, vid, val, trendHtml, note, sub, fid, fc, fw) {
    return '<div class="ww-kpi ' + cls + '">'
        + '<div class="ww-kl">' + lbl + "</div>"
        + '<div class="ww-kv" id="' + vid + '">' + val + "</div>"
        + trendHtml
        + (note ? '<div class="ww-knote">' + note + "</div>" : "")
        + '<div class="ww-ks">' + sub + "</div>"
        + '<div class="ww-bar"><div class="ww-barf" id="' + fid + '" style="background:' + fc + '" data-w="' + fw + '"></div></div>'
        + "</div>";
}

function _woo_pipe_stage(bg, tc, lc, id, val, lbl) {
    return '<div class="ww-ps"><div class="ww-pb" style="background:' + bg + '">'
        + '<div class="ww-pn" style="color:' + tc + '" id="' + id + '">' + val + "</div>"
        + '<div class="ww-pl" style="color:' + lc + '">' + lbl + "</div>"
        + "</div></div>";
}

function _woo_arrow(down) {
    return '<div class="ww-pa"><svg width="12" height="12" viewBox="0 0 12 12" fill="none">'
        + (down
            ? '<path d="M6 2v8M3 7l3 3 3-3" stroke="#E24B4A" stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round"/>'
            : '<path d="M2 6h8M7 3l3 3-3 3" stroke="#cbd5e1" stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round"/>')
        + "</svg></div>";
}

function _woo_fbar_block(lbl, pct, color, id_fill) {
    return '<div class="ww-fbar-wrap">'
        + '<div class="ww-fbar-hd">'
        + '<span class="ww-fbar-lbl">' + lbl + "</span>"
        + '<span class="ww-fbar-pct" style="color:' + color + '">' + pct + "%</span>"
        + "</div>"
        + '<div class="ww-fbar-track"><div class="ww-fbar-fill" id="' + id_fill + '" style="background:' + color + '" data-w="' + pct + '"></div></div>'
        + "</div>";
}


// ================================================================
//  HTML BUILDER
// ================================================================
function _woo_html(d) {
    var kp = d.kpis     || {};
    var sn = d.snapshot || {};
    var ch = d.charts   || {};
    var t5 = d.top5     || {};
    var fd = d.feed     || [];
    var pl = d.pipeline || {};
    var H  = '<div class="ww">';

    // ── Top bar ──────────────────────────────────────────────
    H += '<div class="ww-topbar">'
       + '<div><div class="ww-ttl">WooCommerce Orders Dashboard</div>'
       + '<div class="ww-sub">TAALPLUS CHC Private Limited · taalhealthcare.com · Online orders only</div></div>'
       + '<div class="ww-chips">'
       + '<span class="ww-chip wc-g">' + _woo_inr(kp.total_revenue || 0) + " revenue</span>"
       + '<span class="ww-chip wc-b">' + (kp.total_orders || 0) + " online orders</span>"
       + '<span class="ww-chip wc-a">' + (kp.pending || 0) + " pending</span>"
       + "</div></div>";

    // Filter notice
    H += '<div class="ww-notice">All metrics: custom_offline__online = Online'
       + " · Revenue KPIs: woocommerce_status IN (Shipped, Processing)"
       + " · Pending card: woocommerce_status = Pending only</div>";

    // ════ SECTION 1 — KPIs ════════════════════════════════════
    H += _woo_sechd("Section 1 — Revenue & order KPIs");
    H += '<div class="ww-g3">';

    H += _woo_kpi("kg", "Total revenue (Online)",
        "ww-k1", _woo_inr(kp.total_revenue || 0),
        _woo_trend({dir:"flat",pct:0}, false),
        "Shipped + Processing only",
        "Online · woocommerce_status IN (Shipped, Processing)",
        "ww-kf1", "#639922", 100);

    H += _woo_kpi("kb", "This month's revenue",
        "ww-k2", _woo_inr(kp.month_revenue || 0),
        _woo_trend(kp.month_rev_trend, false),
        "Shipped + Processing only",
        "Current month · Online · Shipped or Processing",
        "ww-kf2", "#378ADD",
        kp.total_revenue > 0
            ? Math.round((kp.month_revenue / kp.total_revenue) * 100) : 0);

    H += _woo_kpi("kt", "Average order value",
        "ww-k3", _woo_inr(kp.avg_order_value || 0),
        _woo_trend({dir:"flat",pct:0}, false),
        "Online orders only",
        "grand_total ÷ total Online orders",
        "ww-kf3", "#1D9E75", 45);

    H += _woo_kpi("ka", "Pending delivery",
        "ww-k4", String(kp.pending || 0),
        _woo_trend({dir:"flat",pct:0}, true),
        "woocommerce_status = Pending only",
        "Online · woocommerce_status = Pending",
        "ww-kf4", "#BA7517",
        kp.total_orders > 0
            ? Math.round(((kp.pending || 0) / kp.total_orders) * 100) : 0);

    H += _woo_kpi("kp", "Orders today",
        "ww-k5", String(kp.today_orders || 0),
        _woo_trend(kp.today_trend, false),
        "Online orders only",
        "transaction_date = " + frappe.datetime.get_today() + " · Online",
        "ww-kf5", "#7F77DD",
        kp.total_orders > 0
            ? Math.round(((kp.today_orders || 0) / kp.total_orders) * 100) : 0);

    H += _woo_kpi("kc", "Cancelled orders",
        "ww-k6", String(kp.cancelled || 0),
        _woo_trend({dir:"flat",pct:0}, true),
        "Online orders only",
        "woocommerce_status = Cancelled · Online",
        "ww-kf6", "#D85A30",
        kp.total_orders > 0
            ? Math.round(((kp.cancelled || 0) / kp.total_orders) * 100) : 0);

    H += "</div>";

    // ════ SECTION 2 — Snapshot ════════════════════════════════
    H += _woo_sechd("Section 2 — Snapshot summary");
    H += '<div class="ww-g3">';

    // Top customer
    H += '<div class="ww-stc">'
       + '<div class="ww-stl">Top customer</div>'
       + '<div class="ww-stv">' + (sn.top_customer || "—") + "</div>"
       + '<div class="ww-sts">' + (sn.top_cust_cnt || 0) + " Online orders · " + _woo_inr(sn.top_cust_rev || 0) + "</div>"
       + '<div class="ww-bar"><div class="ww-barf" style="background:#378ADD" data-w="100"></div></div>'
       + "</div>";

    // Top item
    H += '<div class="ww-stc">'
       + '<div class="ww-stl">Top item by revenue</div>'
       + '<div class="ww-stv">' + (sn.top_item || "—") + "</div>"
       + '<div class="ww-sts">' + (sn.top_item_qty || 0) + " units · " + _woo_inr(sn.top_item_rev || 0) + "</div>"
       + '<div class="ww-bar"><div class="ww-barf" style="background:#1D9E75" data-w="100"></div></div>'
       + "</div>";

    // Both fulfillment rates
    var fs  = kp.fulfil_shipped   || 0;
    var fsp = kp.fulfil_ship_proc || 0;
    H += '<div class="ww-stc">'
       + '<div class="ww-stl">Fulfillment rates</div>'
       + '<div style="margin-bottom:10px">'
       + '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px">'
       + '<span style="font-size:10px;color:#64748b">Shipped only</span>'
       + '<span style="font-size:12px;font-weight:500;color:#085041">' + fs + "%</span></div>"
       + '<div class="ww-bar"><div class="ww-barf" style="background:#1D9E75" data-w="' + fs + '"></div></div>'
       + "</div>"
       + '<div>'
       + '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px">'
       + '<span style="font-size:10px;color:#64748b">Shipped + Processing</span>'
       + '<span style="font-size:12px;font-weight:500;color:#0C447C">' + fsp + "%</span></div>"
       + '<div class="ww-bar"><div class="ww-barf" style="background:#378ADD" data-w="' + fsp + '"></div></div>'
       + "</div></div>";

    H += "</div>";

    // ════ SECTION 3 — Status + Payment doughnuts ══════════════
    H += _woo_sechd("Section 3 — Status & payment distribution");
    H += '<div class="ww-g2">';

    // Status doughnut
    var ws  = ch.woo_status || {};
    var wsl = ws.labels || [];
    var wsv = (ws.datasets && ws.datasets[0] && ws.datasets[0].values) || [];
    var statusColors = {
        "Shipped":"#27ae60","shipped":"#27ae60",
        "Processing":"#f39c12","processing":"#f39c12",
        "Pending":"#BA7517","pending":"#BA7517",
        "on-hold":"#888780","Cancelled":"#e74c3c","cancelled":"#e74c3c",
        "failed":"#e74c3c","Refunded":"#8e44ad","refunded":"#8e44ad"
    };
    var legendRows = "";
    for (var li = 0; li < wsl.length; li++) {
        legendRows += '<div class="ww-leg-row">'
            + '<span class="ww-leg-dot" style="background:' + (statusColors[wsl[li]] || "#888780") + '"></span>'
            + '<span class="ww-leg-lbl">' + wsl[li] + "</span>"
            + '<span class="ww-leg-val">' + (wsv[li] || 0) + "</span>"
            + "</div>";
    }
    H += '<div class="ww-card">'
       + '<div class="ww-ct">WooCommerce order status</div>'
       + '<div class="ww-cs">Online orders · woocommerce_status field</div>'
       + '<div style="display:flex;gap:16px;align-items:center">'
       + '<canvas id="ww-ch1" role="img" aria-label="Status doughnut" style="flex-shrink:0;width:140px;height:140px">Order status distribution</canvas>'
       + '<div style="flex:1">' + legendRows + "</div></div></div>";

    // Payment doughnut
    var pm  = ch.payment || {};
    var pml = pm.labels  || [];
    var pmv = (pm.datasets && pm.datasets[0] && pm.datasets[0].values) || [];
    var pmColors = ["#378ADD","#7F77DD","#D85A30","#888780","#639922","#D4537E"];
    var pmLegend = "";
    for (var pi = 0; pi < pml.length; pi++) {
        pmLegend += '<div class="ww-leg-row">'
            + '<span class="ww-leg-dot" style="background:' + pmColors[pi % pmColors.length] + '"></span>'
            + '<span class="ww-leg-lbl">' + pml[pi] + "</span>"
            + '<span class="ww-leg-val">' + (pmv[pi] || 0) + "</span>"
            + "</div>";
    }
    H += '<div class="ww-card">'
       + '<div class="ww-ct">Payment method</div>'
       + '<div class="ww-cs">Online orders · woocommerce_payment_method field</div>'
       + '<div style="display:flex;gap:16px;align-items:center">'
       + '<canvas id="ww-ch2" role="img" aria-label="Payment doughnut" style="flex-shrink:0;width:140px;height:140px">Payment method distribution</canvas>'
       + '<div style="flex:1">' + pmLegend + "</div></div></div>";

    H += "</div>";

    // ════ SECTION 4 — Order value buckets + Top states ════════
    H += _woo_sechd("Section 4 — Order value distribution & top states");
    H += '<div class="ww-g2">';

    // Order value distribution
    var ob    = ch.order_buckets || {};
    var obl   = ob.labels  || [];
    var obv   = (ob.datasets && ob.datasets[0] && ob.datasets[0].values) || [];
    var obMax = Math.max.apply(null, obv) || 1;
    var bktColors = ["#D4537E","#378ADD","#639922","#7F77DD","#D85A30"];
    var bktHtml = "";
    for (var bi = 0; bi < obl.length; bi++) {
        var bpct = Math.round((obv[bi] / obMax) * 100);
        bktHtml += '<div class="ww-bkt">'
            + '<div class="ww-bkt-hd">'
            + '<span class="ww-bkt-lbl">' + obl[bi] + "</span>"
            + '<span class="ww-bkt-val">' + (obv[bi] || 0) + " orders</span>"
            + "</div>"
            + '<div class="ww-bkt-track"><div class="ww-bkt-fill" style="background:'
            + bktColors[bi % bktColors.length] + '" data-w="' + bpct + '"></div></div>'
            + "</div>";
    }
    var maxBktIdx  = obv.indexOf(Math.max.apply(null, obv));
    var maxBktLbl  = obl[maxBktIdx] || "";
    H += '<div class="ww-card">'
       + '<div class="ww-ct">Order value distribution</div>'
       + '<div class="ww-cs">Online orders grouped by grand_total · price bracket breakdown</div>'
       + bktHtml
       + '<div style="margin-top:10px;padding-top:10px;border-top:0.5px solid #e2e8f0;'
       + 'display:flex;justify-content:space-between;align-items:center">'
       + '<span style="font-size:10px;color:#94a3b8">Most orders in</span>'
       + '<span style="font-size:12px;font-weight:500;color:#639922">' + maxBktLbl + "</span>"
       + "</div></div>";

    // Top states
    var ts  = ch.top_states || {};
    var tsl = ts.labels  || [];
    var tsv = (ts.datasets && ts.datasets[0] && ts.datasets[0].values) || [];
    var tsMax   = Math.max.apply(null, tsv) || 1;
    var tsColors = ["#378ADD","#1D9E75","#7F77DD","#D85A30","#639922","#BA7517","#D4537E","#888780"];
    var stateRows = "";
    for (var si = 0; si < tsl.length; si++) {
        var spct = Math.round((tsv[si] / tsMax) * 100);
        stateRows += '<div class="ww-hbar-row">'
            + '<span class="ww-hbar-lbl">' + tsl[si] + "</span>"
            + '<div class="ww-hbar-track"><div class="ww-hbar-fill" style="background:'
            + tsColors[si % tsColors.length] + '" data-w="' + spct + '">'
            + (spct > 20 ? '<span class="ww-hbar-inner-val">' + tsv[si] + "</span>" : "")
            + "</div></div>"
            + (spct <= 20 ? '<span class="ww-hbar-outer-val">' + tsv[si] + "</span>" : "")
            + "</div>";
    }
    if (!stateRows) stateRows = '<div class="ww-empty">No state data found in shipping addresses.</div>';
    H += '<div class="ww-card">'
       + '<div class="ww-ct">Top states by order count</div>'
       + '<div class="ww-cs">Online orders · shipping address state · top 8</div>'
       + stateRows + "</div>";

    H += "</div>";

    // ════ SECTION 5 — Monthly revenue + order volume ══════════
    H += _woo_sechd("Section 5 — Revenue & order volume trends");
    H += '<div class="ww-g2">';

    H += '<div class="ww-card">'
       + '<div class="ww-ct">Monthly revenue trend</div>'
       + '<div class="ww-cs">Online · Shipped + Processing · last 6 months</div>'
       + '<canvas id="ww-ch3" role="img" aria-label="Monthly revenue line chart" style="width:100%;height:200px">Monthly revenue</canvas>'
       + "</div>";

    H += '<div class="ww-card">'
       + '<div class="ww-ct">Monthly order volume</div>'
       + '<div class="ww-cs">Online orders only · last 6 months</div>'
       + '<canvas id="ww-ch4" role="img" aria-label="Monthly order volume bar chart" style="width:100%;height:200px">Monthly order volume</canvas>'
       + "</div>";

    H += "</div>";

    // ════ SECTION 6 — Top items + top customers ═══════════════
    H += _woo_sechd("Section 6 — Top items & top customers");
    H += '<div class="ww-g2">';

    // Top items
    var items     = t5.items    || [];
    var itemMax   = items.length > 0 ? (items[0].rev || 1) : 1;
    var itemColors = ["#D85A30","#378ADD","#7F77DD","#1D9E75","#639922","#BA7517"];
    var itemRows   = "";
    for (var ii = 0; ii < items.length; ii++) {
        var itm  = items[ii];
        var ipct = Math.round((itm.rev / itemMax) * 100);
        itemRows += '<div class="ww-srow">'
            + '<div class="ww-sbw"><div class="ww-sbf" style="background:'
            + itemColors[ii % itemColors.length] + '" data-w="' + ipct + '"></div></div>'
            + '<span class="ww-sn">' + (itm.name || "—") + "</span>"
            + '<span class="ww-sc">' + (itm.qty  || 0)   + "</span>"
            + '<span class="ww-sv">' + _woo_inr(itm.rev || 0) + "</span>"
            + "</div>";
    }
    if (!itemRows) itemRows = '<div class="ww-empty">No item data</div>';

    H += '<div class="ww-card">'
       + '<div class="ww-ct">Top items by revenue</div>'
       + '<div class="ww-cs">Online · Shipped + Processing · top 6</div>'
       + '<div style="display:flex;gap:8px;padding:5px 0;border-bottom:0.5px solid #e2e8f0;margin-bottom:2px">'
       + '<div style="flex:1;min-width:32px"></div>'
       + '<span style="font-size:10px;font-weight:500;color:#94a3b8;flex:1;min-width:0">Item</span>'
       + '<span style="font-size:10px;font-weight:500;color:#94a3b8;min-width:22px;text-align:right">Qty</span>'
       + '<span style="font-size:10px;font-weight:500;color:#94a3b8;min-width:66px;text-align:right">Revenue</span>'
       + "</div>"
       + itemRows + "</div>";

    // Top customers
    var custs     = t5.customers || [];
    var custMax   = custs.length > 0 ? (custs[0].rev || 1) : 1;
    var custColors = ["#378ADD","#1D9E75","#D85A30","#7F77DD","#BA7517"];
    var custRows   = "";
    for (var ci = 0; ci < custs.length; ci++) {
        var cst  = custs[ci];
        var cpct = Math.round((cst.rev / custMax) * 100);
        custRows += '<div class="ww-srow">'
            + '<div class="ww-sbw"><div class="ww-sbf" style="background:'
            + custColors[ci % custColors.length] + '" data-w="' + cpct + '"></div></div>'
            + '<span class="ww-sn">' + (cst.name || "—") + "</span>"
            + '<span class="ww-sc">' + (cst.cnt  || 0)   + "</span>"
            + '<span class="ww-sv">' + _woo_inr(cst.rev || 0) + "</span>"
            + "</div>";
    }
    if (!custRows) custRows = '<div class="ww-empty">No customer data</div>';

    H += '<div class="ww-card">'
       + '<div class="ww-ct">Top customers by revenue</div>'
       + '<div class="ww-cs">Online orders only · top 5</div>'
       + '<div style="display:flex;gap:8px;padding:5px 0;border-bottom:0.5px solid #e2e8f0;margin-bottom:2px">'
       + '<div style="flex:1;min-width:32px"></div>'
       + '<span style="font-size:10px;font-weight:500;color:#94a3b8;flex:1;min-width:0">Customer</span>'
       + '<span style="font-size:10px;font-weight:500;color:#94a3b8;min-width:22px;text-align:right">Ord</span>'
       + '<span style="font-size:10px;font-weight:500;color:#94a3b8;min-width:66px;text-align:right">Revenue</span>'
       + "</div>"
       + custRows + "</div>";

    H += "</div>";

    // ════ SECTION 7 — Recent orders ═══════════════════════════
    H += _woo_sechd("Section 7 — Recent orders");
    H += '<div class="ww-card">'
       + '<div class="ww-ct">Recent orders</div>'
       + '<div class="ww-cs">Last 8 Online Sales Orders · newest first</div>'
       + '<div class="ww-feed-hd">'
       + "<span>Order ID</span><span>Customer</span><span>Date</span><span>Amount</span><span>Status</span>"
       + "</div>";

    if (fd.length === 0) {
        H += '<div class="ww-empty">No recent orders found.</div>';
    } else {
        for (var fi = 0; fi < fd.length; fi++) {
            var f = fd[fi];
            H += '<div class="ww-frow">'
               + '<div class="ww-oid">'   + (f.name || "—") + "</div>"
               + '<div class="ww-fcust">' + (f.customer || "—") + "</div>"
               + '<div class="ww-fdate">' + (f.transaction_date || "—") + "</div>"
               + '<div class="ww-famt">'  + _woo_inr(f.grand_total || 0) + "</div>"
               + "<div>" + _woo_badge(f.woocommerce_status) + "</div>"
               + "</div>";
        }
    }
    H += "</div>";

    // ════ SECTION 8 — Pipeline + Customer stats ════════════════
    H += _woo_sechd("Section 8 — Order pipeline & customer statistics");
    H += '<div class="ww-g2">';

    // Pipeline
    H += '<div class="ww-card">';
    H += '<div class="ww-ct">WooCommerce order pipeline</div>';
    H += '<div class="ww-cs">Online orders · count at each woocommerce_status stage</div>';
    H += '<div class="ww-pipe">'
       + _woo_pipe_stage("#FAEEDA","#633806","#854F0B","ww-pp1",pl.pending    || 0,"Pending")
       + _woo_arrow(false)
       + _woo_pipe_stage("#F1EFE8","#444441","#5F5E5A","ww-pp2",pl.on_hold    || 0,"On Hold")
       + _woo_arrow(false)
       + _woo_pipe_stage("#E6F1FB","#0C447C","#185FA5","ww-pp3",pl.processing || 0,"Processing")
       + _woo_arrow(false)
       + _woo_pipe_stage("#E1F5EE","#085041","#0F6E56","ww-pp4",pl.shipped    || 0,"Shipped")
       + _woo_arrow(true)
       + _woo_pipe_stage("#EEEDFE","#3C3489","#534AB7","ww-pp5",pl.refunded   || 0,"Refunded")
       + _woo_arrow(true)
       + _woo_pipe_stage("#FCEBEB","#791F1F","#A32D2D","ww-pp6",pl.cancelled  || 0,"Cancelled")
       + "</div>";

    H += _woo_fbar_block(
        "Fulfillment rate — Shipped ÷ Total Online orders",
        pl.fulfil_shipped || 0, "#1D9E75", "ww-fb1");
    H += _woo_fbar_block(
        "Active fulfillment — Shipped + Processing ÷ Total",
        pl.fulfil_ship_proc || 0, "#378ADD", "ww-fb2");

    H += "</div>";

    // Customer stats
    var rr = sn.repeat_rate || 0;
    H += '<div class="ww-card">';
    H += '<div class="ww-ct">Customer statistics</div>';
    H += '<div class="ww-cs">Online order behaviour · repeat purchase metrics</div>';
    H += '<div class="ww-cstat-g">'
       + '<div class="ww-cstat"><div class="ww-csv" id="ww-cs1">' + (sn.unique_customers || 0) + "</div><div class=\"ww-csl\">Unique customers</div></div>"
       + '<div class="ww-cstat"><div class="ww-csv" id="ww-cs2">' + (sn.repeat_customers || 0) + "</div><div class=\"ww-csl\">Returning customers</div></div>"
       + '<div class="ww-cstat"><div class="ww-csv" id="ww-cs3">' + rr + "%</div><div class=\"ww-csl\">Repeat rate</div></div>"
       + "</div>";
    H += '<div style="border-top:0.5px solid #e2e8f0;padding-top:14px">'
       + '<div style="font-size:11px;font-weight:500;color:#64748b;margin-bottom:6px">'
       + rr + "% of Online customers placed more than one order"
       + "</div>"
       + '<div class="ww-fbar-track"><div class="ww-fbar-fill" id="ww-rrbar" style="background:#7F77DD" data-w="' + rr + '"></div></div>'
       + '<div style="display:flex;justify-content:space-between;margin-top:3px">'
       + '<span style="font-size:10px;color:#94a3b8">0%</span>'
       + '<span style="font-size:13px;font-weight:500;color:#3C3489">' + rr + "%</span>"
       + '<span style="font-size:10px;color:#94a3b8">100%</span>'
       + "</div>"
       + '<div style="margin-top:14px;padding-top:14px;border-top:0.5px solid #e2e8f0;'
       + 'display:flex;justify-content:space-between;align-items:center">'
       + '<div><div style="font-size:11px;color:#64748b">Avg orders per customer</div>'
       + '<div style="font-size:9px;color:#94a3b8;margin-top:1px">Total Online orders ÷ unique customers</div></div>'
       + '<span style="font-size:22px;font-weight:500;color:#0C447C">' + (sn.avg_per_customer || 0) + "</span>"
       + "</div></div>";
    H += "</div>";

    H += "</div>"; // close g2
    H += "</div>"; // close ww
    return H;
}


// ================================================================
//  ANIMATIONS
// ================================================================
function _woo_animate(d) {
    var kp = d.kpis     || {};
    var pl = d.pipeline || {};
    var sn = d.snapshot || {};

    function cu(id, target) {
        var el = document.getElementById(id);
        if (!el) return;
        var s = 0, step = Math.max(1, Math.ceil(target / 25));
        var t = setInterval(function () {
            s += step;
            if (s >= target) {
                el.textContent = Math.round(target).toLocaleString("en-IN");
                clearInterval(t);
            } else {
                el.textContent = Math.round(s).toLocaleString("en-IN");
            }
        }, 28);
    }

    cu("ww-pp1", pl.pending    || 0);
    cu("ww-pp2", pl.on_hold    || 0);
    cu("ww-pp3", pl.processing || 0);
    cu("ww-pp4", pl.shipped    || 0);
    cu("ww-pp5", pl.refunded   || 0);
    cu("ww-pp6", pl.cancelled  || 0);
    cu("ww-cs1", sn.unique_customers || 0);
    cu("ww-cs2", sn.repeat_customers || 0);
    cu("ww-cs3", sn.repeat_rate      || 0);
}

function _woo_fill_bars() {
    document.querySelectorAll("[data-w]").forEach(function (el) {
        el.style.width = (el.getAttribute("data-w") || 0) + "%";
    });
}


// ================================================================
//  CHARTS
// ================================================================
function _woo_charts(d) {
    var ch   = d.charts || {};
    var dark = window.matchMedia("(prefers-color-scheme: dark)").matches;
    var gc   = dark ? "rgba(255,255,255,0.07)" : "rgba(0,0,0,0.06)";
    var lc   = dark ? "#94a3b8" : "#888780";
    var bdr  = dark ? "#1a1a1a" : "#ffffff";

    var statusColors = {
        "Shipped":"#27ae60","shipped":"#27ae60",
        "Processing":"#f39c12","processing":"#f39c12",
        "Pending":"#BA7517","pending":"#BA7517",
        "on-hold":"#888780","Cancelled":"#e74c3c","cancelled":"#e74c3c",
        "failed":"#e74c3c","Refunded":"#8e44ad","refunded":"#8e44ad"
    };
    var pmColors = ["#378ADD","#7F77DD","#D85A30","#888780","#639922","#D4537E"];

    var dOpts = {
        responsive: false,
        maintainAspectRatio: false,
        cutout: "65%",
        animation: { animateRotate: true, duration: 800 },
        plugins: {
            legend: { display: false },
            tooltip: {
                callbacks: {
                    label: function (ctx) {
                        var tot = ctx.dataset.data.reduce(function (a, b) { return a + b; }, 0);
                        var pct = tot > 0 ? Math.round(ctx.parsed / tot * 100) : 0;
                        return " " + ctx.label + ": " + ctx.parsed + " (" + pct + "%)";
                    }
                }
            }
        }
    };

    // Chart 1 — Status doughnut
    var c1 = document.getElementById("ww-ch1");
    if (c1) {
        var ws  = ch.woo_status || {};
        var wsl = ws.labels || [];
        new Chart(c1, {
            type: "doughnut",
            data: {
                labels:   wsl,
                datasets: [{
                    data:            (ws.datasets && ws.datasets[0] && ws.datasets[0].values) || [],
                    backgroundColor: wsl.map(function (s) { return statusColors[s] || "#888780"; }),
                    borderWidth: 3, borderColor: bdr, hoverOffset: 8
                }]
            },
            options: dOpts
        });
    }

    // Chart 2 — Payment doughnut
    var c2 = document.getElementById("ww-ch2");
    if (c2) {
        var pm  = ch.payment || {};
        var pml = pm.labels  || [];
        new Chart(c2, {
            type: "doughnut",
            data: {
                labels:   pml,
                datasets: [{
                    data:            (pm.datasets && pm.datasets[0] && pm.datasets[0].values) || [],
                    backgroundColor: pml.map(function (_, i) { return pmColors[i % pmColors.length]; }),
                    borderWidth: 3, borderColor: bdr, hoverOffset: 8
                }]
            },
            options: dOpts
        });
    }

    // Chart 3 — Monthly revenue line
    var c3 = document.getElementById("ww-ch3");
    if (c3) {
        var mr = ch.monthly_rev || {};
        new Chart(c3, {
            type: "line",
            data: {
                labels:   (mr.labels && mr.labels.length) ? mr.labels : ["No data"],
                datasets: [{
                    label:               "Revenue (₹)",
                    data:                (mr.datasets && mr.datasets[0] && mr.datasets[0].values) || [0],
                    borderColor:         "#639922",
                    backgroundColor:     dark ? "rgba(99,153,34,.12)" : "rgba(99,153,34,.08)",
                    borderWidth:         2,
                    pointBackgroundColor: "#639922",
                    pointBorderColor:    bdr,
                    pointBorderWidth:    2,
                    pointRadius:         5,
                    pointHoverRadius:    7,
                    tension:             0.4,
                    fill:                true
                }]
            },
            options: {
                responsive: true, maintainAspectRatio: false,
                animation: { duration: 900 },
                plugins: {
                    legend: { display: false },
                    tooltip: { callbacks: { label: function (ctx) {
                        return " ₹ " + (ctx.parsed.y || 0).toLocaleString("en-IN");
                    }}}
                },
                scales: {
                    x: { ticks: { color: lc, font: { size: 11 }, autoSkip: false, maxRotation: 30 }, grid: { color: gc }, border: { display: false }},
                    y: { beginAtZero: true, ticks: { color: lc, font: { size: 10 },
                        callback: function (v) { return v >= 1000 ? "₹" + Math.round(v/1000) + "K" : "₹" + v; }
                    }, grid: { color: gc }, border: { display: false }}
                }
            }
        });
    }

    // Chart 4 — Monthly order volume bar
    var c4 = document.getElementById("ww-ch4");
    if (c4) {
        var mo  = ch.monthly_ord || {};
        var mol = (mo.labels && mo.labels.length) ? mo.labels : ["No data"];
        new Chart(c4, {
            type: "bar",
            data: {
                labels:   mol,
                datasets: [{
                    label:           "Orders",
                    data:            (mo.datasets && mo.datasets[0] && mo.datasets[0].values) || [0],
                    backgroundColor: "#378ADD",
                    borderRadius:    6,
                    borderSkipped:   false
                }]
            },
            options: {
                responsive: true, maintainAspectRatio: false,
                animation: { duration: 900 },
                plugins: {
                    legend: { display: false },
                    tooltip: { callbacks: { label: function (ctx) { return " " + ctx.parsed.y + " orders"; }}}
                },
                scales: {
                    x: { ticks: { color: lc, font: { size: 11 }, autoSkip: false, maxRotation: 30 }, grid: { color: gc }, border: { display: false }},
                    y: { beginAtZero: true, ticks: { color: lc, font: { size: 11 }, stepSize: 5 }, grid: { color: gc }, border: { display: false }}
                }
            }
        });
    }
}