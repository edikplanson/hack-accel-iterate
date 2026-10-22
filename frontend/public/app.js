const API = "/api";
const state = { clientId: null, threadId: null, chart: null, busy: false };

const TOOL_LABELS = {
  get_client_profile: "Profil",
  get_cards: "Cartes",
  get_spending_summary: "Dépenses",
  get_recent_transactions: "Transactions",
  get_client_alerts: "Alertes",
  search_bank_rules: "Règles internes",
  run_readonly_sql: "Requête SQL",
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
};

async function selectClient(id) {
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

    <div class="grid grid-cols-2 xl:grid-cols-4 gap-4">
      <div class="card"><div class="kpi-label">Revenu annuel</div><div class="kpi-value">${eur(p.yearly_income)}</div></div>
      <div class="card"><div class="kpi-label">Dette totale</div><div class="kpi-value">${eur(p.total_debt)}</div>
        <div class="text-xs text-slate-500 mt-1">${p.debt_to_income_ratio == null ? "n/d" : p.debt_to_income_ratio.toLocaleString("fr-FR")} fois le revenu annuel</div></div>
      <div class="card"><div class="kpi-label">Score de crédit</div><div class="kpi-value">${esc(p.credit_score)}</div></div>
      <div class="card"><div class="kpi-label">Dépenses 30 jours</div><div class="kpi-value">${eur(k.depenses_30j)}</div>
        <div class="text-xs text-slate-500 mt-1">${esc(k.transactions_30j)} opérations · ${esc(k.incidents_30j)} incident(s)</div></div>
    </div>

    <div class="space-y-2">${alerts}</div>

    <div class="grid grid-cols-1 xl:grid-cols-2 gap-4">
      <div class="card"><div class="font-semibold mb-2">Cartes</div>${cards || '<div class="text-sm text-slate-400">Aucune carte</div>'}</div>
      <div class="card"><div class="font-semibold mb-2">Top dépenses 30 jours</div>${cats}</div>
    </div>

    <div class="card"><div class="font-semibold mb-2">Dépenses mensuelles</div><div class="h-56"><canvas id="monthly-chart"></canvas></div></div>

    <div class="card overflow-x-auto"><div class="font-semibold mb-2">Dernières opérations</div>
      <table class="w-full text-sm"><tbody>${tx}</tbody></table></div>`;

  if (state.chart) state.chart.destroy();
  state.chart = new Chart($("monthly-chart"), {
    type: "bar",
    data: { labels: monthly.map((m) => m.mois), datasets: [{ data: monthly.map((m) => m.depenses), backgroundColor: "#047857", borderRadius: 4, maxBarThickness: 48 }] },
    options: { maintainAspectRatio: false, plugins: { legend: { display: false } }, scales: { y: { ticks: { callback: (v) => eur(v) } } } },
  });
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

function renderAnswer(r) {
  const md = r.answer.replace(/\[([^\]\n]{1,80})\](?!\()/g, '<span class="source-tag">$1</span>');
  const steps = r.steps.length
    ? `<div class="mt-2 pt-2 border-t border-slate-200 flex flex-wrap gap-1 text-[11px] text-slate-500">
         ${r.steps.map((s) => `<span class="border border-slate-200 rounded px-1.5 bg-white">${esc(TOOL_LABELS[s.tool] || s.tool)} ${s.ok === false ? "✗" : "✓"}</span>`).join("")}
         <span class="ml-auto">${r.latency_s} s</span></div>`
    : "";
  return DOMPurify.sanitize(marked.parse(md)) + steps;
}

async function send(message, path = "/chat") {
  if (!state.clientId || state.busy) return;
  const text = (message ?? "").trim();
  if (path === "/chat" && !text) {
    showError("Écrivez une question d'abord");
    return;
  }
  showError("");
  state.busy = true;
  $("send-btn").disabled = $("brief-btn").disabled = true;
  addMessage("user", esc(path === "/chat" ? text : "Préparer ma fiche de rendez-vous"));
  const pending = addMessage("assistant", '<span class="dots text-slate-400"><span>●</span><span>●</span><span>●</span></span>');
  try {
    const body = path === "/chat" ? JSON.stringify({ client_id: state.clientId, message: text, thread_id: state.threadId }) : undefined;
    const r = await api(path === "/chat" ? "/chat" : `/clients/${state.clientId}/brief`, { method: "POST", body });
    if (path === "/chat") state.threadId = r.thread_id;
    pending.querySelector("div").innerHTML = renderAnswer(r);
  } catch (e) {
    pending.querySelector("div").innerHTML = `<span class="text-red-600">Erreur : ${esc(e.message)}</span>`;
  } finally {
    state.busy = false;
    $("send-btn").disabled = $("brief-btn").disabled = false;
    $("messages").scrollTop = $("messages").scrollHeight;
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
$("brief-btn").addEventListener("click", () => send("", "/brief"));
let searchTimer;
$("search").addEventListener("input", (e) => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => loadClients(e.target.value.trim()), 250);
});

fetch("/health/agent").then((r) => r.json()).then((h) => {
  $("status").textContent = h.db ? "Base connectée" : "Base injoignable";
}).catch(() => ($("status").textContent = "Agent injoignable"));

loadClients();
