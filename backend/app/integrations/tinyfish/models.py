"""Typed TinyFish response models.

These mirror the official OpenAPI specifications published at
https://agent.tinyfish.ai/v1/openapi/{search,fetch,browser,monitor}
(captured 2026-10-31, spec version 1.0.0). Field names, types and optionality
are taken from those specs -- nothing is invented.

`extra="allow"` is set deliberately: TinyFish may add response fields, and a
new field must never break ingestion. Required fields are still enforced, so a
genuinely malformed response fails validation loudly.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class _Base(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)


# --------------------------------------------------------------------------
# Search API -- GET https://api.search.tinyfish.ai/
# --------------------------------------------------------------------------
class SearchResult(_Base):
    position: int
    site_name: str
    snippet: str
    title: str
    url: str
    thumbnail_url: str | None = None
    # Present for domain_type=news
    date: str | None = None
    publisher: str | None = None
    # Present for domain_type=research_paper
    authors: list[str] | None = None
    venue: str | None = None
    year: int | None = None
    cited_by_count: int | None = None
    pdf_url: str | None = None
    # Present only when the inline `fetch` parameter was supplied
    fetch: Any | None = None
    fetch_error: Any | None = None


class SearchResponse(_Base):
    query: str
    results: list[SearchResult] = Field(default_factory=list)
    total_results: int = 0
    page: int = 0
    request_id: str | None = None


# --------------------------------------------------------------------------
# Fetch API -- POST https://api.fetch.tinyfish.ai/
# --------------------------------------------------------------------------
class FetchResult(_Base):
    url: str
    final_url: str | None = None
    title: str | None = None
    description: str | None = None
    language: str | None = None
    format: Literal["markdown", "html", "json"] | None = None
    # str for markdown/html, dict for json, None when extraction yielded nothing
    text: str | dict[str, Any] | None = None
    author: str | None = None
    published_date: str | None = None
    links: list[Any] | None = None
    image_links: list[Any] | None = None
    iframe_links: list[str] | None = None
    page_metadata: dict[str, Any] | None = None
    latency_ms: float | None = None
    # Conditional-request / caching signals used for version detection
    not_modified: bool | None = None
    etag: str | None = None
    last_modified: str | None = None
    unmatched_selectors: list[str] | None = None

    @property
    def text_as_str(self) -> str:
        """Normalise `text` to a string regardless of requested format."""
        if self.text is None:
            return ""
        if isinstance(self.text, str):
            return self.text
        import json

        return json.dumps(self.text, ensure_ascii=False, indent=2)


class FetchError(_Base):
    url: str
    error: str
    status: int | None = None
    unmatched_selectors: list[str] | None = None
    candidate_selectors: list[str] | None = None


class FetchResponse(_Base):
    results: list[FetchResult] = Field(default_factory=list)
    errors: list[FetchError] = Field(default_factory=list)
    request_id: str | None = None


# --------------------------------------------------------------------------
# Browser API -- POST https://api.browser.tinyfish.ai/
# --------------------------------------------------------------------------
class BrowserSession(_Base):
    session_id: str
    # WebSocket endpoint for Playwright connect_over_cdp. Treated as a secret:
    # it grants control of the session, so it is redacted from all logs.
    cdp_url: str
    base_url: str


# --------------------------------------------------------------------------
# Agent API -- POST https://agent.tinyfish.ai/v1/automation/run-sse
# --------------------------------------------------------------------------
class AgentEvent(_Base):
    """One SSE frame: STARTED | PROGRESS | COMPLETE."""

    type: str
    run_id: str | None = None
    status: str | None = None
    purpose: str | None = None
    result: Any | None = None


# --------------------------------------------------------------------------
# Monitor API -- POST https://agent.tinyfish.ai/v1/monitors
# --------------------------------------------------------------------------
class Monitor(_Base):
    id: str
    name: str | None = None
    schedule_cron: str
    status: str
    type: str
    config: dict[str, Any] | None = None
    webhook_url: str | None = None
    last_error: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class MonitorCreateResponse(_Base):
    monitor: Monitor
    run: dict[str, Any] | None = None


__all__ = [
    "SearchResult",
    "SearchResponse",
    "FetchResult",
    "FetchError",
    "FetchResponse",
    "BrowserSession",
    "AgentEvent",
    "Monitor",
    "MonitorCreateResponse",
]
