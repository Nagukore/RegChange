"""Background job launcher for long-running agent work.

Agent pipelines take tens of seconds (network + LLM), far too long to block an
HTTP request. Each job runs as an asyncio task with its own database session,
and reports progress over the SSE bus.

Scope: single-process, in-memory. A production deployment would move this to a
task queue (Celery/RQ/Arq) behind the same `launch`/`get_job` interface; the
routers do not know the difference.
"""
from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.core.events import Event, EventType, bus
from app.core.logging import get_logger, redact

logger = get_logger(__name__)

# Bounded so a long session cannot grow memory without limit.
MAX_TRACKED_JOBS = 200


@dataclass
class Job:
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    kind: str = "job"
    status: str = "RUNNING"  # RUNNING | SUCCEEDED | FAILED
    detail: str | None = None
    result: dict[str, Any] | None = None
    correlation_id: str | None = None
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime | None = None

    def to_dict(self) -> dict:
        return {
            "job_id": str(self.id),
            "kind": self.kind,
            "status": self.status,
            "detail": self.detail,
            "result": self.result,
            "correlation_id": self.correlation_id,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
        }


_jobs: dict[uuid.UUID, Job] = {}
_tasks: set[asyncio.Task] = set()


def get_job(job_id: uuid.UUID) -> Job | None:
    return _jobs.get(job_id)


def list_jobs(limit: int = 50) -> list[Job]:
    return sorted(_jobs.values(), key=lambda j: j.started_at, reverse=True)[:limit]


def _prune() -> None:
    if len(_jobs) <= MAX_TRACKED_JOBS:
        return
    # Drop the oldest finished jobs first; never evict a running one.
    finished = sorted(
        (j for j in _jobs.values() if j.status != "RUNNING"),
        key=lambda j: j.finished_at or j.started_at,
    )
    for job in finished[: len(_jobs) - MAX_TRACKED_JOBS]:
        _jobs.pop(job.id, None)


def launch(
    kind: str,
    work: Callable[[Session], Awaitable[dict[str, Any]]],
    *,
    correlation_id: str | None = None,
) -> Job:
    """Start `work` in the background with its own DB session.

    `work` receives a Session it owns for the job's lifetime and returns a
    JSON-serialisable summary. Exceptions are captured on the Job and published
    as a pipeline_failed event rather than crashing the worker.
    """
    job = Job(kind=kind, correlation_id=correlation_id)
    _jobs[job.id] = job
    _prune()

    async def runner() -> None:
        db = SessionLocal()
        try:
            job.result = await work(db)
            job.status = "SUCCEEDED"
        except Exception as exc:
            job.status = "FAILED"
            job.detail = redact(str(exc))[:2000]
            logger.warning("Job %s (%s) failed: %s", job.id, kind, exc)
            await bus.publish(
                Event(
                    type=EventType.PIPELINE_FAILED,
                    payload={
                        "job_id": str(job.id),
                        "kind": kind,
                        "error": redact(str(exc))[:500],
                        # Surfaced so the UI can tell the user what to configure.
                        "remediation": getattr(exc, "remediation", None),
                    },
                    correlation_id=correlation_id,
                )
            )
        finally:
            job.finished_at = datetime.now(UTC)
            db.close()

    task = asyncio.create_task(runner())
    # Hold a reference so the task is not garbage-collected mid-flight.
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return job
