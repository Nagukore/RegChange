"""Agent 3 -- Regulatory Analysis Agent (Gemini).

Turns extracted document text into structured `RegulatoryChange` rows, each one
anchored to a verbatim source quote and a clause reference.

Two safeguards sit between the model and the database:
  1. Pydantic validation of the structured output (in the Gemini client).
  2. `_verify_quote` here, which checks each `source_quote` really does occur in
     the document. An item whose quote cannot be located is downgraded to
     UNVERIFIED_ASSUMPTION rather than being presented as an explicit
     requirement. This is the guard against a confidently fabricated citation.
"""
from __future__ import annotations

import re
import uuid
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agents.base import AgentContext, AgentUnavailable, agent_activity
from app.agents.clause_parser import normalise_text
from app.core.config import settings
from app.core.events import EventType
from app.core.logging import get_logger
from app.integrations.gemini import GeminiClient
from app.models.enums import (
    AgentName,
    AssertionBasis,
    ChangeType,
    DataProvenance,
    DocumentStatus,
    ReviewStatus,
)
from app.models.regulatory import RegulatoryChange, RegulatoryClause, RegulatoryDocument
from app.prompts.analysis import (
    ANALYSIS_SYSTEM,
    COMPARISON_SYSTEM,
    build_analysis_prompt,
    build_comparison_prompt,
)
from app.prompts.base import truncate_for_model
from app.schemas.ai_contracts import ExtractedRequirement, RegulatoryAnalysisResult

logger = get_logger(__name__)

# Gemini 2.5 Flash has a large context window, but analysis quality and cost
# both favour a bounded slice. Documents longer than this are truncated and the
# truncation is recorded on the result.
MAX_DOCUMENT_CHARS = 120_000
# For a two-document comparison each side gets half the budget.
MAX_COMPARISON_CHARS = 55_000


class AnalysisOutcome:
    def __init__(
        self,
        *,
        document: RegulatoryDocument,
        changes: list[RegulatoryChange],
        notes: str,
        truncated: bool,
        unverified_quotes: int,
        compared_to: RegulatoryDocument | None = None,
    ) -> None:
        self.document = document
        self.changes = changes
        self.notes = notes
        self.truncated = truncated
        self.unverified_quotes = unverified_quotes
        self.compared_to = compared_to

    def to_dict(self) -> dict:
        return {
            "document_id": str(self.document.id),
            "title": self.document.title,
            "compared_to_document_id": str(self.compared_to.id) if self.compared_to else None,
            "change_count": len(self.changes),
            "explicit_requirements": sum(
                1
                for c in self.changes
                if c.assertion_basis == AssertionBasis.EXPLICIT_REQUIREMENT.value
            ),
            "unverified_quotes": self.unverified_quotes,
            "truncated": self.truncated,
            "notes": self.notes,
        }


def _verify_quote(quote: str, haystack_normalised: str) -> bool:
    """Is this quote genuinely present in the source document?

    Compared on normalised whitespace and case, because markdown conversion
    legitimately reflows text. Anything shorter than 25 characters is too
    generic to verify meaningfully and is treated as unverified.
    """
    if not quote or len(quote.strip()) < 25:
        return False
    return normalise_text(quote) in haystack_normalised


def _match_clause(
    quote: str, reference: str, clauses: list[RegulatoryClause]
) -> RegulatoryClause | None:
    """Link a requirement to the clause it came from.

    Tries the stated reference first, then falls back to locating the quote in
    clause text, which is more reliable than trusting the model's reference.
    """
    if reference:
        wanted = reference.strip().lower()
        # "Para 38" from the model should match our stored "Para 38".
        for clause in clauses:
            if clause.clause_reference.strip().lower() == wanted:
                return clause
        # Tolerate "38" vs "Para 38".
        bare = re.sub(r"^(para|section|clause|paragraph)\s*", "", wanted).strip()
        for clause in clauses:
            stored = re.sub(
                r"^(para|section|clause|paragraph)\s*", "", clause.clause_reference.strip().lower()
            ).strip()
            if stored and stored == bare:
                return clause

    if quote and len(quote.strip()) >= 25:
        needle = normalise_text(quote)
        for clause in clauses:
            if needle in normalise_text(clause.text):
                return clause
    return None


def _coerce_effective_date(raw: str) -> date | None:
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


class AnalysisAgent:
    def __init__(self, client: GeminiClient | None = None) -> None:
        self.client = client or GeminiClient()

    async def analyse_document(
        self,
        db: Session,
        ctx: AgentContext,
        document_id: uuid.UUID,
        *,
        compare_to_document_id: uuid.UUID | None = None,
        use_pro_model: bool = False,
    ) -> AnalysisOutcome:
        """Analyse one document, optionally against a previous version."""
        if not self.client.configured:
            raise AgentUnavailable(
                "Gemini is not configured, so regulatory analysis cannot run.",
                remediation=(
                    "Set GEMINI_API_KEY in backend/.env. Create a key at "
                    "https://aistudio.google.com/apikey"
                ),
            )

        document = db.get(RegulatoryDocument, document_id)
        if document is None:
            raise LookupError(f"Regulatory document {document_id} not found")
        if not document.raw_text:
            raise AgentUnavailable(
                f"Document '{document.title}' has no extracted text to analyse.",
                remediation="Run extraction (Agent 2) for this document first.",
            )

        previous = (
            db.get(RegulatoryDocument, compare_to_document_id)
            if compare_to_document_id
            else None
        )
        if compare_to_document_id and previous is None:
            raise LookupError(f"Comparison document {compare_to_document_id} not found")

        ctx.emit(
            EventType.ANALYSIS_STARTED,
            {
                "document_id": str(document.id),
                "title": document.title,
                "mode": "comparison" if previous else "single",
            },
        )

        model = settings.gemini_pro_model if use_pro_model else settings.gemini_model

        with agent_activity(
            db,
            agent=AgentName.ANALYSIS,
            operation=(
                f"compare: {document.title[:80]}" if previous else f"analyse: {document.title[:80]}"
            ),
            ctx=ctx,
            entity_type="RegulatoryDocument",
            entity_id=document.id,
            external_service="gemini",
            details={"model": model, "comparison": bool(previous)},
        ) as outcome:
            if previous is not None:
                if not previous.raw_text:
                    raise AgentUnavailable(
                        f"Comparison document '{previous.title}' has no extracted text.",
                        remediation="Run extraction for the previous version first.",
                    )
                current_text, cut_current = truncate_for_model(
                    document.raw_text, MAX_COMPARISON_CHARS
                )
                previous_text, cut_previous = truncate_for_model(
                    previous.raw_text, MAX_COMPARISON_CHARS
                )
                truncated = cut_current or cut_previous
                user_content = build_comparison_prompt(
                    document_title=document.title,
                    previous_label=f"{previous.circular_number or 'v' + str(previous.version)}"
                    f" (version {previous.version})",
                    current_label=f"{document.circular_number or 'v' + str(document.version)}"
                    f" (version {document.version})",
                    previous_text=previous_text,
                    current_text=current_text,
                    institution_type=settings.institution_type,
                    truncated=truncated,
                )
                system_instruction = COMPARISON_SYSTEM
            else:
                document_text, truncated = truncate_for_model(
                    document.raw_text, MAX_DOCUMENT_CHARS
                )
                user_content = build_analysis_prompt(
                    document_title=document.title,
                    circular_number=document.circular_number,
                    document_type=document.document_type,
                    source_url=document.source_url,
                    publication_date=(
                        document.publication_date.isoformat()
                        if document.publication_date
                        else None
                    ),
                    institution_type=settings.institution_type,
                    document_text=document_text,
                    was_truncated=truncated,
                )
                system_instruction = ANALYSIS_SYSTEM

            result: RegulatoryAnalysisResult = await self.client.generate_structured(
                system_instruction=system_instruction,
                user_content=user_content,
                output_model=RegulatoryAnalysisResult,
                model=model,
                temperature=0.1,
            )
            outcome["requirement_count"] = len(result.requirements)
            outcome["truncated"] = truncated
            outcome["message"] = f"{len(result.requirements)} requirements extracted"

        changes, unverified = self._persist_changes(
            db,
            document=document,
            previous=previous,
            result=result,
            model=model,
            truncated=truncated,
        )

        notes = result.notes or ""
        if truncated:
            notes = (
                f"{notes} [System note: source text was truncated for analysis.]".strip()
            )
        if unverified:
            notes = (
                f"{notes} [System note: {unverified} item(s) had an unlocatable "
                f"source quote and were downgraded to UNVERIFIED_ASSUMPTION.]"
            ).strip()

        document.status = DocumentStatus.ANALYSED.value
        if result.stated_applicability and not document.applicability_text:
            document.applicability_text = result.stated_applicability[:2000]
        db.commit()

        analysis = AnalysisOutcome(
            document=document,
            changes=changes,
            notes=notes,
            truncated=truncated,
            unverified_quotes=unverified,
            compared_to=previous,
        )
        ctx.emit(EventType.ANALYSIS_COMPLETED, analysis.to_dict())
        return analysis

    # ---- persistence ------------------------------------------------------
    def _next_requirement_sequence(self, db: Session) -> int:
        """Global requirement counter so REQ codes are unique and readable."""
        total = db.execute(select(func.count()).select_from(RegulatoryChange)).scalar() or 0
        return total + 1

    def _persist_changes(
        self,
        db: Session,
        *,
        document: RegulatoryDocument,
        previous: RegulatoryDocument | None,
        result: RegulatoryAnalysisResult,
        model: str,
        truncated: bool,
    ) -> tuple[list[RegulatoryChange], int]:
        clauses = list(document.clauses)
        # Quotes are verified against whichever document they should come from.
        current_normalised = normalise_text(document.raw_text or "")
        previous_normalised = normalise_text(previous.raw_text or "") if previous else ""

        sequence = self._next_requirement_sequence(db)
        created: list[RegulatoryChange] = []
        unverified = 0
        seen_quotes: set[str] = set()

        for item in result.requirements:
            basis, verified = self._resolve_assertion_basis(
                item, current_normalised, previous_normalised
            )
            if not verified:
                unverified += 1

            # Drop exact duplicates the model sometimes emits twice.
            dedupe_key = normalise_text(item.source_quote)[:200] or normalise_text(item.title)
            if dedupe_key and dedupe_key in seen_quotes:
                continue
            seen_quotes.add(dedupe_key)

            clause = _match_clause(item.source_quote, item.source_reference, clauses)
            requirement_code = f"REQ-{sequence:04d}"
            sequence += 1

            change = RegulatoryChange(
                document_id=document.id,
                clause_id=clause.id if clause else None,
                compared_to_document_id=previous.id if previous else None,
                requirement_code=requirement_code,
                title=item.title[:512],
                requirement_text=item.requirement_text,
                change_type=item.change_type.value
                if hasattr(item.change_type, "value")
                else str(item.change_type),
                assertion_basis=basis,
                source_quote=item.source_quote or None,
                source_reference=(
                    clause.clause_reference if clause else (item.source_reference or None)
                ),
                previous_text=item.previous_text or None,
                applicable_entities=item.applicable_entities or [],
                primary_banking_module=(
                    item.primary_banking_module.value
                    if hasattr(item.primary_banking_module, "value")
                    else str(item.primary_banking_module)
                ),
                effective_date=_coerce_effective_date(item.effective_date)
                or document.effective_date,
                priority=item.priority.value
                if hasattr(item.priority, "value")
                else str(item.priority),
                confidence=item.confidence,
                review_status=ReviewStatus.PENDING_REVIEW.value,
                model_name=model,
                provenance=DataProvenance.LIVE_GEMINI.value,
            )
            db.add(change)
            try:
                db.flush()
            except Exception as exc:
                db.rollback()
                logger.warning("Could not persist change %s: %s", requirement_code, exc)
                continue
            created.append(change)

        db.commit()
        logger.info(
            "Analysis of %s produced %d changes (%d with unverifiable quotes)",
            document.title[:80],
            len(created),
            unverified,
        )
        return created, unverified

    def _resolve_assertion_basis(
        self,
        item: ExtractedRequirement,
        current_normalised: str,
        previous_normalised: str,
    ) -> tuple[str, bool]:
        """Decide the provenance label, verifying the quote first.

        A REMOVED_REQUIREMENT is quoted from the previous version, so it is
        checked against that text instead.
        """
        declared = (
            item.assertion_basis.value
            if hasattr(item.assertion_basis, "value")
            else str(item.assertion_basis)
        )
        change_type = (
            item.change_type.value
            if hasattr(item.change_type, "value")
            else str(item.change_type)
        )

        haystack = current_normalised
        if change_type == ChangeType.REMOVED_REQUIREMENT.value and previous_normalised:
            haystack = previous_normalised

        verified = _verify_quote(item.source_quote, haystack)
        # A second chance against the other version: the model occasionally
        # quotes the right text but attributes it to the wrong side.
        if not verified and previous_normalised:
            other = previous_normalised if haystack is current_normalised else current_normalised
            verified = _verify_quote(item.source_quote, other)

        if verified:
            return declared, True

        # Cannot locate the quote, so it must not stand as an explicit
        # requirement no matter what the model claimed.
        if declared == AssertionBasis.EXPLICIT_REQUIREMENT.value:
            return AssertionBasis.UNVERIFIED_ASSUMPTION.value, False
        return declared, False


def find_previous_version(db: Session, document: RegulatoryDocument) -> RegulatoryDocument | None:
    """The immediately preceding stored version of the same document."""
    if document.supersedes_id:
        return db.get(RegulatoryDocument, document.supersedes_id)
    return (
        db.execute(
            select(RegulatoryDocument)
            .where(
                RegulatoryDocument.source_url == document.source_url,
                RegulatoryDocument.version < document.version,
            )
            .order_by(RegulatoryDocument.version.desc())
        )
        .scalars()
        .first()
    )
