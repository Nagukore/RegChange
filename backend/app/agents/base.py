"""Shared agent scaffolding.

Every agent operation is traceable (brief requirement 12.8): the context manager
below writes a START row to `agent_activity_logs`, publishes an SSE event, and
on exit records success or failure with a duration. Secrets are redacted before
anything is persisted or emitted.
"""
from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from app.core.events import Event, EventType, bus
from app.core.logging import get_logger, redact
from app.models.enums import ActivityStatus, AgentName
from app.models.ops import AgentActivityLog

logger = get_logger(__name__)


@dataclass
class AgentContext:
    """Correlates every record produced by one pipeline run."""

    correlation_id: uuid.UUID = field(default_factory=uuid.uuid4)
    actor: str = "system"
    # Set when the agent runs off the event loop so SSE events can still be
    # published from a worker thread.
    loop: asyncio.AbstractEventLoop | None = None

    def emit(self, event_type: EventType, payload: dict[str, Any]) -> None:
        event = Event(
            type=event_type,
            payload=payload,
            correlation_id=str(self.correlation_id),
        )
        if self.loop is not None and not self.loop.is_closed():
            bus.publish_threadsafe(event, self.loop)
            return
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            # No loop at all (e.g. a CLI or test run): events are a UI nicety,
            # so dropping them must never fail the pipeline.
            logger.debug("No event loop available; dropping %s event", event_type)
            return
        running.create_task(bus.publish(event))


@contextmanager
def agent_activity(
    db: Session,
    *,
    agent: AgentName,
    operation: str,
    ctx: AgentContext,
    entity_type: str | None = None,
    entity_id: uuid.UUID | None = None,
    external_service: str | None = None,
    details: dict[str, Any] | None = None,
) -> Iterator[dict[str, Any]]:
    """Record one agent operation.

    Yields a mutable dict the caller fills in; its contents are merged into the
    persisted `details` on exit. Any exception is logged as FAILED and
    re-raised, so a failure is never silently swallowed.
    """
    started = time.monotonic()
    log = AgentActivityLog(
        agent_name=str(agent),
        operation=operation,
        status=ActivityStatus.STARTED.value,
        details=redact(details or {}),
        external_service=external_service,
        correlation_id=ctx.correlation_id,
        entity_type=entity_type,
        entity_id=entity_id,
    )
    db.add(log)
    db.commit()

    outcome: dict[str, Any] = {}
    try:
        yield outcome
    except Exception as exc:
        log.status = ActivityStatus.FAILED.value
        log.message = redact(str(exc))[:2000]
        log.duration_ms = int((time.monotonic() - started) * 1000)
        log.details = redact({**(details or {}), **outcome})
        db.commit()
        ctx.emit(
            EventType.AGENT_ACTIVITY,
            {
                "agent": str(agent),
                "operation": operation,
                "status": ActivityStatus.FAILED.value,
                "message": redact(str(exc))[:500],
            },
        )
        logger.error("Agent %s/%s failed: %s", agent, operation, exc)
        raise
    else:
        log.status = ActivityStatus.SUCCEEDED.value
        log.duration_ms = int((time.monotonic() - started) * 1000)
        log.message = str(outcome.get("message", ""))[:2000] or None
        log.external_request_id = outcome.get("request_id")
        log.details = redact({**(details or {}), **outcome})
        db.commit()
        ctx.emit(
            EventType.AGENT_ACTIVITY,
            {
                "agent": str(agent),
                "operation": operation,
                "status": ActivityStatus.SUCCEEDED.value,
                "duration_ms": log.duration_ms,
                "message": log.message,
            },
        )


def record_skipped(
    db: Session,
    *,
    agent: AgentName,
    operation: str,
    ctx: AgentContext,
    reason: str,
) -> None:
    """Log an operation that could not run, e.g. a missing credential.

    Used instead of fabricating a result so the UI can state what was skipped
    and why.
    """
    db.add(
        AgentActivityLog(
            agent_name=str(agent),
            operation=operation,
            status=ActivityStatus.SKIPPED.value,
            message=reason[:2000],
            correlation_id=ctx.correlation_id,
        )
    )
    db.commit()
    ctx.emit(
        EventType.AGENT_ACTIVITY,
        {
            "agent": str(agent),
            "operation": operation,
            "status": ActivityStatus.SKIPPED.value,
            "message": reason[:500],
        },
    )
    logger.warning("Agent %s/%s skipped: %s", agent, operation, reason)


class AgentUnavailable(RuntimeError):
    """An agent cannot run because a required credential or input is missing.

    Distinct from a failure: the caller should report the gap to the user, not
    retry or substitute data.
    """

    def __init__(self, message: str, *, remediation: str | None = None):
        super().__init__(message)
        self.remediation = remediation
