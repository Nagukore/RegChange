"""Deterministic triage of a live regulatory document onto CBS business rules.

WHY THIS EXISTS
The full pipeline uses Gemini (Agent 3) to turn clauses into summarised
requirements and Gemini + retrieval (Agent 4) to map them. Without
GEMINI_API_KEY those agents correctly refuse to run. This script provides the
honest subset that needs no LLM at all:

  * the requirement text IS the clause text, quoted verbatim -- nothing is
    summarised, interpreted or invented;
  * candidate CBS rules come from the same deterministic lexical retrieval the
    impact agent uses;
  * everything is written as provenance = DETERMINISTIC and
    review_status = PENDING_REVIEW.

It is deliberately weaker than Agent 3 + Agent 4: there is no change-type
detection, no old-versus-new reasoning and no plain-language restatement. It
surfaces "this clause looks relevant to this rule, here is the exact text, a
human should decide".

Usage:
    ./.venv/Scripts/python.exe ../scripts/triage_live_document.py --url-like 169MD.PDF
    ./.venv/Scripts/python.exe ../scripts/triage_live_document.py --url-like 169MD.PDF --clear
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# Run from anywhere: make the backend package importable.
BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from sqlalchemy import select  # noqa: E402

from app.agents.retrieval import score_rules  # noqa: E402
from app.core.database import SessionLocal  # noqa: E402
from app.models.enums import (  # noqa: E402
    AssertionBasis,
    ChangeType,
    DataProvenance,
    ImpactCategory,
    Priority,
    ReviewStatus,
)
from app.models.mapping import BusinessRule, ImpactMapping  # noqa: E402
from app.models.regulatory import RegulatoryChange, RegulatoryDocument  # noqa: E402

# Table-of-contents lines carry dotted leaders and no substantive obligation.
TOC_RE = re.compile(r"\.{4,}")
MIN_CLAUSE_CHARS = 180
# Raw lexical score below which a match is too weak to put in front of a
# reviewer. Tuned against the live RBI KYC Directions.
MIN_SCORE = 3.0

CATEGORY_FOR_MODULE = {
    "KYC_AML": ImpactCategory.KYC_COMPLIANCE.value,
    "CUSTOMER_ONBOARDING": ImpactCategory.CUSTOMER_ONBOARDING.value,
    "ACCOUNT_MANAGEMENT": ImpactCategory.ACCOUNT_MANAGEMENT.value,
    "TRANSACTIONS": ImpactCategory.TRANSACTION_PROCESSING.value,
}


def priority_for(score: float) -> str:
    if score >= 7.0:
        return Priority.HIGH.value
    if score >= 5.0:
        return Priority.MEDIUM.value
    return Priority.LOW.value


def summarise(text: str, limit: int = 180) -> str:
    """First sentence-ish of the clause, for a readable title."""
    flat = " ".join(text.split())
    # Drop a leading enumerator such as "(iv) " or "7. ".
    flat = re.sub(r"^\(?[0-9ivxa-z]{1,5}\)?[.)]\s*", "", flat)
    cut = flat[:limit]
    stop = cut.rfind(". ")
    if stop > 60:
        cut = cut[:stop]
    return cut.strip().rstrip(",;:") or flat[:limit]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--url-like", required=True, help="Substring of the document source_url"
    )
    parser.add_argument("--top", type=int, default=12, help="Max clauses to triage")
    parser.add_argument(
        "--clear",
        action="store_true",
        help="Remove existing DETERMINISTIC triage for this document first",
    )
    args = parser.parse_args()

    db = SessionLocal()
    try:
        document = db.execute(
            select(RegulatoryDocument).where(
                RegulatoryDocument.source_url.like(f"%{args.url_like}%"),
                RegulatoryDocument.is_current.is_(True),
            )
        ).scalars().first()
        if document is None:
            print(f"No current document matching {args.url_like!r}", file=sys.stderr)
            return 1
        if not document.raw_text:
            print("That document has no extracted text. Run extraction first.", file=sys.stderr)
            return 1

        if args.clear:
            removed = 0
            for change in db.execute(
                select(RegulatoryChange).where(
                    RegulatoryChange.document_id == document.id,
                    RegulatoryChange.provenance == DataProvenance.DETERMINISTIC.value,
                )
            ).scalars().all():
                db.delete(change)   # cascades to its impact mappings
                removed += 1
            db.commit()
            print(f"Cleared {removed} previous triage requirement(s).")

        rules = list(
            db.execute(select(BusinessRule).where(BusinessRule.is_active.is_(True)))
            .scalars()
            .all()
        )
        clauses = [
            c
            for c in sorted(document.clauses, key=lambda c: c.ordinal)
            if len(c.text) >= MIN_CLAUSE_CHARS and not TOC_RE.search(c.text[:400])
        ]

        ranked = []
        for clause in clauses:
            candidates = score_rules(clause.text, rules, top_k=2)
            if candidates and candidates[0].score >= MIN_SCORE:
                ranked.append((candidates[0].score, clause, candidates))
        ranked.sort(key=lambda r: -r[0])
        ranked = ranked[: args.top]

        if not ranked:
            print("No clause scored above the triage threshold.")
            return 0

        # The UI renders `retrieval_score` as a 0-1 proportion, so a raw lexical
        # score (which is unbounded) must be scaled before it is stored, or a
        # term-overlap of 11.78 would display as a "100% match". Scaling is by
        # the strongest hit in this batch, making the figure explicitly relative
        # to this document rather than an absolute confidence.
        batch_max = max(score for score, _, _ in ranked) or 1.0

        existing = (
            db.execute(
                select(RegulatoryChange.requirement_code).where(
                    RegulatoryChange.document_id == document.id
                )
            )
            .scalars()
            .all()
        )
        seq = len(existing) + 1

        created_changes = 0
        created_mappings = 0
        for score, clause, candidates in ranked:
            code = f"REQ-T{seq:03d}"
            seq += 1
            change = RegulatoryChange(
                document_id=document.id,
                clause_id=clause.id,
                requirement_code=code,
                title=summarise(clause.text),
                # The clause text IS the requirement. Nothing is restated.
                requirement_text=" ".join(clause.text.split()),
                change_type=ChangeType.NEW_REQUIREMENT.value,
                # Quoted verbatim from the source, so this is explicit -- but it
                # is the clause as published, not a model's reading of it.
                assertion_basis=AssertionBasis.EXPLICIT_REQUIREMENT.value,
                source_quote=" ".join(clause.text.split())[:4000],
                source_reference=clause.clause_reference,
                applicable_entities=document.applicable_institution_types or [],
                primary_banking_module=candidates[0].rule.banking_module,
                effective_date=document.effective_date,
                priority=priority_for(score),
                confidence=None,  # no model involved, so no model confidence
                review_status=ReviewStatus.PENDING_REVIEW.value,
                model_name=None,
                provenance=DataProvenance.DETERMINISTIC.value,
            )
            db.add(change)
            db.flush()
            created_changes += 1

            for candidate in candidates:
                if candidate.score < MIN_SCORE:
                    continue
                db.add(
                    ImpactMapping(
                        change_id=change.id,
                        business_rule_id=candidate.rule.id,
                        impact_category=CATEGORY_FOR_MODULE.get(
                            candidate.rule.banking_module, ImpactCategory.BUSINESS_LOGIC.value
                        ),
                        banking_module=candidate.rule.banking_module,
                        priority=priority_for(candidate.score),
                        rationale=(
                            f"Deterministic lexical retrieval matched this clause to "
                            f"{candidate.rule.rule_code} ({candidate.rule.name}) on the terms: "
                            f"{', '.join(candidate.matched_terms[:10]) or 'n/a'}. "
                            f"Raw lexical score {candidate.score:.2f} "
                            f"(shown as a proportion of the strongest match in this "
                            f"document, not an absolute confidence). No language model "
                            f"was involved; a reviewer must confirm the mapping."
                        ),
                        # Not an AI reading -- a term-overlap signal only.
                        assertion_basis=AssertionBasis.PROPOSED_CONTROL.value,
                        affected_fields=candidate.rule.affected_fields or [],
                        affected_workflows=(
                            [candidate.rule.ui_route] if candidate.rule.ui_route else []
                        ),
                        regression_risk=Priority.LOW.value,
                        confidence=None,
                        retrieval_score=min(candidate.score / batch_max, 1.0),
                        review_status=ReviewStatus.PENDING_REVIEW.value,
                        model_name=None,
                        provenance=DataProvenance.DETERMINISTIC.value,
                    )
                )
                created_mappings += 1
        db.commit()

        print(f"\nDocument: {document.title}")
        print(f"Triaged {created_changes} clause(s) -> {created_mappings} candidate mapping(s).")
        print("All written as DETERMINISTIC / PENDING_REVIEW. Review them at /impact.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
