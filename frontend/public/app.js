const API = "/api";
const state = { clientId: null, threadId: null, chart: null, agentCharts: [], busy: false, abort: null };

const TOOL_LABELS = {
  get_client_profile: "Profil",
  get_cards: "Cartes",
  get_spending_summary: "Dépenses",
  get_recent_transactions: "Transactions",
  get_client_alerts: "Alertes",
  search_bank_rules: "Règles internes",
  get_bank_rule: "Article de règle",
  run_readonly_sql: "Requête SQL",
  ui_show_chart: "Graphique",
  ui_highlight: "Mise en avant",
  ui_show_table: "Tableau",
  ui_show_metric: "Indicateur",
};

const SUGGESTIONS = [
  "Quels sont les risques sur ce client ?",
  "Peut-on augmenter le plafond de sa carte ?",
  "Comment ont évolué ses dépenses sur 3 mois ?",
  "Quel produit lui proposer ?",
];

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const eur = (v) => (v == null ? "n/d" : new Intl.NumberFormat("fr-FR", { style: "currency", currency: "EUR", maximumFractionDigits: 0 }).format(v));
const dateFr = (v) => (v ? new Date(v).toLocaleDateString("fr-FR") : "n/d");

async function api(path, opts = {}) {
  const res = await fetch(API + path, { headers: { "Content-Type": "application/json" }, ...opts });
  if (!res.ok) throw new Error(`${res.status} ${(await res.text()).slice(0, 200)}`);
  return res.json();
}

// Flux SSE de l'agent : chaque événement "event: x / data: {...}" appelle handlers[x](données)
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
      console.error("événement", event, e); // un événement mal formé ne coupe pas le flux
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

/* ---------- Liste des clients ---------- */
async function loadClients(q = "") {
  const list = $("client-list");
  try {
    const clients = await api(`/clients?limit=100${q ? `&q=${encodeURIComponent(q)}` : ""}`);
    list.innerHTML = clients.map((c) => `
      <li class="client-item px-4 py-3 border-b border-slate-100 cursor-pointer hover:bg-slate-50 ${c.client_id === state.clientId ? "active" : ""}"
          data-id="${c.client_id}">
        <div class="flex items-center gap-2">
          <span class="font-medium">Client n°${c.client_id}</span>
          ${c.card_compromised ? '<span class="w-2 h-2 rounded-full bg-red-500" title="Carte compromise"></span>' : ""}
        </div>
        <div class="text-xs text-slate-500">${esc(c.age)} ans · score ${esc(c.credit_score)} · ${esc(c.nb_cards)} carte(s)</div>
      </li>`).join("") || '<li class="p-4 text-slate-400">Aucun client</li>';
    list.querySelectorAll("li[data-id]").forEach((li) => li.addEventListener("click", () => selectClient(Number(li.dataset.id))));
  } catch (e) {
    list.innerHTML = `<li class="p-4 text-red-600 text-xs">Impossible de charger les clients : ${esc(e.message)}</li>`;
  }
}

/* ---------- Vue 360 ---------- */
const LEVEL_STYLE = {
  critique: "bg-red-50 text-red-700 border-red-200",
  attention: "bg-amber-50 text-amber-800 border-amber-200",
  info: "bg-sky-50 text-sky-800 border-sky-200",
  positif: "bg-emerald-50 text-emerald-800 border-emerald-200",
};

async function selectClient(id) {
  if (state.abort) state.abort.abort(); // la réponse en cours concernait l'ancien client
  resetAgentCanvas();
  state.clientId = id;
  state.threadId = null;
  document.querySelectorAll(".client-item").forEach((li) => li.classList.toggle("active", Number(li.dataset.id) === id));
  $("chat-client").textContent = `client n°${id}`;
  ["chat-input", "send-btn", "brief-btn"].forEach((k) => ($(k).disabled = false));
  $("messages").innerHTML = "";
  renderSuggestions();

  const main = $("dashboard");
  main.innerHTML = '<div class="text-slate-400">Chargement…</div>';
  try {
    const [d, monthly] = await Promise.all([api(`/clients/${id}`), api(`/clients/${id}/monthly?months=12`)]);
    renderDashboard(d, monthly);
  } catch (e) {
    main.innerHTML = `<div class="text-red-600">Erreur : ${esc(e.message)}</div>`;
  }
}

function renderDashboard(d, monthly) {
  const p = d.profil, k = d.kpis;
  const alerts = d.alertes.length
    ? d.alertes.map((a) => `
        <div class="border rounded-lg px-3 py-2 text-sm flex items-center gap-2 ${LEVEL_STYLE[a.niveau] || LEVEL_STYLE.info}">
          <span class="font-medium capitalize">${esc(a.niveau)}</span><span>${esc(a.message)}</span>
          <span class="ml-auto text-xs opacity-70">${esc(a.source)}</span>
        </div>`).join("")
    : '<div class="text-sm text-slate-400">Aucune alerte</div>';

  const cards = d.cartes.map((c) => `
      <div class="flex items-center justify-between py-2 border-b border-slate-100 last:border-0 text-sm">
        <div>
          <div class="font-medium">${esc(c.card_brand)} ${esc(c.card_type)} ${esc(c.card_number_masked)}</div>
          <div class="text-xs text-slate-500">Expire le ${dateFr(c.expires)} · plafond ${eur(c.credit_limit)}</div>
        </div>
        ${c.card_on_dark_web ? '<span class="text-xs text-red-600 font-medium">Compromise</span>' : c.expired ? '<span class="text-xs text-slate-400">Expirée</span>' : '<span class="text-xs text-emerald-700">Active</span>'}
      </div>`).join("");

  const cats = d.categories_30j.map((c) => `
      <div class="flex justify-between py-1 text-sm"><span class="truncate pr-2">${esc(c.categorie)}</span><span class="font-medium">${eur(c.total)}</span></div>`).join("")
    || '<div class="text-sm text-slate-400">Pas de dépense sur la période</div>';

  const tx = d.dernieres_transactions.map((t) => `
      <tr class="border-b border-slate-100 last:border-0">
        <td class="py-1.5">${dateFr(t.tx_date)}</td>
        <td class="py-1.5 truncate max-w-[220px]">${esc(t.categorie)}</td>
        <td class="py-1.5">${esc(t.merchant_city)}</td>
        <td class="py-1.5 text-right ${t.amount < 0 ? "text-emerald-700" : ""}">${eur(t.amount)}</td>
        <td class="py-1.5 text-xs text-red-600">${esc(t.errors || "")}</td>
      </tr>`).join("");

  $("dashboard").innerHTML = `
    <div class="flex items-end gap-3">
      <div>
        <h1 class="text-2xl font-semibold">Client n°${p.client_id}</h1>
        <div class="text-sm text-slate-500">${esc(p.age)} ans · ${esc(p.gender)} · ${esc(p.address)}</div>
      </div>
      <div class="ml-auto text-xs text-slate-400">Données au ${dateFr(k.date_reference)}</div>
    </div>

    <div id="agent-canvas" class="space-y-4"></div>

    <div class="grid grid-cols-2 xl:grid-cols-4 gap-4">
      <div class="card" data-section="revenu"><div class="kpi-label">Revenu annuel</div><div class="kpi-value">${eur(p.yearly_income)}</div></div>
      <div class="card" data-section="dette"><div class="kpi-label">Dette totale</div><div class="kpi-value">${eur(p.total_debt)}</div>
        <div class="text-xs text-slate-500 mt-1">${p.debt_to_income_ratio == null ? "n/d" : p.debt_to_income_ratio.toLocaleString("fr-FR")} fois le revenu annuel</div></div>
      <div class="card" data-section="score"><div class="kpi-label">Score de crédit</div><div class="kpi-value">${esc(p.credit_score)}</div></div>
      <div class="card" data-section="depenses"><div class="kpi-label">Dépenses 30 jours</div><div class="kpi-value">${eur(k.depenses_30j)}</div>
        <div class="text-xs text-slate-500 mt-1">${esc(k.transactions_30j)} opérations · ${esc(k.incidents_30j)} incident(s)</div></div>
    </div>

    <div class="space-y-2" data-section="alertes">${alerts}</div>

    <div class="grid grid-cols-1 xl:grid-cols-2 gap-4">
      <div class="card" data-section="cartes"><div class="font-semibold mb-2">Cartes</div>${cards || '<div class="text-sm text-slate-400">Aucune carte</div>'}</div>
      <div class="card" data-section="categories"><div class="font-semibold mb-2">Top dépenses 30 jours</div>${cats}</div>
    </div>

    <div class="card" data-section="mensuel"><div class="font-semibold mb-2">Dépenses mensuelles</div><div class="h-56"><canvas id="monthly-chart"></canvas></div></div>

    <div class="card overflow-x-auto" data-section="transactions"><div class="font-semibold mb-2">Dernières opérations</div>
      <table class="w-full text-sm"><tbody>${tx}</tbody></table></div>`;

  if (state.chart) state.chart.destroy();
  state.chart = new Chart($("monthly-chart"), {
    type: "bar",
    data: { labels: monthly.map((m) => m.mois), datasets: [{ data: monthly.map((m) => m.depenses), backgroundColor: "#047857", borderRadius: 4, maxBarThickness: 48 }] },
    options: { maintainAspectRatio: false, plugins: { legend: { display: false } }, scales: { y: { ticks: { callback: (v) => eur(v) } } } },
  });
}

/* ---------- Écran piloté par l'agent (événements "ui") ---------- */
const LEVELS = ["critique", "attention", "info", "positif"];
const HL_CLASSES = ["agent-hl", ...LEVELS.map((l) => `hl-${l}`)];
const METRIC_COLOR = { critique: "text-red-700", attention: "text-amber-700", info: "text-sky-700", positif: "text-emerald-700" };
const PIE_COLORS = ["#047857", "#10b981", "#6ee7b7", "#0ea5e9", "#f59e0b", "#ef4444", "#8b5cf6", "#64748b"];
const motion = () => (matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth");
const level = (l) => (LEVELS.includes(l) ? l : "info");

function resetAgentCanvas() {
  state.agentCharts.forEach((c) => c.destroy());
  state.agentCharts = [];
}

function clearHighlights() {
  document.querySelectorAll(".agent-hl").forEach((el) => el.classList.remove(...HL_CLASSES));
  document.querySelectorAll(".agent-note").forEach((n) => n.remove());
}

// Panneau commun : badge Agent, titre, contenu, note optionnelle, bouton × ; ajouté en tête du canvas
function agentPanel(title, bodyHtml, note, onClose) {
  const panel = document.createElement("div");
  panel.className = "card agent-panel";
  panel.innerHTML = `
    <div class="flex items-center gap-2 mb-2">
      <span class="agent-badge">Agent</span>
      <span class="font-semibold">${esc(title)}</span>
      <button type="button" class="ml-auto text-slate-400 hover:text-slate-700 text-lg leading-none" title="Fermer">×</button>
    </div>
    ${bodyHtml}
    ${note ? `<div class="text-xs text-slate-500 mt-2">${esc(note)}</div>` : ""}`;
  panel.querySelector("button").addEventListener("click", () => {
    if (onClose) onClose();
    panel.remove();
  });
  $("agent-canvas").prepend(panel);
  panel.scrollIntoView({ behavior: motion(), block: "nearest" });
  return panel;
}

function showChart(ui) {
  const pie = ui.chart_type === "pie";
  const type = ["bar", "line", "pie"].includes(ui.chart_type) ? ui.chart_type : "bar";
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
        backgroundColor: pie ? PIE_COLORS : type === "line" ? "rgba(4,120,87,.12)" : "#047857",
        borderColor: pie ? "#fff" : "#047857",
        fill: type === "line",
        tension: 0.3,
        borderRadius: type === "bar" ? 4 : 0,
        maxBarThickness: 48,
      }],
    },
    options: {
      maintainAspectRatio: false,
      plugins: {
        legend: { display: pie, position: "right" },
        tooltip: pie ? {} : { callbacks: { label: (c) => eur(c.parsed.y) } },
      },
      scales: pie ? {} : { y: { ticks: { callback: (v) => eur(v) } } },
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
  void el.offsetWidth; // relance l'animation si la zone était déjà encadrée
  el.classList.add("agent-hl", `hl-${level(ui.level)}`);
  el.querySelector(":scope > .agent-note")?.remove();
  const note = document.createElement("div");
  note.className = "agent-note";
  note.textContent = ui.message || "";
  el.appendChild(note);
  el.scrollIntoView({ behavior: motion(), block: "center" });
}

function applyUi(ui) {
  if (!ui || !$("agent-canvas")) return; // vue 360 pas encore chargée
  const fn = { chart: showChart, table: showTable, metric: showMetric, highlight }[ui.action];
  if (fn) fn(ui);
}

/* ---------- Chat ---------- */
function renderSuggestions() {
  $("suggestions").innerHTML = SUGGESTIONS.map((s) =>
    `<button class="text-xs border border-slate-200 rounded-full px-3 py-1 hover:bg-slate-50">${esc(s)}</button>`).join("");
  $("suggestions").querySelectorAll("button").forEach((b) => b.addEventListener("click", () => send(b.textContent)));
}

function addMessage(role, html) {
  const div = document.createElement("div");
  div.className = role === "user" ? "flex justify-end" : "";
  div.innerHTML = role === "user"
    ? `<div class="bg-slate-900 text-white rounded-2xl rounded-br-sm px-3 py-2 max-w-[85%]">${html}</div>`
    : `<div class="bg-slate-50 border border-slate-200 rounded-2xl rounded-bl-sm px-3 py-2 prose-chat">${html}</div>`;
  $("messages").appendChild(div);
  $("messages").scrollTop = $("messages").scrollHeight;
  return div;
}

function renderAnswer(answer) {
  const md = answer.replace(/\[([^\]\n]{1,80})\](?!\()/g, '<span class="source-tag">$1</span>');
  return DOMPurify.sanitize(marked.parse(md));
}

const scrollChat = () => ($("messages").scrollTop = $("messages").scrollHeight);

// Bulle assistant en 4 zones : pastilles d'étapes, pensées, texte en direct, pied (latence)
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
    z(".b-live").textContent = t; // texte brut : textContent échappe
  };
  const current = () => state.clientId === clientId; // ignore un flux d'un client quitté

  b.fail = (msg) => {
    b.finished = true;
    if (current()) state.threadId = null; // un tour interrompu peut laisser le fil incohérent : on repart proprement
    if (!live) z(".b-live").textContent = "";
    const err = document.createElement("div");
    err.className = "text-red-600 mt-1";
    err.textContent = `Erreur : ${msg}`;
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
      z(".b-steps").appendChild(chip);
      chips.set(d.id, chip);
      scrollChat();
    },
    step_done: (d) => {
      const chip = chips.get(d.id);
      if (!chip) return;
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
    error: (d) => b.fail(d.message || "erreur inconnue"),
  };
  return b;
}

async function send(message, brief = false) {
  if (!state.clientId || state.busy) return;
  const text = (message ?? "").trim();
  if (!brief && !text) {
    showError("Écrivez une question d'abord");
    return;
  }
  showError("");
  clearHighlights();
  state.busy = true;
  state.abort = new AbortController();
  $("send-btn").disabled = $("brief-btn").disabled = true;
  addMessage("user", esc(brief ? "Préparer ma fiche de rendez-vous" : text));
  const bubble = assistantBubble(state.clientId);
  try {
    await streamChat({ client_id: state.clientId, message: text, thread_id: state.threadId, brief }, bubble.handlers, state.abort.signal);
    if (!bubble.finished) bubble.fail("flux interrompu avant la réponse");
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

/* ---------- Initialisation ---------- */
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
  $("status").textContent = h.db ? "Base connectée" : "Base injoignable";
}).catch(() => ($("status").textContent = "Agent injoignable"));

loadClients();
