"""Pydantic contracts for every Gemini response.

These double as the `responseSchema` sent to Gemini and the validation gate on
the way back, so the model is constrained at generation time and re-checked at
ingestion time. Nothing from an LLM reaches the database without passing one of
these models.

Kept deliberately flat and enum-light: Gemini's structured-output subset does
not support nested unions or `$ref`, and `app/integrations/gemini/client.py`
flattens what it can rather than silently dropping constraints.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.enums import (
    AssertionBasis,
    AutomationType,
    BankingModule,
    ChangeType,
    ImpactCategory,
    Priority,
    TestCategory,
)


class _AIBase(BaseModel):
    # Reject unknown keys: an unexpected field means the contract drifted and
    # we would rather fail loudly than persist something unmodelled.
    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------
# Agent 3 -- Regulatory Analysis
# ---------------------------------------------------------------------------
class ExtractedRequirement(_AIBase):
    title: str = Field(description="Short imperative summary of the obligation, max 15 words")
    requirement_text: str = Field(description="The obligation restated precisely and neutrally")
    change_type: ChangeType = Field(description="Nature of the change relative to prior practice")
    assertion_basis: AssertionBasis = Field(
        description="EXPLICIT_REQUIREMENT only when the source states the obligation itself"
    )
    source_quote: str = Field(
        description="Verbatim quote copied character-for-character from the data block"
    )
    source_reference: str = Field(
        description="Locator such as 'Para 38(a)' or the heading the quote sits under"
    )
    previous_text: str = Field(
        default="",
        description="The superseded wording when comparing two document versions, else empty",
    )
    primary_banking_module: BankingModule
    applicable_entities: list[str] = Field(
        default_factory=list,
        description="Regulated entity types named by the source, e.g. ['COMMERCIAL_BANK']. "
        "Empty when the source does not say.",
    )
    effective_date: str = Field(
        default="",
        description="ISO date YYYY-MM-DD if the source states one, otherwise empty string",
    )
    priority: Priority
    confidence: float = Field(ge=0.0, le=1.0, description="Your own confidence, 0 to 1")

    @field_validator("effective_date")
    @classmethod
    def _blank_or_iso(cls, v: str) -> str:
        """Accept an empty string or a strict ISO date; reject vague text."""
        if not v:
            return ""
        import datetime as _dt

        try:
            _dt.date.fromisoformat(v)
        except ValueError:
            # The model sometimes emits prose like "three months from issue".
            return ""
        return v


class RegulatoryAnalysisResult(_AIBase):
    requirements: list[ExtractedRequirement] = Field(default_factory=list)
    document_summary: str = Field(default="", description="Two sentences, neutral tone")
    stated_applicability: str = Field(
        default="",
        description="Verbatim applicability wording from the source, empty if absent",
    )
    notes: str = Field(
        default="",
        description="Limitations: truncation, unrelated content, unreadable sections",
    )


# ---------------------------------------------------------------------------
# Agent 4 -- Banking Impact Analysis
# ---------------------------------------------------------------------------
class ProposedImpact(_AIBase):
    # Must already exist in the business_rules catalogue; validated
    # deterministically after generation.
    rule_code: str = Field(description="An existing CBS rule code from the supplied catalogue only")
    impact_category: ImpactCategory
    priority: Priority
    regression_risk: Priority
    rationale: str = Field(description="Why this rule is affected, referencing the requirement")
    assertion_basis: AssertionBasis
    affected_fields: list[str] = Field(default_factory=list)
    affected_workflows: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)


class ImpactAnalysisResult(_AIBase):
    impacts: list[ProposedImpact] = Field(default_factory=list)
    notes: str = Field(default="")


# ---------------------------------------------------------------------------
# Agent 5 -- Test Generation
# ---------------------------------------------------------------------------
class GeneratedTestStep(_AIBase):
    step: int = Field(ge=1)
    action: str = Field(description="One concrete action a tester or script performs")
    expected: str = Field(default="", description="Observable outcome of this step, if any")


class GeneratedTestCase(_AIBase):
    title: str
    objective: str
    category: TestCategory
    priority: Priority
    automation_type: AutomationType
    preconditions: list[str] = Field(default_factory=list)
    # Flat string map: the CBS payload. Gemini cannot express arbitrary nesting
    # in responseSchema, so values are strings and the executor coerces them
    # against the endpoint contract.
    test_data: dict[str, str] = Field(default_factory=dict)
    steps: list[GeneratedTestStep] = Field(default_factory=list)
    expected_result: str
    is_state_changing: bool = Field(
        description="True when the test creates or modifies CBS data"
    )
    # Machine-checkable contract. Kept as explicit scalar fields rather than a
    # free-form object so it can be validated before execution.
    http_method: str = Field(default="POST", description="GET, POST, PATCH or PUT")
    http_path: str = Field(
        default="",
        description="CBS API path from the supplied endpoint list, e.g. /api/cbs/customers",
    )
    expect_status: int = Field(ge=100, le=599, description="Expected HTTP status code")
    expect_json_path: str = Field(
        default="",
        description="Dotted path into the response to assert on, e.g. 'rejection_code'. "
        "Empty to assert on status only.",
    )
    expect_json_value: str = Field(
        default="", description="Expected value at expect_json_path, compared as a string"
    )

    @field_validator("http_method")
    @classmethod
    def _known_method(cls, v: str) -> str:
        v = (v or "POST").upper().strip()
        return v if v in {"GET", "POST", "PATCH", "PUT", "DELETE"} else "POST"


class TestGenerationResult(_AIBase):
    test_cases: list[GeneratedTestCase] = Field(default_factory=list)
    notes: str = Field(default="")


# ---------------------------------------------------------------------------
# Clause segmentation assist (used only when deterministic parsing is thin)
# ---------------------------------------------------------------------------
class ClauseCandidate(_AIBase):
    clause_reference: str
    heading: str = Field(default="")
    text: str


class ClauseSegmentationResult(_AIBase):
    clauses: list[ClauseCandidate] = Field(default_factory=list)
    notes: str = Field(default="")


__all__ = [
    "ExtractedRequirement",
    "RegulatoryAnalysisResult",
    "ProposedImpact",
    "ImpactAnalysisResult",
    "GeneratedTestStep",
    "GeneratedTestCase",
    "TestGenerationResult",
    "ClauseCandidate",
    "ClauseSegmentationResult",
]
