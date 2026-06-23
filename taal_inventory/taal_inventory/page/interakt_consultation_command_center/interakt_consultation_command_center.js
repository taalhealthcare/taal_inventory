// =============================================================================
// CONSULTATION COMMAND CENTER — Dashboard Page
// Location: taal_inventory/taal_inventory/page/interakt_consultation_command_center/
//           interakt_consultation_command_center.js
//
// Single page combining:
//   • Section A: Live Operations Hub (auto-refresh every 30 seconds)
//   • Section B: Patient Journey Analytics (period-filterable)
// =============================================================================

frappe.pages["interakt-consultation-command-center"].on_page_load = function (wrapper) {
  const page = frappe.ui.make_app_page({
    parent: wrapper,
    title: "Interakt — Consultation Command Center",
    single_column: true,
  });

  // Inject our custom dashboard markup
  $(page.body).html(getDashboardHTML());

  // Load Chart.js then initialize
  loadChartJs()
    .then(() => {
      initLiveSection();
      initAnalyticsSection();
      startLivePolling();
    })
    .catch((err) => {
      console.error("Chart.js failed to load", err);
      frappe.msgprint(__("Failed to load chart library. Please refresh."));
    });
};


// ─── Markup ──────────────────────────────────────────────────────────────────

function getDashboardHTML() {
  return `
<style>
  .ccc-container { padding: 16px; max-width: 1400px; margin: 0 auto; font-family: var(--font-stack); }
  .ccc-section-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; }
  .ccc-section-title { font-size: 16px; font-weight: 500; color: var(--text-color); }
  .ccc-section-subtitle { font-size: 12px; color: var(--text-muted); margin-top: 2px; }
  .ccc-live-dot { width: 8px; height: 8px; border-radius: 50%; background: #1D9E75; animation: ccc-pulse 2s infinite; }
  @keyframes ccc-pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.4; } }

  .ccc-kpi-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin-bottom: 16px; }
  .ccc-kpi-card { background: var(--card-bg); border: 0.5px solid var(--border-color); border-radius: 8px; padding: 14px; cursor: pointer; transition: transform 0.18s ease, box-shadow 0.18s ease; }
  .ccc-kpi-card:hover { transform: translateY(-2px); box-shadow: 0 2px 8px rgba(0,0,0,0.06); }
  .ccc-kpi-label { font-size: 11px; color: var(--text-muted); margin-bottom: 6px; text-transform: uppercase; letter-spacing: 0.5px; }
  .ccc-kpi-value { font-size: 22px; font-weight: 500; color: var(--text-color); }
  .ccc-kpi-change { font-size: 11px; margin-top: 4px; }
  .ccc-kpi-change.up { color: #0F6E56; }
  .ccc-kpi-change.down { color: #A32D2D; }
  .ccc-kpi-change.neutral { color: var(--text-muted); }

  .ccc-row { display: grid; gap: 12px; margin-bottom: 16px; }
  .ccc-row-2 { grid-template-columns: 1fr 1fr; }
  .ccc-row-3 { grid-template-columns: 1fr 1fr 1fr; }

  .ccc-panel { background: var(--card-bg); border: 0.5px solid var(--border-color); border-radius: 12px; padding: 16px; }
  .ccc-panel-title { font-size: 13px; font-weight: 500; color: var(--text-color); margin-bottom: 12px; }

  .ccc-activity-row { display: flex; gap: 10px; padding: 8px 10px; border-radius: 6px; transition: background 0.15s ease; align-items: flex-start; }
  .ccc-activity-row:hover { background: var(--bg-color); }
  .ccc-activity-icon { width: 24px; height: 24px; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-size: 12px; flex-shrink: 0; margin-top: 2px; }

  .ccc-divider { display: flex; align-items: center; gap: 16px; margin: 32px 0 20px; }
  .ccc-divider-line { flex: 1; height: 1px; background: linear-gradient(to right, transparent, var(--border-color), transparent); }
  .ccc-divider-label { font-size: 11px; color: var(--text-muted); letter-spacing: 3px; text-transform: uppercase; font-weight: 500; }

  .ccc-period-toggle { display: inline-flex; gap: 2px; padding: 3px; background: var(--bg-color); border-radius: 6px; }
  .ccc-period-btn { padding: 5px 12px; border: none; background: transparent; border-radius: 4px; cursor: pointer; font-size: 12px; color: var(--text-muted); transition: all 0.15s ease; }
  .ccc-period-btn.active { background: var(--card-bg); color: var(--text-color); font-weight: 500; box-shadow: 0 1px 2px rgba(0,0,0,0.05); }

  .ccc-health-strip { background: var(--bg-color); border-radius: 12px; padding: 12px 16px; display: grid; grid-template-columns: repeat(5, 1fr); gap: 12px; }
  .ccc-health-item { display: flex; align-items: center; gap: 8px; font-size: 12px; }
  .ccc-health-dot { width: 6px; height: 6px; border-radius: 50%; }

  .ccc-heatmap-grid { display: grid; gap: 2px; }
  .ccc-heatmap-cell { height: 20px; border-radius: 3px; cursor: pointer; transition: transform 0.15s ease, outline 0.15s ease; }
  .ccc-heatmap-cell:hover { transform: scale(1.18); outline: 1.5px solid var(--text-color); position: relative; z-index: 1; }

  .ccc-bar-row { margin-bottom: 10px; }
  .ccc-bar-track { height: 5px; background: var(--bg-color); border-radius: 3px; overflow: hidden; }
  .ccc-bar-fill { height: 100%; background: #1D9E75; transition: width 1.2s cubic-bezier(0.34, 1.56, 0.64, 1); width: 0; }

  @media (max-width: 900px) {
    .ccc-kpi-grid, .ccc-row-3 { grid-template-columns: repeat(2, 1fr); }
    .ccc-row-2 { grid-template-columns: 1fr; }
    .ccc-health-strip { grid-template-columns: repeat(2, 1fr); }
  }
</style>

<div class="ccc-container">

  <!-- ═══════════ SECTION A: LIVE OPERATIONS HUB ═══════════ -->
  <div class="ccc-section-header">
    <div>
      <div class="ccc-section-title">Interakt Live Operations Hub</div>
      <div class="ccc-section-subtitle">Bookings from Interakt · auto-refresh every 30s · <span id="ccc-last-refresh">just now</span></div>
    </div>
    <div style="display:flex;gap:6px;align-items:center;">
      <span class="ccc-live-dot"></span>
      <span style="font-size:12px;color:var(--text-muted);">Live</span>
    </div>
  </div>

  <div class="ccc-kpi-grid" id="ccc-hero-kpis">
    <div class="ccc-kpi-card"><div class="ccc-kpi-label">Today's Revenue</div><div class="ccc-kpi-value" data-key="today_revenue">—</div><div class="ccc-kpi-change neutral" data-key="today_revenue_change">—</div></div>
    <div class="ccc-kpi-card"><div class="ccc-kpi-label">Today's Bookings</div><div class="ccc-kpi-value" data-key="today_bookings">—</div><div class="ccc-kpi-change neutral" data-key="today_bookings_change">—</div></div>
    <div class="ccc-kpi-card"><div class="ccc-kpi-label">Conversion (7d)</div><div class="ccc-kpi-value" data-key="week_conversion">—</div><div class="ccc-kpi-change neutral">Paid / Total</div></div>
    <div class="ccc-kpi-card"><div class="ccc-kpi-label">Active Doctors</div><div class="ccc-kpi-value" data-key="active_doctors">—</div><div class="ccc-kpi-change up"><i class="fa fa-check"></i> All available</div></div>
  </div>

  <div class="ccc-row ccc-row-2">
    <div class="ccc-panel">
      <div class="ccc-panel-title">Booking Conversion Funnel · Last 7 days</div>
      <div style="position:relative;height:240px;"><canvas id="ccc-funnel"></canvas></div>
    </div>
    <div class="ccc-panel">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">
        <div class="ccc-panel-title" style="margin-bottom:0;">Live Activity Feed</div>
        <span style="font-size:11px;color:var(--text-muted);">Last 10</span>
      </div>
      <div id="ccc-activity" style="max-height:260px;overflow-y:auto;display:flex;flex-direction:column;gap:4px;"></div>
    </div>
  </div>

  <div class="ccc-row ccc-row-3">
    <div class="ccc-panel">
      <div class="ccc-panel-title">Doctor Distribution</div>
      <div style="position:relative;height:180px;"><canvas id="ccc-doctors"></canvas></div>
    </div>
    <div class="ccc-panel">
      <div class="ccc-panel-title">Speciality Demand</div>
      <div style="position:relative;height:180px;"><canvas id="ccc-specialities"></canvas></div>
    </div>
    <div class="ccc-panel">
      <div class="ccc-panel-title">Time Slot Demand</div>
      <div style="position:relative;height:180px;"><canvas id="ccc-timeslots"></canvas></div>
    </div>
  </div>

  <div class="ccc-health-strip" id="ccc-health"></div>


  <!-- ═══════════ DIVIDER ═══════════ -->
  <div class="ccc-divider">
    <div class="ccc-divider-line"></div>
    <span class="ccc-divider-label">Strategic Analytics</span>
    <div class="ccc-divider-line"></div>
  </div>


  <!-- ═══════════ SECTION B: PATIENT JOURNEY ANALYTICS ═══════════ -->
  <div class="ccc-section-header">
    <div>
      <div class="ccc-section-title">Interakt Patient Journey Analytics</div>
      <div class="ccc-section-subtitle">Strategic review · period: <span id="ccc-period-label">Last 30 days</span></div>
    </div>
    <div class="ccc-period-toggle">
      <button class="ccc-period-btn" data-period="week">Week</button>
      <button class="ccc-period-btn active" data-period="month">Month</button>
      <button class="ccc-period-btn" data-period="quarter">Quarter</button>
    </div>
  </div>

  <div class="ccc-kpi-grid" id="ccc-acquisition-kpis">
    <div class="ccc-kpi-card"><div class="ccc-kpi-label">New Patients</div><div class="ccc-kpi-value" data-key="new_patients">—</div></div>
    <div class="ccc-kpi-card"><div class="ccc-kpi-label">Repeat Patients</div><div class="ccc-kpi-value" data-key="repeat_patients">—</div></div>
    <div class="ccc-kpi-card"><div class="ccc-kpi-label">Avg Time→Pay</div><div class="ccc-kpi-value" data-key="avg_time_to_pay">—</div></div>
    <div class="ccc-kpi-card"><div class="ccc-kpi-label">Revenue / Patient</div><div class="ccc-kpi-value" data-key="revenue_per_patient">—</div></div>
  </div>

  <div class="ccc-panel" style="margin-bottom:16px;">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">
      <div class="ccc-panel-title" style="margin-bottom:0;">Time Slot × Day Heatmap</div>
      <div style="display:flex;gap:6px;align-items:center;font-size:10px;color:var(--text-muted);">
        <span>Low</span>
        <div style="display:flex;gap:1px;">
          <span style="width:14px;height:10px;background:#E1F5EE;"></span>
          <span style="width:14px;height:10px;background:#9FE1CB;"></span>
          <span style="width:14px;height:10px;background:#5DCAA5;"></span>
          <span style="width:14px;height:10px;background:#1D9E75;"></span>
          <span style="width:14px;height:10px;background:#0F6E56;"></span>
          <span style="width:14px;height:10px;background:#04342C;"></span>
        </div>
        <span>High</span>
      </div>
    </div>
    <div id="ccc-heatmap"></div>
  </div>

  <div class="ccc-row ccc-row-2">
    <div class="ccc-panel">
      <div class="ccc-panel-title">Revenue Trend</div>
      <div style="position:relative;height:200px;"><canvas id="ccc-revenue"></canvas></div>
    </div>
    <div class="ccc-panel">
      <div class="ccc-panel-title">Top Cities</div>
      <div id="ccc-cities"></div>
    </div>
  </div>

  <div class="ccc-panel">
    <div class="ccc-panel-title">Drop-off Causes</div>
    <div class="ccc-row ccc-row-3" id="ccc-dropoff" style="margin-bottom:0;"></div>
  </div>

</div>
`;
}


// ─── Section A: Live Operations ──────────────────────────────────────────────

let ccc_charts = {};
let ccc_polling_timer = null;

function initLiveSection() {
  fetchLiveData();
}

async function fetchLiveData() {
  try {
    const r = await frappe.call({
      method: "taal_inventory.api.interakt_consultation_analytics.get_dashboard_live_data",
    });
    if (r.message) renderLiveSection(r.message);
  } catch (e) {
    console.error("Live data fetch failed", e);
  }
}

function startLivePolling() {
  if (ccc_polling_timer) clearInterval(ccc_polling_timer);
  ccc_polling_timer = setInterval(fetchLiveData, 30000);
}

function renderLiveSection(d) {
  // Hero KPIs with animated count-up
  const hero = d.hero_kpis;
  animateNumber(
    document.querySelector('[data-key="today_revenue"]'),
    hero.today_revenue,
    (v) => "₹" + Math.round(v).toLocaleString("en-IN")
  );
  const changeEl = document.querySelector('[data-key="today_revenue_change"]');
  if (hero.today_revenue_change_pct !== 0) {
    const up = hero.today_revenue_change_pct > 0;
    changeEl.className = "ccc-kpi-change " + (up ? "up" : "down");
    changeEl.innerHTML =
      '<i class="fa fa-arrow-' + (up ? "up" : "down") + '"></i> ' +
      Math.abs(hero.today_revenue_change_pct) + "% vs yesterday";
  } else {
    changeEl.textContent = "No change vs yesterday";
  }

  animateNumber(document.querySelector('[data-key="today_bookings"]'), hero.today_bookings);
  const bookingsChange = document.querySelector('[data-key="today_bookings_change"]');
  bookingsChange.textContent = (hero.today_bookings_change >= 0 ? "+" : "") + hero.today_bookings_change + " vs yesterday";
  bookingsChange.className = "ccc-kpi-change " + (hero.today_bookings_change > 0 ? "up" : hero.today_bookings_change < 0 ? "down" : "neutral");

  animateNumber(document.querySelector('[data-key="week_conversion"]'), hero.week_conversion, (v) => v.toFixed(1) + "%");
  document.querySelector('[data-key="active_doctors"]').textContent = hero.active_doctors + " / " + hero.active_doctors;

  // Activity feed
  renderActivityFeed(d.activity);

  // Charts
  renderFunnelChart(d.funnel);
  renderDoctorChart(d.doctor_distribution);
  renderSpecialityChart(d.speciality_distribution);
  renderTimeslotChart(d.timeslot_demand);

  // Health strip
  renderHealthStrip(d.system_health);

  // Last refreshed indicator
  document.getElementById("ccc-last-refresh").textContent = d.refreshed_at;
}

function renderActivityFeed(activity) {
  const container = document.getElementById("ccc-activity");
  if (!activity || !activity.length) {
    container.innerHTML = '<div style="font-size:12px;color:var(--text-muted);text-align:center;padding:20px;">No recent activity</div>';
    return;
  }
  container.innerHTML = activity
    .map(
      (a, idx) => `
    <div class="ccc-activity-row" style="animation:ccc-slidein 0.4s ease-out ${idx * 0.05}s both;">
      <div class="ccc-activity-icon" style="background:${a.status_color}20;color:${a.status_color};"><i class="fa fa-${iconMap(a.icon)}"></i></div>
      <div style="flex:1;min-width:0;">
        <div style="font-size:12px;color:var(--text-color);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">
          ${escape_html(a.patient_name)} · ${escape_html(a.speciality)}${a.amount ? " · ₹" + a.amount : ""}
        </div>
        <div style="font-size:10px;color:var(--text-muted);">${a.status_label} · ${a.time_ago}</div>
      </div>
    </div>
  `
    )
    .join("");

  if (!document.getElementById("ccc-slidein-style")) {
    const style = document.createElement("style");
    style.id = "ccc-slidein-style";
    style.textContent = "@keyframes ccc-slidein { from { opacity:0; transform: translateX(-10px); } to { opacity: 1; transform: translateX(0); } }";
    document.head.appendChild(style);
  }
}

function iconMap(tiName) {
  const map = { "ti-check": "check", "ti-credit-card": "credit-card", "ti-x": "times", "ti-currency-rupee": "inr", "ti-video": "video-camera", "ti-plus": "plus" };
  return map[tiName] || "circle";
}

function renderFunnelChart(funnel) {
  destroyChart("funnel");
  const labels = ["Submitted", "Booking", "Link Sent", "Paid", "Appointment", "Meet", "Notified"];
  const data = [funnel.submitted, funnel.booking, funnel.link_sent, funnel.paid, funnel.appointment, funnel.meet, funnel.notified];
  ccc_charts.funnel = new Chart(document.getElementById("ccc-funnel"), {
    type: "bar",
    data: { labels: labels, datasets: [{ data: data, backgroundColor: ["#1D9E75", "#1D9E75", "#1D9E75", "#0F6E56", "#0F6E56", "#0F6E56", "#085041"], borderRadius: 4 }] },
    options: {
      indexAxis: "y", responsive: true, maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: { x: { display: false, beginAtZero: true }, y: { grid: { display: false } } },
    },
  });
}

function renderDoctorChart(dist) {
  destroyChart("doctor");
  if (!dist.length) return;
  ccc_charts.doctor = new Chart(document.getElementById("ccc-doctors"), {
    type: "doughnut",
    data: { labels: dist.map((d) => d.label), datasets: [{ data: dist.map((d) => d.value), backgroundColor: ["#1D9E75", "#0F6E56", "#085041", "#5DCAA5", "#9FE1CB", "#378ADD", "#185FA5"], borderWidth: 1 }] },
    options: { responsive: true, maintainAspectRatio: false, cutout: "60%", plugins: { legend: { display: false } } },
  });
}

function renderSpecialityChart(dist) {
  destroyChart("speciality");
  if (!dist.length) return;
  ccc_charts.speciality = new Chart(document.getElementById("ccc-specialities"), {
    type: "pie",
    data: { labels: dist.map((d) => d.label), datasets: [{ data: dist.map((d) => d.value), backgroundColor: ["#1D9E75", "#0F6E56", "#5DCAA5", "#9FE1CB", "#378ADD", "#534AB7", "#D85A30"], borderWidth: 1 }] },
    options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } } },
  });
}

function renderTimeslotChart(dist) {
  destroyChart("timeslot");
  if (!dist.length) return;
  ccc_charts.timeslot = new Chart(document.getElementById("ccc-timeslots"), {
    type: "bar",
    data: { labels: dist.map((d) => d.label), datasets: [{ data: dist.map((d) => d.value), backgroundColor: "#1D9E75", borderRadius: 3 }] },
    options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } }, scales: { x: { grid: { display: false } }, y: { display: false, beginAtZero: true } } },
  });
}

function renderHealthStrip(h) {
  const items = [
    { label: "Razorpay", value: h.razorpay_last_min !== null ? h.razorpay_last_min + "m ago" : "—", ok: h.razorpay_last_min !== null && h.razorpay_last_min < 60 },
    { label: "Interakt", value: h.interakt_last_min !== null ? h.interakt_last_min + "m ago" : "—", ok: h.interakt_last_min !== null && h.interakt_last_min < 60 },
    { label: "Google Meet", value: h.google_meet, ok: h.google_meet === "OK" },
    { label: "WhatsApp", value: h.whatsapp_rate + "%", ok: h.whatsapp_rate >= 90 },
    { label: "Errors 24h", value: h.errors_24h, ok: h.errors_24h < 10 },
  ];
  document.getElementById("ccc-health").innerHTML = items.map((i) => `
    <div class="ccc-health-item">
      <span class="ccc-health-dot" style="background:${i.ok ? "#1D9E75" : "#BA7517"};"></span>
      <span style="color:var(--text-color);">${i.label} · ${i.value}</span>
    </div>
  `).join("");
}


// ─── Section B: Strategic Analytics ──────────────────────────────────────────

function initAnalyticsSection() {
  document.querySelectorAll(".ccc-period-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".ccc-period-btn").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      const period = btn.dataset.period;
      const labels = { week: "Last 7 days", month: "Last 30 days", quarter: "Last 90 days" };
      document.getElementById("ccc-period-label").textContent = labels[period];
      fetchAnalyticsData(period);
    });
  });
  fetchAnalyticsData("month");
}

async function fetchAnalyticsData(period) {
  try {
    const r = await frappe.call({
      method: "taal_inventory.api.interakt_consultation_analytics.get_dashboard_analytics_data",
      args: { period: period },
    });
    if (r.message) renderAnalyticsSection(r.message);
  } catch (e) {
    console.error("Analytics fetch failed", e);
  }
}

function renderAnalyticsSection(d) {
  // KPI cards
  const a = d.acquisition_kpis;
  animateNumber(document.querySelector('[data-key="new_patients"]'), a.new_patients);
  animateNumber(document.querySelector('[data-key="repeat_patients"]'), a.repeat_patients);
  document.querySelector('[data-key="avg_time_to_pay"]').textContent = a.avg_time_to_pay;
  animateNumber(document.querySelector('[data-key="revenue_per_patient"]'), a.revenue_per_patient, (v) => "₹" + Math.round(v).toLocaleString("en-IN"));

  // Heatmap
  renderHeatmap(d.heatmap);

  // Revenue trend
  renderRevenueChart(d.revenue_trend);

  // Top cities
  renderTopCities(d.top_cities);

  // Drop-off
  renderDropoff(d.dropoff);
}

function renderHeatmap(hm) {
  const colors = ["#E1F5EE", "#9FE1CB", "#5DCAA5", "#1D9E75", "#0F6E56", "#04342C"];
  const maxCount = Math.max(...hm.data.flatMap((r) => r.cells.map((c) => c.count)), 1);

  let html = '<div class="ccc-heatmap-grid" style="grid-template-columns: 70px repeat(' + hm.days.length + ', 1fr);">';
  html += "<div></div>";
  hm.days.forEach((d) => {
    html += '<div style="font-size:10px;color:var(--text-muted);text-align:center;padding-bottom:6px;">' + d + "</div>";
  });
  hm.data.forEach((row) => {
    html += '<div style="font-size:10px;color:var(--text-muted);display:flex;align-items:center;">' + row.slot + "</div>";
    row.cells.forEach((cell) => {
      const intensity = Math.min(5, Math.floor((cell.count / maxCount) * 5));
      html += '<div class="ccc-heatmap-cell" title="' + row.slot + " " + cell.day + ": " + cell.count + ' bookings" style="background:' + colors[intensity] + ';"></div>';
    });
  });
  html += "</div>";
  document.getElementById("ccc-heatmap").innerHTML = html;
}

function renderRevenueChart(trend) {
  destroyChart("revenue");
  ccc_charts.revenue = new Chart(document.getElementById("ccc-revenue"), {
    type: "line",
    data: {
      labels: trend.map((t) => t.date),
      datasets: [{ data: trend.map((t) => t.revenue), borderColor: "#1D9E75", backgroundColor: "rgba(29, 158, 117, 0.08)", fill: true, tension: 0.3, pointRadius: 0, borderWidth: 1.5 }],
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { display: false }, tooltip: { callbacks: { label: (c) => "₹" + c.parsed.y.toLocaleString("en-IN") } } },
      scales: { x: { display: false }, y: { ticks: { callback: (v) => "₹" + Math.round(v / 1000) + "k", font: { size: 10 } }, grid: { display: false } } },
    },
  });
}

function renderTopCities(cities) {
  if (!cities.length) {
    document.getElementById("ccc-cities").innerHTML = '<div style="font-size:12px;color:var(--text-muted);text-align:center;padding:20px;">No data yet</div>';
    return;
  }
  const max = cities[0].count;
  const html = cities.map((c) => `
    <div class="ccc-bar-row">
      <div style="display:flex;justify-content:space-between;font-size:12px;margin-bottom:3px;">
        <span style="color:var(--text-color);">${escape_html(c.city)}</span>
        <span style="color:var(--text-muted);">${c.count}</span>
      </div>
      <div class="ccc-bar-track"><div class="ccc-bar-fill" data-pct="${(c.count / max) * 100}"></div></div>
    </div>
  `).join("");
  document.getElementById("ccc-cities").innerHTML = html;
  setTimeout(() => {
    document.querySelectorAll(".ccc-bar-fill").forEach((b) => (b.style.width = b.dataset.pct + "%"));
  }, 100);
}

function renderDropoff(d) {
  document.getElementById("ccc-dropoff").innerHTML = `
    <div class="ccc-kpi-card" style="cursor:default;">
      <div class="ccc-kpi-label">No Form Submitted</div>
      <div class="ccc-kpi-value">${d.no_form_pct}%</div>
      <div class="ccc-kpi-change neutral">Clicked PEP/PrEP instead</div>
    </div>
    <div class="ccc-kpi-card" style="cursor:default;">
      <div class="ccc-kpi-label">Payment Abandoned</div>
      <div class="ccc-kpi-value">${d.abandoned_payment_pct}%</div>
      <div class="ccc-kpi-change neutral">Link sent, never paid</div>
    </div>
    <div class="ccc-kpi-card" style="cursor:default;">
      <div class="ccc-kpi-label">Meet Creation Failed</div>
      <div class="ccc-kpi-value">${d.meet_failed_pct}%</div>
      <div class="ccc-kpi-change neutral">OAuth or API error</div>
    </div>
  `;
}


// ─── Utilities ───────────────────────────────────────────────────────────────

function animateNumber(el, target, formatter) {
  if (!el) return;
  formatter = formatter || ((v) => Math.round(v).toLocaleString("en-IN"));
  const start = performance.now();
  const duration = 1200;
  const startVal = parseFloat(el.dataset.current || "0");
  function step(now) {
    const prog = Math.min((now - start) / duration, 1);
    const eased = 1 - Math.pow(1 - prog, 3);
    const value = startVal + (target - startVal) * eased;
    el.textContent = formatter(value);
    if (prog < 1) requestAnimationFrame(step);
    else el.dataset.current = target;
  }
  requestAnimationFrame(step);
}

function destroyChart(key) {
  if (ccc_charts[key]) {
    ccc_charts[key].destroy();
    delete ccc_charts[key];
  }
}

function escape_html(str) {
  if (str === null || str === undefined) return "";
  return String(str).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#039;");
}

function loadChartJs() {
  return new Promise((resolve, reject) => {
    if (window.Chart) return resolve();
    const script = document.createElement("script");
    script.src = "https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.js";
    script.onload = () => resolve();
    script.onerror = reject;
    document.head.appendChild(script);
  });
}