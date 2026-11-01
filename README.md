# RegChange Sentinel

**From Regulatory Intelligence to Automated Banking Compliance Testing.**

RegChange Sentinel turns a regulator's publication into executable compliance
tests, and keeps every step traceable:

```
Regulatory source → document → clause → requirement → CBS business rule
                 → test scenario → execution → evidence
```

A compliance analyst can click a passing test and read the exact paragraph of
the exact circular that caused it to exist.

---

## Contents

- [What it does](#what-it-does)
- [Why the provenance labelling matters](#why-the-provenance-labelling-matters)
- [Architecture](#architecture)
- [Quick start](#quick-start)
- [Configuration](#configuration)
- [The six agents](#the-six-agents)
- [TinyFish integration](#tinyfish-integration)
- [Simulated core banking system](#simulated-core-banking-system)
- [API reference](#api-reference)
- [Database](#database)
- [Testing](#testing)
- [Demo script](#demo-script)
- [Safety model](#safety-model)
- [Known limitations](#known-limitations)
- [Roadmap](#roadmap)

---

## What it does

Banks receive a stream of regulatory updates. Working out what a new circular
means for a core banking system is slow, manual and hard to evidence. This
project automates the path from publication to tested control:

1. **Discover** — TinyFish Search finds publications on official regulator
   domains (scoped with `include_domains`, so results are not filtered
   after the fact).
2. **Extract** — TinyFish Fetch retrieves the document and it is segmented into
   clauses using *the regulator's own numbering*, so a citation reads
   "Para 38", not "chunk 12".
3. **Analyse** — Gemini extracts discrete obligations, each anchored to a
   verbatim quote that is then **verified to exist in the source**.
4. **Map impact** — candidate CBS rules are retrieved lexically, Gemini reasons
   over that shortlist, and any rule code that is not in the catalogue is
   **discarded** before a human sees it.
5. **Generate tests** — positive, negative, boundary, regression and integration
   scenarios, validated against the CBS API contract.
6. **Execute** — approved tests run against a simulated core banking system and
   produce evidence: request/response pairs, screenshots, assertion diffs.

Human approval gates the two places it matters: an impact mapping must be
approved before tests are generated from it, and a state-changing test must be
approved before it runs.

---

## Why the provenance labelling matters

A compliance tool that blurs "the regulator requires this" with "a model
thinks this" is worse than no tool. Every assertion carries one of four labels,
shown on every screen:

| Label | Meaning |
|---|---|
| `EXPLICIT_REQUIREMENT` | Stated by the regulator, with a quote verified to appear in the source text |
| `AI_INTERPRETATION` | A model's reading. Defensible, not an obligation |
| `PROPOSED_CONTROL` | A control a bank *might* implement. The regulation does not demand it |
| `UNVERIFIED_ASSUMPTION` | No locatable support in the source |

The downgrade is automatic and not advisory: if the model claims
`EXPLICIT_REQUIREMENT` but its quote cannot be found in the document, the item
is rewritten to `UNVERIFIED_ASSUMPTION`
([`analysis_agent.py`](backend/app/agents/analysis_agent.py)).

Stored records are also tagged by origin — `LIVE_TINYFISH`, `LIVE_GEMINI`,
`DETERMINISTIC` or `DEMO_FIXTURE` — and the UI renders a distinct badge for
each. Nothing mocked is ever presented as a live agent result.

---

## Architecture

```
┌──────────────────────────── Frontend (React + TS + Vite) ────────────────────────────┐
│ Dashboard · Regulatory Explorer · Impact Analysis · Test Management                   │
│ Execution Monitor · Banking Simulator · Settings          ← SSE live pipeline events  │
└───────────────────────────────────────┬──────────────────────────────────────────────┘
                                        │ /api  (same-origin, Vite proxy in dev)
┌───────────────────────────────────────┴──────────────────────────────────────────────┐
│ FastAPI                                                                               │
│                                                                                       │
│  Agent 1 Discovery ──── TinyFish Search    api.search.tinyfish.ai                     │
│  Agent 2 Extraction ─── TinyFish Fetch     api.fetch.tinyfish.ai                      │
│  Agent 3 Analysis ───── Gemini             generativelanguage.googleapis.com          │
│  Agent 4 Impact ─────── Gemini + deterministic rule-code validation                   │
│  Agent 5 Test gen ───── Gemini + CBS contract validation                              │
│  Agent 6 Execution ──── in-process API · Playwright · TinyFish Browser                │
│                                                                                       │
│  Simulated CBS: customers · KYC · accounts · transactions · maker-checker · audit     │
└───────────────────────────────────────┬──────────────────────────────────────────────┘
                                        │ SQLAlchemy 2 + Alembic
                            ┌───────────┴───────────┐
                            │ Supabase PostgreSQL   │  (SQLite for local dev/CI)
                            └───────────────────────┘
```

Full detail in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

---

## Quick start

**Prerequisites:** Python 3.11+, Node 20+.

### 1. Backend

```bash
cd backend
py -3.11 -m venv .venv
./.venv/Scripts/python.exe -m pip install -e ".[dev]"      # Windows
# source .venv/bin/activate && pip install -e ".[dev]"     # macOS / Linux

cp .env.example .env        # then fill in the keys you have
./.venv/Scripts/python.exe -m alembic upgrade head
./.venv/Scripts/python.exe -m uvicorn app.main:app --port 8000
```

API: <http://127.0.0.1:8000> · interactive docs: <http://127.0.0.1:8000/docs>

### 2. Frontend

```bash
cd frontend
npm install
npm run dev
```

UI: <http://localhost:5173>

### 3. See it work without any API keys

```bash
curl -X POST "http://127.0.0.1:8000/api/demo/seed?reset=true"
```

This loads a **clearly labelled demo fixture**: real document metadata and
official URLs, with synthetic body text. Clause parsing, version diffing,
candidate-rule retrieval and test execution are all real code paths. Everything
it creates shows a "Demo fixture" badge, and each document records an
`access_limitation` naming the credential needed for the authentic text.

Remove it with `curl -X DELETE http://127.0.0.1:8000/api/demo/seed`.

---

## Configuration

All secrets live in `backend/.env` (gitignored). Nothing is required to start:
features that need a credential report themselves as *not configured* rather
than silently degrading.

| Variable | Needed for | Where to get it |
|---|---|---|
| `TINYFISH_API_KEY` | Discovery, extraction, cloud browser, monitors | [agent.tinyfish.ai/api-keys](https://agent.tinyfish.ai/api-keys) — Search free to 12,000/day, Fetch to 1,000 URLs/day |
| `GEMINI_API_KEY` | Analysis, impact mapping, test generation | [aistudio.google.com/apikey](https://aistudio.google.com/apikey) |
| `DATABASE_URL` | Supabase Postgres (omit for local SQLite) | Supabase → Project Settings → Database → URI |
| `INSTITUTION_TYPE` | Regulatory applicability scoping | See [docs/REGULATORY_SCOPE.md](docs/REGULATORY_SCOPE.md) |
| `CBS_PUBLIC_BASE_URL` | TinyFish cloud-browser execution | A publicly reachable deployment or tunnel |

The **Settings** screen shows live configuration status and can probe each
external service on demand.

---

## The six agents

| # | Agent | Technology | Output |
|---|---|---|---|
| 1 | Regulatory Discovery | TinyFish Search | `regulatory_documents` (DISCOVERED) |
| 2 | Regulatory Extraction | TinyFish Fetch | Document versions + `regulatory_clauses` |
| 3 | Regulatory Analysis | Gemini (structured JSON) | `regulatory_changes` with verified quotes |
| 4 | Banking Impact Analysis | Gemini + deterministic validation | `impact_mappings` (pending review) |
| 5 | Test Generation | Gemini + contract validation | `test_cases` (pending approval) |
| 6 | Test Execution | httpx/ASGI · Playwright · TinyFish Browser | `test_executions` + `execution_evidence` |

Design notes worth knowing:

- **Document versioning is append-only.** A re-fetch whose content hash differs
  creates version *N+1* and links `supersedes_id`; the old row and its clauses
  are never modified. ETag / `Last-Modified` validators are requested from
  TinyFish Fetch so an unchanged document costs nothing.
- **Clause matching for diffs** is by the regulator's numbering first, then by
  content similarity, so a renumbered provision reads as `MODIFIED` rather than
  one `REMOVED` plus one `ADDED`.
- **Regulatory text is untrusted input.** It is wrapped in delimiters,
  delimiter-shaped sequences inside it are neutralised, and every system prompt
  states that the block is data, never instructions
  ([`prompts/base.py`](backend/app/prompts/base.py)).

---

## TinyFish integration

Built against the official OpenAPI specifications published at
`https://agent.tinyfish.ai/v1/openapi/{search,fetch,browser,monitor}`
(captured 2026-10-31). No endpoint or parameter here was guessed — see
[docs/TINYFISH_API.md](docs/TINYFISH_API.md) for the captured contract.

| Product | Endpoint | Used for |
|---|---|---|
| Search | `GET https://api.search.tinyfish.ai/` | Domain-scoped discovery via `include_domains` |
| Fetch | `POST https://api.fetch.tinyfish.ai/` | Document retrieval, ETag-based change detection |
| Agent | `POST https://agent.tinyfish.ai/v1/automation/run-sse` | Complex navigation (metered — used sparingly) |
| Browser | `POST https://api.browser.tinyfish.ai/` | Cloud browser over CDP for Playwright |
| Monitor | `POST https://agent.tinyfish.ai/v1/monitors` | Scheduled regulator monitoring |

Auth is `X-API-Key` on every call. Retries cover only transient conditions
(429 / 5xx / network) — a 4xx is raised immediately rather than burning quota.
API keys and browser CDP URLs are redacted from all logs, activity records and
the SSE stream.

> **TinyBrowser and localhost.** The TinyFish browser runs in TinyFish's cloud
> and cannot resolve `localhost`. Local browser tests therefore use Playwright
> directly; the TinyFish driver activates when `CBS_PUBLIC_BASE_URL` points at
> a reachable deployment. Both share one driver interface — it is a config
> flip, not a different code path. The Settings screen states this explicitly
> rather than letting the driver fail mysteriously.

---

## Simulated core banking system

A deliberately realistic Indian CBS, because generated tests need something
real to assert against. **It is a simulation. There is no code path from this
application to a real banking system.**

Fifteen catalogued business rules (`CBS-001`…`CBS-015`) span customer
onboarding, KYC/AML, account management and transactions, with maker-checker
dual control and an immutable audit trail. Every rejection returns a stable
machine code (`INVALID_PAN_FORMAT`, `NO_ADDRESS_PROOF`,
`PER_TXN_LIMIT_EXCEEDED`, `SELF_APPROVAL_FORBIDDEN`, …) so a negative test
asserts on behaviour, not prose.

The impact engine may only reference a rule code that exists in
`business_rules`, validated against the database rather than against the list
shown to the model.

---

## API reference

Interactive docs at `/docs`. Core endpoints:

```
POST   /api/discovery/start              Agent 1 (background job)
GET    /api/discovery/{id}/status
GET    /api/regulations                  list / filter documents
GET    /api/regulations/{id}             document + clauses
GET    /api/regulations/{id}/versions    version history
GET    /api/regulations/{id}/compare     clause-level old-vs-new diff
POST   /api/regulations/extract          Agent 2
POST   /api/analysis/{document_id}       Agent 3 (optional version comparison)
GET    /api/changes                      extracted requirements
PATCH  /api/changes/{id}/review
POST   /api/impact/analyze               Agent 4
GET    /api/impact
PATCH  /api/impact/{id}/review           approval gate for test generation
POST   /api/test-cases/generate          Agent 5
GET    /api/test-cases
PATCH  /api/test-cases/{id}/approve      approval gate for execution
GET    /api/test-cases/{id}/traceability full provenance chain
POST   /api/executions/start             Agent 6
GET    /api/executions/{id}
GET    /api/dashboard/summary
GET    /api/events/stream                SSE live pipeline events
POST   /api/demo/seed                    labelled demo fixture
```

Plus the simulated CBS under `/api/cbs/*`.

**SSE events:** `discovery_started`, `documents_found`, `extraction_completed`,
`analysis_completed`, `impact_mapping_completed`, `test_generation_completed`,
`execution_started`, `execution_progress`, `execution_completed`,
`agent_activity`, `pipeline_failed`, `heartbeat`.

Long-running agent work returns a `job_id` immediately and streams progress.

---

## Database

Supabase PostgreSQL is the target; SQLite works locally and in CI because the
models use portable `TypeDecorator`s (native `uuid`/`JSONB` on Postgres,
`CHAR(36)`/`JSON` elsewhere). The same Alembic migrations apply to both.

21 tables: `users`, `regulatory_sources`, `regulatory_documents`,
`regulatory_clauses`, `regulatory_changes`, `business_rules`, `impact_mappings`,
`test_cases`, `test_approvals`, `test_execution_runs`, `test_executions`,
`execution_evidence`, `discovery_runs`, `agent_activity_logs`, `notifications`,
plus six `cbs_*` simulator tables.

Switching to Supabase:

```bash
# backend/.env
DATABASE_URL=postgresql://postgres:<password>@db.<ref>.supabase.co:5432/postgres
```

```bash
./.venv/Scripts/python.exe -m alembic upgrade head
```

---

## Testing

```bash
cd backend
./.venv/Scripts/python.exe -m pytest -q            # 151 tests
./.venv/Scripts/python.exe -m pytest -m cbs        # simulator only
```

Coverage includes the CBS rule engine (74 unit tests over validation, limits,
risk classification, dual control), the CBS HTTP API end to end, the execution
agent (approval gate, fixture provisioning, assertion checking, evidence
capture), clause parsing, version diffing and traceability.

Tests point `DATABASE_URL` at a throwaway SQLite file so the execution agent —
which drives the app over ASGI through its own session — and the test share one
database.

Frontend:

```bash
cd frontend
npm run build      # type-check + production build
```

---

## Demo script

The full walkthrough is in [docs/DEMO_SCRIPT.md](docs/DEMO_SCRIPT.md). In short:

1. Dashboard → **Discover Regulatory Updates** (TinyFish Search).
2. Regulatory Explorer → select a KYC document → **Extract** (TinyFish Fetch).
3. Document page → **Analyse requirements** (Gemini), or compare two versions
   for an old-vs-new change report.
4. **Map all to CBS rules** → review proposals on Impact Analysis → approve.
5. **Generate tests** → review on Test Management → approve.
6. **Execute** → watch the Execution Monitor stream results and evidence.
7. Click **Trace to clause** on any test to walk the chain back to the
   originating paragraph and its official source URL.

---

## Safety model

1. Secrets only in backend environment variables; `.env` is gitignored and
   `.env.example` carries placeholders only.
2. API keys and CDP URLs redacted from logs, activity records and SSE.
3. Regulatory content is treated as untrusted data, never as instructions.
4. All LLM output is validated with Pydantic before it reaches the database.
5. Source quotes are verified against the document; unverifiable claims are
   downgraded automatically.
6. CBS rule identifiers are validated against the catalogue; invented codes are
   discarded and the rejection is recorded.
7. Generated tests are validated against the CBS API contract; a test with no
   checkable expectation **fails** rather than passing vacuously.
8. Impact mappings require human approval before test generation.
9. State-changing tests require an `APPROVED` approval record; without one the
   execution is recorded as `BLOCKED`, not run.
10. Execution targets the simulated CBS only.
11. Document versions are append-only; regulatory history is never overwritten.
12. Every agent operation is logged with duration, service and correlation id.
13. Demo fixtures are labelled as such everywhere they appear.
14. Nothing in the product is presented as a compliance determination.

---

## Known limitations

Recorded honestly rather than hidden — see
[docs/LIMITATIONS.md](docs/LIMITATIONS.md) for the full list.

- **Credentials required for live operation.** Without `TINYFISH_API_KEY` the
  discovery and extraction agents refuse to run (HTTP 503 with remediation);
  without `GEMINI_API_KEY` so do analysis, impact mapping and test generation.
  The demo fixture exists so the rest can still be demonstrated.
- **TinyBrowser cannot reach localhost** (see above).
- **Some regulator pages block automated retrieval.** When Fetch cannot get a
  document the limitation is written to `access_limitation` and the document is
  marked `FETCH_FAILED`. No content is invented.
- **Background jobs are in-process.** Single-worker only; a queue would be
  needed for horizontal scale. The `launch`/`get_job` interface is the seam.
- **Retrieval is lexical,** not embedding-based. `business_rules.embedding` is
  reserved for a pgvector upgrade; `score_rules` is the single place to change.
- **Document applicability is read, never inferred.** If a document does not
  state which entities it binds, the field stays empty rather than being filled
  from the configured institution type.

---

## Roadmap

- pgvector semantic retrieval over the rule catalogue
- TinyFish Monitor wired to a webhook for continuous regulator watching
- Redis-backed job queue and event bus for multi-worker deployment
- Per-clause diff highlighting at word level
- Exportable compliance evidence pack (PDF) per regulatory change
- Expanded CBS coverage: loans, deposits, nomination, cheque handling

---

## Licence

Built for a hackathon. Banking behaviour is simulated throughout.
#   R e g C h a n g e  
 