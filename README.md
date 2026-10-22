<p align="center">
  <img src="assets/logo.png" alt="Horizon Bank" width="96">
</p>

<h1 align="center">Horizon Copilot</h1>

<p align="center">
  <b>An AI copilot for bank advisors: it reads the client's data, checks the bank's internal rules, cites its sources and drives the advisor's screen live.</b>
</p>

<p align="center">
  <a href="https://01a1634d-285c-70fa-9e3a-a114692a854f-8080.eur-1.aiven.app/"><b>👉 Try the live demo 👈</b></a><br>
  <code>https://01a1634d-285c-70fa-9e3a-a114692a854f-8080.eur-1.aiven.app/</code>
</p>

<p align="center">Built at the Accel AI Innovate Paris hackathon · Deployed on Aiven Runtime</p>

---

## The problem

Before and during a client meeting, a bank advisor has to juggle several tools: the client file, card details, transaction history, and the bank's internal policies (card limits, overdraft fees, credit eligibility, KYC and anti-money-laundering checks, fraud monitoring). These policies live in long PDFs. Answering a simple question like *"Can I raise this client's card limit?"* means finding the client's figures, finding the right article in the right policy, and comparing the two.

## What Horizon Copilot does

The advisor opens a client and gets a **360° view**: profile, debt ratio, credit score, bank cards, 30-day spending, top spending categories, monthly trend, recent transactions and **automatic alerts** (compromised card, expired card, payments declined for insufficient balance, low credit score).

Next to it, the advisor **asks questions in plain language**. The agent:

1. **Gets the client's facts** from the bank database (profile, cards, spending, transactions, alerts, or a read-only SQL query when nothing else fits).
2. **Checks the internal rules** that apply, by searching the bank's policy articles and following their cross-references.
3. **Compares the two** and gives a short, actionable answer: finding, risk or opportunity, proposed action.
4. **Cites every statement**, e.g. `[clients#825]`, `[transactions client 825, 3 months]`, `[Card Limits and Card Operation §CARD-2.1]`.
5. **Drives the screen while it works**: it highlights the dashboard areas it relies on and adds charts, tables and key figures as the analysis goes on.

The answer, the tool steps ("PostgreSQL · Cards", "Checking internal rules"...) and the screen updates are **streamed in real time**.

A **"Prepare meeting"** button generates a full brief: the situation in 3 lines, points of attention, sales opportunities, and 3 questions to ask the client.

### Example questions

- *What are the risks for this client?*
- *Can we raise their card limit?*
- *How has their spending changed over 3 months?*
- *Which product should we offer them?*

## Why it can be trusted

- **Two separate sources, never mixed.** Client figures come only from PostgreSQL; limits, thresholds, fees and procedures come only from the internal rules. The agent is instructed never to answer a rule question from memory and to say so when no article matches.
- **Sources on every claim**, so the advisor can check anything.
- **Charts and metrics only show real data.** The screen tools take their values from the data tools, and the charts triggered by keywords in the question are computed directly in PostgreSQL, never by the model.
- **The free-form SQL tool is read-only**: a single `SELECT`/`WITH`, run in a read-only transaction with a 5-second timeout and at most 200 rows.

## How it works

```
Browser ──► frontend (nginx, static page) ──► agent (FastAPI + LangGraph + Claude) ──► Aiven PostgreSQL  (client data)
                                                                                  └──► Aiven OpenSearch (internal bank rules)
```

| Part | Role |
|---|---|
| `frontend/` | Advisor UI in vanilla JS (Tailwind, Chart.js): client list, 360° view, alerts, streamed chat with sources and the panels the agent adds. nginx serves the page and proxies `/api` to the agent. |
| `agent/` | FastAPI API and a LangGraph agent built on Claude. The agent loops between reasoning and tool calls until it can answer. |

The agent has three groups of tools:

| Group | Tools | What they do |
|---|---|---|
| Client data (PostgreSQL) | `get_client_profile`, `get_cards`, `get_spending_summary`, `get_recent_transactions`, `get_client_alerts`, `run_readonly_sql` | Facts about the client |
| Internal rules (OpenSearch) | `search_bank_rules`, `get_bank_rule` | Full-text search over the policy articles (common framework, card limits, card blocking and disputes, overdraft and incident fees, credit eligibility, KYC and AML/CFT, savings, anti-fraud), and fetching articles by id to follow cross-references |
| Screen | `ui_highlight`, `ui_show_chart`, `ui_show_table`, `ui_show_metric` | Describe a display change; the backend relays it to the browser as a live event |

**Stack:** Python 3.12, FastAPI, LangGraph, LangChain Anthropic (Claude Sonnet 5.5 by default), psycopg 3, opensearch-py, nginx, Docker Compose, Aiven (PostgreSQL, OpenSearch, Runtime).

## Run it locally

```bash
cp .env.example .env   # fill in at least DATABASE_URL, OPENSEARCH_URL and ANTHROPIC_API_KEY
docker compose up --build
```

Then open http://localhost:8080. The agent API docs are at http://localhost:8000/docs.

For hot reload during development, run `docker compose up --build --watch`: changes to `agent/app` and `frontend/public` are synced into the containers.

### Environment variables

| Variable | Used by | Description |
|---|---|---|
| `DATABASE_URL` | agent | Aiven PostgreSQL service URI (client data) |
| `OPENSEARCH_URL` | agent | Aiven OpenSearch service URI (internal rules, credentials included) |
| `OPENSEARCH_INDEX` | agent | Rules index, default `rules-current` |
| `ANTHROPIC_API_KEY` | agent | Claude API key |
| `ANTHROPIC_MODEL` | agent | Default `claude-sonnet-5-5` |
| `BANK_NAME` | agent | Bank name used in the system prompt |
| `AGENT_URL` | frontend | Leave empty locally; on Aiven Runtime, the agent app's public URL if needed |

## Deploy on Aiven Runtime

1. Push the repo to GitHub.
2. Aiven console > Runtime > Deploy application, pick the repo and `docker-compose.yml`, then Scan and Deploy.
3. On the `agent` app: Connected services > Connect service > the existing PostgreSQL, as variable `DATABASE_URL`. Do the same for OpenSearch (`OPENSEARCH_URL`). Add `ANTHROPIC_API_KEY` as a secret.
4. If the frontend shows "Agent unreachable", set `AGENT_URL` on the `frontend` app to the public URL of the `agent` app, then redeploy.
5. A push does not redeploy automatically: Overview > Actions > Deploy latest commit.

The database is intentionally not declared in `docker-compose.yml`, otherwise Runtime would create a new PostgreSQL service.

## Agent API

| Method | Route | Description |
|---|---|---|
| GET | `/health` | Agent and database status |
| GET | `/api/clients?q=` | Client list (search by name, number or address) |
| GET | `/api/clients/{id}` | 360° view: profile, cards, KPIs, alerts, recent transactions |
| GET | `/api/clients/{id}/monthly` | Monthly spending |
| POST | `/api/chat` | `{client_id, message, thread_id?}`: agent answer, tools used, latency |
| POST | `/api/chat/stream` | `{client_id, message, thread_id?, brief?}`: SSE stream (`start`, `step`, `step_done`, `ui`, `token`, `answer`, `error`); the agent drives the screen live |
| POST | `/api/clients/{id}/brief` | Meeting preparation brief |

## Data

- **Clients, cards and transactions** come from the Kaggle dataset "Financial Transactions Dataset: Analytics" (synthetic). Amounts are shown in euros. Dates are shifted so the latest transaction falls on the day before loading (the offset is recorded in the `app_meta` table). Card numbers are masked and CVVs removed. The dataset has no names, so each client gets a fictitious, stable name derived from their id.
- **Internal bank rules** are articles from the bank's PDF policies, indexed in OpenSearch. They are written in English.

The database and the rules index are expected to be loaded already; the loading script (`seed-bdd/`) was removed from the repo in commit `da2c735`.
