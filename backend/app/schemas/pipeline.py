"""Request and response schemas for the regulatory pipeline API."""
from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# --- Discovery -------------------------------------------------------------
class DiscoveryStartIn(BaseModel):
    # Omit to use the curated RBI query set in app/seeds/regulatory_sources.py.
    queries: list[str] | None = Field(
        default=None, description="Override the default discovery queries"
    )
    institution_type: str | None = None
    recency_minutes: int | None = Field(
        default=None,
        ge=1,
        le=5_256_000,
        description="Restrict to results from the last N minutes (TinyFish limit: 10 years)",
    )
    max_results_per_query: int = Field(default=10, ge=1, le=50)


class DiscoveryRunOut(ORMModel):
    id: uuid.UUID
    status: str
    regulatory_authority: str
    institution_type: str
    queries: list[Any] | None
    search_domains: list[Any] | None
    documents_found: int
    documents_new: int
    documents_duplicate: int
    provenance: str
    tinyfish_request_ids: list[Any] | None
    error_message: str | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime


class JobOut(BaseModel):
    job_id: uuid.UUID
    kind: str
    status: str
    detail: str | None = None
    result: dict[str, Any] | None = None
    correlation_id: str | None = None
    started_at: datetime
    finished_at: datetime | None = None


class DiscoveryStatusOut(BaseModel):
    run: DiscoveryRunOut
    documents: list[RegulatoryDocumentSummary]


# --- Regulations -----------------------------------------------------------
class RegulatoryDocumentSummary(ORMModel):
    id: uuid.UUID
    title: str
    circular_number: str | None
    document_type: str
    source_url: str
    publication_date: date | None
    effective_date: date | None
    banking_domains: list[Any] | None
    applicable_institution_types: list[Any] | None
    version: int
    is_current: bool
    status: str
    provenance: str
    access_limitation: str | None
    char_count: int | None
    content_hash: str | None
    fetched_at: datetime | None
    created_at: datetime


class RegulatoryClauseOut(ORMModel):
    id: uuid.UUID
    clause_reference: str
    heading: str | None
    text: str
    ordinal: int
    clause_hash: str
    provenance: str


class RegulatoryDocumentDetail(RegulatoryDocumentSummary):
    raw_text: str | None
    applicability_text: str | None
    content_format: str
    search_snippet: str | None
    supersedes_id: uuid.UUID | None
    clauses: list[RegulatoryClauseOut] = Field(default_factory=list)


class DocumentVersionOut(ORMModel):
    id: uuid.UUID
    version: int
    is_current: bool
    content_hash: str | None
    char_count: int | None
    publication_date: date | None
    effective_date: date | None
    fetched_at: datetime | None
    created_at: datetime


class ClauseDiffEntry(BaseModel):
    change: Literal["ADDED", "REMOVED", "MODIFIED", "UNCHANGED"]
    clause_reference: str
    heading: str | None = None
    previous_text: str | None = None
    current_text: str | None = None
    # Word-level similarity, 0..1. Only meaningful for MODIFIED entries.
    similarity: float | None = None


class DocumentComparisonOut(BaseModel):
    previous: DocumentVersionOut
    current: DocumentVersionOut
    added: int
    removed: int
    modified: int
    unchanged: int
    entries: list[ClauseDiffEntry]


class DocumentListOut(BaseModel):
    items: list[RegulatoryDocumentSummary]
    total: int
    limit: int
    offset: int


# --- Extraction / Analysis -------------------------------------------------
class ExtractIn(BaseModel):
    document_ids: list[uuid.UUID] = Field(min_length=1, max_length=50)
    # ttl=0 on the TinyFish fetch; use after a regulator edits a page in place.
    force_refetch: bool = False


class AnalyseIn(BaseModel):
    # Supply to run an old-versus-new comparison instead of a single-document read.
    compare_to_document_id: uuid.UUID | None = None
    # Routes to the Pro model for long or dense instruments.
    use_pro_model: bool = False


class RegulatoryChangeOut(ORMModel):
    id: uuid.UUID
    document_id: uuid.UUID
    clause_id: uuid.UUID | None
    compared_to_document_id: uuid.UUID | None
    requirement_code: str
    title: str
    requirement_text: str
    change_type: str
    assertion_basis: str
    source_quote: str | None
    source_reference: str | None
    previous_text: str | None
    applicable_entities: list[Any] | None
    primary_banking_module: str
    effective_date: date | None
    priority: str
    confidence: float | None
    review_status: str
    review_notes: str | None
    reviewed_at: datetime | None
    model_name: str | None
    provenance: str
    created_at: datetime


class ChangeListOut(BaseModel):
    items: list[RegulatoryChangeOut]
    total: int
    limit: int
    offset: int


# --- Impact ----------------------------------------------------------------
class ImpactAnalyseIn(BaseModel):
    change_ids: list[uuid.UUID] = Field(min_length=1, max_length=50)
    candidate_limit: int = Field(default=8, ge=1, le=15)


class BusinessRuleOut(ORMModel):
    id: uuid.UUID
    rule_code: str
    name: str
    description: str
    banking_module: str
    api_endpoint: str | None
    ui_route: str | None
    affected_fields: list[Any] | None
    is_active: bool


class ImpactMappingOut(ORMModel):
    id: uuid.UUID
    change_id: uuid.UUID
    business_rule_id: uuid.UUID
    impact_category: str
    banking_module: str
    priority: str
    rationale: str
    assertion_basis: str
    affected_fields: list[Any] | None
    affected_workflows: list[Any] | None
    regression_risk: str
    confidence: float | None
    retrieval_score: float | None
    review_status: str
    review_notes: str | None
    reviewed_at: datetime | None
    model_name: str | None
    provenance: str
    created_at: datetime
    business_rule: BusinessRuleOut | None = None
    change: RegulatoryChangeOut | None = None


class ImpactListOut(BaseModel):
    items: list[ImpactMappingOut]
    total: int
    limit: int
    offset: int


class ReviewDecisionIn(BaseModel):
    decision: Literal["APPROVE", "REJECT", "NEEDS_CHANGES"]
    notes: str | None = None


# --- Test cases ------------------------------------------------------------
class TestGenerateIn(BaseModel):
    impact_mapping_ids: list[uuid.UUID] = Field(min_length=1, max_length=25)
    tests_per_category: int = Field(default=1, ge=1, le=3)


class TestStepOut(BaseModel):
    step: int
    action: str
    expected: str | None = None


class TestCaseOut(ORMModel):
    id: uuid.UUID
    test_code: str
    impact_mapping_id: uuid.UUID | None
    change_id: uuid.UUID | None
    business_rule_id: uuid.UUID | None
    title: str
    objective: str | None
    banking_module: str
    category: str
    priority: str
    automation_type: str
    preconditions: list[Any] | None
    test_data: dict[str, Any] | None
    steps: list[Any] | None
    expected_result: str
    execution_spec: dict[str, Any] | None
    is_state_changing: bool
    source_clause_reference: str | None
    source_quote: str | None
    requirement_code: str | None
    approval_status: str
    model_name: str | None
    provenance: str
    created_at: datetime


class TestCaseListOut(BaseModel):
    items: list[TestCaseOut]
    total: int
    limit: int
    offset: int


class TestApprovalOut(ORMModel):
    id: uuid.UUID
    test_case_id: uuid.UUID
    decision: str
    notes: str | None
    decided_by_email: str | None
    decided_at: datetime


# --- Executions ------------------------------------------------------------
class ExecutionStartIn(BaseModel):
    test_case_ids: list[uuid.UUID] = Field(min_length=1, max_length=100)
    driver: Literal["PYTEST_API", "PLAYWRIGHT_LOCAL", "TINYFISH_BROWSER"] = "PYTEST_API"


class EvidenceOut(ORMModel):
    id: uuid.UUID
    evidence_type: str
    label: str
    file_path: str | None
    content_type: str | None
    size_bytes: int | None
    payload: dict[str, Any] | None
    text_payload: str | None
    sha256: str | None
    captured_at: datetime | None


class TestExecutionOut(ORMModel):
    id: uuid.UUID
    run_id: uuid.UUID
    test_case_id: uuid.UUID
    status: str
    driver: str
    expected_result: str | None
    actual_result: str | None
    assertions: list[Any] | None
    failure_reason: str | None
    duration_ms: int | None
    approved_by_email: str | None
    started_at: datetime | None
    finished_at: datetime | None
    test_case: TestCaseOut | None = None
    evidence: list[EvidenceOut] = Field(default_factory=list)


class ExecutionRunOut(ORMModel):
    id: uuid.UUID
    status: str
    driver: str
    target_base_url: str | None
    total: int
    passed: int
    failed: int
    blocked: int
    errored: int
    tinyfish_session_id: str | None
    error_message: str | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime


class ExecutionRunDetail(ExecutionRunOut):
    executions: list[TestExecutionOut] = Field(default_factory=list)


class ExecutionRunListOut(BaseModel):
    items: list[ExecutionRunOut]
    total: int
    limit: int
    offset: int


# --- Traceability ----------------------------------------------------------
class TraceabilityNode(BaseModel):
    """One hop in the clause -> test -> execution chain."""

    id: uuid.UUID | None = None
    label: str
    detail: str | None = None
    status: str | None = None
    extra: dict[str, Any] | None = None


class TraceabilityOut(BaseModel):
    test_case: TestCaseOut
    document: RegulatoryDocumentSummary | None = None
    clause: RegulatoryClauseOut | None = None
    change: RegulatoryChangeOut | None = None
    impact_mapping: ImpactMappingOut | None = None
    business_rule: BusinessRuleOut | None = None
    executions: list[TestExecutionOut] = Field(default_factory=list)
    chain: list[TraceabilityNode] = Field(default_factory=list)


# --- Dashboard -------------------------------------------------------------
class DashboardCounts(BaseModel):
    regulatory_documents: int
    current_documents: int
    documents_inaccessible: int
    regulatory_changes: int
    changes_pending_review: int
    explicit_requirements: int
    affected_cbs_modules: int
    impact_mappings: int
    impacts_pending_review: int
    business_rules: int
    test_cases: int
    tests_pending_approval: int
    tests_approved: int
    executions_total: int
    executions_passed: int
    executions_failed: int
    executions_blocked: int


class TimelinePoint(BaseModel):
    period: str
    documents: int
    changes: int


class DistributionBucket(BaseModel):
    label: str
    count: int


class AgentActivityOut(ORMModel):
    id: uuid.UUID
    agent_name: str
    operation: str
    status: str
    message: str | None
    duration_ms: int | None
    external_service: str | None
    external_request_id: str | None
    correlation_id: uuid.UUID | None
    entity_type: str | None
    entity_id: uuid.UUID | None
    created_at: datetime


class DashboardSummaryOut(BaseModel):
    counts: DashboardCounts
    timeline: list[TimelinePoint]
    risk_distribution: list[DistributionBucket]
    module_distribution: list[DistributionBucket]
    impact_category_distribution: list[DistributionBucket]
    test_category_distribution: list[DistributionBucket]
    assertion_basis_distribution: list[DistributionBucket]
    recent_activity: list[AgentActivityOut]
    latest_execution_run: ExecutionRunOut | None = None
    capabilities_configured: dict[str, bool]


# Resolve the forward reference used by DiscoveryStatusOut.
DiscoveryStatusOut.model_rebuild()
