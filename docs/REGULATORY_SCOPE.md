# Regulatory scope and applicability

## The problem this document addresses

It is tempting to treat "an RBI KYC direction" as one thing that binds every
bank. It is not. The RBI issues entity-specific instruments, and an obligation
that binds a commercial bank may not bind a co-operative bank, an NBFC or a
payments bank — or may bind it under a different instrument with different
timelines.

A tool that silently applies one entity's rules to another produces confident,
wrong compliance work. So this project **never infers applicability**.

---

## Three rules the system follows

### 1. Applicability is read from the document, never assumed

`RegulatoryDocument.applicability_text` stores the document's own applicability
wording verbatim. `applicable_institution_types` is derived **only** from
entity types that text actually names
([`detect_applicable_entities`](../backend/app/agents/clause_parser.py)).

If a document does not say who it binds, the field stays **empty**. The UI
renders "not stated" rather than filling the gap.

### 2. The configured institution type never widens a document's scope

`INSTITUTION_TYPE` tells the system which entity *we* are. It is passed to the
analysis prompt as context, with an explicit instruction that it must not
influence what goes into `applicable_entities` — that comes from the source
text alone.

This lets the UI flag "this document names COMMERCIAL_BANK, you are configured
as NBFC" without ever rewriting the document's own scope.

### 3. Requirement-level entities are recorded separately

`RegulatoryChange.applicable_entities` captures what the *specific clause*
names, which is sometimes narrower than the document as a whole (for example a
provision that applies only to Small Finance Banks inside a commercial-bank
direction).

---

## Configuration

```bash
# backend/.env
INSTITUTION_TYPE=COMMERCIAL_BANK
```

| Value | Notes |
|---|---|
| `COMMERCIAL_BANK` | Default. Scheduled commercial banks |
| `SMALL_FINANCE_BANK` | Often named alongside commercial banks, not always |
| `PAYMENTS_BANK` | Distinct product and balance constraints |
| `CO_OPERATIVE_BANK` | Urban and rural co-operative banks |
| `REGIONAL_RURAL_BANK` | |
| `NBFC` | Non-banking financial companies |
| `PAYMENT_SYSTEM_PROVIDER` | |

Visible and explained on the **Settings** screen.

---

## The RBI KYC landscape, as verified

Checked against rbi.org.in on **2026-10-31**. Treat this as a snapshot; the
regulator's position moves and the system is built to re-fetch rather than to
trust a cached summary.

### Current instrument for commercial banks

**Reserve Bank of India (Commercial Banks — Know Your Customer) Directions,
2025** — `RBI/DOR/2025-26/169`, issued 28 November 2025.

- PDF: <https://rbidocs.rbi.org.in/rdocs/notification/PDFs/169MD.PDF>
- Titled for **Commercial Banks**. This project therefore records
  `applicable_institution_types = ["COMMERCIAL_BANK"]` and does not extend it.
- Amended by the **Know Your Customer Amendment Directions, 2025**
  (29 December 2025), which addresses reliance on CKYCR records.

### Prior instrument, retained for comparison

**Master Direction — Know Your Customer (KYC) Direction, 2016** —
`RBI/DBR/2015-16/18`.

- Index page: <https://www.rbi.org.in/Scripts/BS_ViewMasDirections.aspx?id=11566>
- Broader applicability (commercial banks, co-operative banks, NBFCs, payment
  system providers).
- Superseded for commercial banks by the 2025 Directions. Kept as the prior
  version so old-versus-new clause comparison has a real pair to work with.

### Version-dating caveat

The RBI website and the PDF can carry different "updated as on" dates for the
same instrument. This is exactly why the system stores a content hash per
version and compares clause by clause, rather than trusting a date label. When
in doubt, re-extract with `force_refetch` (`ttl=0`) and compare.

---

## Entry points used for discovery

| Source | URL |
|---|---|
| Master Directions index | <https://www.rbi.org.in/Scripts/BS_ViewMasterDirections.aspx> |
| Notifications | <https://www.rbi.org.in/Scripts/NotificationUser.aspx> |
| Press releases | <https://www.rbi.org.in/Scripts/BS_PressReleaseDisplay.aspx> |

Search is scoped with `include_domains=rbi.org.in` so results come from the
regulator rather than from commentary, and a secondary host check discards any
result that is not actually on an allowed domain.

Curated discovery queries live in
[`seeds/regulatory_sources.py`](../backend/app/seeds/regulatory_sources.py) and
cover KYC directions, periodic updation, CDD and OVDs, risk categorisation and
EDD, UCIC, small accounts/BSBDA, CKYCR, and V-CIP.

---

## When a document cannot be retrieved

Regulator sites sometimes block automated retrieval or serve scanned PDFs.
When TinyFish Fetch cannot return content, the system:

1. sets `status = FETCH_FAILED`,
2. writes the specific reason to `access_limitation`, including the upstream
   HTTP status where available,
3. stores **no text**, and
4. surfaces the document on the dashboard under "documents could not be
   retrieved", linking to the official URL for manual review.

Inventing or paraphrasing the content is never an option, and no downstream
analysis runs on an unretrieved document.

---

## What this system does not do

- It does not determine compliance. It produces analysis for human review.
- It does not assert that a requirement applies to your institution. It records
  what the source says and shows you your configured type alongside it.
- It does not treat an AI interpretation as a regulatory obligation — see the
  provenance labels in the main README.
- It does not act on a regulator's instructions found inside document text;
  that text is data, not commands.
