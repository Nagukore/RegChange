# Demo script

Target: **8 minutes**. The payoff is the traceability click at step 7 — build
toward it.

## Before you start

```bash
# Terminal 1
cd backend && ./.venv/Scripts/python.exe -m uvicorn app.main:app --port 8000

# Terminal 2
cd frontend && npm run dev
```

Open <http://localhost:5173>.

**With API keys** (`TINYFISH_API_KEY`, `GEMINI_API_KEY` in `backend/.env`) run
the live path below.

**Without keys**, seed the labelled demo fixture first and skip to step 5:

```bash
curl -X POST "http://127.0.0.1:8000/api/demo/seed?reset=true"
```

Say out loud that the fixture is labelled, and point at the "Demo fixture"
badge. Not apologising for it is the point: the system refuses to pretend.

---

## The 30-second framing

> A bank gets a new RBI circular. Today, working out what it means for the core
> banking system is weeks of manual analysis by compliance, BAs and QA — and at
> the end, nobody can cheaply prove *why* a given test exists.
>
> RegChange Sentinel does that chain automatically and keeps the receipt.

---

## Step 1 — Discover (TinyFish Search)

**Dashboard → Discover Regulatory Updates.**

Watch the **Live pipeline events** panel: `discovery_started` →
`documents_found`. Events stream over SSE; nothing is polling.

> Eight curated queries run against official RBI sources. Scoping uses
> TinyFish Search's `include_domains`, so we're searching the regulator — not
> searching the web and filtering afterwards.

Point out the **Discovery runs** table: found / new / duplicate, plus the
TinyFish request IDs for audit.

---

## Step 2 — Extract (TinyFish Fetch)

**Regulatory Explorer** → select a KYC document → **Extract selected**.

> TinyFish Fetch retrieves the official document and we segment it using the
> regulator's own numbering. That matters: our citation says "Para 38", not
> "chunk 12".

Open the document → **Clauses** tab. Each clause carries its reference, its
heading, and a content hash.

If a document failed to fetch, show it: `FETCH_FAILED` with the reason on the
record.

> When the regulator blocks automated retrieval we record the limitation. We
> don't generate a plausible-looking substitute.

---

## Step 3 — Analyse (Gemini)

**Analyse requirements** on the document page.

Open the **Requirements** tab and stop on one item. Show:

- the **verbatim source quote** and its reference,
- the **provenance badge**.

> Four labels, and they're enforced, not advisory. If the model claims
> something is an explicit regulatory requirement but its quote can't be found
> in the document, we automatically downgrade it to *unverified assumption*.
> That's the guard against a confidently fabricated citation.

---

## Step 4 — Version comparison

**Versions** tab → **Compare versions** against the earlier instrument.

> Clause-level diff. Matching is by the regulator's numbering first, then by
> content similarity — so a provision that moved from Para 12 to Para 38 reads
> as *modified*, not as one deletion plus one addition.

Show a `MODIFIED` entry with previous and current text side by side.

---

## Step 5 — Impact analysis

**Map all to CBS rules**, then go to **Impact Analysis**.

Expand a mapping:

> Three things happened. We retrieved candidate business rules lexically — you
> can see which terms matched and the retrieval score. The model reasoned over
> that shortlist only. Then we validated every rule code **against the
> database**, so an invented-but-plausible `CBS-999` is discarded before a
> human ever sees it.

Click **Approve mapping**.

> Approval isn't decoration. Test generation is blocked until a human approves
> the mapping.

---

## Step 6 — Generate and approve tests

**Generate tests** → **Test Management**.

Show the category spread: positive, negative, boundary, regression,
integration. Expand a negative test:

- the originating regulatory quote,
- the **assertion contract** — method, path, expected status, expected
  rejection code.

> These are executable, not prose. We validate every generated test against
> the CBS API contract: if the model invents an endpoint or asserts a rejection
> code the system can't emit, the test is discarded.

Select all → **Approve for execution**.

Before executing, select an *unapproved* state-changing test and note the
violet warning.

> A test that writes to the banking system without an approval record is
> recorded as BLOCKED, with the block itself stored as evidence.

---

## Step 7 — Execute and trace (**the payoff**)

**Execute tests** → **Execution Monitor**.

Results stream in live. Open a passing negative test and show:

- expected vs actual,
- the assertion table,
- the captured evidence with its SHA-256,
- who authorised the execution.

Now the moment. Back to **Test Management** → any test → **Trace to clause**:

```
Source document  →  Clause Para 4  →  REQ-D003 (explicit requirement, quoted)
      →  Impact: KYC_COMPLIANCE on CBS-004  →  CBS rule CBS-004
      →  TC-DEMO-0002 (approved)  →  Execution PASSED
```

> Every test traces back to the paragraph that caused it, with the official
> source URL and the verbatim quote. That's the artefact a regulator or an
> internal auditor actually asks for — and it was assembled automatically.

---

## Step 8 — The banking simulator (optional, 45s)

**Banking Simulator → KYC.** Pick a customer, submit **PAN only**, verify.

> Rejected: `NO_ADDRESS_PROOF`. PAN evidences identity but not address. That's
> the rule the generated negative test asserted against — this is a real system
> with real rules, which is what makes the tests meaningful.

**Maker-checker** tab: approve a task using the maker's own id →
`SELF_APPROVAL_FORBIDDEN`.

---

## Step 9 — Close on honesty (30s)

**Settings.**

> Everything this deployment can and cannot do, stated plainly. Which
> integrations are configured. Which execution drivers are available — note
> that the TinyFish cloud browser can't reach localhost, so we say so instead
> of failing mysteriously. And the safety controls: approval gates, quote
> verification, rule-code validation, credential redaction.
>
> A compliance tool that overstates its confidence is worse than no tool. This
> one tells you exactly which of its claims the regulator made and which it
> inferred.

---

## Questions you should expect

**"Is the TinyFish integration real?"**
Yes — built against the published OpenAPI specs
(`agent.tinyfish.ai/v1/openapi/*`), captured in `docs/TINYFISH_API.md`. Search,
Fetch, Agent, Browser and Monitor are all wired with typed clients. Request IDs
from live calls are stored on the discovery run.

**"What stops the model inventing a requirement?"**
Three gates: Pydantic validation of structured output, quote verification
against the source document, and automatic downgrade of unverifiable claims.
Plus a human approves every mapping.

**"Could this touch a real banking system?"**
No. There is no code path to one. Execution targets the simulated CBS, and
state-changing tests additionally require an approval record.

**"How does it handle a document it can't read?"**
Records the limitation, marks the document `FETCH_FAILED`, surfaces it on the
dashboard, and runs no downstream analysis on it.

**"What's genuinely hard here?"**
Keeping provenance intact end to end. It's easy to build a pipeline that emits
plausible tests; the work is making every link auditable and making the system
honest about which claims it can and cannot support.
