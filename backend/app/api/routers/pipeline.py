"""The regulatory pipeline API.

Routes map one-to-one onto the agents:
  discovery   -> Agent 1 (TinyFish Search)
  regulations -> Agent 2 (TinyFish Fetch) + version history and diffing
  analysis    -> Agent 3 (Gemini)
  impact      -> Agent 4 (Gemini + deterministic validation)
  test-cases  -> Agent 5 (Gemini) + human approval
  executions  -> Agent 6 (API / Playwright / TinyFish Browser)

Long-running agent work is launched as a background job and followed over SSE
(`GET /api/events/stream`); the job id is returned immediately.
"""
from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.agents.analysis_agent import AnalysisAgent, find_previous_version
from app.agents.base import AgentContext, AgentUnavailable
from app.agents.discovery_agent import DiscoveryAgent
from app.agents.execution_agent import ExecutionAgent, record_test_approval
from app.agents.extraction_agent import ExtractionAgent
from app.agents.impact_agent import ImpactAgent, approve_mapping
from app.agents.testgen_agent import TestGenerationAgent
from app.core.database import get_db
from app.core.jobs import get_job, launch, list_jobs
from app.core.security import RequireRole, actor_label
from app.models.enums import ReviewStatus, UserRole
from app.models.mapping import BusinessRule, ImpactMapping
from app.models.ops import DiscoveryRun, User
from app.models.regulatory import RegulatoryChange, RegulatoryDocument
from app.models.testing import TestApproval, TestCase, TestExecution, TestExecutionRun
from app.schemas.pipeline import (
    AnalyseIn,
    BusinessRuleOut,
    ChangeListOut,
    DiscoveryRunOut,
    DiscoveryStartIn,
    DiscoveryStatusOut,
    DocumentComparisonOut,
    DocumentListOut,
    DocumentVersionOut,
    ExecutionRunDetail,
    ExecutionRunListOut,
    ExecutionStartIn,
    ExtractIn,
    ImpactAnalyseIn,
    ImpactListOut,
    ImpactMappingOut,
    JobOut,
    RegulatoryChangeOut,
    RegulatoryDocumentDetail,
    ReviewDecisionIn,
    TestApprovalOut,
    TestCaseListOut,
    TestCaseOut,
    TestGenerateIn,
    TraceabilityOut,
)
from app.services.diffing import compare_document_versions
from app.services.traceability import build_traceability

router = APIRouter()

ReviewerDep = Annotated[User | None, Depends(RequireRole(UserRole.REVIEWER))]
AnalystDep = Annotated[User | None, Depends(RequireRole(UserRole.ANALYST))]


def _unavailable(exc: AgentUnavailable) -> HTTPException:
    """An agent that cannot run is a 503 with actionable remediation."""
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={
            "error": "agent_unavailable",
            "message": str(exc),
            "remediation": exc.remediation,
        },
    )


# ===========================================================================
# Discovery -- Agent 1
# ===========================================================================
@router.post("/discovery/start", response_model=JobOut, tags=["discovery"])
async def start_discovery(
    payload: DiscoveryStartIn,
    user: AnalystDep = None,
    db: Session = Depends(get_db),
):
    """Launch a TinyFish Search discovery run in the background."""
    # None selects the agent's default run: the curated queries plus the feeds.
    queries = (
        [{"query": q, "banking_domains": [], "purpose": None} for q in payload.queries]
        if payload.queries
        else None
    )

    ctx = AgentContext(actor=actor_label(user))
    agent = DiscoveryAgent()
    if not agent.client.configured:
        raise _unavailable(
            AgentUnavailable(
                "TinyFish Search is not configured, so regulatory discovery cannot run.",
                remediation=(
                    "Set TINYFISH_API_KEY in backend/.env and restart the API. "
                    "Search is free up to 12,000 requests/day: "
                    "https://agent.tinyfish.ai/api-keys"
                ),
            )
        )

    async def work(session: Session) -> dict:
        run = await agent.run(
            session,
            ctx,
            queries=queries,
            institution_type=payload.institution_type,
            recency_minutes=payload.recency_minutes,
            max_results_per_query=payload.max_results_per_query,
        )
        # A custom query is a targeted lookup, not a sweep for what has changed.
        rechecked = (
            []
            if queries
            else await ExtractionAgent(client=agent.client).recheck_current(session, ctx)
        )
        return {
            "discovery_run_id": str(run.id),
            "status": run.status,
            "documents_found": run.documents_found,
            "documents_new": run.documents_new,
            "documents_duplicate": run.documents_duplicate,
            "documents_rechecked": len(rechecked),
            "documents_revised": sum(1 for r in rechecked if r.created_version),
            "recheck_failures": sum(1 for r in rechecked if r.error),
        }

    job = launch("discovery", work, correlation_id=str(ctx.correlation_id))
    return job.to_dict()


@router.get("/discovery/{run_id}/status", response_model=DiscoveryStatusOut, tags=["discovery"])
def discovery_status(run_id: uuid.UUID, db: Session = Depends(get_db)):
    run = db.get(DiscoveryRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Discovery run not found")
    documents = (
        db.execute(
            select(RegulatoryDocument)
            .where(RegulatoryDocument.discovery_run_id == run.id)
            .order_by(RegulatoryDocument.created_at.desc())
        )
        .scalars()
        .all()
    )
    return {"run": run, "documents": list(documents)}


@router.get("/discovery", response_model=list[DiscoveryRunOut], tags=["discovery"])
def list_discovery_runs(
    limit: int = Query(default=20, ge=1, le=100), db: Session = Depends(get_db)
):
    return list(
        db.execute(
            select(DiscoveryRun).order_by(DiscoveryRun.created_at.desc()).limit(limit)
        )
        .scalars()
        .all()
    )


@router.get("/jobs/{job_id}", response_model=JobOut, tags=["discovery"])
def job_status(job_id: uuid.UUID):
    """Status of any background pipeline job."""
    job = get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found or no longer tracked")
    return job.to_dict()


@router.get("/jobs", response_model=list[JobOut], tags=["discovery"])
def all_jobs(limit: int = Query(default=25, ge=1, le=100)):
    return [job.to_dict() for job in list_jobs(limit)]


# ===========================================================================
# Regulations -- Agent 2, versions and diffing
# ===========================================================================
@router.get("/regulations", response_model=DocumentListOut, tags=["regulations"])
def list_regulations(
    q: str | None = Query(default=None, description="Match title or circular number"),
    doc_status: str | None = Query(default=None, alias="status"),
    document_type: str | None = None,
    banking_domain: str | None = Query(
        default=None, description="e.g. KYC_AML; matched within the stored list"
    ),
    current_only: bool = True,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    stmt = select(RegulatoryDocument)
    count_stmt = select(func.count()).select_from(RegulatoryDocument)

    conditions = []
    if current_only:
        conditions.append(RegulatoryDocument.is_current.is_(True))
    if q:
        pattern = f"%{q.strip()}%"
        conditions.append(
            RegulatoryDocument.title.ilike(pattern)
            | RegulatoryDocument.circular_number.ilike(pattern)
        )
    if doc_status:
        conditions.append(RegulatoryDocument.status == doc_status)
    if document_type:
        conditions.append(RegulatoryDocument.document_type == document_type)
    for condition in conditions:
        stmt = stmt.where(condition)
        count_stmt = count_stmt.where(condition)

    stmt = stmt.order_by(
        RegulatoryDocument.publication_date.desc().nullslast(),
        RegulatoryDocument.created_at.desc(),
    )
    # JSON array containment is dialect-specific, so this one filter is applied
    # in Python to keep the same behaviour on SQLite and Postgres. It must run
    # before paging, or the total and the pages would describe unfiltered rows.
    if banking_domain:
        matching = [
            r
            for r in db.execute(stmt).scalars().all()
            if banking_domain in (r.banking_domains or [])
        ]
        return {
            "items": matching[offset : offset + limit],
            "total": len(matching),
            "limit": limit,
            "offset": offset,
        }

    total = db.execute(count_stmt).scalar() or 0
    rows = list(db.execute(stmt.limit(limit).offset(offset)).scalars().all())
    return {"items": rows, "total": total, "limit": limit, "offset": offset}


@router.get(
    "/regulations/{document_id}", response_model=RegulatoryDocumentDetail, tags=["regulations"]
)
def get_regulation(document_id: uuid.UUID, db: Session = Depends(get_db)):
    document = db.execute(
        select(RegulatoryDocument)
        .where(RegulatoryDocument.id == document_id)
        .options(selectinload(RegulatoryDocument.clauses))
    ).scalar_one_or_none()
    if document is None:
        raise HTTPException(status_code=404, detail="Regulatory document not found")
    # Stable clause ordering for the UI.
    document.clauses.sort(key=lambda c: c.ordinal)
    return document


@router.get(
    "/regulations/{document_id}/versions",
    response_model=list[DocumentVersionOut],
    tags=["regulations"],
)
def document_versions(document_id: uuid.UUID, db: Session = Depends(get_db)):
    """Every stored version of this logical document, newest first."""
    document = db.get(RegulatoryDocument, document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="Regulatory document not found")
    return list(
        db.execute(
            select(RegulatoryDocument)
            .where(RegulatoryDocument.source_url == document.source_url)
            .order_by(RegulatoryDocument.version.desc())
        )
        .scalars()
        .all()
    )


@router.get(
    "/regulations/{document_id}/compare",
    response_model=DocumentComparisonOut,
    tags=["regulations"],
)
def compare_versions(
    document_id: uuid.UUID,
    against: uuid.UUID | None = Query(
        default=None, description="Defaults to the immediately previous version"
    ),
    db: Session = Depends(get_db),
):
    """Clause-level old-versus-new comparison."""
    current = db.execute(
        select(RegulatoryDocument)
        .where(RegulatoryDocument.id == document_id)
        .options(selectinload(RegulatoryDocument.clauses))
    ).scalar_one_or_none()
    if current is None:
        raise HTTPException(status_code=404, detail="Regulatory document not found")

    if against is not None:
        previous = db.execute(
            select(RegulatoryDocument)
            .where(RegulatoryDocument.id == against)
            .options(selectinload(RegulatoryDocument.clauses))
        ).scalar_one_or_none()
    else:
        previous = find_previous_version(db, current)

    if previous is None:
        raise HTTPException(
            status_code=404,
            detail=(
                "No earlier version of this document is stored, so there is nothing "
                "to compare against."
            ),
        )
    return compare_document_versions(previous, current)


@router.post("/regulations/extract", response_model=JobOut, tags=["regulations"])
async def extract_documents(
    payload: ExtractIn,
    user: AnalystDep = None,
    db: Session = Depends(get_db),
):
    """Run Agent 2 (TinyFish Fetch) over the selected documents."""
    agent = ExtractionAgent()
    if not agent.client.configured:
        raise _unavailable(
            AgentUnavailable(
                "TinyFish Fetch is not configured, so extraction cannot run.",
                remediation=(
                    "Set TINYFISH_API_KEY in backend/.env and restart the API. "
                    "Fetch is free up to 1,000 URLs/day."
                ),
            )
        )
    ctx = AgentContext(actor=actor_label(user))

    async def work(session: Session) -> dict:
        results = await agent.extract_batch(
            session, ctx, payload.document_ids, force_refetch=payload.force_refetch
        )
        return {
            "extracted": [r.to_dict() for r in results],
            "succeeded": sum(1 for r in results if r.error is None),
            "failed": sum(1 for r in results if r.error is not None),
        }

    job = launch("extraction", work, correlation_id=str(ctx.correlation_id))
    return job.to_dict()


# ===========================================================================
# Analysis -- Agent 3
# ===========================================================================
@router.post("/analysis/{document_id}", response_model=JobOut, tags=["analysis"])
async def analyse_document(
    document_id: uuid.UUID,
    payload: AnalyseIn | None = None,
    user: AnalystDep = None,
    db: Session = Depends(get_db),
):
    """Run Agent 3 (Gemini) over an extracted document."""
    options = payload or AnalyseIn()
    document = db.get(RegulatoryDocument, document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="Regulatory document not found")

    agent = AnalysisAgent()
    if not agent.client.configured:
        raise _unavailable(
            AgentUnavailable(
                "Gemini is not configured, so regulatory analysis cannot run.",
                remediation=(
                    "Set GEMINI_API_KEY in backend/.env and restart the API. "
                    "Create a key at https://aistudio.google.com/apikey"
                ),
            )
        )
    if not document.raw_text:
        raise _unavailable(
            AgentUnavailable(
                f"Document '{document.title}' has no extracted text to analyse.",
                remediation="Run POST /api/regulations/extract for this document first.",
            )
        )

    ctx = AgentContext(actor=actor_label(user))

    async def work(session: Session) -> dict:
        outcome = await agent.analyse_document(
            session,
            ctx,
            document_id,
            compare_to_document_id=options.compare_to_document_id,
            use_pro_model=options.use_pro_model,
        )
        return outcome.to_dict()

    job = launch("analysis", work, correlation_id=str(ctx.correlation_id))
    return job.to_dict()


@router.get("/changes", response_model=ChangeListOut, tags=["analysis"])
def list_changes(
    document_id: uuid.UUID | None = None,
    banking_module: str | None = None,
    assertion_basis: str | None = None,
    change_type: str | None = None,
    priority: str | None = None,
    review_status: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    stmt = select(RegulatoryChange)
    count_stmt = select(func.count()).select_from(RegulatoryChange)
    filters = [
        (RegulatoryChange.document_id, document_id),
        (RegulatoryChange.primary_banking_module, banking_module),
        (RegulatoryChange.assertion_basis, assertion_basis),
        (RegulatoryChange.change_type, change_type),
        (RegulatoryChange.priority, priority),
        (RegulatoryChange.review_status, review_status),
    ]
    for column, value in filters:
        if value is not None:
            stmt = stmt.where(column == value)
            count_stmt = count_stmt.where(column == value)

    total = db.execute(count_stmt).scalar() or 0
    rows = list(
        db.execute(
            stmt.order_by(RegulatoryChange.created_at.desc()).limit(limit).offset(offset)
        )
        .scalars()
        .all()
    )
    return {"items": rows, "total": total, "limit": limit, "offset": offset}


@router.get("/changes/{change_id}", response_model=RegulatoryChangeOut, tags=["analysis"])
def get_change(change_id: uuid.UUID, db: Session = Depends(get_db)):
    change = db.get(RegulatoryChange, change_id)
    if change is None:
        raise HTTPException(status_code=404, detail="Regulatory change not found")
    return change


@router.patch(
    "/changes/{change_id}/review", response_model=RegulatoryChangeOut, tags=["analysis"]
)
def review_change(
    change_id: uuid.UUID,
    payload: ReviewDecisionIn,
    user: ReviewerDep = None,
    db: Session = Depends(get_db),
):
    """Record a human decision on an extracted requirement."""
    from datetime import UTC, datetime

    change = db.get(RegulatoryChange, change_id)
    if change is None:
        raise HTTPException(status_code=404, detail="Regulatory change not found")

    mapped = {
        "APPROVE": ReviewStatus.APPROVED.value,
        "REJECT": ReviewStatus.REJECTED.value,
        "NEEDS_CHANGES": ReviewStatus.NEEDS_CHANGES.value,
    }
    change.review_status = mapped[payload.decision]
    change.reviewed_by_id = user.id if user else None
    change.reviewed_at = datetime.now(UTC)
    change.review_notes = payload.notes
    db.commit()
    db.refresh(change)
    return change


# ===========================================================================
# Impact -- Agent 4
# ===========================================================================
@router.post("/impact/analyze", response_model=JobOut, tags=["impact"])
async def analyze_impact(
    payload: ImpactAnalyseIn,
    user: AnalystDep = None,
    db: Session = Depends(get_db),
):
    """Run Agent 4 over the selected requirements."""
    agent = ImpactAgent()
    if not agent.client.configured:
        raise _unavailable(
            AgentUnavailable(
                "Gemini is not configured, so impact analysis cannot run.",
                remediation="Set GEMINI_API_KEY in backend/.env and restart the API.",
            )
        )
    ctx = AgentContext(actor=actor_label(user))

    async def work(session: Session) -> dict:
        outcomes = await agent.analyse_batch(session, ctx, payload.change_ids)
        return {
            "analysed": [o.to_dict() for o in outcomes],
            "mappings_created": sum(len(o.mappings) for o in outcomes),
            "invented_codes_rejected": sum(len(o.rejected_codes) for o in outcomes),
        }

    job = launch("impact", work, correlation_id=str(ctx.correlation_id))
    return job.to_dict()


@router.get("/impact", response_model=ImpactListOut, tags=["impact"])
def list_impact(
    change_id: uuid.UUID | None = None,
    business_rule_id: uuid.UUID | None = None,
    rule_code: str | None = None,
    impact_category: str | None = None,
    banking_module: str | None = None,
    priority: str | None = None,
    review_status: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    stmt = select(ImpactMapping).options(
        selectinload(ImpactMapping.business_rule), selectinload(ImpactMapping.change)
    )
    count_stmt = select(func.count()).select_from(ImpactMapping)

    if rule_code:
        rule = db.execute(
            select(BusinessRule).where(BusinessRule.rule_code == rule_code)
        ).scalar_one_or_none()
        if rule is None:
            return {"items": [], "total": 0, "limit": limit, "offset": offset}
        business_rule_id = rule.id

    filters = [
        (ImpactMapping.change_id, change_id),
        (ImpactMapping.business_rule_id, business_rule_id),
        (ImpactMapping.impact_category, impact_category),
        (ImpactMapping.banking_module, banking_module),
        (ImpactMapping.priority, priority),
        (ImpactMapping.review_status, review_status),
    ]
    for column, value in filters:
        if value is not None:
            stmt = stmt.where(column == value)
            count_stmt = count_stmt.where(column == value)

    total = db.execute(count_stmt).scalar() or 0
    rows = list(
        db.execute(stmt.order_by(ImpactMapping.created_at.desc()).limit(limit).offset(offset))
        .scalars()
        .all()
    )
    return {"items": rows, "total": total, "limit": limit, "offset": offset}


@router.patch("/impact/{mapping_id}/review", response_model=ImpactMappingOut, tags=["impact"])
def review_impact(
    mapping_id: uuid.UUID,
    payload: ReviewDecisionIn,
    user: ReviewerDep = None,
    db: Session = Depends(get_db),
):
    """Human approval of an impact mapping. Required before test generation."""
    try:
        return approve_mapping(
            db,
            mapping_id,
            decision=payload.decision,
            reviewer_id=user.id if user else None,
            notes=payload.notes,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/business-rules", response_model=list[BusinessRuleOut], tags=["impact"])
def list_business_rules(db: Session = Depends(get_db)):
    """The CBS rule catalogue. The impact engine may reference only these."""
    return list(
        db.execute(select(BusinessRule).order_by(BusinessRule.rule_code)).scalars().all()
    )


# ===========================================================================
# Test cases -- Agent 5 and human approval
# ===========================================================================
@router.post("/test-cases/generate", response_model=JobOut, tags=["test cases"])
async def generate_test_cases(
    payload: TestGenerateIn,
    user: AnalystDep = None,
    db: Session = Depends(get_db),
):
    """Run Agent 5 over APPROVED impact mappings."""
    agent = TestGenerationAgent()
    if not agent.client.configured:
        raise _unavailable(
            AgentUnavailable(
                "Gemini is not configured, so test generation cannot run.",
                remediation="Set GEMINI_API_KEY in backend/.env and restart the API.",
            )
        )
    ctx = AgentContext(actor=actor_label(user))

    async def work(session: Session) -> dict:
        outcomes = await agent.generate_batch(
            session,
            ctx,
            payload.impact_mapping_ids,
            tests_per_category=payload.tests_per_category,
        )
        return {
            "generated": [o.to_dict() for o in outcomes],
            "test_cases_created": sum(len(o.test_cases) for o in outcomes),
            "discarded": sum(len(o.discarded) for o in outcomes),
        }

    job = launch("test_generation", work, correlation_id=str(ctx.correlation_id))
    return job.to_dict()


@router.get("/test-cases", response_model=TestCaseListOut, tags=["test cases"])
def list_test_cases(
    impact_mapping_id: uuid.UUID | None = None,
    change_id: uuid.UUID | None = None,
    requirement_code: str | None = None,
    banking_module: str | None = None,
    category: str | None = None,
    approval_status: str | None = None,
    priority: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    stmt = select(TestCase)
    count_stmt = select(func.count()).select_from(TestCase)
    filters = [
        (TestCase.impact_mapping_id, impact_mapping_id),
        (TestCase.change_id, change_id),
        (TestCase.requirement_code, requirement_code),
        (TestCase.banking_module, banking_module),
        (TestCase.category, category),
        (TestCase.approval_status, approval_status),
        (TestCase.priority, priority),
    ]
    for column, value in filters:
        if value is not None:
            stmt = stmt.where(column == value)
            count_stmt = count_stmt.where(column == value)

    total = db.execute(count_stmt).scalar() or 0
    rows = list(
        db.execute(stmt.order_by(TestCase.created_at.desc()).limit(limit).offset(offset))
        .scalars()
        .all()
    )
    return {"items": rows, "total": total, "limit": limit, "offset": offset}


@router.get("/test-cases/{test_case_id}", response_model=TestCaseOut, tags=["test cases"])
def get_test_case(test_case_id: uuid.UUID, db: Session = Depends(get_db)):
    case = db.get(TestCase, test_case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="Test case not found")
    return case


@router.patch(
    "/test-cases/{test_case_id}/approve", response_model=TestApprovalOut, tags=["test cases"]
)
def approve_test_case(
    test_case_id: uuid.UUID,
    payload: ReviewDecisionIn,
    user: ReviewerDep = None,
    db: Session = Depends(get_db),
):
    """Record a human decision. An APPROVED record authorises execution."""
    try:
        return record_test_approval(
            db,
            test_case_id,
            decision=payload.decision,
            reviewer_id=user.id if user else None,
            reviewer_email=actor_label(user),
            notes=payload.notes,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get(
    "/test-cases/{test_case_id}/approvals",
    response_model=list[TestApprovalOut],
    tags=["test cases"],
)
def test_case_approvals(test_case_id: uuid.UUID, db: Session = Depends(get_db)):
    return list(
        db.execute(
            select(TestApproval)
            .where(TestApproval.test_case_id == test_case_id)
            .order_by(TestApproval.decided_at.desc())
        )
        .scalars()
        .all()
    )


@router.get(
    "/test-cases/{test_case_id}/traceability",
    response_model=TraceabilityOut,
    tags=["test cases"],
)
def test_case_traceability(test_case_id: uuid.UUID, db: Session = Depends(get_db)):
    """Full chain: clause -> change -> impact -> rule -> test -> executions."""
    try:
        return build_traceability(db, test_case_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


# ===========================================================================
# Executions -- Agent 6
# ===========================================================================
@router.post("/executions/start", response_model=JobOut, tags=["executions"])
async def start_execution(
    payload: ExecutionStartIn,
    user: ReviewerDep = None,
    db: Session = Depends(get_db),
):
    """Execute approved test cases against the simulated CBS."""
    agent = ExecutionAgent()
    try:
        agent._resolve_target(payload.driver)  # fail fast on driver prerequisites
    except AgentUnavailable as exc:
        raise _unavailable(exc) from exc

    ctx = AgentContext(actor=actor_label(user))
    triggered_by = user.id if user else None

    async def work(session: Session) -> dict:
        run = await agent.run_batch(
            session,
            ctx,
            payload.test_case_ids,
            driver=payload.driver,
            triggered_by_id=triggered_by,
        )
        return {
            "run_id": str(run.id),
            "status": run.status,
            "total": run.total,
            "passed": run.passed,
            "failed": run.failed,
            "blocked": run.blocked,
            "errored": run.errored,
        }

    job = launch("execution", work, correlation_id=str(ctx.correlation_id))
    return job.to_dict()


@router.get("/executions", response_model=ExecutionRunListOut, tags=["executions"])
def list_execution_runs(
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    total = db.execute(select(func.count()).select_from(TestExecutionRun)).scalar() or 0
    rows = list(
        db.execute(
            select(TestExecutionRun)
            .order_by(TestExecutionRun.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        .scalars()
        .all()
    )
    return {"items": rows, "total": total, "limit": limit, "offset": offset}


@router.get("/executions/{run_id}", response_model=ExecutionRunDetail, tags=["executions"])
def get_execution_run(run_id: uuid.UUID, db: Session = Depends(get_db)):
    run = db.execute(
        select(TestExecutionRun)
        .where(TestExecutionRun.id == run_id)
        .options(
            selectinload(TestExecutionRun.executions).selectinload(TestExecution.evidence),
            selectinload(TestExecutionRun.executions).selectinload(TestExecution.test_case),
        )
    ).scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=404, detail="Execution run not found")
    return run
