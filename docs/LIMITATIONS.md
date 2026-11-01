# Known limitations

Recorded deliberately. A compliance tool that hides its boundaries is a
liability.

---

## 1. Credentials gate the live agents

| Missing | Disabled | Behaviour |
|---|---|---|
| `TINYFISH_API_KEY` | Agents 1 and 2, cloud browser, monitors | HTTP 503 with remediation text; UI renders "Not configured — how to fix" |
| `GEMINI_API_KEY` | Agents 3, 4 and 5 | Same |
| `DATABASE_URL` | Supabase Postgres | Falls back to local SQLite; migrations identical |

Nothing is silently substituted. The demo fixture
(`POST /api/demo/seed`) exists so the pipeline can still be demonstrated, and
everything it creates is labelled `DEMO_FIXTURE` wherever it appears.

**Status at time of writing:** this build has been verified end to end on the
demo-fixture path (seed → approve → execute → evidence → traceability, 5/5
tests passing). The TinyFish and Gemini code paths are implemented against
their verified contracts and unit-tested, but **have not yet been exercised
against the live APIs** because no key was configured in this environment. The
first live run should be treated as the real integration test.

---

## 2. The TinyFish cloud browser cannot reach localhost

TinyFish runs the browser in its own cloud, so `http://localhost:5173` is not
resolvable from it.

- **Default:** browser tests use local Playwright against `CBS_LOCAL_BASE_URL`.
- **To use TinyBrowser:** set `CBS_PUBLIC_BASE_URL` to a deployed instance or a
  tunnel (ngrok, cloudflared).

Both drivers share one interface, so this is a configuration flip rather than a
separate implementation. The Settings screen states the constraint explicitly
instead of letting the driver fail opaquely.

---

## 3. Some regulator pages resist automated retrieval

Anti-bot protection and scanned PDFs both occur. When Fetch cannot return
content the document is marked `FETCH_FAILED`, the reason is written to
`access_limitation`, no text is stored, and the dashboard surfaces the count.

Possible mitigations, not yet implemented: TinyFish Agent for navigation-heavy
archives, and OCR for scanned PDFs (`pypdf` is a dependency but no OCR path
exists).

---

## 4. Single-process background jobs and event bus

`app/core/jobs.py` runs jobs as asyncio tasks in the API process, and
`app/core/events.py` is an in-process pub/sub. This means:

- one worker only; a second uvicorn worker would not see the first's jobs or
  events,
- a restart loses in-flight jobs (persisted results survive; the job record
  does not),
- the job registry is capped at 200 entries.

The `launch()` / `get_job()` and `EventBus` interfaces are the seam for a
queue and Redis respectively.

---

## 5. Retrieval is lexical, not semantic

`score_rules` is TF-IDF over curated keywords, rule name and description. It
performs well on the current 15-rule catalogue (verified: periodic-updation
language ranks `CBS-006` first; transaction-limit language ranks `CBS-012`
first) and has the advantage of being explainable — the UI shows the matched
terms.

It will degrade on a catalogue of hundreds of rules, and on paraphrase that
shares no vocabulary with the rule text. `business_rules.embedding` and the
optional pgvector dependency are reserved for this; `score_rules` is the only
function that changes.

---

## 6. Clause parsing is heuristic

The parser handles the numbering styles RBI actually uses, with heading and
paragraph fallbacks. It will mis-segment:

- deeply nested sub-clauses beyond four levels,
- tables and schedules, which lose structure in markdown conversion,
- documents whose numbering is lost entirely in PDF-to-markdown conversion.

`segmentation_looks_thin()` detects the last case (a long document yielding
very few clauses). An LLM-assisted segmentation contract
(`ClauseSegmentationResult`) is defined but **not yet wired in** — currently
the condition is detectable but not acted upon.

---

## 7. Analysis truncates very long documents

Single-document analysis caps at 120,000 characters; version comparison at
55,000 per side. Truncation is recorded on the result and shown in the UI, but
a requirement beyond the cut-off will be missed. Chunked analysis with
cross-chunk de-duplication is the obvious next step.

---

## 8. Test generation covers the API surface, not the UI

Generated tests target the CBS REST contract. `automation_type: BROWSER`
exists and the Playwright and TinyFish drivers navigate to the rule's
`ui_route` and capture screenshots and HTML, but they assert only that the page
loaded — they do not drive form interactions derived from the test steps.
Full UI-level assertion generation is future work.

---

## 9. Authentication is minimal

JWT with four roles is implemented, but in development, when no user exists,
reviewer-gated endpoints fall through and attribute actions to
`unauthenticated-dev`. This is deliberate so the UI works before anyone signs
in; it is disabled when `ENVIRONMENT=production` or once any user exists.

There is no UI for sign-in, user management or `BOOTSTRAP_ADMIN_PASSWORD`
setup. For a real deployment this needs SSO and mandatory auth.

---

## 10. Evidence is stored on the local filesystem

Screenshots and HTML snapshots go to `backend/evidence/` with a SHA-256
recorded per artefact. Paths are re-resolved and bounds-checked on read, so a
tampered database row cannot escape the evidence root. There is no retention
policy, no object storage and no signing.

---

## 11. Scope of the banking simulation

Fifteen rules across onboarding, KYC, accounts and transactions. Not modelled:
loans, deposits/term products, nomination, cheque handling, standing
instructions, interest accrual, end-of-day batch processing, multi-currency.

Rule parameters (limits, periodicity, thresholds) are plausible but
illustrative. They are not a statement of what regulation requires — the
mapping from regulation to rule is exactly what the impact engine proposes and
a human approves.

---

## 12. Model confidence is self-reported

`confidence` on changes and mappings is the model's own number. It is shown to
reviewers as one signal among several and is never used to gate anything
automatically. Do not read it as calibrated probability.

---

## 13. Gemini structured-output schema flattening

Gemini's `responseSchema` rejects `$ref`, `anyOf` with null, and several
validation keywords that Pydantic emits. `_strip_unsupported()` resolves
references and collapses optional unions to their non-null branch. Constraints
such as `minLength` and `pattern` are therefore dropped from the schema sent to
the model — but they are still enforced on the way back, because the response
is re-validated against the full Pydantic model.

---

## 14. Not a compliance determination

The system produces analysis for human review. It does not and must not be
read as advice on whether an institution is compliant. Every screen carries
this framing, and the prompts forbid the model from making such a claim.
