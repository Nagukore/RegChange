# RegChange Sentinel

**From regulatory intelligence to automated banking compliance testing.**

Turns a regulator's publication into executable compliance tests, and keeps
every step traceable. Click a passing test, read the exact paragraph of the
exact circular that caused it to exist.

```
regulatory source → document → clause → requirement → CBS business rule
                 → test scenario → execution → evidence
```

| | |
|---|---|
| **Stack** | FastAPI · SQLAlchemy 2 · React 19 + TypeScript · Vite · Tailwind |
| **Web intelligence** | TinyFish Search · Fetch · Agent · Browser · Monitor |
| **AI** | Google Gemini, structured JSON output, Pydantic-validated |
| **Database** | Supabase PostgreSQL (SQLite for local / CI) |
| **Tests** | 214 passing |
| **Verified live** | TinyFish Search + Fetch against `rbi.org.in` |

> Banking operations run against a **simulated** core banking system. Nothing
> here contacts a real banking system, and no output is a compliance
> determination.

---

## Quick start

**Prerequisites:** Python 3.11+, Node 20+.

### 1. Backend

```bash
cd backend
py -3.11 -m venv .venv
./.venv/Scripts/python.exe -m pip install -e ".[dev]"      # Windows
# source .venv/bin/activate && pip install -e ".[dev]"     # macOS / Linux

cp .env.example .env
./.venv/Scripts/python.exe -m alembic upgrade head
./.venv/Scripts/python.exe -m uvicorn app.main:app --port 8000
```

API on <http://127.0.0.1:8000> · OpenAPI docs at `/docs`

### 2. Frontend

```bash
cd frontend
npm install
npm run dev
```

UI on <http://localhost:5173>

### 3. Add credentials

Keys live only in `backend/.env`, which is gitignored. A helper writes them
without the value appearing on screen or in shell history:

```bash
& .\scripts\set-api-key.ps1                           # TinyFish
& .\scripts\set-api-key.ps1 -Name GEMINI_API_KEY      # Gemini
```

### 4. Or run it with no credentials at all

```bash
curl -X POST "http://127.0.0.1:8000/api/demo/seed?reset=true"
```

Loads a **clearly labelled** demo fixture: real RBI metadata and URLs,
synthetic body text. Clause parsing, diffing, retrieval and test execution are
all real code paths on it. Remove it with
`curl -X DELETE http://127.0.0.1:8000/api/demo/seed`.

---

## What it does

| # | Agent | Technology | Produces |
|---|---|---|---|
| 1 | Regulatory Discovery | TinyFish Search + RSS feeds | Documents found on official regulator domains |
| 2 | Regulatory Extraction | TinyFish Fetch | Versioned documents, clause-segmented |
| 3 | Regulatory Analysis | Gemini | Requirements anchored to verified quotes |
| 4 | Banking Impact Analysis | Gemini + deterministic validation | Requirement → CBS rule mappings |
| 5 | Test Generation | Gemini + contract validation | Positive / negative / boundary / regression / integration tests |
| 6 | Test Execution | ASGI · Playwright · TinyFish Browser | Results plus hashed evidence |

Human approval gates the two places it matters: an impact mapping must be
approved before tests are generated from it, and a state-changing test must be
approved before it runs.

### Who uses it

| Role | Permitted to | Lives in |
|---|---|---|
| **Analyst** | Run discovery, extraction, analysis, mapping, generation | Regulatory Explorer, Impact Analysis |
| **Reviewer** | Approve requirements, mappings and tests; start executions | Impact Analysis, Test Management |
| **Viewer** | Read everything, change nothing | Dashboard, traceability |
| **Admin** | All of the above, plus configuration | Settings |

An Analyst can *generate* a test but cannot *approve* or *run* it —
maker-checker applied to the compliance tooling itself.

---

## Why the provenance labelling matters

A tool that blurs "the regulator requires this" with "a model thinks this" is
worse than no tool. Every assertion carries one of four labels, shown on every
screen:

| Label | Meaning |
|---|---|
| `EXPLICIT_REQUIREMENT` | Stated by the regulator, quote verified against the source |
| `AI_INTERPRETATION` | A model's reading. Defensible, not an obligation |
| `PROPOSED_CONTROL` | A control a bank *might* implement. Not demanded by the regulation |
| `UNVERIFIED_ASSUMPTION` | No locatable support in the source |

The downgrade is **automatic, not advisory**: if the model claims
`EXPLICIT_REQUIREMENT` but its quote cannot be found in the document, the item
is rewritten to `UNVERIFIED_ASSUMPTION`
([`analysis_agent.py`](backend/app/agents/analysis_agent.py)).

Records are separately tagged by origin — `LIVE_TINYFISH`, `LIVE_GEMINI`,
`DETERMINISTIC`, `DEMO_FIXTURE` — each with its own badge. Nothing mocked is
ever presented as a live result.

---

## Architecture

```
┌──────────────── Frontend (React + TypeScript + Vite) ─────────────────┐
│ Dashboard · Regulatory Explorer · Impact Analysis · Test Management   │
│ Execution Monitor · Banking Simulator · Settings    ← SSE live events │
└───────────────────────────────┬───────────────────────────────────────┘
                                │ /api (same-origin, Vite proxy in dev)
┌───────────────────────────────┴───────────────────────────────────────┐
│ FastAPI                                                               │
│   Agents 1–2  TinyFish Search / Fetch                                 │
│   Agents 3–5  Gemini + deterministic validation                       │
│   Agent 6     in-process ASGI · Playwright · TinyFish Browser         │
│   Simulated CBS: customers · KYC · accounts · transactions ·          │
│                  maker-checker · audit trail                          │
└───────────────────────────────┬───────────────────────────────────────┘
                                │ SQLAlchemy 2 + Alembic
                   ┌────────────┴────────────┐
                   │ Supabase PostgreSQL     │  (SQLite for local / CI)
                   └─────────────────────────┘
```

Design decisions, the traceability model and the production path:
**[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)**

<details>
<summary><b>Project layout</b></summary>

```
backend/app/
├── core/          config, database, security, logging+redaction, SSE bus, jobs
├── models/        SQLAlchemy models + portable type decorators
├── schemas/       Pydantic: API contracts + LLM output contracts
├── integrations/  typed TinyFish and Gemini clients
├── prompts/       prompt construction, injection resistance, honesty clauses
├── agents/        the six agents + clause parser, retrieval, CBS contract
├── cbs/           simulated core banking: deterministic rules + services
├── services/      diffing, traceability
├── seeds/         rule catalogue, regulator sources, demo fixture
└── api/routers/   system, pipeline, dashboard, cbs

frontend/src/
├── components/    AppShell + shared UI primitives
├── lib/           typed API client, types, SSE hook
└── pages/         the seven screens
```
</details>

---

## Configuration

Nothing is required to start. Features needing a credential report themselves
as *not configured* rather than silently degrading.

| Variable | Needed for | Source |
|---|---|---|
| `TINYFISH_API_KEY` | Discovery, extraction, cloud browser, monitors | [agent.tinyfish.ai/api-keys](https://agent.tinyfish.ai/api-keys) — Search free to 12k/day, Fetch to 1k URLs/day |
| `GEMINI_API_KEY` | Analysis, impact mapping, test generation | [aistudio.google.com/apikey](https://aistudio.google.com/apikey) |
| `DATABASE_URL` | Supabase Postgres (omit for SQLite) | Supabase → Settings → Database → URI |
| `INSTITUTION_TYPE` | Regulatory applicability scoping | [docs/REGULATORY_SCOPE.md](docs/REGULATORY_SCOPE.md) |
| `CBS_PUBLIC_BASE_URL` | TinyFish cloud-browser execution | A deployment or tunnel |

The **Settings** screen shows live status and can probe each service on demand.

---

## TinyFish integration

Built against the official OpenAPI specs at
`agent.tinyfish.ai/v1/openapi/{search,fetch,browser,monitor}` — no endpoint or
parameter was guessed. Captured contract:
**[docs/TINYFISH_API.md](docs/TINYFISH_API.md)**

| Product | Endpoint | Used for |
|---|---|---|
| Search | `GET api.search.tinyfish.ai/` | Domain-scoped discovery via `include_domains` |
| Fetch | `POST api.fetch.tinyfish.ai/` | Retrieval + ETag-based change detection |
| Agent | `POST agent.tinyfish.ai/v1/automation/run-sse` | Complex navigation (metered, used sparingly) |
| Browser | `POST api.browser.tinyfish.ai/` | Cloud browser over CDP for Playwright |
| Monitor | `POST agent.tinyfish.ai/v1/monitors` | Scheduled regulator monitoring |

Auth is `X-API-Key` on every call. Retries cover only transient conditions
(429 / 5xx / network); a 4xx raises immediately rather than burning quota. API
keys and browser CDP URLs are redacted from logs, activity records and SSE.

> **TinyBrowser cannot reach `localhost`** — it runs in TinyFish's cloud. Local
> browser tests use Playwright directly; the TinyFish driver activates when
> `CBS_PUBLIC_BASE_URL` points somewhere reachable. Both share one driver
> interface, so it is a config flip. Settings states this rather than letting
> the driver fail mysteriously.

---

## Simulated core banking system

A deliberately realistic Indian CBS, because generated tests need something
real to assert against. **There is no code path from this application to a real
banking system.**

Fifteen catalogued rules (`CBS-001`…`CBS-015`) across customer onboarding,
KYC/AML, account management and transactions, with maker-checker dual control
and an immutable audit trail. Every rejection returns a stable machine code —
`INVALID_PAN_FORMAT`, `NO_ADDRESS_PROOF`, `PER_TXN_LIMIT_EXCEEDED`,
`SELF_APPROVAL_FORBIDDEN` — so a negative test asserts on behaviour, not prose.

The impact engine may only reference a rule code that exists in
`business_rules`, validated against the database rather than against the list
shown to the model.

---

## Testing

```bash
cd backend
./.venv/Scripts/python.exe -m pytest -q        # 214 tests
./.venv/Scripts/python.exe -m pytest -m cbs    # simulator only
./.venv/Scripts/python.exe -m ruff check app tests

cd frontend && npm run build                   # type-check + production build
```

Covers the CBS rule engine, the CBS HTTP API end to end, the execution agent
(approval gate, fixture provisioning, assertions, evidence), clause parsing,
version diffing, traceability, and regressions found against the live RBI PDF.

Tests point `DATABASE_URL` at a throwaway SQLite file so the execution agent —
which drives the app over ASGI through its own session — and the test share one
database.

---

## Reference

<details>
<summary><b>API endpoints</b></summary>

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
PATCH  /api/impact/{id}/review           gate for test generation
POST   /api/test-cases/generate          Agent 5
GET    /api/test-cases
PATCH  /api/test-cases/{id}/approve      gate for execution
GET    /api/test-cases/{id}/traceability full provenance chain
POST   /api/executions/start             Agent 6
GET    /api/executions/{id}
GET    /api/dashboard/summary
GET    /api/events/stream                SSE live pipeline events
POST   /api/demo/seed                    labelled demo fixture
```

Plus the simulated CBS under `/api/cbs/*`. Long-running agent work returns a
`job_id` immediately and streams progress.

**SSE events:** `discovery_started`, `documents_found`, `extraction_completed`,
`analysis_completed`, `impact_mapping_completed`, `test_generation_completed`,
`execution_started`, `execution_progress`, `execution_completed`,
`agent_activity`, `pipeline_failed`, `heartbeat`.
</details>

<details>
<summary><b>Database</b></summary>

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

A local Postgres alternative is in `docker-compose.yml`.
</details>

<details>
<summary><b>Safety model</b></summary>

1. Secrets only in backend environment variables; `.env` gitignored.
2. API keys and CDP URLs redacted from logs, activity records and SSE.
3. Regulatory content treated as untrusted data, never as instructions.
4. All LLM output validated with Pydantic before reaching the database.
5. Source quotes verified against the document; unverifiable claims downgraded.
6. CBS rule identifiers validated against the catalogue; invented codes discarded.
7. Generated tests validated against the CBS API contract; a test with no
   checkable expectation **fails** rather than passing vacuously.
8. Impact mappings require human approval before test generation.
9. State-changing tests require an `APPROVED` record, else recorded `BLOCKED`.
10. Execution targets the simulated CBS only.
11. Document versions append-only; regulatory history never overwritten.
12. Every agent operation logged with duration, service and correlation id.
13. Demo fixtures labelled as such everywhere they appear.
14. Nothing is presented as a compliance determination.
</details>

---

## Documentation

| Document | Covers |
|---|---|
| [ARCHITECTURE.md](docs/ARCHITECTURE.md) | Design decisions, traceability model, production path |
| [TINYFISH_API.md](docs/TINYFISH_API.md) | Verified TinyFish contract, captured from the OpenAPI specs |
| [REGULATORY_SCOPE.md](docs/REGULATORY_SCOPE.md) | Applicability rules, RBI KYC landscape, entity-type scoping |
| [DEMO_SCRIPT.md](docs/DEMO_SCRIPT.md) | Eight-minute walkthrough |
| [LIMITATIONS.md](docs/LIMITATIONS.md) | Everything this does not do |

---

## Known limitations

Recorded rather than hidden — full list in
[docs/LIMITATIONS.md](docs/LIMITATIONS.md).

- **Credentials gate the live agents.** Without them the agents return HTTP 503
  with remediation text. The demo fixture exists so the rest still demonstrates.
- **TinyBrowser cannot reach localhost** (see above).
- **Some regulator pages block automated retrieval.** The limitation is written
  to `access_limitation` and the document marked `FETCH_FAILED`. No content is
  invented.
- **Background jobs are in-process.** Single-worker; `launch`/`get_job` is the
  seam for a queue.
- **Retrieval is lexical, not semantic.** `business_rules.embedding` is reserved
  for pgvector; `score_rules` is the single place to change.
- **Applicability is read, never inferred.** If a document does not state which
  entities it binds, the field stays empty.
- **No sign-in UI.** Roles and JWT exist, but reviewer gates fall through in
  development and attribute actions to `unauthenticated-dev`.

---

## Roadmap

- pgvector semantic retrieval over the rule catalogue
- TinyFish Monitor wired to a webhook for continuous regulator watching
- Redis-backed job queue and event bus for multi-worker deployment
- Word-level diff highlighting within a clause
- Exportable compliance evidence pack (PDF) per regulatory change
- Expanded CBS coverage: loans, deposits, nomination, cheque handling

---

## Licence

Built for a hackathon. Banking behaviour is simulated throughout.
