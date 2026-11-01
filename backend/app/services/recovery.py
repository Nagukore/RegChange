"""Close out work a previous process left unfinished.

Background jobs live in memory (see app/core/jobs.py), so a restart ends them
without any chance to update the database. At startup nothing can legitimately
be in progress, so anything still marked that way was interrupted, and would
otherwise read as running forever. This relies on the single-process job model:
moving to a task queue means retiring it.
"""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models.enums import DiscoveryStatus, DocumentStatus, ExecutionStatus
from app.models.ops import DiscoveryRun
from app.models.regulatory import RegulatoryChange, RegulatoryDocument
from app.models.testing import TestExecution, TestExecutionRun

logger = get_logger(__name__)

INTERRUPTED = "Interrupted by an API restart before completing."


def reconcile_interrupted_work(db: Session) -> dict[str, int]:
    """Mark interrupted work honestly. Returns how many rows of each were closed."""
    now = datetime.now(UTC)

    runs = db.execute(
        select(DiscoveryRun).where(DiscoveryRun.status == DiscoveryStatus.RUNNING.value)
    ).scalars().all()
    for run in runs:
        run.status = DiscoveryStatus.FAILED.value
        run.error_message = INTERRUPTED
        run.finished_at = now

    # The status before the fetch was not recorded, so it is recovered from what
    # the row holds. Stored analysis output means it had been analysed.
    documents = db.execute(
        select(
            RegulatoryDocument,
            exists().where(RegulatoryChange.document_id == RegulatoryDocument.id),
        ).where(RegulatoryDocument.status == DocumentStatus.FETCHING.value)
    ).all()
    for document, has_analysis in documents:
        if has_analysis:
            document.status = DocumentStatus.ANALYSED.value
        elif document.raw_text:
            document.status = DocumentStatus.EXTRACTED.value
        else:
            document.status = DocumentStatus.DISCOVERED.value

    execution_runs = db.execute(
        select(TestExecutionRun).where(TestExecutionRun.status == ExecutionStatus.RUNNING.value)
    ).scalars().all()
    for execution_run in execution_runs:
        execution_run.status = ExecutionStatus.ERROR.value
        execution_run.error_message = INTERRUPTED
        execution_run.finished_at = now

    executions = db.execute(
        select(TestExecution).where(TestExecution.status == ExecutionStatus.RUNNING.value)
    ).scalars().all()
    for execution in executions:
        execution.status = ExecutionStatus.ERROR.value
        execution.failure_reason = INTERRUPTED
        execution.finished_at = now

    db.commit()
    closed = {
        "discovery_runs": len(runs),
        "documents": len(documents),
        "execution_runs": len(execution_runs),
        "executions": len(executions),
    }
    if any(closed.values()):
        logger.warning("Closed work interrupted by a restart: %s", closed)
    return closed
