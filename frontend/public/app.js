const API = "/api";
const state = { clientId: null, threadId: null, chart: null, agentCharts: [], busy: false, abort: null, dash: null, monthly: [] };

const TOOL_LABELS = {
  get_client_profile: "PostgreSQL · Profile",
  get_cards: "PostgreSQL · Cards",
  get_spending_summary: "PostgreSQL · Spending",
  get_recent_transactions: "PostgreSQL · Transactions",
  get_client_alerts: "PostgreSQL · Alerts",
  search_bank_rules: "Checking internal rules",
  get_bank_rule: "Reading a rule article",
  run_readonly_sql: "PostgreSQL query",
  ui_show_chart: "Chart",
  ui_highlight: "Highlight",
  ui_show_table: "Table",
  ui_show_metric: "Metric",
};

const SUGGESTIONS = [
  "What are the risks for this client?",
  "Can we raise their card limit?",
  "How has their spending changed over 3 months?",
  "Which product should we offer them?",
];

const LOCALE = "en-GB";
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const eur = (v) => (v == null ? "n/a" : new Intl.NumberFormat(LOCALE, { style: "currency", currency: "EUR", maximumFractionDigits: 0 }).format(v));
const fmtDate = (v) => (v ? new Date(v).toLocaleDateString(LOCALE) : "n/a");
const initials = (name) => String(name || "?").split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0]).join("").toUpperCase();
const last4 = (masked) => (String(masked || "").match(/(\d{4})\s*$/) || [null, "••••"])[1];
const fmtExpiry = (v) => (v ? new Date(v).toLocaleDateString(LOCALE, { month: "2-digit", year: "2-digit" }) : "--/--");

if (window.Chart) {
  Chart.defaults.font.family = '"Inter", ui-sans-serif, system-ui, sans-serif';
  Chart.defaults.color = "#5b6b63";
  Chart.defaults.borderColor = "rgba(11, 61, 46, .08)";
}

async function api(path, opts = {}) {
  const res = await fetch(API + path, { headers: { "Content-Type": "application/json" }, ...opts });
  if (!res.ok) throw new Error(`${res.status} ${(await res.text()).slice(0, 200)}`);
  return res.json();
}

// Agent SSE stream: each "event: x / data: {...}" event calls handlers[x](data)
async function streamChat(body, handlers, signal) {
  const res = await fetch(API + "/chat/stream", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });
  if (!res.ok || !res.body) throw new Error(`${res.status} ${(await res.text()).slice(0, 200)}`);
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  const dispatch = (raw) => {
    let event = "message", data = "";
    for (const line of raw.split("\n")) {
      if (line.startsWith("event:")) event = line.slice(6).trim();
      else if (line.startsWith("data:")) data += line.slice(5).trim();
    }
    if (!data || !handlers[event]) return;
    try {
      handlers[event](JSON.parse(data));
    } catch (e) {
      console.error("event", event, e); // a malformed event does not break the stream
    }
  };
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let i;
    while ((i = buf.indexOf("\n\n")) >= 0) {
      dispatch(buf.slice(0, i));
      buf = buf.slice(i + 2);
    }
  }
  buf += decoder.decode();
  if (buf.trim()) dispatch(buf);
}

/* ---------- Client list ---------- */
async function loadClients(q = "") {
  const list = $("client-list");
  try {
    const clients = await api(`/clients?limit=100${q ? `&q=${encodeURIComponent(q)}` : ""}`);
    list.innerHTML = clients.map((c) => `
      <li class="client-item flex items-center gap-3 px-4 py-3 cursor-pointer ${c.client_id === state.clientId ? "active" : ""}"
          data-id="${c.client_id}">
        <div class="avatar">${esc(initials(c.nom))}</div>
        <div class="min-w-0 flex-1">
          <div class="flex items-center gap-2">
            <span class="font-medium truncate client-name">${esc(c.nom || `Client #${c.client_id}`)}</span>
            ${c.card_compromised ? '<span class="w-2 h-2 shrink-0 rounded-full bg-red-500" title="Compromised card"></span>' : ""}
            <span class="ml-auto text-[11px] text-slate-400 tabular-nums">#${c.client_id}</span>
          </div>
          <div class="text-xs text-slate-500">${esc(c.age)} y/o · score ${esc(c.credit_score)} · ${esc(c.nb_cards)} card(s)</div>
        </div>
      </li>`).join("") || '<li class="p-4 text-slate-400">No clients</li>';
    list.querySelectorAll("li[data-id]").forEach((li) => li.addEventListener("click", () => selectClient(Number(li.dataset.id))));
  } catch (e) {
    list.innerHTML = `<li class="p-4 text-red-600 text-xs">Could not load clients: ${esc(e.message)}</li>`;
  }
}

/* ---------- 360 view ---------- */
// Level values come from the agent API (French identifiers); only their display labels are translated
const LEVEL_STYLE = {
  critique: "bg-red-50 text-red-700 border-red-200",
  attention: "bg-amber-50 text-amber-800 border-amber-200",
  info: "bg-sky-50 text-sky-800 border-sky-200",
  positif: "bg-emerald-50 text-emerald-800 border-emerald-200",
};
const LEVEL_LABEL = { critique: "Critical", attention: "Warning", info: "Info", positif: "Positive" };

async function selectClient(id) {
  if (state.abort) state.abort.abort(); // the pending answer was about the previous client
  resetAgentCanvas();
  resetStage();
  state.clientId = id;
  state.threadId = null;
  document.querySelectorAll(".client-item").forEach((li) => li.classList.toggle("active", Number(li.dataset.id) === id));
  $("chat-client").textContent = document.querySelector(`.client-item[data-id="${id}"] .client-name`)?.textContent || `client #${id}`;
  ["chat-input", "send-btn", "brief-btn"].forEach((k) => ($(k).disabled = false));
  $("messages").innerHTML = "";
  renderSuggestions();

  const main = $("dashboard");
  main.innerHTML = '<div class="h-full grid place-items-center text-slate-400"><span class="dots"><span>●</span><span>●</span><span>●</span></span></div>';
  try {
    const [d, monthly] = await Promise.all([api(`/clients/${id}`), api(`/clients/${id}/monthly?months=12`)]);
    renderDashboard(d, monthly);
  } catch (e) {
    main.innerHTML = `<div class="text-red-600">Error: ${esc(e.message)}</div>`;
  }
}

function renderDashboard(d, monthly) {
  const p = d.profil, k = d.kpis;
  state.dash = d;
  state.monthly = monthly;
  const alerts = d.alertes.length
    ? d.alertes.map((a) => `
        <div class="alert-row border rounded-xl px-4 py-2.5 text-sm flex items-center gap-2 ${LEVEL_STYLE[a.niveau] || LEVEL_STYLE.info}">
          <span class="font-medium">${esc(LEVEL_LABEL[a.niveau] || a.niveau)}</span><span>${esc(a.message)}</span>
          <span class="ml-auto text-xs opacity-70">${esc(a.source)}</span>
        </div>`).join("")
    : '<div class="text-sm text-slate-400">No alerts</div>';

  const cards = d.cartes.map((c) => {
    const status = c.card_on_dark_web ? ["compromised", "Compromised"] : c.expired ? ["expired", "Expired"] : ["active", "Active"];
    const brand = String(c.card_brand || "").toLowerCase().replace(/[^a-z]/g, "");
    const u = usage(c);
    return `
      <div class="bank-card bc-${esc(brand)} is-${status[0]}">
        <div class="bc-gauge gauge ${usageClass(u)}" title="30-day spending ${eur(c.depenses_30j)} of ${eur(c.credit_limit)}"><span data-fill="${fillPct(u)}"></span></div>
        <div class="flex items-start justify-between">
          <span class="bc-brand">${esc(c.card_brand)}</span>
          <span class="bc-status">${status[1]}</span>
        </div>
        <div class="bc-chip"></div>
        <div class="bc-number">•••• •••• •••• ${esc(last4(c.card_number_masked))}</div>
        <div class="flex items-end justify-between gap-2">
          <div><div class="bc-label">${esc(c.card_type)}</div><div class="bc-value">exp. ${fmtExpiry(c.expires)}</div></div>
          <div class="text-right"><div class="bc-label">Limit</div><div class="bc-value">${eur(c.credit_limit)}</div>
            <div class="bc-usage">${u == null ? "usage n/a" : `${pct(u)} used, 30 days`}</div></div>
        </div>
      </div>`;
  }).join("");

  const cats = d.categories_30j.map((c) => `
      <div class="flex justify-between py-2 text-sm border-b border-dashed border-slate-200 last:border-0"><span class="truncate pr-2 text-slate-600">${esc(c.categorie)}</span><span class="font-semibold tabular-nums">${eur(c.total)}</span></div>`).join("")
    || '<div class="text-sm text-slate-400">No spending over the period</div>';

  const tx = d.dernieres_transactions.map((t) => `
      <tr>
        <td class="text-slate-500 tabular-nums">${fmtDate(t.tx_date)}</td>
        <td class="truncate max-w-[220px]">${esc(t.categorie)}</td>
        <td class="text-slate-500">${esc(t.merchant_city)}</td>
        <td class="text-right font-medium tabular-nums ${t.amount < 0 ? "text-emerald-700" : ""}">${eur(t.amount)}</td>
        <td>${t.errors ? `<span class="tx-error">${esc(t.errors)}</span>` : ""}</td>
      </tr>`).join("");

  // data-section values are the section ids the agent targets with ui_highlight: keep them as is
  $("dashboard").innerHTML = `
    <div class="hero">
      <div class="avatar avatar-lg">${esc(initials(p.nom))}</div>
      <div class="min-w-0">
        <div class="hero-eyebrow">Client file · #${p.client_id}</div>
        <h1 class="hero-name">${esc(p.nom || `Client #${p.client_id}`)}</h1>
        <div class="hero-meta">${esc(p.age)} y/o · ${esc(p.gender)} · ${esc(p.address)}</div>
      </div>
      <div class="ml-auto text-right shrink-0">
        <div class="hero-eyebrow">Data as of</div>
        <div class="hero-date">${fmtDate(k.date_reference)}</div>
      </div>
    </div>

    <div class="grid grid-cols-2 xl:grid-cols-4 gap-4">
      <div class="card kpi" id="kpi-revenu" data-section="revenu"><div class="kpi-label">Annual income</div>${kpiValue(p.yearly_income, "eur")}</div>
      <div class="card kpi" id="kpi-dette" data-section="dette"><div class="kpi-label">Total debt</div>${kpiValue(p.total_debt, "eur")}
        <div class="text-xs text-slate-500 mt-1">${p.debt_to_income_ratio == null ? "n/a" : p.debt_to_income_ratio.toLocaleString(LOCALE)}× annual income</div></div>
      <div class="card kpi" id="kpi-score" data-section="score"><div class="kpi-label">Credit score</div>${kpiValue(p.credit_score, "int")}</div>
      <div class="card kpi" id="kpi-depenses" data-section="depenses"><div class="kpi-label">30-day spending</div>${kpiValue(k.depenses_30j, "eur")}
        <div class="text-xs text-slate-500 mt-1">${esc(k.transactions_30j)} transactions · ${esc(k.incidents_30j)} incident(s)</div>
        ${sparkline(fullMonths(monthly, k.date_reference).slice(-3))}</div>
    </div>

    ${stageHtml()}

    <div id="agent-canvas" class="space-y-4"></div>

    <div class="space-y-2" data-section="alertes">${alerts}</div>

    <div class="card" id="section-cartes" data-section="cartes"><div class="section-title">Cards</div>
      ${cards ? `<div class="grid grid-cols-1 sm:grid-cols-2 2xl:grid-cols-3 gap-4">${cards}</div>` : '<div class="text-sm text-slate-400">No cards</div>'}</div>

    <div class="grid grid-cols-1 xl:grid-cols-[1fr_1.5fr] gap-4">
      <div class="card" data-section="categories"><div class="section-title">Top spending, 30 days</div>${cats}</div>
      <div class="card" data-section="mensuel"><div class="section-title">Monthly spending</div><div class="h-56"><canvas id="monthly-chart"></canvas></div></div>
    </div>

    <div class="card overflow-x-auto" data-section="transactions"><div class="section-title">Recent transactions</div>
      <table class="tx-table w-full text-sm">
        <thead><tr><th>Date</th><th>Category</th><th>City</th><th class="text-right">Amount</th><th>Status</th></tr></thead>
        <tbody>${tx}</tbody></table></div>`;

  if (state.chart) state.chart.destroy();
  state.chart = new Chart($("monthly-chart"), {
    type: "bar",
    data: { labels: monthly.map((m) => m.mois), datasets: [{ data: monthly.map((m) => m.depenses), backgroundColor: "#0b4a36", hoverBackgroundColor: "#c9a45c", borderRadius: 6, maxBarThickness: 40 }] },
    options: {
      maintainAspectRatio: false,
      plugins: { legend: { display: false }, tooltip: { callbacks: { label: (c) => eur(c.parsed.y) } } },
      scales: { x: { grid: { display: false } }, y: { ticks: { callback: (v) => eur(v) } } },
    },
  });
}

/* ---------- KPI cards: count-up and sparkline ---------- */
const KPI_FMT = { eur, int: (x) => String(Math.round(x)) };

function kpiValue(v, fmt) {
  return `<div class="kpi-value" data-value="${v == null ? "" : Number(v)}" data-fmt="${fmt}">${v == null ? "n/a" : KPI_FMT[fmt](Number(v))}</div>`;
}

// Monthly rows without the reference month, which is still in progress
function fullMonths(monthly, ref) {
  const current = String(ref || "").slice(0, 7);
  return (monthly || []).filter((m) => m.mois !== current);
}

// Spending of the last full months (PostgreSQL); the path draws itself when the card is highlighted
function sparkline(rows) {
  if (rows.length < 2) return "";
  const v = rows.map((r) => Number(r.depenses)), max = Math.max(...v), min = Math.min(...v), span = max - min || 1;
  const pts = v.map((x, i) => `${((i / (v.length - 1)) * 116 + 2).toFixed(1)},${(25 - ((x - min) / span) * 21).toFixed(1)}`);
  const title = rows.map((r) => `${fmtMonth(r.mois)} ${eur(r.depenses)}`).join(", ");
  return `<svg class="kpi-spark" viewBox="0 0 120 28" preserveAspectRatio="none" role="img" aria-label="Spending, last ${rows.length} full months: ${esc(title)}">
    <title>${esc(title)}</title><path d="M${pts.join(" L")}" pathLength="1"/></svg>`;
}

// Counts from the value shown before (or 0, also when it has not changed) to the value from the API
function countUp(el) {
  if (!el || el.dataset.value === "") return;
  const target = Number(el.dataset.value), prev = Number(el.dataset.shown ?? NaN), fmt = KPI_FMT[el.dataset.fmt] || eur;
  const from = Number.isFinite(prev) && prev !== target ? prev : 0;
  el.dataset.shown = target;
  cancelAnimationFrame(el._raf);
  if (reducedMotion()) {
    el.textContent = fmt(target);
    return;
  }
  const t0 = performance.now();
  const tick = (t) => {
    const k = Math.min(1, (t - t0) / 800);
    el.textContent = fmt(from + (target - from) * (1 - Math.pow(1 - k, 4)));
    if (k < 1) el._raf = requestAnimationFrame(tick);
  };
  el._raf = requestAnimationFrame(tick);
}

/* ---------- Agent-driven screen ("ui" events) ---------- */
const LEVELS = ["critique", "attention", "info", "positif"];
const HL_CLASSES = ["agent-hl", ...LEVELS.map((l) => `hl-${l}`)];
const METRIC_COLOR = { critique: "text-red-700", attention: "text-amber-700", info: "text-sky-700", positif: "text-emerald-700" };
const PIE_COLORS = ["#0b4a36", "#c9a45c", "#2f7d5f", "#e6d5ae", "#6aa58b", "#8a6d32", "#a7c9b8", "#64748b"];
const level = (l) => (LEVELS.includes(l) ? l : "info");

function resetAgentCanvas() {
  state.agentCharts.forEach((c) => c.destroy());
  state.agentCharts = [];
  if ($("agent-canvas")) $("agent-canvas").innerHTML = "";
}

function clearHighlights() {
  document.querySelectorAll(".agent-hl").forEach((el) => el.classList.remove(...HL_CLASSES));
  document.querySelectorAll(".agent-note").forEach((n) => n.remove());
}

// Shared panel: Agent badge, title, body, optional note, × button; prepended to the canvas
function agentPanel(title, bodyHtml, note, onClose) {
  const panel = document.createElement("div");
  panel.className = "card agent-panel";
  panel.innerHTML = `
    <div class="flex items-center gap-2 mb-2">
      <span class="agent-badge">Agent</span>
      <span class="font-semibold">${esc(title)}</span>
      <button type="button" class="ml-auto text-slate-400 hover:text-slate-700 text-lg leading-none" title="Close">×</button>
    </div>
    ${bodyHtml}
    ${note ? `<div class="text-xs text-slate-500 mt-2">${esc(note)}</div>` : ""}`;
  panel.querySelector("button").addEventListener("click", () => {
    if (onClose) onClose();
    panel.remove();
  });
  $("agent-canvas").prepend(panel);
  return panel;
}

function showChart(ui) {
  const pie = ui.chart_type === "pie";
  const type = ["bar", "line", "pie"].includes(ui.chart_type) ? ui.chart_type : "bar";
  const fmt = (v) => (ui.unit === "count" ? String(v) : eur(v));
  let chart = null;
  const panel = agentPanel(ui.title, '<div class="h-56"><canvas></canvas></div>', ui.note, () => {
    chart.destroy();
    state.agentCharts = state.agentCharts.filter((c) => c !== chart);
  });
  chart = new Chart(panel.querySelector("canvas"), {
    type,
    data: {
      labels: (ui.labels || []).map(String),
      datasets: [{
        label: ui.series_label || ui.title || "",
        data: ui.values || [],
        backgroundColor: pie ? PIE_COLORS : type === "line" ? "rgba(11,74,54,.10)" : "#0b4a36",
        borderColor: pie ? "#fff" : "#0b4a36",
        pointBackgroundColor: "#c9a45c",
        pointRadius: type === "line" ? 4 : 0,
        fill: type === "line",
        tension: 0.35,
        borderRadius: type === "bar" ? 4 : 0,
        maxBarThickness: 48,
      }, ...(Array.isArray(ui.values2) ? [{
        label: ui.series_label2 || "",
        data: ui.values2,
        backgroundColor: "#e6d5ae",
        borderColor: "#c9a45c",
        borderRadius: 4,
        maxBarThickness: 48,
      }] : [])],
    },
    options: {
      maintainAspectRatio: false,
      animation: { duration: 1200, easing: "easeOutQuart" },
      plugins: {
        legend: { display: pie || Array.isArray(ui.values2), position: pie ? "right" : "top" },
        tooltip: pie ? {} : { callbacks: { label: (c) => (fmt(c.parsed.y)) } },
      },
      scales: pie ? {} : { y: { beginAtZero: true, ticks: { callback: (v) => fmt(v) } } },
    },
  });
  state.agentCharts.push(chart);
}

function showTable(ui) {
  const head = (ui.columns || []).map((c) => `<th class="text-left font-medium text-slate-500 py-1.5 pr-3">${esc(c)}</th>`).join("");
  const rows = (ui.rows || []).map((r) =>
    `<tr class="border-b border-slate-100 last:border-0">${r.map((c) => `<td class="py-1.5 pr-3">${esc(c)}</td>`).join("")}</tr>`).join("");
  agentPanel(ui.title, `<div class="overflow-x-auto"><table class="w-full text-sm">
    <thead class="border-b border-slate-200"><tr>${head}</tr></thead><tbody>${rows}</tbody></table></div>`, ui.note);
}

function showMetric(ui) {
  agentPanel(ui.label, `
    <div class="text-3xl font-semibold ${METRIC_COLOR[level(ui.level)]}">${esc(ui.value)}</div>
    ${ui.detail ? `<div class="text-sm text-slate-600 mt-1">${esc(ui.detail)}</div>` : ""}`);
}

function highlight(ui) {
  const el = document.querySelector(`#dashboard [data-section="${CSS.escape(String(ui.section))}"]`);
  if (!el) return;
  el.classList.remove(...HL_CLASSES);
  void el.offsetWidth; // restarts the animation if the area was already outlined
  el.classList.add("agent-hl", `hl-${level(ui.level)}`);
  if (el.classList.contains("kpi")) countUp(el.querySelector(".kpi-value"));
  if (ui.section === "cartes") fillBars(el);
  bringToStage(ui.section);
  el.querySelector(":scope > .agent-note")?.remove();
  const note = document.createElement("div");
  note.className = "agent-note";
  note.textContent = note.title = ui.message || "";
  el.appendChild(note);
}

function applyUi(ui) {
  if (!ui || !$("insight-stage")) return; // 360 view not loaded yet
  startUiTurn();
  // Only charts computed in PostgreSQL (origin "db") feed the stage; charts typed by the model keep their own panel
  if (ui.action === "chart" && ui.origin === "db") {
    stage.touched.add("chart");
    stageChart(ui);
    return;
  }
  const fn = { chart: showChart, table: showTable, metric: showMetric, highlight }[ui.action];
  if (fn) fn(ui);
}

/* ---------- Usage gauges and bars (values from the dashboard API) ---------- */
const usage = (c) => (Number(c.credit_limit) > 0 ? Number(c.depenses_30j || 0) / Number(c.credit_limit) : null);
const usageClass = (u) => (u == null ? "" : u > 0.9 ? "is-red" : u > 0.7 ? "is-amber" : "");
const fillPct = (x) => `${Math.min(100, Math.max(0, (x || 0) * 100)).toFixed(1)}%`;

// Bars start empty and fill to their data-fill width (instant with reduced motion: no transition)
function fillBars(root) {
  const bars = [...root.querySelectorAll("[data-fill]")];
  bars.forEach((b) => { b.style.transition = "none"; b.style.width = "0"; });
  void root.offsetWidth;
  bars.forEach((b) => { b.style.transition = ""; b.style.width = b.dataset.fill; });
  root.classList.add("gauges-on");
}

/* ---------- Agent insight stage (sticky, under the KPI row) ---------- */
// One Chart.js instance morphed from answer to answer; previews of off-screen sections come to it instead of scrolling
const stage = { chart: null, type: null, fmt: eur, queue: Promise.resolve(), touched: null, question: "", previewKind: null };
const reducedMotion = () => matchMedia("(prefers-reduced-motion: reduce)").matches;
const wait = (ms) => new Promise((r) => setTimeout(r, reducedMotion() ? 0 : ms));
const GOLD = "#c9a45c";
const pct = (x) => `${Math.round(x * 100)}%`;
const signedPct = (x) => `${x >= 0 ? "+" : "-"}${Math.abs(Math.round(x * 100))}%`;
const fmtMonth = (s) => (/^\d{4}-\d{2}$/.test(s) ? new Date(`${s}-01T00:00:00`).toLocaleDateString(LOCALE, { month: "short", year: "2-digit" }) : String(s));
const shortLabel = (s) => fmtMonth(String(s)).replace(/\*{4}\s*/, "••");
const tickLabel = (s) => { const m = shortLabel(s).match(/^(.*\S)\s+(••\d+)$/); return m ? [m[1], m[2]] : shortLabel(s); }; // card: 2 lines

function stageHtml() {
  const ghost = [38, 62, 48, 80, 56, 70, 44].map((h) => `<span style="height:${h}%"></span>`).join("");
  return `
    <div class="stage-dock"><section id="insight-stage" class="insight-stage" data-state="empty">
      <div class="stage-head"><span class="stage-badge">Agent insight</span><span class="stage-title"></span></div>
      <div class="stage-empty"><div class="stage-ghost">${ghost}</div><span>Ask a question to see the data move</span></div>
      <div class="stage-body">
        <div class="stage-chart is-fading" hidden>
          <div class="slot-title"></div>
          <div class="stage-canvas"><canvas></canvas></div>
          <div class="stage-caption"></div>
        </div>
        <div class="stage-preview is-fading" hidden></div>
      </div>
    </section></div>`;
}

function resetStage() {
  stage.chart?.destroy();
  stage.chart = stage.type = null;
  stage.touched = null;
}

// First display order of an answer: only now does the previous answer's state go away (no ui, nothing moves)
function startUiTurn() {
  if (stage.touched) return;
  const touched = (stage.touched = new Set());
  clearHighlights();
  resetAgentCanvas();
  const el = $("insight-stage");
  el.dataset.state = "live";
  el.querySelector(".stage-title").textContent = stage.question;
  // Display orders of one answer arrive together: slots that got nothing this time are put away
  setTimeout(() => {
    if (stage.touched !== touched || !touched.size) return;
    if (!touched.has("chart")) hideSlot(".stage-chart");
    if (!touched.has("preview")) hideSlot(".stage-preview");
  }, 400);
}

function enqueue(fn) {
  stage.queue = stage.queue.then(fn).catch((e) => console.error("stage", e));
}

function hideSlot(sel) {
  enqueue(async () => {
    const slot = document.querySelector(`#insight-stage ${sel}`);
    if (!slot || slot.hidden) return;
    slot.classList.add("is-fading");
    await wait(300);
    slot.hidden = true;
  });
}

function stageChartConfig(type) {
  const tick = "#cfe0d7", grid = "rgba(230, 213, 174, .12)";
  return {
    type,
    data: { labels: [], datasets: [] },
    options: {
      maintainAspectRatio: false,
      animation: reducedMotion() ? false : { duration: 900, easing: "easeOutQuart" },
      plugins: {
        legend: { display: false, position: "top", align: "end", labels: { color: tick, boxWidth: 10, boxHeight: 10 } },
        tooltip: { callbacks: { label: (c) => `${c.dataset.label ? `${c.dataset.label}: ` : ""}${stage.fmt(c.parsed.y)}` } },
      },
      scales: {
        x: { grid: { display: false }, border: { color: grid }, ticks: { color: tick, maxRotation: 0, autoSkip: true, font: { size: 11 } } },
        y: { beginAtZero: true, grid: { color: grid }, border: { display: false }, ticks: { color: tick, maxTicksLimit: 5, callback: (v) => stage.fmt(v) } },
      },
    },
  };
}

function datasetStyle(type, i) {
  if (type === "line") {
    return { borderColor: GOLD, backgroundColor: "rgba(201, 164, 92, .16)", fill: true, tension: 0.35, borderWidth: 2.5,
      pointRadius: 4, pointHoverRadius: 6, pointBackgroundColor: GOLD, pointBorderColor: "#062e22", pointBorderWidth: 2 };
  }
  return i === 0
    ? { backgroundColor: GOLD, hoverBackgroundColor: "#e6d5ae", borderWidth: 0, borderRadius: 6, maxBarThickness: 44 }
    : { backgroundColor: "rgba(230, 213, 174, .18)", borderColor: "rgba(230, 213, 174, .55)", borderWidth: 1, borderRadius: 6, maxBarThickness: 44 };
}

// Key number of the chart, computed from the values the chart shows
function chartCaption(ui) {
  const v = (ui.values || []).map(Number), L = (ui.labels || []).map(shortLabel), fmt = stage.fmt;
  if (!v.length) return "";
  if (Array.isArray(ui.values2)) {
    let best = -1, bi = 0;
    v.forEach((x, i) => { const lim = Number(ui.values2[i]); if (lim > 0 && x / lim > best) { best = x / lim; bi = i; } });
    return best < 0 ? "" : `Highest usage: <b>${esc(L[bi])}</b> at <b>${pct(best)}</b> of its limit`;
  }
  const label = ui.series_label || "Value";
  if (ui.chart_type === "line" && v.length > 1 && v[0]) {
    const change = (v[v.length - 1] - v[0]) / v[0];
    return `${esc(label)} <b>${signedPct(change)}</b> over ${v.length} months (${fmt(v[0])} in ${esc(L[0])}, ${fmt(v[v.length - 1])} in ${esc(L[L.length - 1])})`;
  }
  const peak = v.indexOf(Math.max(...v));
  if (ui.unit === "count") {
    const total = v.reduce((a, b) => a + b, 0);
    return `<b>${total}</b> ${esc(label.toLowerCase())} from ${esc(L[0])} to ${esc(L[L.length - 1])}, peak <b>${v[peak]}</b> in ${esc(L[peak])}`;
  }
  return `Peak: <b>${fmt(v[peak])}</b> in ${esc(L[peak])}`;
}

// Section below the fold targeted by the answer: a compact preview comes to the stage, the real section stays put
const PREVIEW_OF = { cartes: "cards", categories: "categories", alertes: "alerts", revenu: "capacity", dette: "capacity" };

const PREVIEWS = {
  cards: (d) => {
    const rows = d.cartes.map((c) => ({ c, u: usage(c) })).sort((a, b) => (b.u ?? -1) - (a.u ?? -1));
    if (!rows.length) return "";
    return `<div class="slot-title">Cards · 30-day usage of the limit</div>
      <div class="pv-list">${rows.slice(0, 4).map(({ c, u }) => `
        <div title="30-day spending ${eur(c.depenses_30j)} of ${eur(c.credit_limit)}">
          <div class="pv-row"><span class="pv-name">${esc(c.card_brand)} ${esc(c.card_type)} ••${esc(last4(c.card_number_masked))}</span>
            <span class="pv-sub">${eur(c.depenses_30j)} / ${eur(c.credit_limit)}</span><span class="pv-val">${u == null ? "n/a" : pct(u)}</span></div>
          <div class="gauge ${usageClass(u)}"><span data-fill="${fillPct(u)}"></span></div>
        </div>`).join("")}</div>
      ${rows.length > 4 ? `<div class="pv-sub">+${rows.length - 4} more in Cards below</div>` : ""}`;
  },
  categories: (d) => {
    const top = d.categories_30j.slice(0, 3);
    if (!top.length) return "";
    const max = Math.max(...top.map((c) => Number(c.total))) || 1;
    return `<div class="slot-title">Top spending · 30 days</div>
      <div class="pv-list">${top.map((c) => `
        <div>
          <div class="pv-row"><span class="pv-name">${esc(c.categorie)}</span><span class="pv-val">${eur(c.total)}</span></div>
          <div class="hbar"><span data-fill="${fillPct(Number(c.total) / max)}"></span></div>
          <div class="pv-sub">${esc(c.nb)} transaction(s)</div>
        </div>`).join("")}</div>`;
  },
  alerts: (d) => `<div class="slot-title">Alerts</div>
    <div class="pv-list">${d.alertes.slice(0, 4).map((a) => `
      <div class="pv-alert lv-${esc(level(a.niveau))}"><span class="pv-dot"></span><span>${esc(a.message)}</span></div>`).join("") || '<div class="pv-sub">No alerts</div>'}</div>
    ${d.alertes.length > 4 ? `<div class="pv-sub">+${d.alertes.length - 4} more below</div>` : ""}`,
  capacity: (d) => {
    const p = d.profil, inc = Number(p.yearly_income || 0), debt = Number(p.total_debt || 0), max = Math.max(inc, debt) || 1;
    return `<div class="slot-title">Financial capacity</div>
      <div class="pv-list">${[["Annual income", inc, ""], ["Total debt", debt, "is-debt"]].map(([label, v, cls]) => `
        <div>
          <div class="pv-row"><span class="pv-name">${label}</span><span class="pv-val">${eur(v)}</span></div>
          <div class="hbar ${cls}"><span data-fill="${fillPct(v / max)}"></span></div>
        </div>`).join("")}</div>
      ${p.debt_to_income_ratio == null ? "" : `<div class="pv-kpi">Debt = <b>${p.debt_to_income_ratio.toLocaleString(LOCALE)}×</b> annual income · score <b>${esc(p.credit_score)}</b></div>`}`;
  },
};

function bringToStage(section) {
  if (!state.dash || !stage.touched) return;
  if (section === "mensuel") {
    if (stage.touched.has("chart")) return; // the chart of the question itself wins
    stage.touched.add("chart");
    const rows = fullMonths(state.monthly, state.dash.kpis.date_reference);
    stageChart({ chart_type: "bar", title: "Monthly spending (full months)", series_label: "Spending",
      labels: rows.map((r) => r.mois), values: rows.map((r) => Number(r.depenses)) });
    return;
  }
  const kind = PREVIEW_OF[section];
  if (!kind || (stage.touched.has("preview") && stage.previewKind === kind)) return; // income + debt: one preview
  const html = PREVIEWS[kind](state.dash);
  if (!html) return;
  stage.touched.add("preview");
  stage.previewKind = kind;
  enqueue(async () => {
    const slot = document.querySelector("#insight-stage .stage-preview");
    if (!slot) return;
    if (!slot.hidden) {
      slot.classList.add("is-fading");
      await wait(300);
    }
    slot.dataset.kind = kind;
    slot.innerHTML = html;
    slot.hidden = false;
    fillBars(slot);
    requestAnimationFrame(() => slot.classList.remove("is-fading"));
  });
}

function stageChart(ui) {
  enqueue(async () => {
    const slot = document.querySelector("#insight-stage .stage-chart");
    if (!slot) return;
    const type = ui.chart_type === "line" ? "line" : "bar";
    const entering = slot.hidden;
    const newType = !stage.chart || stage.type !== type;
    // Same type: bars and points morph in place. Other type: crossfade, then a new chart on the same canvas
    if (newType && !entering) {
      slot.classList.add("is-fading");
      await wait(300);
    }
    slot.hidden = false;
    stage.fmt = ui.unit === "count" ? (x) => Number(x).toLocaleString(LOCALE) : eur;
    slot.querySelector(".slot-title").textContent = ui.title || "";
    slot.querySelector(".stage-caption").innerHTML = chartCaption(ui);
    if (newType) {
      stage.chart?.destroy();
      stage.chart = new Chart(slot.querySelector("canvas"), stageChartConfig(type));
      stage.type = type;
    } else {
      stage.chart.resize(); // the slot width may have changed while a preview came or went
    }
    const series = [{ label: ui.series_label || ui.title || "", data: (ui.values || []).map(Number) }];
    if (Array.isArray(ui.values2)) series.push({ label: ui.series_label2 || "", data: ui.values2.map(Number) });
    const ds = stage.chart.data.datasets;
    // Dataset objects are kept and mutated so Chart.js animates the existing bars and points to their new values
    series.forEach((serie, i) => Object.assign(ds[i] || (ds[i] = {}), serie, datasetStyle(type, i)));
    ds.length = series.length;
    stage.chart.data.labels = (ui.labels || []).map(tickLabel);
    stage.chart.options.plugins.legend.display = series.length > 1;
    stage.chart.options.animation = reducedMotion() ? false : { duration: 900, easing: "easeOutQuart" };
    stage.chart.update();
    requestAnimationFrame(() => slot.classList.remove("is-fading"));
  });
}

/* ---------- Chat ---------- */
function renderSuggestions() {
  $("suggestions").innerHTML = SUGGESTIONS.map((s) =>
    `<button class="suggestion">${esc(s)}</button>`).join("");
  $("suggestions").querySelectorAll("button").forEach((b) => b.addEventListener("click", () => send(b.textContent)));
}

// Follows the stream only while the advisor is already at the bottom: a scrolled-up chat never moves on its own
function scrollChat(force = false) {
  const m = $("messages");
  if (force || m.scrollHeight - m.scrollTop - m.clientHeight < 120) m.scrollTop = m.scrollHeight;
}

function addMessage(role, html) {
  const div = document.createElement("div");
  div.className = role === "user" ? "flex justify-end" : "";
  div.innerHTML = role === "user"
    ? `<div class="bubble-user">${html}</div>`
    : `<div class="flex gap-2 items-start"><img src="/logo.png" alt="" class="w-7 h-7 shrink-0 mt-0.5"><div class="bubble-assistant prose-chat min-w-0 flex-1">${html}</div></div>`;
  $("messages").appendChild(div);
  scrollChat(true);
  return div;
}

function renderAnswer(answer) {
  const md = answer.replace(/\[([^\]\n]{1,80})\](?!\()/g, '<span class="source-tag">$1</span>');
  return DOMPurify.sanitize(marked.parse(md));
}

// Assistant bubble in 4 zones: step chips, thoughts, live text, footer (latency)
function assistantBubble(clientId) {
  const wrap = addMessage("assistant", `
    <div class="b-steps bubble-zone flex flex-wrap gap-1 mb-2"></div>
    <div class="b-thoughts bubble-zone text-xs text-slate-400 italic space-y-1 mb-2"></div>
    <div class="b-live whitespace-pre-wrap"><span class="dots text-slate-400"><span>●</span><span>●</span><span>●</span></span></div>
    <div class="b-foot bubble-zone mt-2 pt-2 border-t border-slate-200 flex text-[11px] text-slate-500"></div>`);
  const z = (c) => wrap.querySelector(c);
  const chips = new Map();
  let live = "";
  const b = { finished: false };
  const setLive = (t) => {
    live = t;
    z(".b-live").textContent = t; // plain text: textContent escapes
  };
  const current = () => state.clientId === clientId; // ignore a stream for a client we have left

  b.fail = (msg) => {
    b.finished = true;
    if (current()) state.threadId = null; // an interrupted turn can leave the thread inconsistent: start fresh
    if (!live) z(".b-live").textContent = "";
    const err = document.createElement("div");
    err.className = "text-red-600 mt-1";
    err.textContent = `Error: ${msg}`;
    z(".b-live").after(err);
    scrollChat();
  };

  b.handlers = {
    start: (d) => {
      if (current()) state.threadId = d.thread_id;
    },
    step: (d) => {
      if (live.trim()) {
        const t = document.createElement("div");
        t.textContent = live.trim();
        z(".b-thoughts").appendChild(t);
      }
      setLive("");
      const chip = document.createElement("span");
      chip.className = `step-chip running${d.tool.startsWith("ui_") ? " ui" : ""}`;
      chip.innerHTML = '<span class="lbl"></span><span class="st">…</span>';
      chip.querySelector(".lbl").textContent = TOOL_LABELS[d.tool] || d.tool;
      chip.dataset.tool = d.tool;
      z(".b-steps").appendChild(chip);
      chips.set(d.id, chip);
      scrollChat();
    },
    step_done: (d) => {
      const chip = chips.get(d.id);
      if (!chip) return;
      if (!d.ok && ["search_bank_rules", "get_bank_rule"].includes(chip.dataset.tool)) {
        console.warn("Internal rules unavailable", d); // no error badge in the UI
        chip.remove();
        return;
      }
      chip.classList.remove("running");
      chip.classList.toggle("fail", !d.ok);
      chip.querySelector(".st").textContent = d.ok ? "✓" : "✗";
    },
    token: (d) => {
      setLive(live + d.text);
      scrollChat();
    },
    ui: (d) => {
      if (current()) applyUi(d);
    },
    answer: (d) => {
      b.finished = true;
      if (current() && d.thread_id) state.threadId = d.thread_id;
      const el = z(".b-live");
      el.classList.remove("whitespace-pre-wrap");
      el.innerHTML = renderAnswer(d.answer || live);
      const foot = document.createElement("span");
      foot.className = "ml-auto";
      foot.textContent = `${d.latency_s} s`;
      z(".b-foot").appendChild(foot);
      scrollChat();
    },
    error: (d) => b.fail(d.message || "unknown error"),
  };
  return b;
}

async function send(message, brief = false) {
  if (state.clientId == null || state.busy) return; // client #0 is a valid id
  const text = (message ?? "").trim();
  if (!brief && !text) {
    showError("Type a question first");
    return;
  }
  showError("");
  // The screen is left as is until this answer sends its first display order
  stage.touched = null;
  stage.question = brief ? "Meeting brief" : text;
  state.busy = true;
  state.abort = new AbortController();
  $("send-btn").disabled = $("brief-btn").disabled = true;
  addMessage("user", esc(brief ? "Prepare my meeting brief" : text));
  const bubble = assistantBubble(state.clientId);
  try {
    await streamChat({ client_id: state.clientId, message: text, thread_id: state.threadId, brief }, bubble.handlers, state.abort.signal);
    if (!bubble.finished) bubble.fail("stream ended before the answer");
  } catch (e) {
    if (e.name !== "AbortError") bubble.fail(e.message);
  } finally {
    state.busy = false;
    state.abort = null;
    $("send-btn").disabled = $("brief-btn").disabled = false;
    scrollChat();
  }
}

function showError(msg) {
  $("chat-error").textContent = msg;
  $("chat-error").classList.toggle("hidden", !msg);
}

/* ---------- Init ---------- */
$("chat-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const v = $("chat-input").value;
  $("chat-input").value = "";
  send(v);
});
$("chat-input").addEventListener("input", () => showError(""));
$("brief-btn").addEventListener("click", () => send("", true));
let searchTimer;
$("search").addEventListener("input", (e) => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => loadClients(e.target.value.trim()), 250);
});

fetch("/health/agent").then((r) => r.json()).then((h) => {
  $("status").textContent = h.db ? "Database connected" : "Database unreachable";
  $("status").classList.toggle("down", !h.db);
}).catch(() => {
  $("status").textContent = "Agent unreachable";
  $("status").classList.add("down");
});

loadClients();
