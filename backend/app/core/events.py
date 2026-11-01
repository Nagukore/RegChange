"""In-process pub/sub bus backing the SSE stream.

Scope: single-process. Each connected client gets its own bounded queue, so a
slow consumer drops its own oldest events instead of blocking publishers or
growing without limit. For multi-worker deployment this would be swapped for
Redis pub/sub behind the same `EventBus` interface.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from app.core.logging import get_logger, redact

logger = get_logger(__name__)

MAX_QUEUE_SIZE = 256
# Replayed to a client on connect so a late subscriber still renders context.
REPLAY_BUFFER_SIZE = 50


class EventType(StrEnum):
    """Workflow events the frontend reacts to (brief section 11)."""

    DISCOVERY_STARTED = "discovery_started"
    DOCUMENTS_FOUND = "documents_found"
    EXTRACTION_STARTED = "extraction_started"
    EXTRACTION_COMPLETED = "extraction_completed"
    ANALYSIS_STARTED = "analysis_started"
    ANALYSIS_COMPLETED = "analysis_completed"
    IMPACT_MAPPING_STARTED = "impact_mapping_started"
    IMPACT_MAPPING_COMPLETED = "impact_mapping_completed"
    TEST_GENERATION_STARTED = "test_generation_started"
    TEST_GENERATION_COMPLETED = "test_generation_completed"
    EXECUTION_STARTED = "execution_started"
    EXECUTION_PROGRESS = "execution_progress"
    EXECUTION_COMPLETED = "execution_completed"
    AGENT_ACTIVITY = "agent_activity"
    APPROVAL_RECORDED = "approval_recorded"
    PIPELINE_FAILED = "pipeline_failed"
    HEARTBEAT = "heartbeat"


@dataclass(slots=True)
class Event:
    type: EventType
    payload: dict[str, Any] = field(default_factory=dict)
    correlation_id: str | None = None
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def to_sse_dict(self) -> dict[str, str]:
        """Shape sse-starlette expects: event name + serialised JSON data."""
        body = {
            "id": self.id,
            "type": str(self.type),
            "correlation_id": self.correlation_id,
            "created_at": self.created_at,
            "payload": redact(self.payload),
        }
        return {"event": str(self.type), "data": json.dumps(body, default=str), "id": self.id}


class EventBus:
    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[Event]] = set()
        self._recent: list[Event] = []
        self._lock = asyncio.Lock()

    async def publish(self, event: Event) -> None:
        async with self._lock:
            self._recent.append(event)
            if len(self._recent) > REPLAY_BUFFER_SIZE:
                self._recent = self._recent[-REPLAY_BUFFER_SIZE:]
            targets = list(self._subscribers)

        for queue in targets:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                # Drop this subscriber's oldest event rather than stall the
                # publisher; the UI re-syncs from REST on reconnect.
                try:
                    queue.get_nowait()
                    queue.put_nowait(event)
                except (asyncio.QueueEmpty, asyncio.QueueFull):
                    logger.warning("Dropping SSE event for a saturated subscriber")

    def publish_threadsafe(self, event: Event, loop: asyncio.AbstractEventLoop) -> None:
        """Publish from a worker thread (agents run off the event loop)."""
        asyncio.run_coroutine_threadsafe(self.publish(event), loop)

    async def subscribe(self, replay: bool = True) -> AsyncIterator[Event]:
        queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=MAX_QUEUE_SIZE)
        async with self._lock:
            self._subscribers.add(queue)
            backlog = list(self._recent) if replay else []
        try:
            # Emit immediately so the client transitions out of CONNECTING at
            # once. Without a first byte some proxies hold the response open
            # and the browser never fires `onopen`, leaving the UI showing
            # "reconnecting" on a stream that is in fact healthy.
            yield Event(type=EventType.HEARTBEAT, payload={"connected": True})
            for event in backlog:
                yield event
            while True:
                try:
                    yield await asyncio.wait_for(queue.get(), timeout=20.0)
                except TimeoutError:
                    # Keeps proxies and browsers from closing an idle stream.
                    yield Event(type=EventType.HEARTBEAT, payload={})
        finally:
            async with self._lock:
                self._subscribers.discard(queue)

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)


bus = EventBus()
