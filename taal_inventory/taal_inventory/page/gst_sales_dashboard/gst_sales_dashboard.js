// ================================================================
//  Sales & GST Dashboard
//  App  : taal_inventory
//  Page : gst-sales-dashboard
//  URL  : /app/gst-sales-dashboard
//
//  Every helper in this file is prefixed _gd_ on purpose.  Frappe loads
//  page scripts into the shared window scope, so the un-prefixed helpers
//  in woocommerce_dashboard.js (_INR, _kpi, _dash_styles ...) would
//  collide if both pages were opened in one session.
// ================================================================

frappe.pages["gst-sales-dashboard"].on_page_load = function (wrapper) {
    var page = frappe.ui.make_app_page({
        parent:        wrapper,
        title:         "Sales & GST Dashboard",
        single_column: true
    });

    window._gd_page          = page;
    window._gd_company       = null;
    window._gd_from          = null;
    window._gd_to            = null;
    window._gd_label         = "This Month";
    window._gd_drafts        = 0;
    window._gd_compare       = "none";
    window._gd_matrix_view   = "gross";
    window._gd_data          = null;

    // ── Date helpers (pure JS, no moment dependency) ─────────────
    function _pad(n) { return String(n).padStart(2, "0"); }

    function _monthStart(d) { return d.slice(0, 7) + "-01"; }

    function _monthEnd(d) {
        var p = d.split("-");
        var last = new Date(parseInt(p[0]), parseInt(p[1]), 0).getDate();
        return p[0] + "-" + p[1] + "-" + _pad(last);
    }

    function _addMonths(d, n) {
        var p = d.split("-");
        var y = parseInt(p[0]), m = parseInt(p[1]) + n;
        while (m <= 0) { m += 12; y -= 1; }
        while (m > 12) { m -= 12; y += 1; }
        return y + "-" + _pad(m) + "-01";
    }

    function _fyStart(d) {
        var p = d.split("-");
        var y = parseInt(p[0]);
        if (parseInt(p[1]) < 4) y -= 1;
        return y + "-04-01";
    }

    window._gd_range_display = function (fd, td) {
        if (!fd || !td) return "";
        var M = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
        var f = fd.split("-"), t = td.split("-");
        var fs = M[parseInt(f[1]) - 1] + " " + parseInt(f[2]) + ", " + f[0];
        var ts = M[parseInt(t[1]) - 1] + " " + parseInt(t[2]) + ", " + t[0];
        return fd === td ? fs : fs + " \u2013 " + ts;
    };

    // ── Date bar in the toolbar ──────────────────────────────────
    var $bar = $(
        '<div id="gd-datebar" style="display:inline-flex;align-items:center;gap:6px;'
        + 'background:#FFFBEB;border:1.5px solid #F59E0B;border-radius:8px;'
        + 'padding:5px 12px;font-size:12px;font-weight:500;color:#633806;'
        + 'margin-right:6px;white-space:nowrap;cursor:default">'
        + '<svg width="13" height="13" viewBox="0 0 14 14" fill="none" style="flex-shrink:0">'
        + '<rect x="1" y="2" width="12" height="11" rx="2" stroke="#BA7517" stroke-width="1.3"/>'
        + '<path d="M4 1v2M10 1v2M1 5h12" stroke="#BA7517" stroke-width="1.3" stroke-linecap="round"/>'
        + "</svg>"
        + '<span id="gd-datebar-label"></span>'
        + '<span id="gd-datebar-range" style="font-weight:600"></span>'
        + "</div>"
    );
    $(page.wrapper).find(".page-actions").prepend($bar);

    window._gd_update_datebar = function (label, fd, td) {
        $("#gd-datebar-label").text((label || "Custom") + ":\u00a0\u00a0");
        $("#gd-datebar-range").text(window._gd_range_display(fd, td));
    };

    // ── Toolbar buttons ──────────────────────────────────────────
    page.add_inner_button(__("Refresh"), function () { _gd_load(); });

    var today = frappe.datetime.get_today();

    var presets = [
        { label: "This Month",    from: _monthStart(today),                  to: _monthEnd(today) },
        { label: "Last Month",    from: _addMonths(today, -1),               to: _monthEnd(_addMonths(today, -1)) },
        { label: "Last 3 Months", from: _addMonths(today, -2),               to: _monthEnd(today) },
        { label: "This Quarter",  from: (function () {
                                        var m = parseInt(today.split("-")[1]);
                                        var qs = Math.floor((m - 1) / 3) * 3 + 1;
                                        return today.split("-")[0] + "-" + _pad(qs) + "-01";
                                    })(),                                    to: today },
        { label: "Current FY",    from: _fyStart(today),                     to: today },
        { label: "Full Current FY", from: _fyStart(today),                   to: (parseInt(_fyStart(today).split("-")[0]) + 1) + "-03-31" },
        { label: "Last FY",       from: (parseInt(_fyStart(today).split("-")[0]) - 1) + "-04-01",
                                  to:   parseInt(_fyStart(today).split("-")[0]) + "-03-31" }
    ];

    presets.forEach(function (p) {
        page.add_inner_button(__(p.label), function () {
            window._gd_from  = p.from;
            window._gd_to    = p.to;
            window._gd_label = p.label;
            window._gd_update_datebar(p.label, p.from, p.to);
            _gd_load();
        }, __("Date Presets"));
    });

    [["No comparison", "none"],
     ["vs previous period", "prev_period"],
     ["vs same period last year", "prev_year"]].forEach(function (c) {
        page.add_inner_button(__(c[0]), function () {
            window._gd_compare = c[1];
            _gd_load();
        }, __("Compare"));
    });

    page.add_inner_button(__("Export Excel"), function () { _gd_export_excel(); });

    // ── Boot ─────────────────────────────────────────────────────
    _gd_styles();
    window._gd_from  = _monthStart(today);
    window._gd_to    = _monthEnd(today);
    window._gd_label = "This Month";
    window._gd_update_datebar(window._gd_label, window._gd_from, window._gd_to);
    _gd_load();
};


// ================================================================
//  GLOBAL CONTROL HANDLERS  (called from inline markup)
// ================================================================
window._gd_set_company = function (v) {
    window._gd_company = v;
    _gd_load();
};

window._gd_set_drafts = function (checked) {
    window._gd_drafts = checked ? 1 : 0;
    _gd_load();
};

// Custom date range. Typing in either box switches the period label to
// "Custom", which makes every section tag show the actual range instead.
window._gd_set_custom_date = function (which, value) {
    if (!value) return;

    var from = which === "from" ? value : window._gd_from;
    var to   = which === "to"   ? value : window._gd_to;

    if (from > to) {
        frappe.msgprint({
            title: __("Check the dates"),
            message: __("From Date cannot be later than To Date."),
            indicator: "orange"
        });
        return;
    }

    window._gd_from  = from;
    window._gd_to    = to;
    window._gd_label = "Custom";
    window._gd_update_datebar("Custom", from, to);
    _gd_load();
};

window._gd_set_matrix_view = function (v) {
    window._gd_matrix_view = v;
    if (window._gd_data) _gd_render(window._gd_data);
};

// Click-through: every number on this page opens the invoice list behind it.
window._gd_route = function (extra) {
    var f = {
        company:      window._gd_company,
        posting_date: ["between", [window._gd_from, window._gd_to]]
    };
    if (!window._gd_drafts) f.docstatus = 1;
    Object.keys(extra || {}).forEach(function (k) { f[k] = extra[k]; });
    if (f.company === "__all__") delete f.company;
    frappe.set_route("List", "Sales Invoice", f);
};

window._gd_route_cell = function (sk, segKey) {
    var y = Math.floor(sk / 100), m = sk % 100;
    var last = new Date(y, m, 0).getDate();
    var from = y + "-" + String(m).padStart(2, "0") + "-01";
    var to   = y + "-" + String(m).padStart(2, "0") + "-" + String(last).padStart(2, "0");

    var f = { company: window._gd_company, posting_date: ["between", [from, to]] };
    if (!window._gd_drafts) f.docstatus = 1;
    if (f.company === "__all__") delete f.company;

    if (segKey === "online_intra")  { f.custom_online__offline = "Online";  f.custom_state_type = "Intra-State"; }
    if (segKey === "online_inter")  { f.custom_online__offline = "Online";  f.custom_state_type = "Inter-State"; }
    if (segKey === "offline_intra") { f.custom_online__offline = "Offline"; f.custom_state_type = "Intra-State"; }
    if (segKey === "offline_inter") { f.custom_online__offline = "Offline"; f.custom_state_type = "Inter-State"; }
    if (segKey === "international") { f.custom_state_type = "International"; }

    frappe.set_route("List", "Sales Invoice", f);
};


// ================================================================
//  LOAD
// ================================================================
function _gd_load() {
    var page = window._gd_page;
    if (!page) return;

    $(page.main).find(".gd,.gd-loading").remove();
    $(page.main).append('<div class="gd-loading">Loading dashboard\u2026</div>');

    frappe.call({
        method: "taal_inventory.utils.gst_sales_dashboard.get_gst_dashboard_data",
        args: {
            company:        window._gd_company || "",
            from_date:      window._gd_from    || "",
            to_date:        window._gd_to      || "",
            include_drafts: window._gd_drafts  || 0,
            period_label:   window._gd_label   || "",
            compare_mode:   window._gd_compare || "none"
        },
        freeze: false,
        callback: function (r) {
            $(page.main).find(".gd-loading").remove();
            if (!r.message) {
                $(page.main).append('<div class="gd-loading">No invoices in this period.</div>');
                return;
            }
            window._gd_data = r.message;
            if (!window._gd_company) window._gd_company = r.message.meta.company;
            _gd_cjs(function () { _gd_render(r.message); });
        },
        error: function () {
            $(page.main).find(".gd-loading").remove();
            $(page.main).append('<div class="gd-loading">Could not load. Press Refresh to try again.</div>');
        }
    });
}

function _gd_cjs(cb) {
    if (window.Chart && typeof window.Chart.register === "function") { cb(); return; }
    var s = document.createElement("script");
    s.src = "https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js";
    s.onload = cb;
    s.onerror = function () { cb(); };
    document.head.appendChild(s);
}

function _gd_render(d) {
    var page = window._gd_page;
    $(page.main).find(".gd,.gd-loading").remove();
    $(page.main).append(_gd_html(d));
    _gd_bind_hover();
    setTimeout(function () { _gd_animate(d); },  110);
    setTimeout(function () { _gd_charts(d);  },  330);
    setTimeout(function () { _gd_fill_bars(); }, 520);
}


// ================================================================
//  FORMATTERS
// ================================================================
function _gd_num(v) { return (parseInt(v) || 0).toLocaleString("en-IN"); }

function _gd_INR(v) {
    var n = parseFloat(v) || 0;
    var neg = n < 0; n = Math.abs(n);
    var s;
    if (n >= 10000000)   s = "\u20B9" + (n / 10000000).toFixed(2) + " Cr";
    else if (n >= 100000) s = "\u20B9" + (n / 100000).toFixed(2) + " L";
    else                  s = "\u20B9" + n.toLocaleString("en-IN", { maximumFractionDigits: 0 });
    return (neg ? "\u2212 " : "") + s;
}

function _gd_INR2(v) {
    var n = parseFloat(v) || 0;
    var neg = n < 0; n = Math.abs(n);
    return (neg ? "\u2212 " : "") + "\u20B9"
        + n.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function _gd_plain(v) {
    var n = parseFloat(v) || 0;
    if (!n) return '<span class="gd-z">0</span>';
    return n.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function _gd_esc(s) {
    return String(s === undefined || s === null ? "" : s)
        .replace(/&/g, "&amp;").replace(/</g, "&lt;")
        .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

function _gd_pct(a, b) {
    if (!b) return null;
    return Math.round(((a - b) / Math.abs(b)) * 1000) / 10;
}


// ================================================================
//  HTML
// ================================================================
function _gd_html(d) {
    var m = d.meta || {}, k = d.kpis || {};
    var H = '<div class="gd">';

    // ── Topbar ───────────────────────────────────────────────────
    var opts = '<option value="__all__"' + (m.company === "__all__" ? " selected" : "")
             + ">All companies</option>";
    (m.companies || []).forEach(function (c) {
        opts += '<option value="' + _gd_esc(c) + '"'
             + (c === m.company ? " selected" : "") + ">" + _gd_esc(c) + "</option>";
    });

    H += '<div class="gd-topbar">'
       + "<div>"
       + '<div class="gd-ttl">Sales &amp; GST Dashboard</div>'
       + '<div class="gd-sub">' + _gd_esc(m.company === "__all__" ? "All companies" : m.company)
       + " \u00b7 Sales Invoices \u00b7 generated " + _gd_esc(m.generated_on) + "</div>"
       + "</div>"
       + '<div class="gd-controls">'
       + '<label class="gd-sel"><span>Company</span>'
       + '<select onchange="window._gd_set_company(this.value)">' + opts + "</select></label>"
       + '<label class="gd-chk"><input type="checkbox" '
       + (m.include_drafts ? "checked " : "")
       + 'onchange="window._gd_set_drafts(this.checked)"> Include drafts</label>'
       + '<label class="gd-sel gd-date"><span>From</span>'
       + '<input type="date" value="' + _gd_esc(m.from_date) + '" '
       + "onchange=\"window._gd_set_custom_date('from', this.value)\"></label>"
       + '<label class="gd-sel gd-date"><span>To</span>'
       + '<input type="date" value="' + _gd_esc(m.to_date) + '" '
       + "onchange=\"window._gd_set_custom_date('to', this.value)\"></label>"
       + '<span class="gd-chip gd-chip-g">' + _gd_INR(k.grand_total ? k.grand_total.value : 0) + " gross</span>"
       + '<span class="gd-chip gd-chip-b">' + _gd_num(k.inv_count ? k.inv_count.value : 0) + " invoices</span>"
       + "</div></div>";

    // ── Period bar ───────────────────────────────────────────────
    H += '<div class="gd-period"><div>'
       + '<div class="gd-period-l">Active filter: ' + _gd_esc(m.period_label) + "</div>"
       + '<div class="gd-period-r">' + _gd_esc(window._gd_range_display(m.from_date, m.to_date))
       + " \u00b7 every section below is filtered to this period</div></div>"
       + '<span class="gd-period-note">'
       + (m.include_drafts ? "Drafts included \u00b7 docstatus 0 and 1"
                           : "Submitted only \u00b7 docstatus 1") + "</span></div>";

    H += '<div class="gd-notice">Segments: Online-Intra, Online-Inter, Offline-Intra, '
       + "Offline-Inter \u00b7 credit and debit notes excluded from the matrix \u00b7 "
       + "<b>click any number to open the invoices behind it</b></div>";

    // ── Section 1 — KPIs ─────────────────────────────────────────
    H += _gd_sechd("Section 1 \u2014 Sales &amp; GST KPIs", m.period_label);
    H += '<div class="gd-g4">';
    H += _gd_kpi("kb", "Total invoices",       k.inv_count,   "#378ADD", "#0C447C", {});
    H += _gd_kpi("kg", "Net sales (taxable)",  k.net_sales,   "#639922", "#27500A", {});
    H += _gd_kpi("ka", "Product GST",          k.product_gst, "#BA7517", "#633806", {});
    H += _gd_kpi("kc", "Courier + courier GST", k.courier_all, "#D85A30", "#712B13",
                 { custom_courier_charges_required: "Yes" });
    H += "</div>";
    H += '<div class="gd-g4">';
    H += _gd_kpi("kr", "Credit &amp; debit notes", k.credit_notes, "#E24B4A", "#791F1F", { is_return: 1 });
    H += _gd_kpi("ks", "Round off",            k.round_off,   "#888780", "#5F5E5A", {});
    H += _gd_kpi("kt", "Grand total (rounded)", k.grand_total, "#1D9E75", "#085041", {});
    H += _gd_kpi("kp", "Outstanding",          k.outstanding, "#7F77DD", "#3C3489",
                 { status: ["in", ["Unpaid", "Overdue", "Partly Paid"]] });
    H += "</div>";

    // ── Section 2 — Matrix ───────────────────────────────────────
    H += _gd_sechd("Section 2 \u2014 Month-wise GST matrix", m.period_label);
    H += _gd_matrix(d);

    // ── Section 3 — Rate-wise ────────────────────────────────────
    H += _gd_sechd("Section 3 \u2014 Rate-wise taxable value", m.period_label);
    H += _gd_rate_cards(d.rate_wise || []);

    // ── Section 4 — Document mix & status ────────────────────────
    H += _gd_sechd("Section 4 \u2014 Document mix &amp; status", m.period_label);
    H += '<div class="gd-g2">';
    H += '<div class="gd-card"><div class="gd-ct">Document type</div>'
       + '<div class="gd-cs">Sales Invoice variants in this period</div>'
       + '<div style="display:flex;gap:20px;align-items:center">'
       + '<div style="width:140px;height:140px;flex:0 0 140px;position:relative">'
       + '<canvas id="gd-ch-doc" role="img" aria-label="Document type breakdown"></canvas>'
       + "</div>"
       + '<div style="flex:1;min-width:0">' + _gd_legend(d.doc_mix || []) + "</div></div></div>";
    H += '<div class="gd-card"><div class="gd-ct">Status by month</div>'
       + '<div class="gd-cs">Invoice count per status</div>'
       + '<div style="position:relative;height:212px">'
       + '<canvas id="gd-ch-status" role="img" aria-label="Status by month"></canvas></div></div>';
    H += "</div>";

    // ── Section 5 — Credit & debit notes ─────────────────────────
    H += _gd_sechd("Section 5 \u2014 Credit &amp; debit notes", m.period_label);
    H += _gd_notes(d);

    // ── Section 6 — Exceptions ───────────────────────────────────
    if ((d.exceptions || []).length) {
        H += _gd_sechd("Section 6 \u2014 Things to check before filing", m.period_label);
        H += _gd_exceptions(d.exceptions);
    }

    // ── Section 7 — Trends ───────────────────────────────────────
    H += _gd_sechd("Section 7 \u2014 Monthly trends", m.period_label);
    H += '<div class="gd-g2">'
       + '<div class="gd-card"><div class="gd-ct">Net sales and GST by month</div>'
       + '<div class="gd-cs">FY order \u00b7 sales bars with GST overlay</div>'
       + '<div style="position:relative;height:230px">'
       + '<canvas id="gd-ch-trend" role="img" aria-label="Monthly sales and GST"></canvas></div></div>'
       + '<div class="gd-card"><div class="gd-ct">Online vs Offline by month</div>'
       + '<div class="gd-cs">Net sales split by channel</div>'
       + '<div style="position:relative;height:230px">'
       + '<canvas id="gd-ch-channel" role="img" aria-label="Channel split by month"></canvas></div></div>'
       + "</div>";

    // ── Section 8 — Courier ──────────────────────────────────────
    H += _gd_sechd("Section 8 \u2014 Courier &amp; logistics", m.period_label);
    H += _gd_courier(d.courier || {});

    // ── Section 9 — Receivables ──────────────────────────────────
    H += _gd_sechd("Section 9 \u2014 Receivables", m.period_label);
    H += _gd_aging(d.aging || []);

    // ── Section 10 — Feed ────────────────────────────────────────
    H += _gd_sechd("Section 10 \u2014 Recent invoices", m.period_label);
    H += _gd_feed(d.feed || []);

    H += "</div>";
    return H;
}


function _gd_sechd(label, period) {
    // A preset shows its name ("This Month"). A custom range shows the range
    // itself, so the accountant always knows what a section is filtered to.
    var text = period || "";
    if (text === "Custom") {
        text = window._gd_range_display(window._gd_from, window._gd_to);
    }
    var tag = text ? ' <span class="gd-ptag">' + _gd_esc(text) + "</span>" : "";
    return '<div class="gd-sh"><span class="gd-sl">' + label + tag
         + '</span><div class="gd-sline"></div></div>';
}


function _gd_kpi(cls, label, card, accent, textColor, route) {
    card = card || { value: 0, breakdown: [], note: "" };
    var isCount = !!card.is_count;
    var val = isCount ? _gd_num(card.value) : _gd_INR(card.value);

    var pop = "";
    (card.breakdown || []).forEach(function (b) {
        var v = b.is_count ? _gd_num(b.value) : _gd_INR2(b.value);
        pop += '<div class="gd-pr"><span class="gd-pl">' + _gd_esc(b.label)
             + '</span><span class="gd-pv">' + v + "</span></div>";
    });

    return '<div class="gd-kpi ' + cls + '" style="--gd-a:' + accent + '" '
        + 'onclick=\'window._gd_route(' + JSON.stringify(route || {}) + ')\' '
        + 'role="button" tabindex="0" '
        + 'onkeypress="if(event.key===\'Enter\')this.click()">'
        + '<div class="gd-kl">' + label + "</div>"
        + '<div class="gd-kv" style="color:' + textColor + '"'
        + (isCount ? ' data-count="' + (parseInt(card.value) || 0) + '"' : "")
        + ">" + val + "</div>"
        + '<div class="gd-knote">' + _gd_esc(card.note || "") + "</div>"
        + '<div class="gd-bar"><div class="gd-barf" style="background:' + accent
        + '" data-w="100"></div></div>'
        + (pop ? '<div class="gd-pop">' + pop + "</div>" : "")
        + "</div>";
}


function _gd_legend(items) {
    var H = "";
    items.forEach(function (i) {
        if (!i.value) return;
        H += '<div class="gd-leg"><span class="gd-leg-l">'
           + '<span class="gd-dot" style="background:' + i.color + '"></span>'
           + _gd_esc(i.label) + '</span><b>' + _gd_num(i.value) + "</b></div>";
    });
    return H || '<div class="gd-empty">Nothing in this period</div>';
}


// ================================================================
//  MATRIX
// ================================================================
var _GD_COLS = [
    ["month",   "Month",           false],
    ["sales",   "Sales",           true],
    ["cgst",    "CGST",            true],
    ["sgst",    "SGST",            true],
    ["igst",    "IGST",            true],
    ["courier", "Courier Charges", true],
    ["c_cgst",  "Courier CGST",    true],
    ["c_sgst",  "Courier SGST",    true],
    ["c_igst",  "Courier IGST",    true],
    ["roff",    "R/off",           true],
    ["total",   "Total",           true]
];

var _GD_SEG_BG = {
    online_intra:  ["#E6F1FB", "#0C447C"],
    online_inter:  ["#EEEDFE", "#3C3489"],
    offline_intra: ["#EAF3DE", "#27500A"],
    offline_inter: ["#FAEEDA", "#633806"],
    international: ["#E1F5EE", "#085041"],
    unclassified:  ["#F1EFE8", "#444441"]
};


function _gd_matrix(d) {
    var mx = d.matrix || {};
    var cmp = d.compare;
    var net = window._gd_matrix_view === "net";

    if (!(mx.segments || []).length) {
        return '<div class="gd-card"><div class="gd-empty">'
             + "No submitted invoices in this period. Try a wider date range, "
             + "or tick Include drafts.</div></div>";
    }

    // Comparison lookup: segment key + sk -> cell
    var cmpMap = {};
    if (cmp && cmp.matrix) {
        (cmp.matrix.segments || []).forEach(function (s) {
            s.months.forEach(function (r) { cmpMap[s.key + "|" + r.sk] = r; });
            cmpMap[s.key + "|sub"] = s.subtotal;
        });
        (cmp.matrix.total_months || []).forEach(function (r) {
            cmpMap["__total__|" + r.sk] = r;
        });
        cmpMap["__total__|sub"] = cmp.matrix.grand;
    }

    var showCmp = !!cmp;
    var span = _GD_COLS.length + (showCmp ? 1 : 0);

    var H = '<div class="gd-card">'
        + '<div class="gd-card-hd">'
        + "<div><div class=\"gd-ct\">GST summary by segment and month</div>"
        + '<div class="gd-cs" style="margin:0">Four segments sum to Total \u00b7 '
        + "click any row to open its invoices</div></div>"
        + '<div class="gd-seg-toggle">'
        + '<button class="gd-tb' + (net ? "" : " on")
        + '" onclick="window._gd_set_matrix_view(\'gross\')">Gross</button>'
        + '<button class="gd-tb' + (net ? " on" : "")
        + '" onclick="window._gd_set_matrix_view(\'net\')">Net of credit notes</button>'
        + "</div></div>";

    if (net) {
        H += '<div class="gd-inline-note">Net view subtracts credit notes from the '
           + "Total block only. Segment blocks stay gross \u2014 a credit note carries "
           + "no channel or state type of its own.</div>";
    }

    if (showCmp) {
        H += '<div class="gd-inline-note">Comparing against '
           + _gd_esc(cmp.from_date) + " to " + _gd_esc(cmp.to_date)
           + " \u00b7 the \u0394 column shows the change in Total.</div>";
    }

    H += '<div class="gd-scroll"><table class="gd-mx"><thead><tr>';
    _GD_COLS.forEach(function (c) {
        H += "<th" + (c[2] ? "" : ' class="gd-l"') + ">" + c[1] + "</th>";
    });
    if (showCmp) H += "<th>\u0394 Total</th>";
    H += "</tr></thead><tbody>";

    function delta(key, sk, cur) {
        if (!showCmp) return "";
        var prev = cmpMap[key + "|" + sk];
        if (!prev) return '<td class="gd-z">\u2014</td>';
        var p = _gd_pct(cur, prev.total);
        if (p === null) return '<td class="gd-z">\u2014</td>';
        var cl = p > 0 ? "gd-up" : (p < 0 ? "gd-dn" : "gd-z");
        var ar = p > 0 ? "\u2191" : (p < 0 ? "\u2193" : "\u2014");
        return '<td class="' + cl + '">' + ar + " " + Math.abs(p) + "%</td>";
    }

    function rows(segKey, label, monthRows, subtotal, bg, fg, adjust) {
        H += '<tr class="gd-blk"><td colspan="' + span + '" style="background:'
           + bg + ";color:" + fg + '">' + _gd_esc(label) + "</td></tr>";

        monthRows.forEach(function (r) {
            var tot = r.total - (adjust ? (adjust[r.sk] || 0) : 0);
            H += '<tr class="gd-r" onclick="window._gd_route_cell(' + r.sk + ",'"
               + segKey + "')\" role=\"button\" tabindex=\"0\">";
            _GD_COLS.forEach(function (c) {
                if (c[0] === "month") {
                    H += '<td class="gd-l">' + _gd_esc(r.month) + "</td>";
                } else if (c[0] === "total") {
                    H += "<td>" + _gd_plain(tot) + "</td>";
                } else {
                    H += "<td>" + _gd_plain(r[c[0]]) + "</td>";
                }
            });
            H += delta(segKey, r.sk, tot);
            H += "</tr>";
        });

        var subTot = subtotal.total;
        if (adjust) {
            Object.keys(adjust).forEach(function (sk) { subTot -= adjust[sk]; });
        }
        H += '<tr class="gd-sub"><td class="gd-l">Subtotal</td>';
        _GD_COLS.slice(1).forEach(function (c) {
            H += "<td>" + (c[0] === "total" ? _gd_plain(subTot) : _gd_plain(subtotal[c[0]])) + "</td>";
        });
        H += delta(segKey, "sub", subTot);
        H += "</tr>";
    }

    (mx.segments || []).forEach(function (s) {
        var col = _GD_SEG_BG[s.key] || ["#F1EFE8", "#444441"];
        rows(s.key, s.label, s.months, s.subtotal, col[0], col[1], null);
    });

    // Credit-note adjustment for the Net view, keyed by month
    var adjust = null;
    if (net) {
        adjust = {};
        (d.notes || []).forEach(function (n) { adjust[n.sk] = n.cn_value; });
    }

    rows("__total__", "Total \u2014 all segments", mx.total_months || [],
         mx.grand || {}, "#E1F5EE", "#085041", adjust);

    H += "</tbody></table></div></div>";
    return H;
}


// ================================================================
//  RATE-WISE  —  number cards + chart, matching the rest of the page
// ================================================================
var _GD_RATE_COLORS = ["#639922", "#378ADD", "#7F77DD", "#BA7517",
                       "#1D9E75", "#D85A30", "#888780"];

function _gd_rate_cards(rows) {
    if (!rows.length) {
        return '<div class="gd-card"><div class="gd-empty">'
             + "No taxable lines in this period</div></div>";
    }

    var totTaxable = 0, totTax = 0;
    rows.forEach(function (r) { totTaxable += r.taxable; totTax += r.tax; });

    var H = '<div class="gd-g4">';
    rows.forEach(function (r, i) {
        var c = _GD_RATE_COLORS[i % _GD_RATE_COLORS.length];
        var share = totTaxable ? Math.round(r.taxable / totTaxable * 100) : 0;

        H += '<div class="gd-kpi gd-nolink" style="--gd-a:' + c + '">'
           + '<div class="gd-kl">' + _gd_esc(r.label)
           + (r.is_service ? ' <span class="gd-mini">service</span>' : "")
           + "</div>"
           + '<div class="gd-kv" style="color:' + c + '">' + _gd_INR(r.taxable) + "</div>"
           + '<div class="gd-knote">' + _gd_num(r.inv_count) + " invoices \u00b7 "
           + share + "% of taxable value</div>"
           + '<div class="gd-bar"><div class="gd-barf" style="background:' + c
           + '" data-w="' + share + '"></div></div>'
           + '<div class="gd-pop">'
           + '<div class="gd-pr"><span class="gd-pl">Taxable value</span>'
           + '<span class="gd-pv">' + _gd_INR2(r.taxable) + "</span></div>"
           + '<div class="gd-pr"><span class="gd-pl">CGST</span>'
           + '<span class="gd-pv">' + _gd_INR2(r.cgst) + "</span></div>"
           + '<div class="gd-pr"><span class="gd-pl">SGST</span>'
           + '<span class="gd-pv">' + _gd_INR2(r.sgst) + "</span></div>"
           + '<div class="gd-pr"><span class="gd-pl">IGST</span>'
           + '<span class="gd-pv">' + _gd_INR2(r.igst) + "</span></div>"
           + '<div class="gd-pr"><span class="gd-pl">Total tax</span>'
           + '<span class="gd-pv">' + _gd_INR2(r.tax) + "</span></div>"
           + '<div class="gd-pr"><span class="gd-pl">Invoice value</span>'
           + '<span class="gd-pv">' + _gd_INR2(r.total) + "</span></div>"
           + "</div></div>";
    });
    H += "</div>";

    H += '<div class="gd-card"><div class="gd-card-hd"><div>'
       + '<div class="gd-ct">Taxable value and tax by GST rate</div>'
       + '<div class="gd-cs" style="margin:0">The shape GSTR-1 asks for \u00b7 '
       + "courier service is listed separately because it lives in its own "
       + "child table</div></div>"
       + '<span class="gd-chip gd-chip-g">' + _gd_INR(totTaxable)
       + " taxable \u00b7 " + _gd_INR(totTax) + " tax</span></div>"
       + '<div style="position:relative;height:'
       + Math.max(190, rows.length * 58) + 'px">'
       + '<canvas id="gd-ch-rate" role="img" '
       + 'aria-label="Taxable value and tax by GST rate"></canvas></div></div>';

    return H;
}


// ================================================================
//  NOTES, EXCEPTIONS, COURIER, AGING, FEED
// ================================================================
function _gd_notes(d) {
    var k = (d.kpis || {}).credit_notes || { breakdown: [] };
    var b = {};
    (k.breakdown || []).forEach(function (x) { b[x.label] = x.value; });
    var monthly = d.notes || [];
    var max = 1;
    monthly.forEach(function (m) { if (m.cn_value > max) max = m.cn_value; });

    var H = '<div class="gd-card"><div class="gd-g3" style="margin-bottom:14px">'
        + '<div class="gd-stc"><div class="gd-stl">Credit notes</div>'
        + '<div class="gd-stv" style="color:#791F1F">' + _gd_INR(k.value) + "</div>"
        + '<div class="gd-sts">' + _gd_num(b["Credit notes"] || 0)
        + " notes \u00b7 GST reversed " + _gd_INR2(b["GST reversed"] || 0) + "</div></div>"
        + '<div class="gd-stc"><div class="gd-stl">Debit notes</div>'
        + '<div class="gd-stv" style="color:#0C447C">' + _gd_INR(b["Debit value"] || 0) + "</div>"
        + '<div class="gd-sts">' + _gd_num(b["Debit notes"] || 0)
        + " rate adjustments</div></div>"
        + '<div class="gd-stc"><div class="gd-stl">Return rate</div>';

    var gross = ((d.matrix || {}).grand || {}).total || 0;
    var rate = gross ? Math.abs(k.value) / gross * 100 : 0;
    H += '<div class="gd-stv" style="color:#633806">' + rate.toFixed(2) + "%</div>"
       + '<div class="gd-sts">credit note value \u00f7 gross total</div></div></div>';

    monthly.forEach(function (mo) {
        var w = Math.round(mo.cn_value / max * 100);
        H += '<div class="gd-mbar"><b>' + _gd_esc(mo.label) + "</b>"
           + '<div class="gd-mtrack"><div class="gd-mfill" style="background:#E24B4A" '
           + 'data-w="' + w + '"></div></div>'
           + '<span class="gd-mval">' + (mo.cn_value ? "\u2212 " + _gd_INR(mo.cn_value) : "\u2014")
           + "</span>"
           + _gd_tip([["Month", _gd_esc(mo.label)],
                      ["Credit note value", _gd_INR2(mo.cn_value)],
                      ["Notes issued", _gd_num(mo.cn_count)]])
           + "</div>";
    });

    var rn = d.recent_notes || [];
    if (rn.length) {
        H += '<div class="gd-subhd">Recent notes</div>';
        H += '<div class="gd-nfeed gd-nhd"><span>Document</span><span>Against</span>'
           + "<span>Customer</span><span>Date</span><span>Amount</span><span>Type</span></div>";
        rn.forEach(function (r) {
            H += '<div class="gd-nfeed"><span class="gd-link">' + _gd_esc(r.name) + "</span>"
               + '<span class="gd-muted">' + _gd_esc(r.against) + "</span>"
               + "<span>" + _gd_esc(r.customer) + "</span>"
               + '<span class="gd-muted">' + _gd_esc(r.date) + "</span>"
               + '<span style="color:#791F1F">' + _gd_INR2(r.amount) + "</span>"
               + "<span><span class=\"gd-bdg gd-bdg-p\">" + _gd_esc(r.kind) + "</span></span></div>";
        });
    }

    H += "</div>";
    return H;
}


function _gd_exceptions(list) {
    var total = 0;
    list.forEach(function (e) { total += e.count; });

    var lv = {
        high:   ["#FCEBEB", "#A32D2D", "#791F1F"],
        medium: ["#FAEEDA", "#854F0B", "#633806"],
        low:    ["#F1EFE8", "#5F5E5A", "#444441"]
    };

    var H = '<div class="gd-card"><div class="gd-card-hd">'
        + "<div><div class=\"gd-ct\">Things to check before filing</div>"
        + '<div class="gd-cs" style="margin:0">Each row opens the invoices behind it</div></div>'
        + '<span class="gd-chip gd-chip-r">' + _gd_num(total) + " to review</span></div>";

    list.forEach(function (e) {
        var c = lv[e.level] || lv.low;
        H += '<div class="gd-ex" style="background:' + c[0] + '" '
           + "onclick='window._gd_route(" + JSON.stringify(e.route || {}) + ")' "
           + 'role="button" tabindex="0">'
           + '<span class="gd-exn" style="color:' + c[1] + '">' + _gd_num(e.count) + "</span>"
           + '<span class="gd-ext"><span class="gd-exl" style="color:' + c[2] + '">'
           + _gd_esc(e.label) + "</span>"
           + '<span class="gd-exd">' + _gd_esc(e.detail) + "</span></span>"
           + '<span class="gd-fix" style="color:' + c[2] + '">' + _gd_esc(e.action) + "</span></div>";
    });

    H += "</div>";
    return H;
}


function _gd_courier(c) {
    var monthly = c.monthly || [], loc = c.by_location || [];
    if (!monthly.length && !loc.length) {
        return '<div class="gd-card"><div class="gd-empty">No courier charges in this period</div></div>';
    }
    var maxM = 1;
    monthly.forEach(function (m) { if (m.charges + m.gst > maxM) maxM = m.charges + m.gst; });
    var maxL = 1, totLoc = 0;
    loc.forEach(function (l) { if (l.value > maxL) maxL = l.value; totLoc += l.value; });

    var H = '<div class="gd-g2">'
        + '<div class="gd-card"><div class="gd-ct">Courier by month</div>'
        + '<div class="gd-cs">Base charges and courier GST</div>';
    monthly.forEach(function (m) {
        H += '<div class="gd-mbar"><b>' + _gd_esc(m.label) + "</b>"
           + '<div class="gd-mtrack">'
           + '<div class="gd-mfill" style="background:#D85A30" data-w="'
           + Math.round(m.charges / maxM * 100) + '"></div>'
           + '<div class="gd-mfill" style="background:#f0b9a4" data-w="'
           + Math.round(m.gst / maxM * 100) + '"></div></div>'
           + '<span class="gd-mval">' + _gd_INR(m.charges + m.gst) + "</span>"
           + _gd_tip([["Month", _gd_esc(m.label)],
                      ["Base charges", _gd_INR2(m.charges)],
                      ["Courier GST", _gd_INR2(m.gst)],
                      ["Total", _gd_INR2(m.charges + m.gst)]])
           + "</div>";
    });
    H += '<div class="gd-legend">'
       + '<span><span class="gd-dot" style="background:#D85A30"></span>Charges</span>'
       + '<span><span class="gd-dot" style="background:#f0b9a4"></span>Courier GST</span></div></div>';

    H += '<div class="gd-card"><div class="gd-ct">Courier by location</div>'
       + '<div class="gd-cs">From the Courier Charge Entry location field</div>';
    loc.forEach(function (l) {
        var lShare = totLoc ? Math.round(l.value / totLoc * 100) : 0;
        H += '<div class="gd-mbar"><b style="width:96px">' + _gd_esc(l.label) + "</b>"
           + '<div class="gd-mtrack"><div class="gd-mfill" style="background:#BA7517" '
           + 'data-w="' + Math.round(l.value / maxL * 100) + '"></div></div>'
           + '<span class="gd-mval">' + _gd_INR(l.value) + "</span>"
           + _gd_tip([["Location", _gd_esc(l.label)],
                      ["Courier charges", _gd_INR2(l.value)],
                      ["Invoices", _gd_num(l.count)],
                      ["Share", lShare + "%"]])
           + "</div>";
    });
    H += "</div></div>";
    return H;
}


function _gd_aging(rows) {
    var max = 1, total = 0;
    rows.forEach(function (r) { if (r.value > max) max = r.value; total += r.value; });
    if (!total) {
        return '<div class="gd-card"><div class="gd-empty">Nothing outstanding \u2014 '
             + "every invoice in this period is settled.</div></div>";
    }
    var colors = ["#639922", "#BA7517", "#D85A30", "#E24B4A", "#791F1F"];
    var H = '<div class="gd-card"><div class="gd-ct">Outstanding by age</div>'
        + '<div class="gd-cs">Aged on due date \u00b7 total ' + _gd_INR2(total) + "</div>";
    rows.forEach(function (r, i) {
        var aShare = total ? Math.round(r.value / total * 100) : 0;
        H += '<div class="gd-mbar"><b style="width:84px">' + _gd_esc(r.label) + "</b>"
           + '<div class="gd-mtrack"><div class="gd-mfill" style="background:'
           + colors[i % colors.length] + '" data-w="' + Math.round(r.value / max * 100)
           + '"></div></div><span class="gd-mval">' + _gd_INR(r.value) + "</span>"
           + _gd_tip([["Bucket", _gd_esc(r.label)],
                      ["Outstanding", _gd_INR2(r.value)],
                      ["Share of total", aShare + "%"]])
           + "</div>";
    });
    H += "</div>";
    return H;
}


function _gd_feed(feed) {
    if (!feed.length) {
        return '<div class="gd-card"><div class="gd-empty">No invoices in this period</div></div>';
    }
    var badge = {
        "Paid": "gd-bdg-g", "Unpaid": "gd-bdg-a", "Overdue": "gd-bdg-r",
        "Return": "gd-bdg-p", "Credit Note Issued": "gd-bdg-p",
        "Draft": "gd-bdg-s", "Cancelled": "gd-bdg-s", "Partly Paid": "gd-bdg-a"
    };

    var H = '<div class="gd-card">'
        + '<div class="gd-feed gd-fhd"><span>Invoice</span><span>Customer</span>'
        + "<span>Date</span><span>HSN</span><span>Segment</span><span>Amount</span>"
        + "<span>Status</span></div>";

    var lastMonth = null;
    feed.forEach(function (f) {
        if (f.month !== lastMonth) {
            H += '<div class="gd-mo-hd">' + _gd_esc(f.month) + "</div>";
            lastMonth = f.month;
        }
        var seg = (f.channel || "-") + " \u00b7 " + (f.state_type || "-");
        H += '<div class="gd-feed gd-r" '
           + "onclick=\"frappe.set_route('Form','Sales Invoice','" + _gd_esc(f.name) + "')\" "
           + 'role="button" tabindex="0">'
           + '<span class="gd-link">' + _gd_esc(f.name) + "</span>"
           + "<span>" + _gd_esc(f.customer) + "</span>"
           + '<span class="gd-muted">' + _gd_esc(f.date) + "</span>"
           + '<span class="gd-muted">' + _gd_esc(f.hsn) + "</span>"
           + '<span class="gd-muted">' + _gd_esc(seg) + "</span>"
           + "<span" + (f.amount < 0 ? ' style="color:#791F1F"' : ' style="color:#27500A"')
           + ">" + _gd_INR2(f.amount) + "</span>"
           + '<span><span class="gd-bdg ' + (badge[f.status] || "gd-bdg-s") + '">'
           + _gd_esc(f.status) + "</span></span></div>";
    });

    H += "</div>";
    return H;
}


// ================================================================
//  BAR TOOLTIP  —  the CSS bars are plain divs, so they get their own
//  hover card rather than a Chart.js tooltip.
// ================================================================
function _gd_tip(rows) {
    var H = '<div class="gd-tip">';
    rows.forEach(function (r) {
        H += '<div class="gd-tip-r"><span>' + r[0] + "</span><b>" + r[1] + "</b></div>";
    });
    return H + "</div>";
}


// A hovered KPI card must paint above the grid row that follows it, otherwise
// its popover slides underneath the next row of cards.
function _gd_bind_hover() {
    document.querySelectorAll(".gd .gd-kpi").forEach(function (card) {
        card.addEventListener("mouseenter", function () {
            card.style.zIndex = "300";
            if (card.parentNode && card.parentNode.style) {
                card.parentNode.style.position = "relative";
                card.parentNode.style.zIndex = "300";
            }
        });
        card.addEventListener("mouseleave", function () {
            card.style.zIndex = "";
            if (card.parentNode && card.parentNode.style) {
                card.parentNode.style.zIndex = "";
            }
        });
    });
}


// ================================================================
//  ANIMATION
// ================================================================
function _gd_animate() {
    document.querySelectorAll(".gd-kv[data-count]").forEach(function (el) {
        var target = parseInt(el.getAttribute("data-count")) || 0;
        if (!target) return;
        var cur = 0, step = Math.max(1, Math.ceil(target / 32));
        el.textContent = "0";
        var t = setInterval(function () {
            cur += step;
            if (cur >= target) { el.textContent = _gd_num(target); clearInterval(t); }
            else el.textContent = _gd_num(cur);
        }, 26);
    });
}

function _gd_fill_bars() {
    document.querySelectorAll(".gd [data-w]").forEach(function (el) {
        el.style.width = (el.getAttribute("data-w") || 0) + "%";
    });
}


// ================================================================
//  CHARTS
// ================================================================
function _gd_charts(d) {
    if (!window.Chart) return;

    var dark = window.matchMedia("(prefers-color-scheme: dark)").matches;
    var grid = dark ? "rgba(255,255,255,0.07)" : "rgba(0,0,0,0.06)";
    var tick = dark ? "#94a3b8" : "#888780";
    var brd  = dark ? "#1a1a1a" : "#ffffff";

    function destroy(c) {
        if (c && window.Chart.getChart) {
            var ex = window.Chart.getChart(c);
            if (ex) ex.destroy();
        }
    }

    // Document mix donut
    var c1 = document.getElementById("gd-ch-doc");
    if (c1) {
        destroy(c1);
        var mix = (d.doc_mix || []).filter(function (x) { return x.value > 0; });
        new Chart(c1, {
            type: "doughnut",
            data: {
                labels: mix.map(function (x) { return x.label; }),
                datasets: [{
                    data: mix.map(function (x) { return x.value; }),
                    backgroundColor: mix.map(function (x) { return x.color; }),
                    borderWidth: 3, borderColor: brd, hoverOffset: 8
                }]
            },
            options: {
                responsive: true, maintainAspectRatio: false, cutout: "60%",
                animation: { animateRotate: true, duration: 750 },
                plugins: {
                    legend: { display: false },
                    tooltip: { callbacks: { label: function (ctx) {
                        var tot = ctx.dataset.data.reduce(function (a, b) { return a + b; }, 0);
                        var p = tot ? Math.round(ctx.parsed / tot * 100) : 0;
                        return " " + ctx.label + ": " + ctx.parsed + " (" + p + "%)";
                    } } }
                }
            }
        });
    }

    // Status by month
    var c2 = document.getElementById("gd-ch-status");
    if (c2) {
        destroy(c2);
        var st = d.status || { months: [], statuses: [] };
        var palette = {
            "Paid": "#639922", "Unpaid": "#BA7517", "Overdue": "#E24B4A",
            "Return": "#7F77DD", "Credit Note Issued": "#7F77DD",
            "Draft": "#888780", "Cancelled": "#A32D2D", "Partly Paid": "#D85A30",
            "Submitted": "#378ADD"
        };
        new Chart(c2, {
            type: "bar",
            data: {
                labels: st.months.map(function (m) { return m.label; }),
                datasets: st.statuses.map(function (s) {
                    return {
                        label: s,
                        data: st.months.map(function (m) { return m.counts[s] || 0; }),
                        backgroundColor: palette[s] || "#888780",
                        borderRadius: 5, borderSkipped: false
                    };
                })
            },
            options: {
                responsive: true, maintainAspectRatio: false,
                animation: { duration: 750 },
                interaction: { mode: "index", intersect: false },
                plugins: {
                    legend: { position: "bottom",
                              labels: { font: { size: 10 }, usePointStyle: true, padding: 10 } },
                    tooltip: { callbacks: { footer: function (items) {
                        var t = items.reduce(function (s, i) { return s + i.parsed.y; }, 0);
                        return "Total: " + t + " invoices";
                    } } }
                },
                scales: {
                    x: { stacked: true, grid: { display: false }, ticks: { color: tick, font: { size: 11 } } },
                    y: { stacked: true, beginAtZero: true, grid: { color: grid },
                         ticks: { color: tick, font: { size: 10 }, precision: 0 } }
                }
            }
        });
    }

    // Monthly sales + GST
    var c3 = document.getElementById("gd-ch-trend");
    if (c3) {
        destroy(c3);
        var tm = (d.matrix || {}).total_months || [];
        new Chart(c3, {
            type: "bar",
            data: {
                labels: tm.map(function (r) { return r.month; }),
                datasets: [
                    { label: "Net sales", type: "bar",
                      data: tm.map(function (r) { return r.sales; }),
                      backgroundColor: "#639922", borderRadius: 6,
                      borderSkipped: false, order: 2 },
                    { label: "Total GST", type: "line",
                      data: tm.map(function (r) {
                          return r.cgst + r.sgst + r.igst + r.c_cgst + r.c_sgst + r.c_igst;
                      }),
                      borderColor: "#BA7517", backgroundColor: "rgba(186,117,23,.10)",
                      borderWidth: 2, pointRadius: 4, pointHoverRadius: 6,
                      tension: 0.35, fill: true, yAxisID: "y1", order: 1 }
                ]
            },
            options: {
                responsive: true, maintainAspectRatio: false,
                animation: { duration: 800 },
                interaction: { mode: "index", intersect: false },
                plugins: {
                    legend: { position: "bottom",
                              labels: { font: { size: 10 }, usePointStyle: true, padding: 10 } },
                    tooltip: { callbacks: { label: function (ctx) {
                        return " " + ctx.dataset.label + ": " + _gd_INR2(ctx.parsed.y);
                    } } }
                },
                scales: {
                    x: { grid: { display: false }, ticks: { color: tick, font: { size: 11 } } },
                    y: { beginAtZero: true, grid: { color: grid },
                         ticks: { color: tick, font: { size: 10 },
                                  callback: function (v) { return _gd_INR(v); } } },
                    y1: { position: "right", beginAtZero: true, grid: { display: false },
                          ticks: { color: tick, font: { size: 10 },
                                   callback: function (v) { return _gd_INR(v); } } }
                }
            }
        });
    }

    // Taxable value and tax by GST rate
    var c5 = document.getElementById("gd-ch-rate");
    if (c5) {
        destroy(c5);
        var rw = d.rate_wise || [];
        new Chart(c5, {
            type: "bar",
            data: {
                labels: rw.map(function (r) { return r.label; }),
                datasets: [
                    { label: "Taxable value",
                      data: rw.map(function (r) { return r.taxable; }),
                      backgroundColor: rw.map(function (_, i) {
                          return _GD_RATE_COLORS[i % _GD_RATE_COLORS.length];
                      }),
                      borderRadius: 6, borderSkipped: false },
                    { label: "Total tax",
                      data: rw.map(function (r) { return r.tax; }),
                      backgroundColor: "#cbd5e1",
                      borderRadius: 6, borderSkipped: false }
                ]
            },
            options: {
                indexAxis: "y",
                responsive: true, maintainAspectRatio: false,
                animation: { duration: 800 },
                interaction: { mode: "index", intersect: false },
                plugins: {
                    legend: { position: "bottom",
                              labels: { font: { size: 10 }, usePointStyle: true, padding: 10 } },
                    tooltip: { callbacks: {
                        label: function (ctx) {
                            return " " + ctx.dataset.label + ": " + _gd_INR2(ctx.parsed.x);
                        },
                        afterBody: function (items) {
                            var r = rw[items[0].dataIndex];
                            if (!r) return "";
                            return ["CGST: " + _gd_INR2(r.cgst),
                                    "SGST: " + _gd_INR2(r.sgst),
                                    "IGST: " + _gd_INR2(r.igst),
                                    "Invoices: " + _gd_num(r.inv_count)];
                        }
                    } }
                },
                scales: {
                    x: { beginAtZero: true, grid: { color: grid },
                         ticks: { color: tick, font: { size: 10 },
                                  callback: function (v) { return _gd_INR(v); } } },
                    y: { grid: { display: false }, ticks: { color: tick, font: { size: 11 } } }
                }
            }
        });
    }

    // Channel split
    var c4 = document.getElementById("gd-ch-channel");
    if (c4) {
        destroy(c4);
        var ch = d.channel || [];
        new Chart(c4, {
            type: "bar",
            data: {
                labels: ch.map(function (r) { return r.label; }),
                datasets: [
                    { label: "Online",  data: ch.map(function (r) { return r.Online || 0; }),
                      backgroundColor: "#378ADD", borderRadius: 5, borderSkipped: false },
                    { label: "Offline", data: ch.map(function (r) { return r.Offline || 0; }),
                      backgroundColor: "#639922", borderRadius: 5, borderSkipped: false }
                ]
            },
            options: {
                responsive: true, maintainAspectRatio: false,
                animation: { duration: 800 },
                interaction: { mode: "index", intersect: false },
                plugins: {
                    legend: { position: "bottom",
                              labels: { font: { size: 10 }, usePointStyle: true, padding: 10 } },
                    tooltip: { callbacks: {
                        label: function (ctx) {
                            return " " + ctx.dataset.label + ": " + _gd_INR2(ctx.parsed.y);
                        },
                        footer: function (items) {
                            var t = items.reduce(function (s, i) { return s + i.parsed.y; }, 0);
                            return "Total: " + _gd_INR2(t);
                        }
                    } }
                },
                scales: {
                    x: { grid: { display: false }, ticks: { color: tick, font: { size: 11 } } },
                    y: { beginAtZero: true, grid: { color: grid },
                         ticks: { color: tick, font: { size: 10 },
                                  callback: function (v) { return _gd_INR(v); } } }
                }
            }
        });
    }
}


// ================================================================
//  EXCEL EXPORT  —  accountant's column order, one sheet per view
// ================================================================
function _gd_export_excel() {
    var d = window._gd_data;
    if (!d || !(d.matrix.segments || []).length) {
        frappe.msgprint({ title: __("Nothing to export"),
                          message: __("Load a period with invoices first."),
                          indicator: "orange" });
        return;
    }
    frappe.show_alert({ message: __("Preparing Excel\u2026"), indicator: "blue" }, 2);

    _gd_sheetjs(function () {
        var header = ["Segment", "Month", "Sales", "CGST", "SGST", "IGST",
                      "Courier Charges", "Courier CGST", "Courier SGST",
                      "Courier IGST", "R/off", "Total"];
        var aoa = [header];

        function push(seg, r, monthLabel) {
            aoa.push([seg, monthLabel || r.month,
                      r.sales || 0, r.cgst || 0, r.sgst || 0, r.igst || 0,
                      r.courier || 0, r.c_cgst || 0, r.c_sgst || 0,
                      r.c_igst || 0, r.roff || 0, r.total || 0]);
        }

        d.matrix.segments.forEach(function (s) {
            s.months.forEach(function (r) { push(s.label, r); });
            push(s.label, s.subtotal, "Subtotal");
            aoa.push([]);
        });
        d.matrix.total_months.forEach(function (r) { push("Total", r); });
        push("Total", d.matrix.grand, "Grand total");

        var wb = XLSX.utils.book_new();
        var ws = XLSX.utils.aoa_to_sheet(aoa);
        ws["!cols"] = header.map(function (_, i) {
            var w = 10;
            aoa.forEach(function (row) {
                w = Math.max(w, String(row[i] === undefined ? "" : row[i]).length);
            });
            return { wch: Math.min(30, w + 2) };
        });
        XLSX.utils.book_append_sheet(wb, ws, "GST Summary");

        // Second sheet: rate-wise
        if ((d.rate_wise || []).length) {
            var r2 = [["Rate", "Taxable value", "CGST", "SGST", "IGST",
                       "Total tax", "Invoices", "Total"]];
            d.rate_wise.forEach(function (r) {
                r2.push([r.label, r.taxable, r.cgst, r.sgst, r.igst,
                         r.tax, r.inv_count, r.total]);
            });
            XLSX.utils.book_append_sheet(wb, XLSX.utils.aoa_to_sheet(r2), "Rate-wise");
        }

        var fname = "TAAL_GST_Summary_" + d.meta.from_date + "_to_" + d.meta.to_date + ".xlsx";
        XLSX.writeFile(wb, fname);
        frappe.show_alert({ message: __("Downloaded ") + fname, indicator: "green" }, 4);
    });
}

function _gd_sheetjs(cb) {
    if (window.XLSX) { cb(); return; }
    var s = document.createElement("script");
    s.src = "https://cdnjs.cloudflare.com/ajax/libs/xlsx/0.18.5/xlsx.full.min.js";
    s.onload = cb;
    s.onerror = function () {
        frappe.msgprint({ title: __("Export unavailable"),
                          message: __("The spreadsheet library could not be loaded."),
                          indicator: "red" });
    };
    document.head.appendChild(s);
}


// ================================================================
//  STYLES
// ================================================================
function _gd_styles() {
    if (document.getElementById("_gd_css")) return;
    var s = document.createElement("style");
    s.id = "_gd_css";
    s.textContent = ""
    + ".gd{padding:14px 20px 60px;max-width:1480px;margin:0 auto}"
    + ".gd-loading{text-align:center;padding:80px 20px;font-size:14px;color:#94a3b8}"
    + ".gd-empty{text-align:center;padding:26px 0;font-size:13px;color:#94a3b8;font-style:italic}"

    + ".gd-topbar{display:flex;align-items:center;justify-content:space-between;"
    +   "padding:14px 18px;background:#fff;border:0.5px solid #e2e8f0;border-radius:12px;"
    +   "margin-bottom:10px;flex-wrap:wrap;gap:12px}"
    + ".gd-ttl{font-size:16px;font-weight:500;color:#111;margin-bottom:2px}"
    + ".gd-sub{font-size:11px;color:#94a3b8}"
    + ".gd-controls{display:flex;align-items:center;gap:9px;flex-wrap:wrap}"
    + ".gd-sel{display:inline-flex;align-items:center;gap:7px;border:0.5px solid #cbd5e1;"
    +   "border-radius:8px;padding:4px 10px;font-size:12px;background:#fff}"
    + ".gd-sel span{color:#94a3b8;font-size:11px}"
    + ".gd-sel select{border:none;background:transparent;font-size:12px;font-weight:500;"
    +   "color:#111;outline:none;max-width:190px}"
    + ".gd-chk{display:inline-flex;align-items:center;gap:6px;border:0.5px solid #cbd5e1;"
    +   "border-radius:8px;padding:6px 11px;font-size:11.5px;color:#64748b;cursor:pointer}"
    + ".gd-chip{font-size:11px;font-weight:500;padding:3px 10px;border-radius:999px;"
    +   "border:0.5px solid}"
    + ".gd-chip-g{background:#EAF3DE;color:#27500A;border-color:#639922}"
    + ".gd-chip-b{background:#E6F1FB;color:#0C447C;border-color:#378ADD}"
    + ".gd-chip-r{background:#FCEBEB;color:#791F1F;border-color:#F09595}"

    + ".gd-period{background:#FFF7ED;border:1.5px solid #F59E0B;border-radius:10px;"
    +   "padding:9px 15px;margin-bottom:10px;display:flex;justify-content:space-between;"
    +   "flex-wrap:wrap;gap:8px;align-items:center}"
    + ".gd-period-l{font-size:13px;font-weight:600;color:#633806}"
    + ".gd-period-r{font-size:11px;color:#92400e}"
    + ".gd-period-note{font-size:11px;color:#92400e;font-weight:500}"
    + ".gd-notice{background:#E6F1FB;border:0.5px solid #378ADD;color:#0C447C;"
    +   "border-radius:8px;padding:7px 14px;margin-bottom:16px;font-size:11px}"
    + ".gd-inline-note{font-size:10.5px;color:#92400e;background:#FFFBEB;"
    +   "border:0.5px solid #FCD9A0;border-radius:6px;padding:6px 10px;margin-bottom:10px}"

    + ".gd-sh{display:flex;align-items:center;gap:8px;margin:24px 0 10px}"
    + ".gd-sl{font-size:10px;font-weight:500;text-transform:uppercase;letter-spacing:.08em;"
    +   "color:#94a3b8;white-space:nowrap}"
    + ".gd-sline{flex:1;height:0.5px;background:#e2e8f0}"
    + ".gd-ptag{display:inline-block;background:#EAF3DE;color:#27500A;"
    +   "border:0.5px solid #639922;border-radius:4px;padding:1px 7px;font-size:9px;"
    +   "font-weight:600;letter-spacing:.05em;margin-left:4px}"
    + ".gd-subhd{font-size:10px;font-weight:500;text-transform:uppercase;"
    +   "letter-spacing:.06em;color:#94a3b8;margin:16px 0 6px;padding-top:12px;"
    +   "border-top:0.5px solid #e2e8f0}"

    + ".gd-g4{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px;"
    +   "margin-bottom:10px;position:relative}"
    + ".gd-g3{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px}"
    + ".gd-g2{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px;margin-bottom:12px}"

    + ".gd-kpi{background:#fff;border:0.5px solid #e2e8f0;border-radius:12px;"
    +   "padding:15px 16px;position:relative;cursor:pointer;"
    +   "transition:transform .18s,box-shadow .18s,border-color .2s}"
    + ".gd-kpi::before{content:'';position:absolute;top:0;left:0;right:0;height:3px;"
    +   "background:var(--gd-a,#378ADD);border-radius:12px 12px 0 0}"
    + ".gd-kpi:hover{transform:translateY(-3px);box-shadow:0 8px 22px rgba(0,0,0,.10);"
    +   "border-color:#cbd5e1}"
    + ".gd-kpi:focus-visible{outline:2px solid var(--gd-a,#378ADD);outline-offset:2px}"
    + ".gd-kpi:hover{z-index:300}"
    + ".gd-nolink{cursor:default}"
    + ".gd-nolink:hover{transform:translateY(-3px)}"
    + ".gd-sel.gd-date input{border:none;background:transparent;font-size:12px;"
    +   "font-weight:500;color:#111;outline:none;font-family:inherit;"
    +   "padding:0;max-width:132px}"
    + ".gd-kl{font-size:11px;font-weight:500;color:#64748b;margin-bottom:4px}"
    + ".gd-kv{font-size:25px;font-weight:500;line-height:1;margin-bottom:5px;"
    +   "font-variant-numeric:tabular-nums}"
    + ".gd-knote{font-size:9.5px;color:#94a3b8;margin-bottom:8px;min-height:12px}"
    + ".gd-bar{height:3px;border-radius:999px;background:#eef2f6;overflow:hidden}"
    + ".gd-barf{height:100%;border-radius:999px;width:0;transition:width 1.1s ease}"

    + ".gd-pop{position:absolute;left:12px;right:12px;top:calc(100% - 6px);z-index:40;"
    +   "background:#fff;border:0.5px solid #cbd5e1;border-radius:10px;padding:9px 12px;"
    +   "box-shadow:0 8px 26px rgba(0,0,0,.15);opacity:0;visibility:hidden;"
    +   "transform:translateY(-4px);transition:opacity .13s ease,transform .13s ease,"
    +   "visibility .13s;pointer-events:none}"
    + ".gd-kpi:hover .gd-pop,.gd-kpi:focus-visible .gd-pop{opacity:1;visibility:visible;"
    +   "transform:translateY(0)}"
    + ".gd-pr{display:flex;justify-content:space-between;align-items:center;padding:4px 0;"
    +   "font-size:11px}"
    + ".gd-pr + .gd-pr{border-top:0.5px solid #eef2f6}"
    + ".gd-pl{color:#64748b}"
    + ".gd-pv{font-weight:500;font-variant-numeric:tabular-nums;color:#111}"

    + ".gd-card{background:#fff;border:0.5px solid #e2e8f0;border-radius:12px;"
    +   "padding:16px 18px;margin-bottom:12px;transition:box-shadow .18s}"
    + ".gd-card:hover{box-shadow:0 4px 14px rgba(0,0,0,.06)}"
    + ".gd-card-hd{display:flex;justify-content:space-between;align-items:center;"
    +   "flex-wrap:wrap;gap:8px;margin-bottom:12px}"
    + ".gd-ct{font-size:13px;font-weight:500;color:#111;margin-bottom:2px}"
    + ".gd-cs{font-size:11px;color:#94a3b8;margin-bottom:12px}"

    + ".gd-seg-toggle{display:flex;background:#f1f5f9;border-radius:9px;padding:3px}"
    + ".gd-tb{padding:5px 13px;border-radius:7px;border:none;background:transparent;"
    +   "font-size:11.5px;font-weight:500;color:#64748b;cursor:pointer}"
    + ".gd-tb.on{background:#378ADD;color:#fff}"

    + ".gd-scroll{overflow-x:auto}"
    + ".gd-mx{width:100%;border-collapse:collapse;font-size:11px;"
    +   "font-variant-numeric:tabular-nums;min-width:940px}"
    + ".gd-mx th{padding:6px 8px;font-size:9.5px;font-weight:500;color:#94a3b8;"
    +   "text-transform:uppercase;letter-spacing:.03em;text-align:right;"
    +   "white-space:nowrap;border-bottom:0.5px solid #e2e8f0}"
    + ".gd-mx th.gd-l{text-align:left}"
    + ".gd-mx td{padding:5px 8px;text-align:right;border-bottom:0.5px solid #eef2f6;"
    +   "white-space:nowrap}"
    + ".gd-mx td.gd-l{text-align:left;color:#64748b}"
    + ".gd-r{cursor:pointer;transition:background .12s}"
    + ".gd-mx tr.gd-r:hover td{background:#f8fafc}"
    + ".gd-blk td{padding:6px 9px;font-weight:500;font-size:11px;text-align:left;"
    +   "border-bottom:none}"
    + ".gd-sub td{font-weight:500;background:#EAF3DE;color:#27500A}"
    + ".gd-z{color:#cbd5e1}"
    + ".gd-up{color:#27500A;font-weight:500}"
    + ".gd-dn{color:#791F1F;font-weight:500}"
    + ".gd-mini{font-size:9px;color:#94a3b8;background:#f1f5f9;border-radius:3px;"
    +   "padding:1px 5px;margin-left:4px}"

    + ".gd-stc{background:#f8fafc;border-radius:10px;padding:12px 14px;"
    +   "transition:transform .15s}"
    + ".gd-stc:hover{transform:translateY(-2px)}"
    + ".gd-stl{font-size:10px;font-weight:500;text-transform:uppercase;"
    +   "letter-spacing:.05em;color:#94a3b8;margin-bottom:5px}"
    + ".gd-stv{font-size:20px;font-weight:500;margin-bottom:2px;"
    +   "font-variant-numeric:tabular-nums}"
    + ".gd-sts{font-size:10px;color:#94a3b8}"

    + ".gd-mbar{display:flex;align-items:center;gap:8px;margin-bottom:7px;position:relative}"
    + ".gd-tip{position:absolute;left:56px;bottom:calc(100% + 5px);z-index:120;"
    +   "background:#1f2937;color:#fff;border-radius:8px;padding:8px 11px;"
    +   "font-size:10.5px;white-space:nowrap;opacity:0;visibility:hidden;"
    +   "transform:translateY(3px);pointer-events:none;"
    +   "box-shadow:0 6px 20px rgba(0,0,0,.24);"
    +   "transition:opacity .13s ease,transform .13s ease,visibility .13s}"
    + ".gd-mbar:hover .gd-tip{opacity:1;visibility:visible;transform:translateY(0)}"
    + ".gd-mbar:hover .gd-mfill{filter:brightness(1.08)}"
    + ".gd-tip-r{display:flex;justify-content:space-between;gap:16px;padding:2px 0}"
    + ".gd-tip-r span{color:#cbd5e1}"
    + ".gd-tip-r b{font-weight:600;font-variant-numeric:tabular-nums}"
    + ".gd-mbar b{font-size:10px;color:#94a3b8;width:56px;font-weight:400;"
    +   "overflow:hidden;text-overflow:ellipsis;white-space:nowrap}"
    + ".gd-mtrack{flex:1;height:14px;border-radius:4px;background:#f1f5f9;"
    +   "display:flex;overflow:hidden}"
    + ".gd-mfill{height:100%;width:0;transition:width 1s ease}"
    + ".gd-mval{font-size:10px;color:#64748b;width:84px;text-align:right;"
    +   "font-variant-numeric:tabular-nums}"
    + ".gd-legend{display:flex;gap:14px;flex-wrap:wrap;margin-top:10px}"
    + ".gd-legend span{display:flex;align-items:center;gap:5px;font-size:10px;color:#64748b}"
    + ".gd-dot{width:9px;height:9px;border-radius:2px;flex-shrink:0}"
    + ".gd-leg{display:flex;justify-content:space-between;align-items:center;"
    +   "margin-bottom:6px;font-size:11px}"
    + ".gd-leg-l{display:flex;align-items:center;gap:7px;color:#64748b}"
    + ".gd-leg b{font-weight:500;color:#111}"

    + ".gd-ex{display:flex;align-items:center;gap:11px;padding:9px 11px;"
    +   "border-radius:8px;margin-bottom:7px;cursor:pointer;transition:transform .15s}"
    + ".gd-ex:hover{transform:translateX(3px)}"
    + ".gd-exn{font-size:17px;font-weight:500;min-width:30px;text-align:center;"
    +   "font-variant-numeric:tabular-nums}"
    + ".gd-ext{flex:1;min-width:0}"
    + ".gd-exl{font-size:11.5px;font-weight:500;display:block}"
    + ".gd-exd{font-size:10px;color:#94a3b8;margin-top:1px;display:block;"
    +   "overflow:hidden;text-overflow:ellipsis}"
    + ".gd-fix{font-size:10px;font-weight:500;background:#fff;border-radius:6px;"
    +   "padding:3px 9px;white-space:nowrap}"

    + ".gd-feed{display:grid;grid-template-columns:1.2fr 1.4fr .8fr .8fr 1.1fr .9fr .9fr;"
    +   "gap:8px;padding:7px 0;border-bottom:0.5px solid #eef2f6;align-items:center;"
    +   "font-size:11.5px}"
    + ".gd-fhd span,.gd-nhd span{font-size:9.5px;color:#94a3b8;text-transform:uppercase;"
    +   "letter-spacing:.04em}"
    + ".gd-nfeed{display:grid;grid-template-columns:1.2fr 1.2fr 1.3fr .8fr .9fr .9fr;"
    +   "gap:8px;padding:7px 0;border-bottom:0.5px solid #eef2f6;align-items:center;"
    +   "font-size:11.5px}"
    + ".gd-link{color:#0C447C;font-weight:500;overflow:hidden;text-overflow:ellipsis;"
    +   "white-space:nowrap}"
    + ".gd-muted{color:#94a3b8;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}"
    + ".gd-mo-hd{font-size:10px;font-weight:500;color:#94a3b8;text-transform:uppercase;"
    +   "letter-spacing:.06em;padding:11px 0 3px}"
    + ".gd-bdg{font-size:9.5px;font-weight:500;padding:1px 8px;border-radius:999px;"
    +   "display:inline-block}"
    + ".gd-bdg-g{background:#EAF3DE;color:#27500A}"
    + ".gd-bdg-a{background:#FAEEDA;color:#633806}"
    + ".gd-bdg-r{background:#FCEBEB;color:#791F1F}"
    + ".gd-bdg-p{background:#EEEDFE;color:#3C3489}"
    + ".gd-bdg-s{background:#F1EFE8;color:#444441}"

    + "@keyframes _gdIn{from{opacity:0;transform:translateY(9px)}"
    +   "to{opacity:1;transform:translateY(0)}}"
    + ".gd-kpi{animation:_gdIn .38s ease both}"
    + ".gd-g4 .gd-kpi:nth-child(1){animation-delay:.03s}"
    + ".gd-g4 .gd-kpi:nth-child(2){animation-delay:.07s}"
    + ".gd-g4 .gd-kpi:nth-child(3){animation-delay:.11s}"
    + ".gd-g4 .gd-kpi:nth-child(4){animation-delay:.15s}"
    + ".gd-card{animation:_gdIn .38s .2s ease both}"

    + "@media (max-width:1180px){.gd-g4{grid-template-columns:repeat(2,minmax(0,1fr))}"
    +   ".gd-g2,.gd-g3{grid-template-columns:1fr}}"
    + "@media (max-width:640px){.gd-g4{grid-template-columns:1fr}"
    +   ".gd-feed,.gd-nfeed{grid-template-columns:1fr 1fr;row-gap:2px}}"
    + "@media (prefers-reduced-motion:reduce){.gd-kpi,.gd-card{animation:none}"
    +   ".gd-barf,.gd-mfill{transition:none}"
    +   ".gd-kpi:hover,.gd-stc:hover,.gd-ex:hover{transform:none}}";
    document.head.appendChild(s);
}