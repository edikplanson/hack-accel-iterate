# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Horizon Copilot: an AI copilot for bank advisors (fictional Horizon Bank, hackathon project). The advisor picks a client, sees a 360 view, and chats with a LangGraph + Claude agent that answers with cited sources and drives the dashboard live (highlights, charts, tables, metrics).

```
Browser -> frontend (nginx, static) -> agent (FastAPI + LangGraph + Claude) -> Aiven PostgreSQL (client data)
                                                                           -> Aiven OpenSearch (internal bank rules)
```

## Commands

```bash
cp .env.example .env                 # needs DATABASE_URL, OPENSEARCH_URL, ANTHROPIC_API_KEY
docker compose up --build            # frontend http://localhost:8080, agent API docs http://localhost:8000/docs
docker compose up --build --watch    # hot reload: agent/app sync+restart, frontend/public synced (no restart)
```

Running the agent outside Docker: `cd agent && pip install -r requirements.txt && uvicorn app.main:app --port 8000` (`db.py` calls `load_dotenv()`, which picks up the root `.env`). The frontend calls relative `/api` paths, so it must be served behind the nginx proxy (or equivalent).

There are no tests, linter or build step. Verify changes by running the stack and hitting `/health`, `/api/clients/{id}`, or `/api/chat/stream`.

## Architecture

**External data is not in this repo.** PostgreSQL (`clients`, `cards`, `transactions`, `app_meta`) and the OpenSearch rules index (`rules-current`) must already be loaded; the seed script was removed. The database is deliberately not declared in `docker-compose.yml` (Aiven Runtime would otherwise provision a new one). `KAGGLE_*`/`TX_MONTHS` in `.env.example` are leftovers from that seed script.

### agent/app

- `main.py`: FastAPI routes. Dashboard endpoints return `data.*` results directly. Chat has a blocking variant (`/api/chat`, `_run`) and an SSE variant (`/api/chat/stream`, `_stream`) that emits `start`, `step`, `step_done`, `ui`, `token`, `answer`, `error`. `brief: true` substitutes the fixed `BRIEF_PROMPT`.
- `graph.py`: single-node ReAct loop (`agent` <-> `ToolNode`) with `InMemorySaver` per `thread_id` (conversation memory is lost on restart). The system prompt is rebuilt every turn with the client id and the data reference date. Do not pass `temperature` to `ChatAnthropic` (the model rejects non-default values); keep `max_tokens` headroom for adaptive thinking.
- `tools.py`: tools bound to the LLM. Data tools wrap `data.py`/`rules.py` and return JSON with a `source` field the model cites. `run_readonly_sql` guards with a regex plus `db.query_readonly` (read-only transaction, 5s timeout, 200 rows). Adding a tool means adding it to `TOOLS` and to `TOOL_LABELS` in `frontend/public/app.js`.
- `data.py`: all SQL, shared by the dashboard API and the agent tools. Client names are synthetic, derived in SQL from `client_id` + gender (`NAME_SQL`). Rolling windows ("30 days", "N months") are relative to `reference_date()` = `max(tx_date)` (cached), not `now()`.
- `rules.py`: OpenSearch BM25 search and fetch-by-article-id (e.g. `CARD-2.1`). Articles carry `refs` the agent follows with `get_bank_rule`.

### How the agent drives the screen

`ui_*` tools never touch the database; they return `{"ok": true, "ui": {...}}`. `_stream` parses each `ToolMessage`, and any JSON with a `ui` key is forwarded as an SSE `ui` event that `applyUi()` in `app.js` renders (`chart`, `table`, `metric`, `highlight`).

Separately, `keyword_ui()` in `main.py` emits deterministic `ui` events before the LLM runs, based on keywords in the question (FR and EN, accent-folded), using values queried from PostgreSQL.

**French ids are a cross-file contract.** The LLM-facing API is English (`income`, `debt`, `warning`...), but the frontend `data-section` attributes and levels use French ids (`revenu`, `dette`, `depenses`, `alertes`, `cartes`, `mensuel`, `critique`, `attention`, `positif`...). `_SECTION_ID`/`_LEVEL_ID` in `tools.py` map between them, and `keyword_ui` uses the French ids directly. Several SQL result keys are also French (`nom`, `mois`, `depenses`, `depenses_30j`) and are read by `app.js`. Renaming any of these requires changing both sides.

### frontend

Static vanilla JS (`public/app.js`), Tailwind via the vendored CDN build, Chart.js, marked + DOMPurify for rendering answers. `nginx.conf.template` proxies `/api/` to `${AGENT_URL}` with buffering off (required for SSE); envsubst only runs at container start. On Aiven Runtime, if the frontend shows "Agent unreachable", set `AGENT_URL` to the agent app's public URL.

## Conventions

- User-facing text, agent prompts and tool docstrings are in English; some code comments and infra files are in French.
- Amounts are displayed in euros. The system prompt forbids em dashes and double hyphens in answers; keep that if editing it.
- Default model is set via `ANTHROPIC_MODEL` (default `claude-sonnet-5-5`) in `docker-compose.yml`, `.env.example` and `graph.py`.
