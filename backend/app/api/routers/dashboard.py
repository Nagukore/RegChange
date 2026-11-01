"""Executive dashboard aggregates and evidence serving."""
from __future__ import annotations

import uuid
from collections import Counter
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.models.enums import AssertionBasis, ExecutionStatus, ReviewStatus
from app.models.mapping import BusinessRule, ImpactMapping
from app.models.ops import AgentActivityLog
from app.models.regulatory import RegulatoryChange, RegulatoryDocument
from app.models.testing import ExecutionEvidence, TestCase, TestExecution, TestExecutionRun
from app.schemas.pipeline import AgentActivityOut, DashboardSummaryOut

router = APIRouter(tags=["dashboard"])


def _count(db: Session, model, *conditions) -> int:  # noqa: ANN001
    stmt = select(func.count()).select_from(model)
    for condition in conditions:
        stmt = stmt.where(condition)
    return db.execute(stmt).scalar() or 0


def _buckets(db: Session, column, *conditions) -> list[dict]:  # noqa: ANN001
    """GROUP BY one column into display buckets, largest first."""
    stmt = select(column, func.count()).group_by(column)
    for condition in conditions:
        stmt = stmt.where(condition)
    rows = db.execute(stmt).all()
    return sorted(
        [{"label": str(label or "UNSPECIFIED"), "count": count} for label, count in rows],
        key=lambda b: b["count"],
        reverse=True,
    )


@router.get("/dashboard/summary", response_model=DashboardSummaryOut)
def dashboard_summary(db: Session = Depends(get_db)):
    """Everything Screen 1 needs, in one round trip."""
    # --- counts ---
    counts = {
        "regulatory_documents": _count(db, RegulatoryDocument),
        "current_documents": _count(db, RegulatoryDocument, RegulatoryDocument.is_current.is_(True)),
        "documents_inaccessible": _count(
            db, RegulatoryDocument, RegulatoryDocument.access_limitation.isnot(None)
        ),
        "regulatory_changes": _count(db, RegulatoryChange),
        "changes_pending_review": _count(
            db, RegulatoryChange, RegulatoryChange.review_status == ReviewStatus.PENDING_REVIEW.value
        ),
        "explicit_requirements": _count(
            db,
            RegulatoryChange,
            RegulatoryChange.assertion_basis == AssertionBasis.EXPLICIT_REQUIREMENT.value,
        ),
        "impact_mappings": _count(db, ImpactMapping),
        "impacts_pending_review": _count(
            db, ImpactMapping, ImpactMapping.review_status == ReviewStatus.PENDING_REVIEW.value
        ),
        "business_rules": _count(db, BusinessRule),
        "test_cases": _count(db, TestCase),
        "tests_pending_approval": _count(
            db, TestCase, TestCase.approval_status == ReviewStatus.PENDING_REVIEW.value
        ),
        "tests_approved": _count(
            db, TestCase, TestCase.approval_status == ReviewStatus.APPROVED.value
        ),
        "executions_total": _count(db, TestExecution),
        "executions_passed": _count(
            db, TestExecution, TestExecution.status == ExecutionStatus.PASSED.value
        ),
        "executions_failed": _count(
            db, TestExecution, TestExecution.status == ExecutionStatus.FAILED.value
        ),
        "executions_blocked": _count(
            db, TestExecution, TestExecution.status == ExecutionStatus.BLOCKED.value
        ),
    }
    # Distinct CBS modules actually touched by a mapping.
    counts["affected_cbs_modules"] = len(
        {
            row[0]
            for row in db.execute(select(ImpactMapping.banking_module).distinct()).all()
            if row[0]
        }
    )

    # --- regulatory timeline, by publication month ---
    timeline_counter: Counter[str] = Counter()
    change_counter: Counter[str] = Counter()
    for (published,) in db.execute(
        select(RegulatoryDocument.publication_date).where(
            RegulatoryDocument.publication_date.isnot(None)
        )
    ).all():
        timeline_counter[published.strftime("%Y-%m")] += 1
    for (effective,) in db.execute(
        select(RegulatoryChange.effective_date).where(RegulatoryChange.effective_date.isnot(None))
    ).all():
        change_counter[effective.strftime("%Y-%m")] += 1

    periods = sorted(set(timeline_counter) | set(change_counter))
    timeline = [
        {
            "period": period,
            "documents": timeline_counter.get(period, 0),
            "changes": change_counter.get(period, 0),
        }
        for period in periods
    ][-18:]  # last 18 months keeps the chart readable

    latest_run = (
        db.execute(select(TestExecutionRun).order_by(TestExecutionRun.created_at.desc()).limit(1))
        .scalars()
        .first()
    )
    recent_activity = list(
        db.execute(
            select(AgentActivityLog).order_by(AgentActivityLog.created_at.desc()).limit(20)
        )
        .scalars()
        .all()
    )

    return {
        "counts": counts,
        "timeline": timeline,
        # Risk here is the priority assigned to an impact, which is what a
        # compliance lead triages by.
        "risk_distribution": _buckets(db, ImpactMapping.priority),
        "module_distribution": _buckets(db, ImpactMapping.banking_module),
        "impact_category_distribution": _buckets(db, ImpactMapping.impact_category),
        "test_category_distribution": _buckets(db, TestCase.category),
        "assertion_basis_distribution": _buckets(db, RegulatoryChange.assertion_basis),
        "recent_activity": [AgentActivityOut.model_validate(a) for a in recent_activity],
        "latest_execution_run": latest_run,
        "capabilities_configured": {
            "tinyfish": settings.tinyfish_configured,
            "gemini": settings.gemini_configured,
            "supabase_postgres": settings.is_postgres,
            "tinyfish_browser_cbs": settings.tinybrowser_cbs_configured,
        },
    }


@router.get("/activity", response_model=list[AgentActivityOut])
def agent_activity(
    agent_name: str | None = None,
    activity_status: str | None = Query(default=None, alias="status"),
    correlation_id: uuid.UUID | None = None,
    limit: int = Query(default=50, ge=1, le=300),
    db: Session = Depends(get_db),
):
    """Agent activity log. Backs the live Execution Monitor feed."""
    stmt = select(AgentActivityLog)
    for column, value in (
        (AgentActivityLog.agent_name, agent_name),
        (AgentActivityLog.status, activity_status),
        (AgentActivityLog.correlation_id, correlation_id),
    ):
        if value is not None:
            stmt = stmt.where(column == value)
    return list(
        db.execute(stmt.order_by(AgentActivityLog.created_at.desc()).limit(limit))
        .scalars()
        .all()
    )


@router.get("/evidence/{evidence_id}/file")
def evidence_file(evidence_id: uuid.UUID, db: Session = Depends(get_db)):
    """Serve a stored screenshot or HTML snapshot.

    The stored path is relative to the evidence root and is re-resolved and
    bounds-checked here, so a tampered database row cannot be used to read an
    arbitrary file from disk.
    """
    evidence = db.get(ExecutionEvidence, evidence_id)
    if evidence is None:
        raise HTTPException(status_code=404, detail="Evidence not found")
    if not evidence.file_path:
        raise HTTPException(
            status_code=404,
            detail="This evidence item is stored inline; read it from the execution record.",
        )

    root = Path(settings.evidence_dir).resolve()
    candidate = (root / evidence.file_path).resolve()
    if not candidate.is_relative_to(root):
        raise HTTPException(status_code=400, detail="Invalid evidence path")
    if not candidate.is_file():
        raise HTTPException(status_code=404, detail="Evidence file is no longer on disk")

    return FileResponse(
        candidate,
        media_type=evidence.content_type or "application/octet-stream",
        filename=candidate.name,
    )


# ---------------------------------------------------------------------------
# Demo fixture
# ---------------------------------------------------------------------------
@router.post("/demo/seed")
def seed_demo(reset: bool = Query(default=False), db: Session = Depends(get_db)):
    """Load the clearly-labelled demo dataset.

    Lets the full pipeline be demonstrated without API credentials. Everything
    it creates is stamped DEMO_FIXTURE and rendered as such in the UI; the
    document body text is synthetic and never presented as a regulator's words.
    """
    from app.seeds.demo_fixture import seed_demo_fixture

    return seed_demo_fixture(db, reset=reset)


@router.delete("/demo/seed")
def clear_demo(db: Session = Depends(get_db)):
    """Remove every demo-fixture record."""
    from app.seeds.demo_fixture import clear_demo_fixture

    return {"documents_removed": clear_demo_fixture(db)}
