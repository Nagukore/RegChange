# Architecture

## The central idea

A compliance test is only useful if you can answer "why does this test exist?"
with a paragraph number and a URL. Everything here is shaped by keeping that
chain intact:

```
regulatory_sources
      │
      ▼
regulatory_documents ──(version N+1, supersedes_id)──► regulatory_documents
      │                                                        │
      ▼                                                        ▼
regulatory_clauses  ◄──── clause-level diff ────────►  regulatory_clauses
      │
      ▼
regulatory_changes      (requirement + verbatim quote + assertion_basis)
      │
      ▼
impact_mappings         (→ business_rules, human-approved)
      │
      ▼
test_cases              (→ test_approvals, human-approved)
      │
      ▼
test_executions ──────► execution_evidence
```

`GET /api/test-cases/{id}/traceability` walks this in one call and returns a
flat, display-ready chain. The UI renders it as a vertical trail on the Test
Management screen.

---

## Layering

```
app/
├── core/          config, database, security, logging+redaction, SSE bus, jobs
├── models/        SQLAlchemy 2 models + portable type decorators
├── schemas/       Pydantic: API contracts + LLM output contracts
├── integrations/  typed TinyFish and Gemini clients (no business logic)
├── prompts/       prompt construction, injection resistance, honesty clauses
├── agents/        the six agents + clause parser, retrieval, CBS contract
├── cbs/           simulated core banking: deterministic rules + services
├── services/      diffing, traceability
├── seeds/         rule catalogue, regulator sources, demo fixture
└── api/routers/   system, pipeline, dashboard, cbs
```

The dependency direction is strictly downward. `integrations/` knows nothing
about banking; `cbs/` knows nothing about regulation; `agents/` is where the
two meet.

---

## Key design decisions

### 1. Portable database types

Supabase PostgreSQL is the deployment target, but the suite must run in CI and
on a laptop without Postgres. `GUID` and `JSONBType`
([`models/types.py`](../backend/app/models/types.py)) resolve to native
`uuid`/`JSONB` on Postgres and `CHAR(36)`/`JSON` elsewhere, so one set of
Alembic migrations serves both. Alembic runs in `render_as_batch` mode on
SQLite because SQLite cannot `ALTER` most things in place.

### 2. Synchronous persistence, asynchronous I/O

Agent work is I/O-bound on external HTTP (async `httpx`), while persistence is
short and transactional (sync SQLAlchemy). Mixing them deliberately keeps the
database code simple to reason about. Background jobs own their session for
the job's lifetime.

### 3. Append-only regulatory history

A document is never overwritten. `content_hash` (SHA-256 of normalised text)
identifies a version:

- same hash → refresh `fetched_at` only
- new hash → insert version *N+1*, set `supersedes_id`, flip the old row's
  `is_current` to false

ETag / `Last-Modified` validators from TinyFish Fetch make the "unchanged" case
nearly free, and `not_modified: true` short-circuits before any parsing.

### 4. Clause segmentation by the regulator's own numbering

[`clause_parser.py`](../backend/app/agents/clause_parser.py) splits on the
document's own markers (`5.`, `5.2.1`, `(a)`, `38(a)`), falling back to
markdown headings, then to paragraph blocks. The fallback labels its references
positionally (`Para 3`) so a reviewer can tell it is not the regulator's
numbering. This is what makes a citation meaningful instead of "chunk 12".

Normalised-whitespace hashing means cosmetic reflow never registers as a
change.

### 5. Verification over trust, at every LLM boundary

| Boundary | Guard |
|---|---|
| Gemini → database | Pydantic validation (schema also sent as `responseSchema`) |
| Quote → requirement | Quote must appear in the source text, else downgraded to `UNVERIFIED_ASSUMPTION` |
| Rule code → impact mapping | Must exist in `business_rules` *in the database* |
| Generated test → stored test | `http_path` must be a real CBS endpoint; asserted rejection code must be one the system emits |
| Test with no assertion | Fails, never passes vacuously |

The rule-code check validates against the database rather than the candidate
list shown to the model, so a plausible-but-invented code is still caught.

### 6. Regulatory text is untrusted input

A regulator PDF converted to markdown by a third party can contain anything,
including text shaped like an instruction. So source text is wrapped in
`<<<REGULATORY_SOURCE_DATA>>>` delimiters, delimiter-shaped sequences inside it
are neutralised, and every system prompt states that the block is data and that
instruction-like content inside it must be reported as document content.

### 7. Hybrid impact mapping

1. LLM extracts the requirement (Agent 3)
2. **Lexical retrieval** shortlists candidate CBS rules — TF-IDF over curated
   keywords, name and description, with a module boost rather than a hard
   filter so cross-module impacts can still surface
3. LLM reasons over the shortlist only
4. **Deterministic validation** of rule codes
5. **Human approval** before anything downstream

Retrieval is explainable: the UI shows which terms matched and the normalised
score. `business_rules.embedding` is reserved for a pgvector upgrade;
`score_rules` is the only function that would change.

### 8. Three execution drivers behind one interface

| Driver | Transport | Use |
|---|---|---|
| `PYTEST_API` | in-process ASGI via `httpx.ASGITransport` | Deterministic assertion checking, no network |
| `PLAYWRIGHT_LOCAL` | local Chromium | Real browser evidence against the simulator UI |
| `TINYFISH_BROWSER` | TinyFish cloud browser over CDP | Browser evidence from managed infrastructure |

The API driver calls the application's own FastAPI app, so tests exercise the
real routing, validation and persistence stack with no server required.

**Fixture provisioning.** A generated test says `{customer_id}` without knowing
a real id. The executor provisions what the endpoint needs — including the full
onboarding chain (create → maker-checker approve → KYC verify → open account →
activate) when an `{account_id}` is required — and satisfies entity references
that arrive as *body* fields as well as path placeholders.

### 9. Human approval as a hard gate

`require_approval_for_execution` (default on) means a state-changing test
without an `APPROVED` `test_approvals` record is recorded as `BLOCKED` with the
reason stored as evidence. Read-only tests may run unapproved; the result still
records that no approval existed.

### 10. SSE with coalesced invalidation

One `EventSource` is shared app-wide through React context. Pipeline events map
to React Query keys, and invalidations are debounced 350 ms — an execution run
emits one event per test, and refetching on each would hammer the API.

The server emits an event immediately on subscribe so the browser leaves
`CONNECTING` at once; without a first byte some proxies hold the response open
and `onopen` never fires, leaving a healthy stream looking disconnected.

---

## Request lifecycle: a discovery run

```
POST /api/discovery/start
  └─ DiscoveryAgent configured?  no → 503 + remediation
  └─ launch() background job, return job_id immediately
       │
       ├─ emit discovery_started                     ──► SSE
       ├─ for each curated query:
       │    ├─ agent_activity(START)                 ──► agent_activity_logs
       │    ├─ TinyFish Search (include_domains)
       │    ├─ filter off-domain results
       │    ├─ de-duplicate by source_url
       │    └─ agent_activity(SUCCEEDED, request_id)
       ├─ persist DiscoveryRun totals + request ids
       └─ emit documents_found                       ──► SSE → React Query invalidation
```

A failing query degrades the run to `PARTIAL` with the error recorded; it does
not abandon the remaining queries.

---

## Frontend

React 19 + TypeScript + Vite 8, Tailwind 3, TanStack Query, React Router 7,
Recharts, Lucide.

- `/api` is same-origin via the Vite dev proxy, so no CORS preflight and SSE is
  first-party.
- Server state lives in React Query; SSE pushes invalidations rather than the
  UI polling.
- `ErrorState` surfaces the backend's `remediation` field, so a missing
  credential renders as "Not configured — how to fix" rather than a stack trace.
- Provenance (`AssertionBadge`, `ProvenanceTag`) is rendered everywhere the
  underlying data appears. This is a product requirement, not decoration.

---

## What would change for production

| Concern | Now | Production |
|---|---|---|
| Job queue | in-process asyncio, `launch()`/`get_job()` | Celery/Arq behind the same interface |
| Event bus | in-process pub/sub | Redis pub/sub behind `EventBus` |
| Retrieval | lexical TF-IDF | pgvector embeddings in `score_rules` |
| Auth | JWT, dev-mode fallthrough when no users exist | SSO, mandatory everywhere |
| Evidence | local filesystem | Object storage with retention policy |
| CBS | simulated | Read-only adapter against a UAT core, never production |
