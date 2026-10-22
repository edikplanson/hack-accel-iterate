# Horizon Copilot

AI copilot for bank advisors at a fictional bank (Horizon Bank), built for the Accel AI Innovate Paris hackathon.

The advisor picks a client, gets a 360 view (profile, cards, KPIs, alerts, spending), and asks the agent questions. The agent answers with cited sources and drives the screen live: it highlights dashboard sections and adds charts, tables and metrics as it works.

```
Browser -> frontend (nginx, static page) -> agent (FastAPI + LangGraph + Claude) -> Aiven PostgreSQL (client data)
                                                                                -> Aiven OpenSearch (internal bank rules)
```

## Structure

| Folder | Role |
|---|---|
| `frontend/` | Advisor UI: client list, 360 view, alerts, streamed chat with sources and agent-driven panels. nginx proxies `/api` to the agent. |
| `agent/` | FastAPI API and LangGraph graph. The agent calls SQL tools (profile, cards, spending, transactions, alerts), a read-only free-form SQL tool, internal rules search (OpenSearch, BM25) and `ui_*` tools that drive the screen. |

## Run locally

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

## Agent endpoints

| Method | Route | Description |
|---|---|---|
| GET | `/health` | Agent and database status |
| GET | `/api/clients?q=` | Client list |
| GET | `/api/clients/{id}` | 360 view: profile, cards, KPIs, alerts, recent transactions |
| GET | `/api/clients/{id}/monthly` | Monthly spending |
| POST | `/api/chat` | `{client_id, message, thread_id?}`: agent answer, tools used, latency |
| POST | `/api/chat/stream` | `{client_id, message, thread_id?, brief?}`: SSE stream (`start`, `step`, `step_done`, `ui`, `token`, `answer`, `error`); the agent drives the screen live |
| POST | `/api/clients/{id}/brief` | Meeting preparation brief |

## Data

Transactions come from the Kaggle dataset "Financial Transactions Dataset: Analytics" (synthetic). Amounts are shown in euros. Dates are shifted so the latest transaction falls on the day before loading (the offset is recorded in the `app_meta` table). Card numbers are masked and CVVs removed.

The database is expected to be loaded already; the loading script (`seed-bdd/`) was removed from the repo in commit `da2c735`.

Internal bank rules are articles from the bank's PDF policies, indexed in OpenSearch. They are written in English.
