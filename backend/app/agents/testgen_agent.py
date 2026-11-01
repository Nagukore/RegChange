"""Agent 5 -- Test Generation Agent (Gemini + structured templates).

Generates positive, negative, boundary, regression and integration test cases
for an APPROVED impact mapping, and validates each one against the CBS contract
before persisting it.

Two gates stand between the model and a stored test case:
  1. The impact mapping must already be APPROVED by a human (configurable).
  2. `_validate_generated` checks `http_path` is a real CBS endpoint, the method
     matches, and any asserted `rejection_code` is one the system can actually
     return. A test that cannot possibly pass or fail meaningfully is dropped.
"""
from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agents.base import AgentContext, AgentUnavailable, agent_activity
from app.agents.cbs_contract import (
    ENDPOINTS_BY_KEY,
    PATHS,
    REJECTION_CODES,
    render_contract,
)
from app.core.config import settings
from app.core.events import EventType
from app.core.logging import get_logger
from app.integrations.gemini import GeminiClient
from app.models.enums import (
    AgentName,
    AutomationType,
    DataProvenance,
    Priority,
    ReviewStatus,
    TestCategory,
)
from app.models.mapping import ImpactMapping
from app.models.testing import TestCase
from app.prompts.impact import TEST_GENERATION_SYSTEM, build_test_generation_prompt
from app.schemas.ai_contracts import GeneratedTestCase, TestGenerationResult

logger = get_logger(__name__)

# Short module tags used in the test code, e.g. TC-KYC-0042.
MODULE_TAG = {
    "CUSTOMER_ONBOARDING": "ONB",
    "KYC_AML": "KYC",
    "ACCOUNT_MANAGEMENT": "ACC",
    "TRANSACTIONS": "TXN",
    "CROSS_CUTTING": "GEN",
}


class TestGenOutcome:
    def __init__(
        self,
        *,
        mapping: ImpactMapping,
        test_cases: list[TestCase],
        discarded: list[dict],
        notes: str,
    ) -> None:
        self.mapping = mapping
        self.test_cases = test_cases
        self.discarded = discarded
        self.notes = notes

    def to_dict(self) -> dict:
        by_category: dict[str, int] = {}
        for case in self.test_cases:
            by_category[case.category] = by_category.get(case.category, 0) + 1
        return {
            "impact_mapping_id": str(self.mapping.id),
            "rule_code": self.mapping.business_rule.rule_code
            if self.mapping.business_rule
            else None,
            "requirement_code": self.mapping.change.requirement_code
            if self.mapping.change
            else None,
            "test_count": len(self.test_cases),
            "by_category": by_category,
            "discarded_count": len(self.discarded),
            "discarded": self.discarded[:10],
            "notes": self.notes,
        }


class TestGenerationAgent:
    def __init__(self, client: GeminiClient | None = None) -> None:
        self.client = client or GeminiClient()

    async def generate_for_mapping(
        self,
        db: Session,
        ctx: AgentContext,
        mapping_id: uuid.UUID,
        *,
        tests_per_category: int = 1,
        require_approval: bool | None = None,
    ) -> TestGenOutcome:
        """Generate test cases for one approved impact mapping."""
        if not self.client.configured:
            raise AgentUnavailable(
                "Gemini is not configured, so test generation cannot run.",
                remediation=(
                    "Set GEMINI_API_KEY in backend/.env. Create a key at "
                    "https://aistudio.google.com/apikey"
                ),
            )

        mapping = db.get(ImpactMapping, mapping_id)
        if mapping is None:
            raise LookupError(f"Impact mapping {mapping_id} not found")

        gate = (
            settings.require_approval_for_execution
            if require_approval is None
            else require_approval
        )
        if gate and mapping.review_status != ReviewStatus.APPROVED.value:
            raise AgentUnavailable(
                f"Impact mapping {mapping_id} is {mapping.review_status}, not APPROVED, "
                "so test generation is blocked.",
                remediation=(
                    "A reviewer must approve the impact mapping on the Impact "
                    "Analysis screen before tests are generated from it."
                ),
            )

        change = mapping.change
        rule = mapping.business_rule
        if change is None or rule is None:
            raise AgentUnavailable(
                "This impact mapping is missing its requirement or business rule.",
                remediation="Re-run impact analysis for the requirement.",
            )

        ctx.emit(
            EventType.TEST_GENERATION_STARTED,
            {
                "impact_mapping_id": str(mapping.id),
                "requirement_code": change.requirement_code,
                "rule_code": rule.rule_code,
            },
        )

        with agent_activity(
            db,
            agent=AgentName.TEST_GENERATION,
            operation=f"generate: {change.requirement_code} -> {rule.rule_code}",
            ctx=ctx,
            entity_type="ImpactMapping",
            entity_id=mapping.id,
            external_service="gemini",
            details={"model": settings.gemini_model, "rule_code": rule.rule_code},
        ) as outcome:
            user_content = build_test_generation_prompt(
                requirement_code=change.requirement_code,
                requirement_title=change.title,
                requirement_text=change.requirement_text,
                source_quote=change.source_quote,
                source_reference=change.source_reference,
                rule_code=rule.rule_code,
                rule_name=rule.name,
                rule_description=rule.description,
                rule_api_endpoint=rule.api_endpoint,
                impact_category=mapping.impact_category,
                affected_fields=mapping.affected_fields or rule.affected_fields or [],
                api_contract=render_contract(rule.rule_code),
                rejection_codes=REJECTION_CODES,
                tests_per_category=tests_per_category,
            )
            result: TestGenerationResult = await self.client.generate_structured(
                system_instruction=TEST_GENERATION_SYSTEM,
                user_content=user_content,
                output_model=TestGenerationResult,
                temperature=0.3,  # a little variety across categories
                max_output_tokens=32768,
            )
            outcome["generated_count"] = len(result.test_cases)
            outcome["message"] = f"{len(result.test_cases)} test cases generated"

        valid, discarded = self._validate_generated(result.test_cases)
        test_cases = self._persist(db, mapping=mapping, generated=valid)

        notes = result.notes or ""
        if discarded:
            notes = (
                f"{notes} [System note: discarded {len(discarded)} generated test(s) "
                "that did not match the CBS API contract.]"
            ).strip()

        gen_outcome = TestGenOutcome(
            mapping=mapping, test_cases=test_cases, discarded=discarded, notes=notes
        )
        ctx.emit(EventType.TEST_GENERATION_COMPLETED, gen_outcome.to_dict())
        return gen_outcome

    # ---- validation against the CBS contract -----------------------------
    def _validate_generated(
        self, generated: list[GeneratedTestCase]
    ) -> tuple[list[GeneratedTestCase], list[dict]]:
        """Drop tests that could never execute meaningfully."""
        valid: list[GeneratedTestCase] = []
        discarded: list[dict] = []

        for case in generated:
            path = (case.http_path or "").strip()
            method = (case.http_method or "POST").strip().upper()

            if not path:
                discarded.append({"title": case.title[:120], "reason": "no http_path"})
                continue
            if path not in PATHS:
                discarded.append(
                    {
                        "title": case.title[:120],
                        "reason": f"http_path {path!r} is not a CBS endpoint",
                    }
                )
                continue
            if f"{method} {path}" not in ENDPOINTS_BY_KEY:
                discarded.append(
                    {
                        "title": case.title[:120],
                        "reason": f"{method} is not supported on {path}",
                    }
                )
                continue
            # An assertion on rejection_code must name a code we can emit.
            if (
                case.expect_json_path.strip() == "rejection_code"
                and case.expect_json_value.strip()
                and case.expect_json_value.strip() not in REJECTION_CODES
            ):
                discarded.append(
                    {
                        "title": case.title[:120],
                        "reason": (
                            f"rejection_code {case.expect_json_value!r} is not a "
                            "code this system returns"
                        ),
                    }
                )
                continue
            valid.append(case)

        for entry in discarded:
            logger.warning("Discarded generated test: %s (%s)", entry["title"], entry["reason"])
        return valid, discarded

    # ---- persistence ------------------------------------------------------
    def _next_test_sequence(self, db: Session) -> int:
        total = db.execute(select(func.count()).select_from(TestCase)).scalar() or 0
        return total + 1

    def _persist(
        self,
        db: Session,
        *,
        mapping: ImpactMapping,
        generated: list[GeneratedTestCase],
    ) -> list[TestCase]:
        change = mapping.change
        rule = mapping.business_rule
        tag = MODULE_TAG.get(mapping.banking_module, "GEN")
        sequence = self._next_test_sequence(db)
        created: list[TestCase] = []

        for case in generated:
            endpoint = ENDPOINTS_BY_KEY[f"{case.http_method.upper()} {case.http_path}"]
            test_code = f"TC-{tag}-{sequence:04d}"
            sequence += 1

            execution_spec = {
                "driver": "PYTEST_API",
                "method": case.http_method.upper(),
                "path": case.http_path,
                "expect_status": case.expect_status,
                "expect_json_path": case.expect_json_path.strip() or None,
                "expect_json_value": case.expect_json_value.strip() or None,
                # Carried through so the executor knows whether to seed fixtures.
                "endpoint_state_changing": endpoint.state_changing,
            }

            test_case = TestCase(
                test_code=test_code,
                impact_mapping_id=mapping.id,
                change_id=change.id if change else None,
                business_rule_id=rule.id if rule else None,
                title=case.title[:512],
                objective=case.objective,
                banking_module=mapping.banking_module,
                category=_enum_value(case.category, TestCategory.POSITIVE),
                priority=_enum_value(case.priority, Priority.MEDIUM),
                automation_type=_enum_value(case.automation_type, AutomationType.API),
                preconditions=case.preconditions or [],
                test_data=dict(case.test_data or {}),
                steps=[
                    {"step": s.step, "action": s.action, "expected": s.expected}
                    for s in case.steps
                ],
                expected_result=case.expected_result,
                execution_spec=execution_spec,
                is_state_changing=bool(case.is_state_changing or endpoint.state_changing),
                # Denormalised traceability back to the clause.
                source_clause_reference=change.source_reference if change else None,
                source_quote=change.source_quote if change else None,
                requirement_code=change.requirement_code if change else None,
                approval_status=ReviewStatus.PENDING_REVIEW.value,
                model_name=settings.gemini_model,
                provenance=DataProvenance.LIVE_GEMINI.value,
            )
            db.add(test_case)
            try:
                db.flush()
            except Exception as exc:
                db.rollback()
                logger.warning("Could not persist test case %s: %s", test_code, exc)
                continue
            created.append(test_case)

        db.commit()
        for case in created:
            db.refresh(case)
        logger.info(
            "Generated %d test case(s) for %s -> %s",
            len(created),
            change.requirement_code if change else "?",
            rule.rule_code if rule else "?",
        )
        return created

    async def generate_batch(
        self,
        db: Session,
        ctx: AgentContext,
        mapping_ids: list[uuid.UUID],
        *,
        tests_per_category: int = 1,
        require_approval: bool | None = None,
    ) -> list[TestGenOutcome]:
        outcomes: list[TestGenOutcome] = []
        for mapping_id in mapping_ids:
            try:
                outcomes.append(
                    await self.generate_for_mapping(
                        db,
                        ctx,
                        mapping_id,
                        tests_per_category=tests_per_category,
                        require_approval=require_approval,
                    )
                )
            except AgentUnavailable as exc:
                # A mapping that is not yet approved is an expected skip, not a
                # failure of the batch.
                logger.info("Skipping mapping %s: %s", mapping_id, exc)
            except Exception as exc:
                logger.warning("Test generation failed for %s: %s", mapping_id, exc)
        return outcomes


def _enum_value(value, default) -> str:  # noqa: ANN001
    if value is None:
        return default.value
    return value.value if hasattr(value, "value") else str(value)
