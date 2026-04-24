// ================================================================
//  Orders Dashboard  —  Online / Offline toggle + Period Labels
//  App  : taal_inventory
//  Page : woocommerce-dashboard
//  URL  : /app/woocommerce-dashboard
//
//  Every card, chart and section shows the active period label.
//  All Online metrics filter to woocommerce_status IN (Shipped, Processing).
//  Date presets use pure JavaScript — no Frappe date helpers needed.
// ================================================================

frappe.pages["woocommerce-dashboard"].on_page_load = function (wrapper) {
    var page = frappe.ui.make_app_page({
        parent:        wrapper,
        title:         "Orders Dashboard",
        single_column: true
    });

    window._woo_page_ref    = page;
    window._woo_mode        = "online";
    window._woo_from_date   = null;
    window._woo_to_date     = null;
    window._woo_period_label = "All Time";
    window._woo_period_range = "";

    page.add_inner_button(__("Refresh"), function () {
        _dash_load(window._woo_mode, window._woo_from_date,
                   window._woo_to_date, window._woo_period_label);
    });

    // ── Pure-JS date helpers (frappe.datetime.month_start etc
    //    do NOT exist in Frappe v15 — never use them) ──────────
    function _pad(n) { return String(n).padStart(2, "0"); }

    function _fmt(d) {
        // Returns YYYY-MM-DD from a Date object
        return d.getFullYear() + "-" + _pad(d.getMonth() + 1) + "-" + _pad(d.getDate());
    }

    function _monthStart(dateStr) {
        return dateStr.slice(0, 7) + "-01";
    }

    function _monthEnd(dateStr) {
        var p = dateStr.split("-");
        var last = new Date(parseInt(p[0]), parseInt(p[1]), 0).getDate();
        return p[0] + "-" + p[1] + "-" + _pad(last);
    }

    function _weekStart(dateStr) {
        var d   = new Date(dateStr);
        var day = d.getDay();                      // 0=Sun
        var diff = (day === 0) ? -6 : 1 - day;    // to Monday
        d.setDate(d.getDate() + diff);
        return _fmt(d);
    }

    function _monthsAgo(dateStr, n) {
        var p = dateStr.split("-");
        var y = parseInt(p[0]), m = parseInt(p[1]) - n;
        while (m <= 0) { m += 12; y -= 1; }
        var mm  = _pad(m);
        var last = new Date(y, m, 0).getDate();
        var dd  = _pad(Math.min(parseInt(p[2]), last));
        return y + "-" + mm + "-" + dd;
    }

    // Friendly display of date range, e.g. "Apr 24, 2026" or "Apr 1 – Apr 24, 2026"
    function _rangeDisplay(fd, td) {
        if (!fd || !td) return "";
        var months = ["Jan","Feb","Mar","Apr","May","Jun",
                      "Jul","Aug","Sep","Oct","Nov","Dec"];
        var fd_parts = fd.split("-"), td_parts = td.split("-");
        var fdStr = months[parseInt(fd_parts[1])-1] + " " + parseInt(fd_parts[2]) + ", " + fd_parts[0];
        var tdStr = months[parseInt(td_parts[1])-1] + " " + parseInt(td_parts[2]) + ", " + td_parts[0];
        return fd === td ? fdStr : fdStr + " – " + tdStr;
    }

    // ── Date preset buttons ───────────────────────────────────
    var presets = [
        { label: "Today",           abbr: "Today" },
        { label: "This Week",       abbr: "This Week" },
        { label: "This Month",      abbr: "This Month" },
        { label: "Last 3 Months",   abbr: "Last 3 Months" },
        { label: "Last 6 Months",   abbr: "Last 6 Months" }
    ];

    presets.forEach(function (preset) {
        page.add_inner_button(__(preset.label), function () {
            var today = frappe.datetime.get_today();   // YYYY-MM-DD ✓ always works
            var fd, td;

            if      (preset.label === "Today")         { fd = today;                  td = today; }
            else if (preset.label === "This Week")     { fd = _weekStart(today);      td = today; }
            else if (preset.label === "This Month")    { fd = _monthStart(today);     td = _monthEnd(today); }
            else if (preset.label === "Last 3 Months") { fd = _monthsAgo(today, 3);  td = today; }
            else if (preset.label === "Last 6 Months") { fd = _monthsAgo(today, 6);  td = today; }

            if (!fd || !td) {
                frappe.msgprint("Could not compute date range — please try again.");
                return;
            }

            window._woo_from_date    = fd;
            window._woo_to_date      = td;
            window._woo_period_label = preset.label;
            window._woo_period_range = _rangeDisplay(fd, td);

            console.log("[Dashboard] Preset:", preset.label, "| fd:", fd, "| td:", td);
            _dash_load(window._woo_mode, fd, td, preset.label);
        }, __("Date Presets"));
    });

    page.add_inner_button(__("Clear (All Time)"), function () {
        window._woo_from_date    = null;
        window._woo_to_date      = null;
        window._woo_period_label = "All Time";
        window._woo_period_range = "";
        _dash_load(window._woo_mode, null, null, "All Time");
    }, __("Date Presets"));

    _dash_styles();
    _dash_load("online", null, null, "All Time");
};


// ================================================================
//  GLOBAL TOGGLE
// ================================================================
window._dash_switch = function (mode) {
    if (mode === window._woo_mode) return;
    window._woo_mode = mode;
    _dash_load(mode, window._woo_from_date, window._woo_to_date, window._woo_period_label);
};


// ================================================================
//  LOAD
// ================================================================
function _dash_load(mode, fd, td, periodLabel) {
    var page = window._woo_page_ref;
    if (!page) return;
    $(page.main).find(".dw,.dw-loading").remove();
    $(page.main).append('<div class="dw-loading">Loading dashboard…</div>');

    console.log("[Dashboard] API call — mode:", mode, "from:", fd||"all", "to:", td||"all", "period:", periodLabel);

    frappe.call({
        method: "taal_inventory.utils.woocommerce_dashboard.get_woocommerce_dashboard_data",
        args: {
            mode:         mode,
            from_date:    fd  || "",
            to_date:      td  || "",
            period_label: periodLabel || "All Time"
        },
        freeze: false,
        callback: function (r) {
            $(page.main).find(".dw-loading").remove();
            if (r.message) {
                _dash_cjs(function () {
                    _dash_build(r.message, mode, fd, td, periodLabel || "All Time");
                });
            } else {
                $(page.main).append('<div class="dw-loading">No data found.</div>');
            }
        },
        error: function () {
            $(page.main).find(".dw-loading").remove();
            $(page.main).append('<div class="dw-loading">Failed to load. Please refresh.</div>');
        }
    });
}

function _dash_cjs(cb) {
    if (window.Chart && typeof window.Chart.register === "function") { cb(); return; }
    var s = document.createElement("script");
    s.src = "https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js";
    s.onload = cb;
    document.head.appendChild(s);
}

function _dash_build(data, mode, fd, td, periodLabel) {
    var html = mode === "offline"
        ? _offline_html(data, fd, td, periodLabel)
        : _online_html(data, fd, td, periodLabel);
    var page = window._woo_page_ref;
    $(page.main).append(html);
    setTimeout(function () { _dash_animate(data, mode); }, 120);
    setTimeout(function () { _fill_bars();               }, 540);
    setTimeout(function () { _dash_charts(data, mode);  }, 360);
}


// ================================================================
//  STYLES
// ================================================================
function _dash_styles() {
    if (document.getElementById("_dash_css")) return;
    var s = document.createElement("style");
    s.id  = "_dash_css";
    s.textContent = ""
        + ".dw{padding:14px 20px 56px;max-width:1400px;margin:0 auto}"
        + ".dw-topbar{display:flex;align-items:center;justify-content:space-between;"
        +   "padding:14px 18px;background:#fff;border:0.5px solid #e2e8f0;"
        +   "border-radius:12px;margin-bottom:12px;flex-wrap:wrap;gap:12px}"
        + ".dw-ttl{font-size:16px;font-weight:500;color:#111;margin-bottom:2px}"
        + ".dw-sub{font-size:11px;color:#94a3b8}"
        + ".dw-chips{display:flex;gap:8px;flex-wrap:wrap}"
        + ".dw-chip{font-size:11px;font-weight:500;padding:3px 10px;border-radius:999px;border:0.5px solid}"
        // Period badge — prominent orange pill
        + ".dw-period-badge{display:inline-flex;align-items:center;gap:6px;"
        +   "background:#FAEEDA;color:#633806;border:1.5px solid #BA7517;"
        +   "border-radius:8px;padding:5px 12px;font-size:12px;font-weight:600;"
        +   "white-space:nowrap}"
        + ".dw-period-badge svg{flex-shrink:0}"
        // Period tag on cards and chart subtitles
        + ".dw-period-tag{display:inline-block;background:#EAF3DE;color:#27500A;"
        +   "border:0.5px solid #639922;border-radius:4px;padding:1px 7px;"
        +   "font-size:9px;font-weight:600;text-transform:uppercase;letter-spacing:.05em;"
        +   "margin-left:6px;vertical-align:middle}"
        + ".dw-toggle{display:flex;background:#f1f5f9;border-radius:10px;padding:4px;gap:0}"
        + ".dw-tb{padding:8px 22px;border-radius:8px;border:none;cursor:pointer;"
        +   "font-size:13px;font-weight:500;transition:all .2s ease;outline:none}"
        + ".dw-notice{border-radius:8px;padding:7px 14px;margin-bottom:14px;"
        +   "font-size:11px;font-weight:500}"
        + ".dw-notice-g{background:#EAF3DE;border:0.5px solid #639922;color:#27500A}"
        + ".dw-notice-b{background:#E6F1FB;border:0.5px solid #378ADD;color:#0C447C}"
        // Period filter bar — shows active period across full width
        + ".dw-period-bar{background:#FFF7ED;border:1.5px solid #F59E0B;"
        +   "border-radius:10px;padding:10px 16px;margin-bottom:16px;"
        +   "display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:8px}"
        + ".dw-period-bar-lbl{font-size:13px;font-weight:600;color:#633806}"
        + ".dw-period-bar-range{font-size:11px;color:#92400e}"
        + ".dw-sh{display:flex;align-items:center;gap:8px;margin:22px 0 10px}"
        + ".dw-sl{font-size:10px;font-weight:500;text-transform:uppercase;"
        +   "letter-spacing:.08em;color:#94a3b8;white-space:nowrap}"
        + ".dw-sl-line{flex:1;height:0.5px;background:#e2e8f0}"
        + ".dw-g3{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px;margin-bottom:14px}"
        + ".dw-g2{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px;margin-bottom:14px}"
        + ".dw-kpi{background:#fff;border:0.5px solid #e2e8f0;border-radius:12px;"
        +   "padding:15px 17px;position:relative;overflow:hidden;cursor:default;"
        +   "transition:transform .18s,box-shadow .18s,border-color .2s}"
        + ".dw-kpi:hover{transform:translateY(-4px);"
        +   "box-shadow:0 8px 22px rgba(0,0,0,.09);border-color:#bbb}"
        + ".dw-kpi::before{content:'';position:absolute;top:0;left:0;right:0;"
        +   "height:3px;border-radius:12px 12px 0 0}"
        + ".dw-kpi.kg::before{background:#639922}.dw-kpi.kg .dw-kv{color:#27500A}"
        + ".dw-kpi.kb::before{background:#378ADD}.dw-kpi.kb .dw-kv{color:#0C447C}"
        + ".dw-kpi.kt::before{background:#1D9E75}.dw-kpi.kt .dw-kv{color:#085041}"
        + ".dw-kpi.ka::before{background:#BA7517}.dw-kpi.ka .dw-kv{color:#633806}"
        + ".dw-kpi.kp::before{background:#7F77DD}.dw-kpi.kp .dw-kv{color:#3C3489}"
        + ".dw-kpi.kc::before{background:#D85A30}.dw-kpi.kc .dw-kv{color:#712B13}"
        + ".dw-kl{font-size:11px;font-weight:500;color:#64748b;margin-bottom:4px}"
        + ".dw-kv{font-size:26px;font-weight:500;line-height:1;"
        +   "font-variant-numeric:tabular-nums;margin-bottom:4px}"
        + ".dw-ktr{font-size:10px;font-weight:500;margin-bottom:4px}"
        + ".dw-t-up{color:#27500A}.dw-t-dn{color:#791F1F}.dw-t-fl{color:#94a3b8}"
        + ".dw-t-inv-up{color:#791F1F}.dw-t-inv-dn{color:#27500A}"
        + ".dw-knote{font-size:9px;font-weight:500;color:#BA7517;background:#FAEEDA;"
        +   "border-radius:4px;padding:1px 6px;display:inline-block;margin-bottom:5px}"
        + ".dw-ks{font-size:10px;color:#94a3b8;margin-bottom:8px}"
        + ".dw-bar{height:3px;border-radius:999px;background:#e2e8f0}"
        + ".dw-barf{height:100%;border-radius:999px;transition:width 1.1s ease;width:0}"
        + ".dw-card{background:#fff;border:0.5px solid #e2e8f0;border-radius:12px;"
        +   "padding:16px 18px;transition:box-shadow .18s}"
        + ".dw-card:hover{box-shadow:0 4px 14px rgba(0,0,0,.07)}"
        + ".dw-ct{font-size:13px;font-weight:500;color:#111;margin-bottom:2px}"
        + ".dw-cs{font-size:11px;color:#94a3b8;margin-bottom:12px}"
        + ".dw-stc{background:#f8fafc;border:0.5px solid #e2e8f0;border-radius:10px;"
        +   "padding:12px 14px;transition:transform .15s}"
        + ".dw-stc:hover{transform:translateY(-2px)}"
        + ".dw-stl{font-size:10px;font-weight:500;text-transform:uppercase;"
        +   "letter-spacing:.05em;color:#94a3b8;margin-bottom:5px}"
        + ".dw-stv{font-size:13px;font-weight:500;color:#111;margin-bottom:2px}"
        + ".dw-sts{font-size:10px;color:#94a3b8;margin-bottom:6px}"
        + ".dw-tax-row{display:flex;justify-content:space-between;align-items:center;"
        +   "padding:6px 0;border-bottom:0.5px solid #e2e8f0}"
        + ".dw-tax-row:last-of-type{border-bottom:none}"
        + ".dw-tax-lbl{display:flex;align-items:center;gap:7px;font-size:11px;color:#64748b}"
        + ".dw-tax-dot{width:9px;height:9px;border-radius:2px;flex-shrink:0}"
        + ".dw-tax-val{font-size:12px;font-weight:500}"
        + ".dw-tds-row{display:flex;justify-content:space-between;align-items:center;"
        +   "background:#FCEBEB;border-radius:6px;padding:6px 10px;margin:6px 0}"
        + ".dw-net-row{display:flex;justify-content:space-between;align-items:center;"
        +   "background:#EAF3DE;border-radius:6px;padding:8px 10px;margin-top:8px}"
        + ".dw-leg-row{display:flex;align-items:center;gap:6px;margin-bottom:6px}"
        + ".dw-leg-dot{width:9px;height:9px;border-radius:2px;flex-shrink:0}"
        + ".dw-leg-lbl{font-size:11px;color:#64748b;flex:1}"
        + ".dw-leg-val{font-size:11px;font-weight:500;color:#111}"
        + ".dw-hbar-row{display:flex;align-items:center;gap:8px;margin-bottom:8px}"
        + ".dw-hbar-lbl{font-size:10px;color:#94a3b8;width:100px;flex-shrink:0;"
        +   "text-align:right;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}"
        + ".dw-hbar-track{flex:1;height:16px;border-radius:4px;background:#f1f5f9;overflow:hidden}"
        + ".dw-hbar-fill{height:100%;border-radius:4px;transition:width 1s ease;width:0;"
        +   "display:flex;align-items:center;justify-content:flex-end;padding-right:5px}"
        + ".dw-hbar-outer{font-size:10px;font-weight:500;color:#555;min-width:20px}"
        + ".dw-srow{display:flex;align-items:center;gap:8px;padding:7px 0;"
        +   "border-bottom:0.5px solid #e2e8f0}"
        + ".dw-srow:last-child{border-bottom:none}"
        + ".dw-sn{font-size:11px;color:#111;flex:1;min-width:0;overflow:hidden;"
        +   "text-overflow:ellipsis;white-space:nowrap}"
        + ".dw-sc{font-size:10px;font-weight:500;color:#555;min-width:18px;text-align:right}"
        + ".dw-sv{font-size:11px;color:#27500A;min-width:64px;text-align:right}"
        + ".dw-sbw{flex:1;min-width:32px;height:4px;border-radius:999px;background:#e2e8f0}"
        + ".dw-sbf{height:100%;border-radius:999px;transition:width 1.1s ease;width:0}"
        + ".dw-feed-hd{display:grid;grid-template-columns:1.4fr 1.6fr 1fr 1fr 1.2fr;"
        +   "gap:8px;padding:6px 0;border-bottom:0.5px solid #e2e8f0;margin-bottom:4px}"
        + ".dw-feed-hd span{font-size:10px;font-weight:500;color:#94a3b8;"
        +   "text-transform:uppercase;letter-spacing:.04em}"
        + ".dw-frow{display:grid;grid-template-columns:1.4fr 1.6fr 1fr 1fr 1.2fr;"
        +   "gap:8px;padding:7px 0;border-bottom:0.5px solid #e2e8f0;"
        +   "align-items:center;transition:background .12s;border-radius:4px}"
        + ".dw-frow:last-child{border-bottom:none}"
        + ".dw-frow:hover{background:#f8fafc}"
        + ".dw-oid{font-size:12px;font-weight:500;overflow:hidden;"
        +   "text-overflow:ellipsis;white-space:nowrap}"
        + ".dw-fcust{font-size:12px;color:#111;overflow:hidden;"
        +   "text-overflow:ellipsis;white-space:nowrap}"
        + ".dw-fdate{font-size:11px;color:#94a3b8}"
        + ".dw-famt{font-size:12px;font-weight:500;color:#27500A}"
        + ".dw-badge{font-size:10px;font-weight:500;padding:1px 7px;border-radius:999px}"
        + ".db-sh{background:#E1F5EE;color:#085041}"
        + ".db-pr{background:#E6F1FB;color:#0C447C}"
        + ".db-pe{background:#FAEEDA;color:#633806}"
        + ".db-oh{background:#F1EFE8;color:#444441}"
        + ".db-rf{background:#EEEDFE;color:#3C3489}"
        + ".db-cn{background:#FCEBEB;color:#791F1F}"
        + ".db-cp{background:#EAF3DE;color:#27500A}"
        + ".db-tb{background:#E6F1FB;color:#0C447C}"
        + ".db-td{background:#FAEEDA;color:#633806}"
        + ".dw-pipe{display:flex;align-items:stretch;margin:12px 0 14px}"
        + ".dw-ps{flex:1;text-align:center}"
        + ".dw-pb{border-radius:10px;padding:11px 4px;"
        +   "transition:transform .15s,box-shadow .15s;cursor:default}"
        + ".dw-pb:hover{transform:translateY(-3px);box-shadow:0 5px 14px rgba(0,0,0,.09)}"
        + ".dw-pn{font-size:20px;font-weight:500;line-height:1;margin-bottom:2px;"
        +   "font-variant-numeric:tabular-nums}"
        + ".dw-pl{font-size:8px;font-weight:500;text-transform:uppercase;letter-spacing:.04em}"
        + ".dw-pa{width:14px;flex-shrink:0;display:flex;align-items:center;justify-content:center}"
        + ".dw-fbar{background:#f8fafc;border-radius:8px;padding:10px 14px;margin-bottom:8px}"
        + ".dw-fbar:last-child{margin-bottom:0}"
        + ".dw-fbar-hd{display:flex;justify-content:space-between;align-items:center;margin-bottom:4px}"
        + ".dw-fbar-lbl{font-size:11px;font-weight:500;color:#64748b}"
        + ".dw-fbar-pct{font-size:13px;font-weight:500}"
        + ".dw-fbar-track{height:6px;border-radius:999px;background:#e2e8f0}"
        + ".dw-fbar-fill{height:100%;border-radius:999px;transition:width 1.2s ease;width:0}"
        + ".dw-csg{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-bottom:14px}"
        + ".dw-cst{background:#f8fafc;border-radius:10px;padding:12px 10px;"
        +   "text-align:center;transition:transform .15s}"
        + ".dw-cst:hover{transform:translateY(-2px)}"
        + ".dw-csv{font-size:22px;font-weight:500;margin-bottom:3px;font-variant-numeric:tabular-nums}"
        + ".dw-csl{font-size:10px;font-weight:500;color:#94a3b8;"
        +   "text-transform:uppercase;letter-spacing:.04em}"
        + ".dw-tax-mo-row{display:flex;align-items:center;gap:8px;margin-bottom:8px}"
        + ".dw-tax-mo-lbl{font-size:10px;color:#94a3b8;width:44px;flex-shrink:0}"
        + ".dw-tax-seg-wrap{flex:1;height:14px;border-radius:4px;background:#f1f5f9;overflow:hidden;display:flex}"
        + ".dw-tds-pill{height:14px;border-radius:4px;background:#FCEBEB;border:0.5px solid #F09595;"
        +   "display:flex;align-items:center;justify-content:center;flex-shrink:0;min-width:24px}"
        + ".dw-tds-inner{font-size:8px;font-weight:500;color:#791F1F}"
        + ".dw-tax-mo-val{font-size:10px;color:#94a3b8;width:52px;text-align:right;flex-shrink:0}"
        + ".dw-loading{text-align:center;padding:80px 20px;font-size:14px;color:#94a3b8}"
        + ".dw-empty{text-align:center;padding:24px 0;font-size:13px;color:#94a3b8;font-style:italic}"
        + "@keyframes _dwIn{from{opacity:0;transform:translateY(10px)}to{opacity:1;transform:translateY(0)}}"
        + ".dw-kpi:nth-child(1){animation:_dwIn .4s .04s ease both}"
        + ".dw-kpi:nth-child(2){animation:_dwIn .4s .08s ease both}"
        + ".dw-kpi:nth-child(3){animation:_dwIn .4s .12s ease both}"
        + ".dw-kpi:nth-child(4){animation:_dwIn .4s .16s ease both}"
        + ".dw-kpi:nth-child(5){animation:_dwIn .4s .20s ease both}"
        + ".dw-kpi:nth-child(6){animation:_dwIn .4s .24s ease both}"
        + ".dw-card,.dw-stc{animation:_dwIn .4s .28s ease both}";
    document.head.appendChild(s);
}


// ================================================================
//  SHARED HELPERS
// ================================================================
var _INR = function (n) { return "₹ " + Math.round(n || 0).toLocaleString("en-IN"); };

// Period tag shown on every card — e.g. "Today" in green
function _ptag(label) {
    if (!label || label === "All Time") return "";
    return '<span class="dw-period-tag">' + label + "</span>";
}

// Section header with period label
function _sechd(lbl, periodLabel) {
    var pt = (periodLabel && periodLabel !== "All Time")
        ? ' <span style="font-size:9px;font-weight:600;text-transform:uppercase;'
          + 'letter-spacing:.06em;color:#639922;background:#EAF3DE;border-radius:4px;'
          + 'padding:1px 6px;margin-left:4px">' + periodLabel + "</span>"
        : "";
    return '<div class="dw-sh"><span class="dw-sl">' + lbl + pt
         + '</span><div class="dw-sl-line"></div></div>';
}

// Period bar — prominent banner showing active filter
function _period_bar(periodLabel, fd, td) {
    if (!periodLabel || periodLabel === "All Time") return "";
    var months = ["Jan","Feb","Mar","Apr","May","Jun",
                  "Jul","Aug","Sep","Oct","Nov","Dec"];
    var rangeStr = "";
    if (fd && td) {
        var fp = fd.split("-"), tp = td.split("-");
        var fStr = months[parseInt(fp[1])-1] + " " + parseInt(fp[2]) + ", " + fp[0];
        var tStr = months[parseInt(tp[1])-1] + " " + parseInt(tp[2]) + ", " + tp[0];
        rangeStr = fd === td ? fStr : fStr + " – " + tStr;
    }
    return '<div class="dw-period-bar">'
        + '<div>'
        + '<div class="dw-period-bar-lbl">📅 Active filter: ' + periodLabel + "</div>"
        + (rangeStr ? '<div class="dw-period-bar-range">' + rangeStr + " · All sections filtered to this period</div>" : "")
        + "</div>"
        + '<span style="font-size:11px;color:#92400e;font-weight:500">Shipped + Processing only</span>'
        + "</div>";
}

function _kpi(cls, lbl, vid, val, trendHtml, note, sub, fid, fc, fw) {
    return '<div class="dw-kpi ' + cls + '">'
        + '<div class="dw-kl">' + lbl + "</div>"
        + '<div class="dw-kv" id="' + vid + '">' + val + "</div>"
        + trendHtml
        + (note ? '<div class="dw-knote">' + note + "</div>" : "")
        + '<div class="dw-ks">' + sub + "</div>"
        + '<div class="dw-bar"><div class="dw-barf" id="' + fid
        + '" style="background:' + fc + '" data-w="' + fw + '"></div></div>'
        + "</div>";
}

function _trend_html(t, inv) {
    if (!t || t.dir === "flat" || t.pct === 0)
        return '<div class="dw-ktr dw-t-fl">— stable</div>';
    var up  = t.dir === "up";
    var arr = up ? "↑" : "↓";
    var cls = inv ? (up ? "dw-t-inv-up" : "dw-t-inv-dn") : (up ? "dw-t-up" : "dw-t-dn");
    return '<div class="dw-ktr ' + cls + '">' + arr + " " + t.pct + "% vs prev period</div>";
}

function _badge_online(s) {
    var k = (s || "").toLowerCase();
    var c = k === "shipped" ? "db-sh" : k === "processing" ? "db-pr" : k === "pending" ? "db-pe"
          : k === "on-hold" ? "db-oh" : k === "refunded"   ? "db-rf" : "db-cn";
    return '<span class="dw-badge ' + c + '">' + (s || "—") + "</span>";
}

function _badge_offline(s) {
    var k = (s || "").toLowerCase();
    var c = k === "completed"           ? "db-cp"
          : k === "to deliver and bill" ? "db-td"
          : k === "to bill"             ? "db-tb"
          : k === "cancelled"           ? "db-cn" : "db-oh";
    return '<span class="dw-badge ' + c + '">' + (s || "—") + "</span>";
}

function _pipe_stage(bg, tc, lc, id, val, lbl) {
    return '<div class="dw-ps"><div class="dw-pb" style="background:' + bg + '">'
        + '<div class="dw-pn" style="color:' + tc + '" id="' + id + '">' + val + "</div>"
        + '<div class="dw-pl" style="color:' + lc + '">' + lbl + "</div>"
        + "</div></div>";
}

function _arrow(down) {
    return '<div class="dw-pa"><svg width="12" height="12" viewBox="0 0 12 12" fill="none">'
        + (down
           ? '<path d="M6 2v8M3 7l3 3 3-3" stroke="#E24B4A" stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round"/>'
           : '<path d="M2 6h8M7 3l3 3-3 3" stroke="#cbd5e1" stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round"/>')
        + "</svg></div>";
}

function _fbar_block(lbl, pct, color, fid, note) {
    return '<div class="dw-fbar">'
        + '<div class="dw-fbar-hd"><span class="dw-fbar-lbl">' + lbl + "</span>"
        + '<span class="dw-fbar-pct" style="color:' + color + '">' + pct + "%</span></div>"
        + (note ? '<div style="font-size:9px;color:#94a3b8;margin-bottom:3px">' + note + "</div>" : "")
        + '<div class="dw-fbar-track"><div class="dw-fbar-fill" id="' + fid
        + '" style="background:' + color + '" data-w="' + pct + '"></div></div>'
        + "</div>";
}

function _ranked_rows(items, colors, maxRev) {
    if (!items || !items.length) return '<div class="dw-empty">No data for this period</div>';
    var H = "";
    for (var i = 0; i < items.length; i++) {
        var itm = items[i];
        var pct = maxRev > 0 ? Math.round((itm.rev / maxRev) * 100) : (itm.pct || 0);
        H += '<div class="dw-srow">'
           + '<div class="dw-sbw"><div class="dw-sbf" style="background:'
           + colors[i % colors.length] + '" data-w="' + pct + '"></div></div>'
           + '<span class="dw-sn">' + (itm.name || "—") + "</span>"
           + '<span class="dw-sc">' + (itm.qty || itm.cnt || 0) + "</span>"
           + '<span class="dw-sv">' + _INR(itm.rev || 0) + "</span>"
           + "</div>";
    }
    return H;
}

function _feed_rows(feed, badgeFn, idColor) {
    if (!feed || !feed.length) return '<div class="dw-empty">No orders in this period</div>';
    var H = "";
    for (var i = 0; i < feed.length; i++) {
        var f = feed[i];
        H += '<div class="dw-frow">'
           + '<div class="dw-oid" style="color:' + idColor + '">' + (f.name || "—") + "</div>"
           + '<div class="dw-fcust">' + (f.customer || "—") + "</div>"
           + '<div class="dw-fdate">' + (f.transaction_date || "—") + "</div>"
           + '<div class="dw-famt">'  + _INR(f.grand_total || 0) + "</div>"
           + "<div>" + badgeFn(f.status) + "</div>"
           + "</div>";
    }
    return H;
}

function _snapshot_top(stlabel, stval, stsub, barColor, periodLabel) {
    return '<div class="dw-stc">'
        + '<div class="dw-stl">' + stlabel + _ptag(periodLabel) + "</div>"
        + '<div class="dw-stv">' + stval   + "</div>"
        + '<div class="dw-sts">' + stsub   + "</div>"
        + '<div class="dw-bar"><div class="dw-barf" style="background:' + barColor + '" data-w="100"></div></div>'
        + "</div>";
}

function _customer_stats_html(sn, rateColor, periodLabel) {
    var H = '<div class="dw-csg">'
        + '<div class="dw-cst"><div class="dw-csv" style="color:#0C447C" id="dw-cs1">' + (sn.unique_customers || 0) + '</div><div class="dw-csl">Unique' + _ptag(periodLabel) + "</div></div>"
        + '<div class="dw-cst"><div class="dw-csv" style="color:#0C447C" id="dw-cs2">' + (sn.repeat_customers || 0) + '</div><div class="dw-csl">Returning' + _ptag(periodLabel) + "</div></div>"
        + '<div class="dw-cst"><div class="dw-csv" style="color:#3C3489" id="dw-cs3">' + (sn.repeat_rate || 0) + '%</div><div class="dw-csl">Repeat rate</div></div>'
        + "</div>"
        + '<div style="border-top:0.5px solid #e2e8f0;padding-top:14px">'
        + '<div style="font-size:11px;font-weight:500;color:#64748b;margin-bottom:6px">'
        + (sn.repeat_rate || 0) + "% of customers placed more than one order"
        + "</div>"
        + '<div class="dw-fbar-track"><div class="dw-fbar-fill" id="dw-rr-bar" style="background:#7F77DD" data-w="' + (sn.repeat_rate || 0) + '"></div></div>'
        + '<div style="display:flex;justify-content:space-between;margin-top:3px">'
        + '<span style="font-size:10px;color:#94a3b8">0%</span>'
        + '<span style="font-size:13px;font-weight:500;color:#3C3489">' + (sn.repeat_rate || 0) + "%</span>"
        + '<span style="font-size:10px;color:#94a3b8">100%</span>'
        + "</div>"
        + '<div style="margin-top:14px;padding-top:14px;border-top:0.5px solid #e2e8f0;display:flex;justify-content:space-between;align-items:center">'
        + '<span style="font-size:11px;color:#64748b">Avg orders per customer</span>'
        + '<span style="font-size:22px;font-weight:500;color:' + rateColor + '">' + (sn.avg_per_customer || 0) + "</span>"
        + "</div></div>";
    return H;
}

// Period sub for chart cards: "Shipped + Processing · Today"
function _pcs(base, periodLabel) {
    if (!periodLabel || periodLabel === "All Time") return base;
    return base + " · " + periodLabel;
}

// Top bar
function _dash_topbar(mode, kp, fd, td, periodLabel) {
    var isOnline = mode === "online";
    var chips = isOnline
        ? [["#EAF3DE","#27500A","#639922", _INR(kp.total_revenue||0)+" revenue"],
           ["#E6F1FB","#0C447C","#378ADD", (kp.ship_proc_count||0)+" shipped/processing"],
           ["#FAEEDA","#633806","#BA7517", (kp.pending||0)+" pending"]]
        : [["#EAF3DE","#27500A","#639922", _INR(kp.total_revenue||0)+" revenue"],
           ["#FAEEDA","#633806","#BA7517", (kp.total_orders||0)+" orders"],
           ["#E6F1FB","#0C447C","#378ADD", (kp.pending||0)+" pending"]];

    var chipHtml = "";
    for (var i = 0; i < chips.length; i++) {
        chipHtml += '<span class="dw-chip" style="background:' + chips[i][0]
            + ';color:' + chips[i][1] + ';border-color:' + chips[i][2] + '">'
            + chips[i][3] + "</span>";
    }

    return '<div class="dw-topbar">'
        + '<div>'
        + '<div class="dw-ttl">' + (isOnline ? "WooCommerce Orders Dashboard" : "In-Clinic Orders Dashboard") + "</div>"
        + '<div class="dw-sub">' + (isOnline
            ? "TAALPLUS CHC Private Limited · taalhealthcare.com · Online orders only"
            : "TAALPLUS CHC Private Limited · In-clinic · Offline orders only") + "</div>"
        + "</div>"
        + '<div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap">'
        + '<div class="dw-toggle">'
        + '<button class="dw-tb" onclick="window._dash_switch(\'online\')"'
        + ' style="background:' + (isOnline ? "#378ADD" : "transparent")
        + ';color:'             + (isOnline ? "#fff"    : "#64748b") + '">Online</button>'
        + '<button class="dw-tb" onclick="window._dash_switch(\'offline\')"'
        + ' style="background:' + (!isOnline ? "#639922"     : "transparent")
        + ';color:'             + (!isOnline ? "#fff"         : "#64748b") + '">Offline</button>'
        + "</div>"
        + '<div class="dw-chips">' + chipHtml + "</div>"
        + "</div></div>";
}


// ================================================================
//  ONLINE HTML
// ================================================================
function _online_html(d, fd, td, periodLabel) {
    var kp = d.kpis     || {};
    var sn = d.snapshot || {};
    var ch = d.charts   || {};
    var t5 = d.top5     || {};
    var feed = d.feed   || [];
    var pl = d.pipeline || {};
    var pl_active = (periodLabel && periodLabel !== "All Time");
    var iColors = ["#D85A30","#378ADD","#7F77DD","#1D9E75","#639922","#BA7517"];
    var cColors = ["#378ADD","#1D9E75","#D85A30","#7F77DD","#BA7517"];
    var H = '<div class="dw">';

    H += _dash_topbar("online", kp, fd, td, periodLabel);

    // Prominent period bar
    H += _period_bar(periodLabel, fd, td);

    H += '<div class="dw-notice dw-notice-b">All metrics: custom_offline__online = Online · '
       + 'All sections: woocommerce_status IN (Shipped, Processing) · Pending: status = Pending only'
       + (pl_active ? " · Period: <strong>" + periodLabel + "</strong>" : "")
       + "</div>";

    // S1 — KPIs
    H += _sechd("Section 1 — Revenue & order KPIs", periodLabel);
    H += '<div class="dw-g3">';

    // Card 1: Total Revenue — labelled with period
    var revLabel = pl_active ? periodLabel + " revenue (Online)" : "Total revenue (Online)";
    H += _kpi("kg", revLabel, "dw-k1", _INR(kp.total_revenue||0),
        _trend_html(kp.month_rev_trend, false),
        "Shipped + Processing only",
        _pcs("Online · woocommerce_status IN (Shipped, Processing)", periodLabel),
        "dw-kf1","#639922",100);

    // Card 2: Period revenue (adaptive label)
    var card2Label = pl_active ? periodLabel + " revenue" : "This month's revenue";
    H += _kpi("kb", card2Label, "dw-k2", _INR(kp.month_revenue||0),
        _trend_html(kp.month_rev_trend, false),
        "Shipped + Processing only",
        _pcs("Shipped or Processing · " + (pl_active ? periodLabel : "current month"), periodLabel),
        "dw-kf2","#378ADD",
        kp.total_revenue>0?Math.round(kp.month_revenue/kp.total_revenue*100):0);

    // Card 3: AOV
    H += _kpi("kt","Average order value","dw-k3",_INR(kp.avg_order_value||0),
        _trend_html({dir:"flat",pct:0},false),
        "Shipped + Processing only",
        _pcs("base_net_total ÷ Shipped+Processing order count", periodLabel),
        "dw-kf3","#1D9E75",45);

    // Card 4: Pending
    H += _kpi("ka","Pending delivery","dw-k4",String(kp.pending||0),
        _trend_html({dir:"flat",pct:0},true),
        "woocommerce_status = Pending only",
        _pcs("Online · woocommerce_status = Pending", periodLabel),
        "dw-kf4","#BA7517",
        kp.total_orders>0?Math.round((kp.pending||0)/kp.total_orders*100):0);

    // Card 5: Orders today
    var todayLabel = pl_active ? "Orders in period" : "Orders today";
    H += _kpi("kp", todayLabel, "dw-k5",
        String(pl_active ? (kp.ship_proc_count||0) : (kp.today_orders||0)),
        _trend_html(pl_active?{dir:"flat",pct:0}:kp.today_trend, false),
        pl_active ? "Shipped + Processing · " + periodLabel : "Online orders only",
        pl_active
            ? "All Shipped+Processing orders in selected period"
            : "transaction_date = " + frappe.datetime.get_today() + " · Online",
        "dw-kf5","#7F77DD",2);

    // Card 6: Cancelled
    H += _kpi("kc","Cancelled orders","dw-k6",String(kp.cancelled||0),
        _trend_html({dir:"flat",pct:0},true),
        "Online orders only",
        _pcs("woocommerce_status = Cancelled · Online", periodLabel),
        "dw-kf6","#D85A30",
        kp.total_orders>0?Math.round((kp.cancelled||0)/kp.total_orders*100):0);

    H += "</div>";

    // S2 — Snapshot
    H += _sechd("Section 2 — Snapshot summary", periodLabel);
    H += '<div class="dw-g3">';
    H += _snapshot_top("Top customer",
        (sn.top_customer||"—"),
        (sn.top_cust_cnt||0)+" orders · "+_INR(sn.top_cust_rev||0),
        "#378ADD", periodLabel);
    H += _snapshot_top("Top item by revenue",
        (sn.top_item||"—"),
        (sn.top_item_qty||0)+" units · "+_INR(sn.top_item_rev||0),
        "#1D9E75", periodLabel);

    var fs  = kp.fulfil_shipped||0, fsp = kp.fulfil_ship_proc||0;
    H += '<div class="dw-stc"><div class="dw-stl">Fulfillment rates ' + _ptag(periodLabel) + "</div>"
       + '<div style="margin-bottom:10px">'
       + '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px">'
       + '<span style="font-size:10px;color:#64748b">Shipped only</span>'
       + '<span style="font-size:12px;font-weight:500;color:#085041">' + fs + "%</span></div>"
       + '<div class="dw-bar"><div class="dw-barf" style="background:#1D9E75" data-w="' + fs + '"></div></div></div>'
       + '<div>'
       + '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px">'
       + '<span style="font-size:10px;color:#64748b">Shipped + Processing</span>'
       + '<span style="font-size:12px;font-weight:500;color:#0C447C">' + fsp + "%</span></div>"
       + '<div class="dw-bar"><div class="dw-barf" style="background:#378ADD" data-w="' + fsp + '"></div></div></div>'
       + ((fs===fsp&&fsp>0)?'<div style="font-size:9px;color:#94a3b8;margin-top:6px;font-style:italic">Equal — no Processing orders in this period</div>':"")
       + "</div>";
    H += "</div>";

    // S3 — Doughnuts
    H += _sechd("Section 3 — Status & payment distribution", periodLabel);
    H += '<div class="dw-g2">';
    var ws=ch.woo_status||{},wsl=ws.labels||[],wsv=(ws.datasets&&ws.datasets[0]&&ws.datasets[0].values)||[];
    var stC={"Shipped":"#27ae60","shipped":"#27ae60","Processing":"#f39c12","processing":"#f39c12",
             "Pending":"#BA7517","pending":"#BA7517","Cancelled":"#e74c3c","cancelled":"#e74c3c",
             "on-hold":"#888780","Refunded":"#8e44ad","refunded":"#8e44ad"};
    var stLeg="";for(var i=0;i<wsl.length;i++){stLeg+='<div class="dw-leg-row"><span class="dw-leg-dot" style="background:'+(stC[wsl[i]]||"#888")+'"></span><span class="dw-leg-lbl">'+wsl[i]+'</span><span class="dw-leg-val">'+(wsv[i]||0)+'</span></div>';}
    H += '<div class="dw-card"><div class="dw-ct">WooCommerce order status ' + _ptag(periodLabel) + "</div>"
       + '<div class="dw-cs">' + _pcs("Online orders · woocommerce_status", periodLabel) + "</div>"
       + '<div style="display:flex;gap:16px;align-items:center"><canvas id="dw-ch1" role="img" aria-label="Status doughnut" style="flex-shrink:0;width:140px;height:140px">Status</canvas>'
       + '<div style="flex:1">'+stLeg+'</div></div></div>';
    var pm=ch.payment||{},pml=pm.labels||[],pmv=(pm.datasets&&pm.datasets[0]&&pm.datasets[0].values)||[];
    var pmC=["#378ADD","#7F77DD","#D85A30","#888780","#639922","#D4537E"];
    var pmLeg="";for(var j=0;j<pml.length;j++){pmLeg+='<div class="dw-leg-row"><span class="dw-leg-dot" style="background:'+pmC[j%pmC.length]+'"></span><span class="dw-leg-lbl">'+pml[j]+'</span><span class="dw-leg-val">'+(pmv[j]||0)+'</span></div>';}
    H += '<div class="dw-card"><div class="dw-ct">Payment method ' + _ptag(periodLabel) + "</div>"
       + '<div class="dw-cs">' + _pcs("Online · Shipped + Processing · woocommerce_payment_method", periodLabel) + "</div>"
       + '<div style="display:flex;gap:16px;align-items:center"><canvas id="dw-ch2" role="img" aria-label="Payment doughnut" style="flex-shrink:0;width:140px;height:140px">Payment</canvas>'
       + '<div style="flex:1">'+pmLeg+'</div></div></div>';
    H += "</div>";

    // S4 — Order value + Top states
    H += _sechd("Section 4 — Order value distribution & top states", periodLabel);
    H += '<div class="dw-g2">';
    var ob=ch.order_buckets||{},obl=ob.labels||[],obv=(ob.datasets&&ob.datasets[0]&&ob.datasets[0].values)||[];
    var obMax=Math.max.apply(null,obv)||1;var obC=["#D4537E","#378ADD","#639922","#7F77DD","#D85A30"];
    var bktH="";for(var bi=0;bi<obl.length;bi++){var bp=Math.round((obv[bi]/obMax)*100);bktH+='<div style="margin-bottom:10px"><div style="display:flex;justify-content:space-between;margin-bottom:3px"><span style="font-size:11px;color:#64748b">'+obl[bi]+'</span><span style="font-size:11px;font-weight:500;color:#111">'+(obv[bi]||0)+' orders</span></div><div style="height:8px;border-radius:4px;background:#e2e8f0"><div style="height:100%;border-radius:4px;background:'+obC[bi%obC.length]+';width:0;transition:width 1.1s ease" data-w="'+bp+'"></div></div></div>';}
    var mbIdx=obv.indexOf(Math.max.apply(null,obv));
    H += '<div class="dw-card"><div class="dw-ct">Order value distribution ' + _ptag(periodLabel) + "</div>"
       + '<div class="dw-cs">' + _pcs("Online · Shipped + Processing · grand_total buckets", periodLabel) + "</div>"
       + bktH
       + '<div style="margin-top:10px;padding-top:10px;border-top:0.5px solid #e2e8f0;display:flex;justify-content:space-between"><span style="font-size:10px;color:#94a3b8">Most orders in</span><span style="font-size:12px;font-weight:500;color:#639922">'+(obl[mbIdx]||"")+'</span></div></div>';
    var ts=ch.top_states||{},tsl=ts.labels||[],tsv=(ts.datasets&&ts.datasets[0]&&ts.datasets[0].values)||[];
    var tsMax=Math.max.apply(null,tsv)||1;var tsC=["#378ADD","#1D9E75","#7F77DD","#D85A30","#639922","#BA7517","#D4537E","#888780"];
    var stateH="";for(var si=0;si<tsl.length;si++){var sp=Math.round((tsv[si]/tsMax)*100);stateH+='<div class="dw-hbar-row"><span class="dw-hbar-lbl">'+tsl[si]+'</span><div class="dw-hbar-track"><div class="dw-hbar-fill" style="background:'+tsC[si%tsC.length]+'" data-w="'+sp+'">'+(sp>25?'<span style="font-size:9px;font-weight:500;color:#fff">'+(tsv[si]||0)+'</span>':'')+'</div></div>'+(sp<=25?'<span class="dw-hbar-outer">'+(tsv[si]||0)+'</span>':'')+'</div>';}
    if(!stateH)stateH='<div class="dw-empty">No state data for this period</div>';
    H += '<div class="dw-card"><div class="dw-ct">Top states by order count ' + _ptag(periodLabel) + "</div>"
       + '<div class="dw-cs">' + _pcs("Online · Shipped + Processing · shipping address", periodLabel) + "</div>"
       + stateH+'</div>';
    H += "</div>";

    // S5 — Trends
    H += _sechd("Section 5 — Revenue & order volume trends", periodLabel);
    H += '<div class="dw-g2">';
    H += '<div class="dw-card"><div class="dw-ct">Revenue trend ' + _ptag(periodLabel) + "</div>"
       + '<div class="dw-cs">' + _pcs("Online · Shipped + Processing · grouped by period", periodLabel) + "</div>"
       + '<div style="position:relative;height:200px"><canvas id="dw-ch3" role="img" aria-label="Revenue trend">Revenue</canvas></div></div>';
    H += '<div class="dw-card"><div class="dw-ct">Order volume ' + _ptag(periodLabel) + "</div>"
       + '<div class="dw-cs">' + _pcs("Online · Shipped + Processing · grouped by period", periodLabel) + "</div>"
       + '<div style="position:relative;height:200px"><canvas id="dw-ch4" role="img" aria-label="Order volume">Orders</canvas></div></div>';
    H += "</div>";

    // S6 — Top items + customers
    H += _sechd("Section 6 — Top items & top customers", periodLabel);
    H += '<div class="dw-g2">';
    var items=t5.items||[],im=items.length>0?(items[0].rev||1):1;
    H += '<div class="dw-card"><div class="dw-ct">Top items by revenue ' + _ptag(periodLabel) + "</div>"
       + '<div class="dw-cs">' + _pcs("Online · Shipped + Processing", periodLabel) + "</div>"
       + '<div style="display:flex;gap:8px;padding:5px 0;border-bottom:0.5px solid #e2e8f0;margin-bottom:2px"><div style="flex:1;min-width:32px"></div><span style="font-size:10px;font-weight:500;color:#94a3b8;flex:1;min-width:0">Item</span><span style="font-size:10px;font-weight:500;color:#94a3b8;min-width:22px;text-align:right">Qty</span><span style="font-size:10px;font-weight:500;color:#94a3b8;min-width:66px;text-align:right">Revenue</span></div>'
       + _ranked_rows(items, iColors, im) + "</div>";
    var custs=t5.customers||[],cm=custs.length>0?(custs[0].rev||1):1;
    H += '<div class="dw-card"><div class="dw-ct">Top customers by revenue ' + _ptag(periodLabel) + "</div>"
       + '<div class="dw-cs">' + _pcs("Online · Shipped + Processing", periodLabel) + "</div>"
       + '<div style="display:flex;gap:8px;padding:5px 0;border-bottom:0.5px solid #e2e8f0;margin-bottom:2px"><div style="flex:1;min-width:32px"></div><span style="font-size:10px;font-weight:500;color:#94a3b8;flex:1;min-width:0">Customer</span><span style="font-size:10px;font-weight:500;color:#94a3b8;min-width:22px;text-align:right">Ord</span><span style="font-size:10px;font-weight:500;color:#94a3b8;min-width:66px;text-align:right">Revenue</span></div>'
       + _ranked_rows(custs, cColors, cm) + "</div>";
    H += "</div>";

    // S7 — Recent orders
    H += _sechd("Section 7 — Recent orders", periodLabel);
    H += '<div class="dw-card" style="margin-bottom:14px">'
       + '<div class="dw-ct">Recent orders ' + _ptag(periodLabel) + "</div>"
       + '<div class="dw-cs">' + _pcs("Last 8 Online Sales Orders · newest first", periodLabel) + "</div>"
       + '<div class="dw-feed-hd"><span>Order ID</span><span>Customer</span><span>Date</span><span>Amount</span><span>Status</span></div>'
       + _feed_rows(feed, _badge_online, "#0C447C") + "</div>";

    // S8 — Pipeline + customer stats
    H += _sechd("Section 8 — Order pipeline & customer statistics", periodLabel);
    H += '<div class="dw-g2">';
    H += '<div class="dw-card"><div class="dw-ct">WooCommerce order pipeline ' + _ptag(periodLabel) + "</div>"
       + '<div class="dw-cs">' + _pcs("Online orders · woocommerce_status stages", periodLabel) + "</div>"
       + '<div class="dw-pipe">'
       + _pipe_stage("#FAEEDA","#633806","#854F0B","dw-pp1",pl.pending||0,"Pending") + _arrow(false)
       + _pipe_stage("#F1EFE8","#444441","#5F5E5A","dw-pp2",pl.on_hold||0,"On Hold") + _arrow(false)
       + _pipe_stage("#E6F1FB","#0C447C","#185FA5","dw-pp3",pl.processing||0,"Processing") + _arrow(false)
       + _pipe_stage("#E1F5EE","#085041","#0F6E56","dw-pp4",pl.shipped||0,"Shipped") + _arrow(true)
       + _pipe_stage("#EEEDFE","#3C3489","#534AB7","dw-pp5",pl.refunded||0,"Refunded") + _arrow(true)
       + _pipe_stage("#FCEBEB","#791F1F","#A32D2D","dw-pp6",pl.cancelled||0,"Cancelled")
       + "</div>"
       + _fbar_block("Fulfillment rate — Shipped ÷ Total" + (pl_active?" · "+periodLabel:""), pl.fulfil_shipped||0, "#1D9E75","dw-fb1",null)
       + _fbar_block("Active fulfillment — Shipped + Processing ÷ Total" + (pl_active?" · "+periodLabel:""), pl.fulfil_ship_proc||0, "#378ADD","dw-fb2",
           (pl.fulfil_shipped===pl.fulfil_ship_proc&&pl.fulfil_shipped>0)
               ? "Equal — no Processing orders in this period" : null)
       + "</div>";
    H += '<div class="dw-card"><div class="dw-ct">Customer statistics ' + _ptag(periodLabel) + "</div>"
       + '<div class="dw-cs">' + _pcs("Online · Shipped + Processing", periodLabel) + "</div>"
       + _customer_stats_html(sn, "#0C447C", periodLabel) + "</div>";
    H += "</div>";
    H += "</div>";
    return H;
}


// ================================================================
//  OFFLINE HTML
// ================================================================
function _offline_html(d, fd, td, periodLabel) {
    var kp = d.kpis     || {};
    var sn = d.snapshot || {};
    var ch = d.charts   || {};
    var t5 = d.top5     || {};
    var feed = d.feed   || [];
    var pl = d.pipeline || {};
    var pl_active = (periodLabel && periodLabel !== "All Time");
    var iColors = ["#D85A30","#378ADD","#7F77DD","#1D9E75","#639922","#BA7517"];
    var cColors = ["#639922","#378ADD","#D85A30","#7F77DD","#BA7517"];
    var H = '<div class="dw">';

    H += _dash_topbar("offline", kp, fd, td, periodLabel);
    H += _period_bar(periodLabel, fd, td);
    H += '<div class="dw-notice dw-notice-g">All metrics: custom_offline__online = Offline · ERPNext native status · Tax from tabSales Taxes and Charges'
       + (pl_active ? " · Period: <strong>" + periodLabel + "</strong>" : "") + "</div>";

    H += _sechd("Section 1 — Revenue & order KPIs", periodLabel);
    H += '<div class="dw-g3">';
    var revLbl2 = pl_active ? periodLabel + " revenue (Offline)" : "Total revenue (Offline)";
    H += _kpi("kg",revLbl2,"dw-k1",_INR(kp.total_revenue||0),_trend_html(kp.month_rev_trend,false),null,_pcs("SUM(base_net_total) · Offline Sales Orders",periodLabel),"dw-kf1","#639922",100);
    var c2lbl2 = pl_active ? periodLabel + " revenue" : "This month's revenue";
    H += _kpi("kb",c2lbl2,"dw-k2",_INR(kp.month_revenue||0),_trend_html(kp.month_rev_trend,false),null,_pcs("Offline orders · "+( pl_active?periodLabel:"current month"),periodLabel),"dw-kf2","#378ADD",kp.total_revenue>0?Math.round(kp.month_revenue/kp.total_revenue*100):0);
    H += _kpi("kt","Average order value","dw-k3",_INR(kp.avg_order_value||0),_trend_html({dir:"flat",pct:0},false),null,_pcs("base_net_total ÷ total Offline orders",periodLabel),"dw-kf3","#1D9E75",55);
    H += _kpi("ka","Pending delivery","dw-k4",String(kp.pending||0),_trend_html({dir:"flat",pct:0},true),null,_pcs("status = To Deliver and Bill · Offline",periodLabel),"dw-kf4","#BA7517",kp.total_orders>0?Math.round((kp.pending||0)/kp.total_orders*100):0);
    var kp5lbl2 = pl_active ? "Orders in period" : "Orders today";
    H += _kpi("kp",kp5lbl2,"dw-k5",String(pl_active?(kp.total_orders||0):(kp.today_orders||0)),_trend_html(pl_active?{dir:"flat",pct:0}:kp.today_trend,false),null,_pcs(pl_active?"All Offline orders in period":"transaction_date = "+frappe.datetime.get_today(),periodLabel),"dw-kf5","#7F77DD",5);
    H += _kpi("kc","Cancelled orders","dw-k6",String(kp.cancelled||0),_trend_html({dir:"flat",pct:0},true),null,_pcs("status = Cancelled · Offline",periodLabel),"dw-kf6","#D85A30",kp.total_orders>0?Math.round((kp.cancelled||0)/kp.total_orders*100):0);
    H += "</div>";

    H += _sechd("Section 2 — Snapshot summary", periodLabel);
    H += '<div class="dw-g3">';
    H += _snapshot_top("Top customer",(sn.top_customer||"—"),(sn.top_cust_cnt||0)+" Offline orders · "+_INR(sn.top_cust_rev||0),"#639922",periodLabel);
    H += _snapshot_top("Top item by revenue",(sn.top_item||"—"),(sn.top_item_qty||0)+" units · "+_INR(sn.top_item_rev||0),"#D85A30",periodLabel);
    var sg=sn.tax_sgst||0,cg=sn.tax_cgst||0,ig=sn.tax_igst||0,tds=sn.tax_tds||0,gross=sn.tax_gross||0,net=sn.tax_net||0;
    var hasAnyTax=(sg+cg+ig)>0;
    var taxBody="";
    taxBody+='<div class="dw-tax-row"><span class="dw-tax-lbl"><span class="dw-tax-dot" style="background:#1D9E75"></span>SGST</span><span class="dw-tax-val" style="color:#085041">'+_INR(sg)+"</span></div>";
    taxBody+='<div class="dw-tax-row"><span class="dw-tax-lbl"><span class="dw-tax-dot" style="background:#378ADD"></span>CGST</span><span class="dw-tax-val" style="color:#0C447C">'+_INR(cg)+"</span></div>";
    taxBody+='<div class="dw-tax-row"><span class="dw-tax-lbl"><span class="dw-tax-dot" style="background:#7F77DD"></span>IGST</span><span class="dw-tax-val" style="color:#3C3489">'+_INR(ig)+"</span></div>";
    taxBody+='<div class="dw-tds-row"><span style="display:flex;align-items:center;gap:7px;font-size:11px;color:#791F1F"><span style="width:9px;height:9px;border-radius:2px;background:#E24B4A;flex-shrink:0"></span>TDS deducted</span><span style="font-size:12px;font-weight:500;color:#791F1F">- '+_INR(tds)+"</span></div>";
    if(!hasAnyTax)taxBody+='<div style="font-size:9px;color:#BA7517;background:#FAEEDA;border-radius:4px;padding:2px 7px;margin:4px 0;display:inline-block">GST exempt · HSN 30049099</div>';
    taxBody+='<div style="display:flex;justify-content:space-between;align-items:center;padding:5px 0;border-top:0.5px solid #e2e8f0;margin-top:4px"><span style="font-size:10px;color:#64748b">Gross tax collected</span><span style="font-size:11px;font-weight:500;color:#111">'+_INR(gross)+"</span></div>";
    taxBody+='<div class="dw-net-row"><span style="font-size:11px;font-weight:500;color:#27500A">Net tax retained</span><span style="font-size:14px;font-weight:500;color:#27500A">'+_INR(net)+"</span></div>";
    H += '<div class="dw-stc"><div class="dw-stl">Tax collected ' + _ptag(periodLabel) + "</div>" + taxBody + "</div>";
    H += "</div>";

    H += _sechd("Section 3 — Order status & tax breakdown", periodLabel);
    H += '<div class="dw-g2">';
    var st=ch.status||{},stl=st.labels||[],stv=(st.datasets&&st.datasets[0]&&st.datasets[0].values)||[];
    var offStC={"Completed":"#27ae60","To Deliver and Bill":"#f39c12","To Bill":"#2490ef","Cancelled":"#e74c3c","Draft":"#888780"};
    var stLeg2="";for(var k=0;k<stl.length;k++){stLeg2+='<div class="dw-leg-row"><span class="dw-leg-dot" style="background:'+(offStC[stl[k]]||"#888")+'"></span><span class="dw-leg-lbl">'+stl[k]+'</span><span class="dw-leg-val">'+(stv[k]||0)+'</span></div>';}
    H += '<div class="dw-card"><div class="dw-ct">Order status distribution ' + _ptag(periodLabel) + "</div>"
       + '<div class="dw-cs">' + _pcs("Offline orders · ERPNext status field", periodLabel) + "</div>"
       + '<div style="display:flex;gap:16px;align-items:center"><canvas id="dw-ch1" role="img" aria-label="Status doughnut" style="flex-shrink:0;width:140px;height:140px">Status</canvas><div style="flex:1">'+stLeg2+'</div></div></div>';
    var taxMonths=ch.tax_monthly||[];var taxMax=0;
    for(var tm=0;tm<taxMonths.length;tm++){var tg=(taxMonths[tm].sgst||0)+(taxMonths[tm].cgst||0)+(taxMonths[tm].igst||0);if(tg>taxMax)taxMax=tg;}
    var taxRows="";
    for(var ti2=0;ti2<taxMonths.length;ti2++){
        var m=taxMonths[ti2];var mg=(m.sgst||0)+(m.cgst||0)+(m.igst||0);
        var sw2=taxMax>0?Math.round((m.sgst||0)/taxMax*100):0;
        var cw2=taxMax>0?Math.round((m.cgst||0)/taxMax*100):0;
        var iw2=taxMax>0?Math.round((m.igst||0)/taxMax*100):0;
        var dw2=taxMax>0?Math.round((m.tds||0)/taxMax*40):0;
        taxRows+='<div class="dw-tax-mo-row"><span class="dw-tax-mo-lbl">'+m.mo+'</span>'
            +'<div class="dw-tax-seg-wrap">'
            +'<div style="height:100%;background:#1D9E75;width:0;transition:width 1s ease" data-w="'+sw2+'"></div>'
            +'<div style="height:100%;background:#378ADD;width:0;transition:width 1s ease" data-w="'+cw2+'"></div>'
            +'<div style="height:100%;background:#7F77DD;width:0;transition:width 1s ease" data-w="'+iw2+'"></div>'
            +'</div><div class="dw-tds-pill" style="width:'+Math.max(dw2*2,24)+'px"><span class="dw-tds-inner">-'+Math.round((m.tds||0)/1000*10)/10+'K</span></div>'
            +'<span class="dw-tax-mo-val">'+_INR(mg)+'</span></div>';
    }
    if(!taxRows)taxRows='<div class="dw-empty">No tax data · Orders may be GST exempt</div>';
    var totSg=0,totCg=0,totIg=0,totTds=0;
    for(var tx=0;tx<taxMonths.length;tx++){totSg+=taxMonths[tx].sgst||0;totCg+=taxMonths[tx].cgst||0;totIg+=taxMonths[tx].igst||0;totTds+=taxMonths[tx].tds||0;}
    H += '<div class="dw-card"><div class="dw-ct">Tax breakdown ' + _ptag(periodLabel) + "</div>"
       + '<div class="dw-cs">' + _pcs("SGST + CGST + IGST stacked · TDS as deduction", periodLabel) + "</div>"
       + taxRows
       + '<div style="display:flex;gap:14px;flex-wrap:wrap;margin-top:10px">'
       + '<span style="display:flex;align-items:center;gap:4px;font-size:10px;color:#64748b"><span style="width:10px;height:10px;border-radius:2px;background:#1D9E75"></span>SGST</span>'
       + '<span style="display:flex;align-items:center;gap:4px;font-size:10px;color:#64748b"><span style="width:10px;height:10px;border-radius:2px;background:#378ADD"></span>CGST</span>'
       + '<span style="display:flex;align-items:center;gap:4px;font-size:10px;color:#64748b"><span style="width:10px;height:10px;border-radius:2px;background:#7F77DD"></span>IGST</span>'
       + '<span style="display:flex;align-items:center;gap:4px;font-size:10px;color:#64748b"><span style="width:10px;height:10px;border-radius:2px;background:#E24B4A"></span>TDS deducted</span></div>'
       + '<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-top:12px;padding-top:12px;border-top:0.5px solid #e2e8f0">'
       + '<div style="text-align:center"><div style="font-size:12px;font-weight:500;color:#085041">'+_INR(totSg)+'</div><div style="font-size:9px;color:#94a3b8;margin-top:2px">Total SGST</div></div>'
       + '<div style="text-align:center"><div style="font-size:12px;font-weight:500;color:#0C447C">'+_INR(totCg)+'</div><div style="font-size:9px;color:#94a3b8;margin-top:2px">Total CGST</div></div>'
       + '<div style="text-align:center"><div style="font-size:12px;font-weight:500;color:#3C3489">'+_INR(totIg)+'</div><div style="font-size:9px;color:#94a3b8;margin-top:2px">Total IGST</div></div>'
       + '<div style="text-align:center"><div style="font-size:12px;font-weight:500;color:#791F1F">- '+_INR(totTds)+'</div><div style="font-size:9px;color:#94a3b8;margin-top:2px">Total TDS</div></div>'
       + "</div></div>";
    H += "</div>";

    H += _sechd("Section 4 — Order value distribution & top items", periodLabel);
    H += '<div class="dw-g2">';
    var ob2=ch.order_buckets||{},obl2=ob2.labels||[],obv2=(ob2.datasets&&ob2.datasets[0]&&ob2.datasets[0].values)||[];
    var obMax2=Math.max.apply(null,obv2)||1;var obC2=["#D4537E","#378ADD","#639922","#7F77DD","#D85A30"];
    var bktH2="";for(var bi2=0;bi2<obl2.length;bi2++){var bp2=Math.round((obv2[bi2]/obMax2)*100);bktH2+='<div style="margin-bottom:10px"><div style="display:flex;justify-content:space-between;margin-bottom:3px"><span style="font-size:11px;color:#64748b">'+obl2[bi2]+'</span><span style="font-size:11px;font-weight:500;color:#111">'+(obv2[bi2]||0)+' orders</span></div><div style="height:8px;border-radius:4px;background:#e2e8f0"><div style="height:100%;border-radius:4px;background:'+obC2[bi2%obC2.length]+';width:0;transition:width 1.1s ease" data-w="'+bp2+'"></div></div></div>';}
    var mbIdx2=obv2.indexOf(Math.max.apply(null,obv2));
    H += '<div class="dw-card"><div class="dw-ct">Order value distribution ' + _ptag(periodLabel) + "</div>"
       + '<div class="dw-cs">' + _pcs("Offline orders grouped by grand_total", periodLabel) + "</div>"
       + bktH2+'<div style="margin-top:10px;padding-top:10px;border-top:0.5px solid #e2e8f0;display:flex;justify-content:space-between"><span style="font-size:10px;color:#94a3b8">Most orders in</span><span style="font-size:12px;font-weight:500;color:#639922">'+(obl2[mbIdx2]||"")+'</span></div></div>';
    var items2=t5.items||[],im2=items2.length>0?(items2[0].rev||1):1;
    H += '<div class="dw-card"><div class="dw-ct">Top items by revenue ' + _ptag(periodLabel) + "</div>"
       + '<div class="dw-cs">' + _pcs("Offline Sales Order Items · top 6", periodLabel) + "</div>"
       + '<div style="display:flex;gap:8px;padding:5px 0;border-bottom:0.5px solid #e2e8f0;margin-bottom:2px"><div style="flex:1;min-width:32px"></div><span style="font-size:10px;font-weight:500;color:#94a3b8;flex:1;min-width:0">Item</span><span style="font-size:10px;font-weight:500;color:#94a3b8;min-width:22px;text-align:right">Qty</span><span style="font-size:10px;font-weight:500;color:#94a3b8;min-width:66px;text-align:right">Revenue</span></div>'
       + _ranked_rows(items2, iColors, im2) + "</div>";
    H += "</div>";

    H += _sechd("Section 5 — Revenue & order volume trends", periodLabel);
    H += '<div class="dw-g2">';
    H += '<div class="dw-card"><div class="dw-ct">Revenue trend ' + _ptag(periodLabel) + "</div>"
       + '<div class="dw-cs">' + _pcs("Offline orders · grouped by period", periodLabel) + "</div>"
       + '<div style="position:relative;height:200px"><canvas id="dw-ch3" role="img" aria-label="Revenue">Revenue</canvas></div></div>';
    H += '<div class="dw-card"><div class="dw-ct">Order volume ' + _ptag(periodLabel) + "</div>"
       + '<div class="dw-cs">' + _pcs("Offline orders · grouped by period", periodLabel) + "</div>"
       + '<div style="position:relative;height:200px"><canvas id="dw-ch4" role="img" aria-label="Orders">Orders</canvas></div></div>';
    H += "</div>";

    H += _sechd("Section 6 — Top customers", periodLabel);
    var custs2=t5.customers||[],cm2=custs2.length>0?(custs2[0].rev||1):1;
    H += '<div class="dw-card" style="margin-bottom:14px"><div class="dw-ct">Top customers by revenue ' + _ptag(periodLabel) + "</div>"
       + '<div class="dw-cs">' + _pcs("Offline orders only · top 5", periodLabel) + "</div>"
       + '<div style="display:flex;gap:8px;padding:5px 0;border-bottom:0.5px solid #e2e8f0;margin-bottom:2px"><div style="flex:1;min-width:32px"></div><span style="font-size:10px;font-weight:500;color:#94a3b8;flex:1;min-width:0">Customer</span><span style="font-size:10px;font-weight:500;color:#94a3b8;min-width:22px;text-align:right">Ord</span><span style="font-size:10px;font-weight:500;color:#94a3b8;min-width:66px;text-align:right">Revenue</span></div>'
       + _ranked_rows(custs2, cColors, cm2) + "</div>";

    H += _sechd("Section 7 — Recent orders", periodLabel);
    H += '<div class="dw-card" style="margin-bottom:14px"><div class="dw-ct">Recent orders ' + _ptag(periodLabel) + "</div>"
       + '<div class="dw-cs">' + _pcs("Last 8 Offline Sales Orders · newest first", periodLabel) + "</div>"
       + '<div class="dw-feed-hd"><span>Order ID</span><span>Customer</span><span>Date</span><span>Amount</span><span>Status</span></div>'
       + _feed_rows(feed, _badge_offline, "#27500A") + "</div>";

    H += _sechd("Section 8 — Order pipeline & customer statistics", periodLabel);
    H += '<div class="dw-g2">';
    var cr=pl.completion_rate||0;
    H += '<div class="dw-card"><div class="dw-ct">Offline order pipeline ' + _ptag(periodLabel) + "</div>"
       + '<div class="dw-cs">' + _pcs("ERPNext status stages", periodLabel) + "</div>"
       + '<div class="dw-pipe">'
       + _pipe_stage("#F1EFE8","#444441","#5F5E5A","dw-pp1",pl.draft||0,"Draft") + _arrow(false)
       + _pipe_stage("#FAEEDA","#633806","#854F0B","dw-pp2",pl.to_deliver_bill||0,"To Deliver") + _arrow(false)
       + _pipe_stage("#E6F1FB","#0C447C","#185FA5","dw-pp3",pl.to_bill||0,"To Bill") + _arrow(false)
       + _pipe_stage("#EAF3DE","#27500A","#3B6D11","dw-pp4",pl.completed||0,"Completed") + _arrow(true)
       + _pipe_stage("#FCEBEB","#791F1F","#A32D2D","dw-pp5",pl.cancelled||0,"Cancelled")
       + "</div>"
       + _fbar_block("Completion rate — Completed ÷ Total" + (pl_active?" · "+periodLabel:""), cr,"#639922","dw-fb1",null)
       + "</div>";
    H += '<div class="dw-card"><div class="dw-ct">Customer statistics ' + _ptag(periodLabel) + "</div>"
       + '<div class="dw-cs">' + _pcs("Offline order behaviour", periodLabel) + "</div>"
       + _customer_stats_html(sn, "#27500A", periodLabel) + "</div>";
    H += "</div>";
    H += "</div>";
    return H;
}


// ================================================================
//  ANIMATIONS
// ================================================================
function _dash_animate(d, mode) {
    var pl = d.pipeline || {};
    var sn = d.snapshot || {};

    function cu(id, target) {
        var el = document.getElementById(id);
        if (!el) return;
        var s = 0, step = Math.max(1, Math.ceil(target / 25));
        var t = setInterval(function () {
            s += step;
            if (s >= target) { el.textContent = Math.round(target).toLocaleString("en-IN"); clearInterval(t); }
            else               el.textContent = Math.round(s).toLocaleString("en-IN");
        }, 28);
    }

    if (mode === "online") {
        cu("dw-pp1", pl.pending    || 0);
        cu("dw-pp2", pl.on_hold    || 0);
        cu("dw-pp3", pl.processing || 0);
        cu("dw-pp4", pl.shipped    || 0);
        cu("dw-pp5", pl.refunded   || 0);
        cu("dw-pp6", pl.cancelled  || 0);
    } else {
        cu("dw-pp1", pl.draft           || 0);
        cu("dw-pp2", pl.to_deliver_bill || 0);
        cu("dw-pp3", pl.to_bill         || 0);
        cu("dw-pp4", pl.completed       || 0);
        cu("dw-pp5", pl.cancelled       || 0);
    }
    cu("dw-cs1", sn.unique_customers || 0);
    cu("dw-cs2", sn.repeat_customers || 0);
    cu("dw-cs3", sn.repeat_rate      || 0);
}

function _fill_bars() {
    document.querySelectorAll("[data-w]").forEach(function (el) {
        el.style.width = (el.getAttribute("data-w") || 0) + "%";
    });
}


// ================================================================
//  CHARTS
// ================================================================
function _dash_charts(d, mode) {
    var ch   = d.charts || {};
    var dark = window.matchMedia("(prefers-color-scheme: dark)").matches;
    var gc   = dark ? "rgba(255,255,255,0.07)" : "rgba(0,0,0,0.06)";
    var lc   = dark ? "#94a3b8" : "#888780";
    var bdr  = dark ? "#1a1a1a" : "#ffffff";

    var dOpts = {
        responsive: false, maintainAspectRatio: false, cutout: "65%",
        animation: { animateRotate: true, duration: 800 },
        plugins: {
            legend: { display: false },
            tooltip: { callbacks: { label: function (ctx) {
                var tot = ctx.dataset.data.reduce(function (a, b) { return a + b; }, 0);
                var pct = tot > 0 ? Math.round(ctx.parsed / tot * 100) : 0;
                return " " + ctx.label + ": " + ctx.parsed + " (" + pct + "%)";
            }}}
        }
    };

    var c1 = document.getElementById("dw-ch1");
    if (c1) {
        var stColors = mode === "online"
            ? {"Shipped":"#27ae60","shipped":"#27ae60","Processing":"#f39c12","processing":"#f39c12",
               "Pending":"#BA7517","pending":"#BA7517","Cancelled":"#e74c3c","cancelled":"#e74c3c",
               "on-hold":"#888780","Refunded":"#8e44ad","refunded":"#8e44ad"}
            : {"Completed":"#27ae60","To Deliver and Bill":"#f39c12","To Bill":"#2490ef",
               "Cancelled":"#e74c3c","Draft":"#888780"};
        var stData = mode === "online" ? (ch.woo_status||{}) : (ch.status||{});
        var stL    = stData.labels || [];
        new Chart(c1, {
            type: "doughnut",
            data: {
                labels:   stL,
                datasets: [{
                    data:            (stData.datasets&&stData.datasets[0]&&stData.datasets[0].values)||[],
                    backgroundColor: stL.map(function (s) { return stColors[s] || "#888780"; }),
                    borderWidth: 3, borderColor: bdr, hoverOffset: 8
                }]
            },
            options: dOpts
        });
    }

    var c2 = document.getElementById("dw-ch2");
    if (c2 && mode === "online") {
        var pm  = ch.payment || {};
        var pml = pm.labels  || [];
        var pmC = ["#378ADD","#7F77DD","#D85A30","#888780","#639922","#D4537E"];
        new Chart(c2, {
            type: "doughnut",
            data: {
                labels:   pml,
                datasets: [{
                    data:            (pm.datasets&&pm.datasets[0]&&pm.datasets[0].values)||[],
                    backgroundColor: pml.map(function (_, i) { return pmC[i % pmC.length]; }),
                    borderWidth: 3, borderColor: bdr, hoverOffset: 8
                }]
            },
            options: dOpts
        });
    }

    var c3 = document.getElementById("dw-ch3");
    if (c3) {
        var mr       = ch.monthly_rev || {};
        var revColor = mode === "online" ? "#639922" : "#D85A30";
        new Chart(c3, {
            type: "line",
            data: {
                labels:   (mr.labels&&mr.labels.length) ? mr.labels : ["No data"],
                datasets: [{
                    label: "Revenue",
                    data:  (mr.datasets&&mr.datasets[0]&&mr.datasets[0].values)||[0],
                    borderColor: revColor,
                    backgroundColor: dark?"rgba(99,153,34,.12)":"rgba(99,153,34,.08)",
                    borderWidth:2,pointBackgroundColor:revColor,pointBorderColor:bdr,
                    pointBorderWidth:2,pointRadius:5,pointHoverRadius:7,tension:0.4,fill:true
                }]
            },
            options: {
                responsive:true,maintainAspectRatio:false,animation:{duration:900},
                plugins:{legend:{display:false},tooltip:{callbacks:{label:function(ctx){
                    return " ₹ "+(ctx.parsed.y||0).toLocaleString("en-IN");
                }}}},
                scales:{
                    x:{ticks:{color:lc,font:{size:11},autoSkip:false,maxRotation:30},grid:{color:gc},border:{display:false}},
                    y:{beginAtZero:true,ticks:{color:lc,font:{size:10},callback:function(v){
                        return v>=1000?"₹"+Math.round(v/1000)+"K":"₹"+v;
                    }},grid:{color:gc},border:{display:false}}
                }
            }
        });
    }

    var c4 = document.getElementById("dw-ch4");
    if (c4) {
        var mo       = ch.monthly_ord || {};
        var barColor = mode === "online" ? "#378ADD" : "#639922";
        new Chart(c4, {
            type: "bar",
            data: {
                labels:   (mo.labels&&mo.labels.length) ? mo.labels : ["No data"],
                datasets: [{
                    label:"Orders",
                    data:(mo.datasets&&mo.datasets[0]&&mo.datasets[0].values)||[0],
                    backgroundColor:barColor,borderRadius:6,borderSkipped:false
                }]
            },
            options: {
                responsive:true,maintainAspectRatio:false,animation:{duration:900},
                plugins:{legend:{display:false},tooltip:{callbacks:{label:function(ctx){
                    return " "+ctx.parsed.y+" orders";
                }}}},
                scales:{
                    x:{ticks:{color:lc,font:{size:11},autoSkip:false,maxRotation:30},grid:{color:gc},border:{display:false}},
                    y:{beginAtZero:true,ticks:{color:lc,font:{size:11},stepSize:1},grid:{color:gc},border:{display:false}}
                }
            }
        });
    }
}
