"""Agent 4 -- Banking Impact Analysis Agent (Gemini + deterministic mapping).

Implements the five-step hybrid approach from the brief:
  1. The LLM has already extracted the requirement (Agent 3).
  2. `app/agents/retrieval.py` finds candidate CBS rules lexically.
  3. Gemini evaluates which candidates are genuinely affected and why.
  4. `_validate_proposals` here deterministically rejects any rule code that is
     not in the catalogue -- the model cannot invent a CBS rule ID.
  5. Every surviving mapping is persisted as PENDING_REVIEW for a human.

Step 4 is the load-bearing one: it is checked against the database, not against
a list the model was shown, so even a hallucinated-but-plausible code is dropped.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.base import AgentContext, AgentUnavailable, agent_activity
from app.agents.retrieval import normalise_scores, score_rules
from app.core.config import settings
from app.core.events import EventType
from app.core.logging import get_logger
from app.integrations.gemini import GeminiClient
from app.models.enums import (
    AgentName,
    AssertionBasis,
    DataProvenance,
    ImpactCategory,
    Priority,
    ReviewStatus,
)
from app.models.mapping import BusinessRule, ImpactMapping
from app.models.regulatory import RegulatoryChange
from app.prompts.impact import IMPACT_SYSTEM, build_impact_prompt
from app.schemas.ai_contracts import ImpactAnalysisResult, ProposedImpact

logger = get_logger(__name__)

# How many candidate rules the model is allowed to consider per requirement.
CANDIDATE_LIMIT = 8


class ImpactOutcome:
    def __init__(
        self,
        *,
        change: RegulatoryChange,
        mappings: list[ImpactMapping],
        rejected_codes: list[str],
        candidates: list[dict],
        notes: str,
    ) -> None:
        self.change = change
        self.mappings = mappings
        self.rejected_codes = rejected_codes
        self.candidates = candidates
        self.notes = notes

    def to_dict(self) -> dict:
        return {
            "change_id": str(self.change.id),
            "requirement_code": self.change.requirement_code,
            "mapping_count": len(self.mappings),
            "rule_codes": [m.business_rule.rule_code for m in self.mappings if m.business_rule],
            "rejected_invented_codes": self.rejected_codes,
            "candidates_considered": [c["rule_code"] for c in self.candidates],
            "notes": self.notes,
        }


class ImpactAgent:
    def __init__(self, client: GeminiClient | None = None) -> None:
        self.client = client or GeminiClient()

    async def analyse_change(
        self,
        db: Session,
        ctx: AgentContext,
        change_id: uuid.UUID,
        *,
        candidate_limit: int = CANDIDATE_LIMIT,
    ) -> ImpactOutcome:
        """Map one regulatory requirement onto CBS business rules."""
        if not self.client.configured:
            raise AgentUnavailable(
                "Gemini is not configured, so impact analysis cannot run.",
                remediation=(
                    "Set GEMINI_API_KEY in backend/.env. Create a key at "
                    "https://aistudio.google.com/apikey"
                ),
            )

        change = db.get(RegulatoryChange, change_id)
        if change is None:
            raise LookupError(f"Regulatory change {change_id} not found")

        rules = list(
            db.execute(select(BusinessRule).where(BusinessRule.is_active.is_(True)))
            .scalars()
            .all()
        )
        if not rules:
            raise AgentUnavailable(
                "The CBS business-rule catalogue is empty, so nothing can be mapped.",
                remediation="Restart the API to re-run seeding, or check the database.",
            )

        ctx.emit(
            EventType.IMPACT_MAPPING_STARTED,
            {
                "change_id": str(change.id),
                "requirement_code": change.requirement_code,
                "title": change.title,
            },
        )

        # Step 2: lexical retrieval over the catalogue.
        query_text = " ".join(
            filter(None, [change.title, change.requirement_text, change.source_quote or ""])
        )
        candidates = normalise_scores(
            score_rules(
                query_text,
                rules,
                banking_module=change.primary_banking_module,
                top_k=candidate_limit,
            )
        )
        candidate_payload = [
            {
                **candidate.to_dict(),
                "description": candidate.rule.description,
                "api_endpoint": candidate.rule.api_endpoint,
                "affected_fields": candidate.rule.affected_fields or [],
            }
            for candidate in candidates
        ]

        if not candidate_payload:
            # Honest empty result rather than forcing a mapping.
            logger.info(
                "No candidate CBS rules retrieved for %s; recording no impact",
                change.requirement_code,
            )
            ctx.emit(
                EventType.IMPACT_MAPPING_COMPLETED,
                {
                    "change_id": str(change.id),
                    "requirement_code": change.requirement_code,
                    "mapping_count": 0,
                    "notes": "No candidate CBS rule matched this requirement.",
                },
            )
            return ImpactOutcome(
                change=change,
                mappings=[],
                rejected_codes=[],
                candidates=[],
                notes="No candidate CBS rule matched this requirement lexically.",
            )

        # Step 3: the model reasons over the shortlist.
        with agent_activity(
            db,
            agent=AgentName.IMPACT,
            operation=f"impact: {change.requirement_code}",
            ctx=ctx,
            entity_type="RegulatoryChange",
            entity_id=change.id,
            external_service="gemini",
            details={
                "model": settings.gemini_model,
                "candidates": [c["rule_code"] for c in candidate_payload],
            },
        ) as outcome:
            user_content = build_impact_prompt(
                requirement_code=change.requirement_code,
                requirement_title=change.title,
                requirement_text=change.requirement_text,
                source_quote=change.source_quote,
                source_reference=change.source_reference,
                change_type=change.change_type,
                assertion_basis=change.assertion_basis,
                banking_module=change.primary_banking_module,
                candidate_rules=candidate_payload,
            )
            result: ImpactAnalysisResult = await self.client.generate_structured(
                system_instruction=IMPACT_SYSTEM,
                user_content=user_content,
                output_model=ImpactAnalysisResult,
                temperature=0.15,
            )
            outcome["proposed_count"] = len(result.impacts)
            outcome["message"] = f"{len(result.impacts)} impacts proposed"

        # Steps 4 and 5: deterministic validation, then persist for review.
        valid, rejected = self._validate_proposals(db, result.impacts)
        mappings = self._persist_mappings(
            db, change=change, proposals=valid, candidates=candidates
        )

        notes = result.notes or ""
        if rejected:
            notes = (
                f"{notes} [System note: rejected {len(rejected)} proposed mapping(s) "
                f"referencing rule code(s) not in the catalogue: {', '.join(rejected)}.]"
            ).strip()

        impact = ImpactOutcome(
            change=change,
            mappings=mappings,
            rejected_codes=rejected,
            candidates=candidate_payload,
            notes=notes,
        )
        ctx.emit(EventType.IMPACT_MAPPING_COMPLETED, impact.to_dict())
        return impact

    # ---- step 4: deterministic validation --------------------------------
    def _validate_proposals(
        self, db: Session, proposals: list[ProposedImpact]
    ) -> tuple[list[tuple[ProposedImpact, BusinessRule]], list[str]]:
        """Keep only proposals naming a rule code that exists in the database.

        Validated against `business_rules`, not against the prompt's candidate
        list, so this catches an invented code even if it looks plausible.
        """
        valid: list[tuple[ProposedImpact, BusinessRule]] = []
        rejected: list[str] = []
        seen: set[str] = set()

        for proposal in proposals:
            code = (proposal.rule_code or "").strip().upper()
            if not code or code in seen:
                continue
            rule = db.execute(
                select(BusinessRule).where(BusinessRule.rule_code == code)
            ).scalar_one_or_none()
            if rule is None:
                rejected.append(code)
                logger.warning(
                    "Discarding impact mapping for non-existent CBS rule code %r", code
                )
                continue
            seen.add(code)
            valid.append((proposal, rule))
        return valid, rejected

    # ---- step 5: persist for human review --------------------------------
    def _persist_mappings(
        self,
        db: Session,
        *,
        change: RegulatoryChange,
        proposals: list[tuple[ProposedImpact, BusinessRule]],
        candidates: list,
    ) -> list[ImpactMapping]:
        retrieval_scores = {c.rule.rule_code: c.score for c in candidates}
        created: list[ImpactMapping] = []

        for proposal, rule in proposals:
            existing = db.execute(
                select(ImpactMapping).where(
                    ImpactMapping.change_id == change.id,
                    ImpactMapping.business_rule_id == rule.id,
                )
            ).scalar_one_or_none()
            if existing is not None:
                # Re-running analysis must not duplicate a reviewer's decision.
                created.append(existing)
                continue

            mapping = ImpactMapping(
                change_id=change.id,
                business_rule_id=rule.id,
                impact_category=_enum_value(proposal.impact_category, ImpactCategory.BUSINESS_LOGIC),
                banking_module=rule.banking_module,
                priority=_enum_value(proposal.priority, Priority.MEDIUM),
                rationale=proposal.rationale,
                assertion_basis=_enum_value(
                    proposal.assertion_basis, AssertionBasis.AI_INTERPRETATION
                ),
                # Prefer the rule's declared fields when the model echoes them.
                affected_fields=proposal.affected_fields or (rule.affected_fields or []),
                affected_workflows=proposal.affected_workflows or [],
                regression_risk=_enum_value(proposal.regression_risk, Priority.LOW),
                confidence=proposal.confidence,
                retrieval_score=retrieval_scores.get(rule.rule_code),
                review_status=ReviewStatus.PENDING_REVIEW.value,
                model_name=settings.gemini_model,
                provenance=DataProvenance.LIVE_GEMINI.value,
            )
            db.add(mapping)
            try:
                db.flush()
            except Exception as exc:
                db.rollback()
                logger.warning(
                    "Could not persist impact mapping %s -> %s: %s",
                    change.requirement_code,
                    rule.rule_code,
                    exc,
                )
                continue
            created.append(mapping)

        db.commit()
        for mapping in created:
            db.refresh(mapping)
        logger.info(
            "Impact analysis for %s persisted %d mapping(s)",
            change.requirement_code,
            len(created),
        )
        return created

    async def analyse_batch(
        self,
        db: Session,
        ctx: AgentContext,
        change_ids: list[uuid.UUID],
    ) -> list[ImpactOutcome]:
        """Map several requirements, continuing past individual failures."""
        outcomes: list[ImpactOutcome] = []
        for change_id in change_ids:
            try:
                outcomes.append(await self.analyse_change(db, ctx, change_id))
            except AgentUnavailable:
                raise
            except Exception as exc:
                logger.warning("Impact analysis failed for %s: %s", change_id, exc)
        return outcomes


def _enum_value(value, default) -> str:  # noqa: ANN001
    """Normalise a Pydantic enum or raw string into its stored string value."""
    if value is None:
        return default.value
    return value.value if hasattr(value, "value") else str(value)


def approve_mapping(
    db: Session,
    mapping_id: uuid.UUID,
    *,
    decision: str,
    reviewer_id: uuid.UUID | None,
    notes: str | None = None,
) -> ImpactMapping:
    """Record a human review decision on an impact mapping."""
    mapping = db.get(ImpactMapping, mapping_id)
    if mapping is None:
        raise LookupError(f"Impact mapping {mapping_id} not found")

    decision_upper = decision.strip().upper()
    allowed = {
        "APPROVE": ReviewStatus.APPROVED.value,
        "APPROVED": ReviewStatus.APPROVED.value,
        "REJECT": ReviewStatus.REJECTED.value,
        "REJECTED": ReviewStatus.REJECTED.value,
        "NEEDS_CHANGES": ReviewStatus.NEEDS_CHANGES.value,
    }
    if decision_upper not in allowed:
        raise ValueError("decision must be APPROVE, REJECT or NEEDS_CHANGES")

    mapping.review_status = allowed[decision_upper]
    mapping.reviewed_by_id = reviewer_id
    mapping.reviewed_at = datetime.now(UTC)
    mapping.review_notes = notes
    db.commit()
    db.refresh(mapping)
    return mapping
