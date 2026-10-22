# Main-branch agent adaptation

This directory adapts the existing Horizon frontend and public PostgreSQL client/card/transaction schema. Changes are confined to agent/ on dev-arthur-agents; main and the other dev branches remain untouched. It does not replace the frontend, import the reference workbook or change the banking schema. Prior implementation/subagent work remains available on hackathon/integration and the specialist branches; only reusable session, evidence, TLS and inference-budget parts were ported here.

## Preserved frontend contracts

The served frontend can keep calling:

- GET /health, /api/clients?q=&limit=, /api/clients/{id}, /api/clients/{id}/monthly?months=.
- POST /api/chat with `{"client_id":825,"message":"Quels sont les risques sur ce client ?","thread_id":null}`.
- POST /api/clients/{id}/brief.

Responses retain answer, thread_id, steps (`tool`, `args`, `ok`) and latency_s. Added fields include status, issue_summary, clarification_questions, sources, warnings, trace_id and session_id. Guidance/questions/warnings are also included in answer so the existing frontend can display them without a frontend deployment. Chart/action execution is not added.

For example, the chat response retains this shape (values below are illustrative):

```json
{"answer":"Demande concernant une carte…","thread_id":"client-825-example","steps":[{"tool":"Données client","args":{},"ok":true}],"latency_s":0.5,"session_id":"client-825-example","status":"needs_clarification","issue_summary":"Demande concernant une carte","clarification_questions":["Quelle carte est concernée ?"],"sources":[{"source_id":"clients#825","kind":"database","title":"Profil client","table":"clients","record_id":"825"}],"warnings":["Synthèse Claude désactivée; aucun appel d’inférence."],"trace_id":"example-trace"}
```

An explicit client_id is the advisor's selected dossier. Reusing a thread for another client returns 409. Transcript-supplied IDs cannot switch the selected customer. The current banking dataset has no names, phone numbers or assurance-vie contracts: these gaps are reported explicitly; workbook facts are never substituted.

## Workflow

LangGraph runs three bounded nodes: resolve issue/identity, retrieve relevant facts and documentation, compose advisor guidance. There is no free-SQL model tool or unbounded model/tool loop. The customer repository binds its allowed ID at construction, rejects other IDs/categories, uses parameterized SQL and validates returned ownership. Banking queries run in read-only transactions with bounded results/timeouts. Reference dates are refreshed on each read rather than cached for process lifetime.

Sessions, canonical turns, revisions, worker leases, execution events and model usage persist under the app schema. Explicit migrations create only application tables; existing public banking tables remain untouched. Two workers process independent sessions; a session is serialized by a lease. Duplicate/corrected/interim events persist, stale results are discarded, and crashed work is reclaimable after its 180-second lease. Pending work does not depend on an open browser.

Documentation uses lexical OpenSearch retrieval and explicit page-aware PDF ingestion, ported from the documentation specialist. Complete corpus replacement is published through an atomic alias; unchanged ingestion is idempotent. Failed replacement leaves the previous corpus active. Historical/staged indexes need operator cleanup; run one ingestion operator at a time. Effective dates, versions, content hashes and citations are retained. Structured sidecar conflicts are detected; arbitrary free-text contradictions require human review. Applicability is never inferred from ranking alone.

The demo PDF is clearly fictional assurance-vie documentation, not Horizon or BNP policy. It does not prove a real client's contract exists. Supply actual validated card/credit PDFs before presenting card/credit procedures as supported. Missing/unavailable/conflicting evidence remains visible and never becomes silent mock success.

## Configure and start

Use Python 3.12. Merge the needed names from agent/.env.example into the existing ignored root .env or Runtime secrets. Do not overwrite credentials. Existing DATABASE_URL, ANTHROPIC_API_KEY, ANTHROPIC_MODEL and BANK_NAME names remain supported. Managed PostgreSQL enforces verify-full; provide DATABASE_CA_CERT inline PEM or DATABASE_SSLROOTCERT path when the system CA store is insufficient. OpenSearch uses a host-only HTTPS URL, separate username/password and OPENSEARCH_CA_PEM or OPENSEARCH_CA_CERT. Local plaintext requires ALLOW_INSECURE_LOCAL=true and an allowlisted local host.

```sh
uv venv --python 3.12 .tmp/codex/main-agent/venv
uv pip install --python .tmp/codex/main-agent/venv/bin/python -r agent/requirements.txt pytest httpx
.tmp/codex/main-agent/venv/bin/python -m agent.app.migrate
.tmp/codex/main-agent/venv/bin/python -m agent.app.ingest_documents --directory agent/demo-documents
.tmp/codex/main-agent/venv/bin/uvicorn agent.app.main:app --host 127.0.0.1 --port 8000
```

Use a migration/ingestion credential for explicit setup; runtime needs SELECT on banking tables and app-state writes. No automatic import/migration occurs on every start. /health preserves the frontend's db boolean and hides connection errors. /ready additionally checks persistence, banking reads, search availability and workers.

```sh
docker build -t horizon-copilot-agent:main-adapted agent
docker run --env-file .env -p 8000:8000 horizon-copilot-agent:main-adapted
```

The image runs as UID 10001 and excludes local env/keys/tests from its build context. The existing frontend and root deployment manifests were deliberately left unchanged. Set new environment variables directly on the Runtime agent service; root docker-compose.yml forwards only its original variable list. Runtime does not run arbitrary one-off migrations: execute controlled setup commands before updating the agent application. Do not redeploy the existing frontend or provision another database for this code adaptation.

Runtime steps were checked against [deploy applications](https://aiven.io/docs/products/runtime/deploy-apps), [Compose files](https://aiven.io/docs/products/runtime/manifest-files/compose-files), and [connect existing services](https://aiven.io/docs/products/runtime/connect-services-to-apps). No Aiven redeployment or managed-service verification is claimed in this adaptation.

## Inference and cost

MODEL_ENABLED defaults false, even when a key exists. Guidance currently uses deterministic retrieved facts and passages, with zero Anthropic requests. Opt-in synthesis verifies the exact account model first, makes one bounded request with no retries, validates citation IDs/numbers and persists latency/token/cost accounting. PostgreSQL atomically reserves budget and concurrent slots before inference; uncertain failures retain conservative reservations. Default application budget is $5 and configuration above the overall $300 ceiling is rejected. This limit does not cover external coding-agent use or cloud infrastructure. Tooling did not expose coding-agent billing.

Main's configured model claude-sonnet-5-5 is documented with $2/$10 per million input/output tokens; its API rejects temperature, so this integration omits that parameter. Verify account availability/pricing before enabling. See the [official model page](https://platform.claude.com/docs/en/models/sonnet-5-5/overview). No provider/baseline metrics have been fabricated.

## Transcript / future voice boundary

New integration routes are separate from the already served frontend:

1. POST /api/sessions with Authorization Bearer ADVISOR_API_TOKEN creates a call.
2. POST /api/transcript-events with Authorization Bearer TRANSCRIPT_TOKEN accepts:

```json
{"session_id":"call-1","event_id":"event-1","turn_id":"turn-1","revision":1,"speaker":"customer","text":"Mon identifiant est 825. Je souhaite vérifier ma carte.","is_final":true,"timestamp":"2026-10-22T10:00:00Z","kind":"transcript"}
```

3. GET /api/sessions/{id} returns state/candidates without banking facts until advisor confirmation.
4. POST /api/sessions/{id}/customer with advisor token and `{"client_id":825}` confirms scope. The transcript token cannot confirm a customer.
5. GET /api/sessions/{id}/events is authenticated SSE; reconnect with Last-Event-ID or after. format=json returns bounded replay pages. Fetch the current session after answer_produced/failed. These events are execution summaries, not historical answer snapshots.
6. A new final event with kind=end_call and empty text closes the call. A changed turn requires greater revision; duplicate event IDs cannot carry different content. Interim turns persist without inference.

No ElevenLabs adapter is claimed. Verify the actual product's live transcript transport/payload and signature scheme before implementing a provider-specific boundary. Validate raw signatures and timestamps, bind provider conversation IDs to application sessions, preserve event/turn/revision IDs, and map end-of-call explicitly. A post-call webhook is not a live transcript. Provider events cannot grant advisor scope. The existing frontend does not yet expose the new transcript controls; its current chat/brief workflow already uses the same durable state internally.

The existing main dashboard/chat access model was preserved for frontend compatibility. It is a public synthetic-data demo surface, not newly authenticated per-advisor access. Put it behind Runtime identity/network access before using private customer data. New transcript/session routes require separate tokens and default to unavailable when unconfigured.

## Validation

```sh
PYTHONPATH=. .tmp/codex/main-agent/venv/bin/python -m pytest -q --basetemp=.tmp/codex/main-agent/tests agent/tests
```

Fixtures are explicitly synthetic records shaped like main's schema. Optional TEST_DATABASE_URL integration checks refuse managed hosts and any database except local copilot_main_agent_test; they create small fixture tables, change/restore a test income, check API shapes and verify persistence after pool replacement. Do not point tests at the served database. Prior reference-workbook evaluations are preserved on the integration branch and are not measurements of this main-schema adaptation.

Recorded on 2026-10-22: **42 tests passed in 6.92 seconds** from an export of tracked agent files, including the dedicated local PostgreSQL check. Updating the fixture income from 48000 to 51000 changed the next answer without restarting the API. The Docker build from that same export succeeded. Local HTTP checks against PostgreSQL/OpenSearch passed for customer confirmation, corrected/duplicate transcripts, scoped facts, page/version citations, separate tokens, SSE framing/replay and session recovery after container replacement. PDF ingestion published three chunks and then returned unchanged on reingestion. One local chat reported 0.21 seconds; this is a single synthetic sample with inference disabled, not a benchmark. [Recorded results](validation-results.json) distinguish this local run from cloud/provider validation.

Anthropic requests and recorded model usage were zero; application inference cost for this run is $0. Aiven access/TLS, Runtime deployment, the served browser journey, live Claude synthesis and a same-model no-data baseline remain unverified. The current-schema adaptation has no completed 20-scenario model comparison. The earlier reference-schema mock scenarios are retained on the integration branch, not presented as current or deployed results.

The earlier specialist worktrees are retained, with clean tracked work at PostgreSQL d1244e3, OpenSearch 0bbf29c, orchestration/evaluations b41f05d and deployment 2ac3cfe. The reference integration is saved at a5e2e88. They are implementation history, not additional runtime services.
