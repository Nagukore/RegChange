"""TinyFish API client.

One typed wrapper per verified TinyFish product. Endpoints, parameters and
response shapes come from the official OpenAPI specs (see models.py header).

Design points:
  * Auth is the `X-API-Key` header on every call, as documented.
  * Retries use exponential backoff and only cover transient conditions
    (429 / 5xx / network). A 4xx that is not 429 is a caller error and is
    raised immediately -- retrying it would just burn quota.
  * `TinyFishNotConfigured` is raised when no key is present, so callers can
    degrade honestly instead of silently fabricating results.
  * Nothing here ever logs the API key or a `cdp_url`.
"""
from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlparse

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.core.config import settings
from app.core.logging import get_logger
from app.integrations.tinyfish.models import (
    AgentEvent,
    BrowserSession,
    FetchResponse,
    MonitorCreateResponse,
    SearchResponse,
)

logger = get_logger(__name__)


class TinyFishError(RuntimeError):
    """Any failure talking to TinyFish."""

    def __init__(self, message: str, *, status_code: int | None = None, request_id: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.request_id = request_id


class TinyFishNotConfigured(TinyFishError):
    """No TINYFISH_API_KEY is set. Callers must degrade visibly, never fake."""


class TinyFishTransient(TinyFishError):
    """Retryable: rate limited, upstream 5xx, or a network fault."""


def _is_retryable(exc: BaseException) -> bool:
    return isinstance(exc, (TinyFishTransient, httpx.TransportError))


class TinyFishClient:
    """Async client covering Search, Fetch, Browser, Agent and Monitor."""

    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = api_key if api_key is not None else settings.tinyfish_api_key

    # ---- plumbing ---------------------------------------------------------
    @property
    def configured(self) -> bool:
        return bool(self._api_key)

    def _headers(self, json_body: bool = False) -> dict[str, str]:
        if not self._api_key:
            raise TinyFishNotConfigured(
                "TINYFISH_API_KEY is not set. Create a key at "
                "https://agent.tinyfish.ai/api-keys and add it to backend/.env"
            )
        headers = {"X-API-Key": self._api_key, "Accept": "application/json"}
        if json_body:
            headers["Content-Type"] = "application/json"
        return headers

    @staticmethod
    def _raise_for_status(response: httpx.Response, product: str) -> None:
        if response.status_code < 400:
            return
        request_id = response.headers.get("X-Request-ID")
        # Body may be JSON or text; never echo headers (they carry the key).
        try:
            detail = json.dumps(response.json())[:600]
        except Exception:
            detail = response.text[:600]

        message = f"TinyFish {product} returned HTTP {response.status_code}: {detail}"
        if response.status_code == 429 or response.status_code >= 500:
            raise TinyFishTransient(
                message, status_code=response.status_code, request_id=request_id
            )
        if response.status_code in (401, 403):
            raise TinyFishError(
                f"TinyFish {product} rejected the API key (HTTP {response.status_code}). "
                "Check TINYFISH_API_KEY and that the product is enabled for the account.",
                status_code=response.status_code,
                request_id=request_id,
            )
        raise TinyFishError(message, status_code=response.status_code, request_id=request_id)

    # ---- Search -----------------------------------------------------------
    @retry(
        retry=retry_if_exception_type((TinyFishTransient, httpx.TransportError)),
        stop=stop_after_attempt(settings.tinyfish_max_retries),
        wait=wait_exponential(multiplier=1.5, min=2, max=20),
        reraise=True,
    )
    async def search(
        self,
        query: str,
        *,
        purpose: str | None = None,
        include_domains: list[str] | None = None,
        exclude_domains: list[str] | None = None,
        domain_type: str | None = None,
        after_date: str | None = None,
        before_date: str | None = None,
        recency_minutes: int | None = None,
        location: str | None = None,
        language: str | None = None,
        page: int | None = None,
    ) -> SearchResponse:
        """GET https://api.search.tinyfish.ai/

        `include_domains` is how we constrain discovery to official regulator
        sites (e.g. rbi.org.in) rather than filtering noisy results afterwards.
        """
        params: dict[str, Any] = {"query": query}
        if purpose:
            params["purpose"] = purpose[:2000]
        if include_domains:
            params["include_domains"] = ",".join(include_domains)
        if exclude_domains:
            params["exclude_domains"] = ",".join(exclude_domains)
        if domain_type:
            params["domain_type"] = domain_type
        # The spec forbids combining recency_minutes with explicit date bounds.
        if recency_minutes is not None:
            params["recency_minutes"] = recency_minutes
        else:
            if after_date:
                params["after_date"] = after_date
            if before_date:
                params["before_date"] = before_date
        if location:
            params["location"] = location
        if language:
            params["language"] = language
        if page is not None:
            params["page"] = page

        async with httpx.AsyncClient(timeout=settings.tinyfish_timeout_seconds) as client:
            response = await client.get(
                settings.tinyfish_search_url.rstrip("/") + "/",
                params=params,
                headers=self._headers(),
            )
        self._raise_for_status(response, "Search")
        parsed = SearchResponse.model_validate(response.json())
        logger.info(
            "TinyFish Search ok | query=%r results=%d request_id=%s",
            query,
            len(parsed.results),
            parsed.request_id,
        )
        return parsed

    # ---- Fetch ------------------------------------------------------------
    @retry(
        retry=retry_if_exception_type((TinyFishTransient, httpx.TransportError)),
        stop=stop_after_attempt(settings.tinyfish_max_retries),
        wait=wait_exponential(multiplier=1.5, min=2, max=20),
        reraise=True,
    )
    async def fetch(
        self,
        urls: list[str],
        *,
        purpose: str | None = None,
        fmt: str = "markdown",
        ttl: int | None = None,
        include_etag_and_last_modified: bool = True,
        if_none_match: str | None = None,
        if_modified_since: str | None = None,
        include_selectors: list[str] | None = None,
        exclude_selectors: list[str] | None = None,
        page_metadata: bool = True,
        links: bool = False,
        per_url_timeout_ms: int | None = None,
    ) -> FetchResponse:
        """POST https://api.fetch.tinyfish.ai/

        The spec allows 1-10 URLs per request, so callers must chunk. ETag and
        Last-Modified validators are requested by default because document
        version detection depends on them.
        """
        if not urls:
            raise TinyFishError("fetch() requires at least one URL")
        if len(urls) > 10:
            raise TinyFishError(
                f"TinyFish Fetch accepts at most 10 URLs per request, got {len(urls)}. "
                "Use chunked_fetch() instead."
            )
        # Per the spec these conditional headers are single-URL only.
        if (if_none_match or if_modified_since) and len(urls) > 1:
            raise TinyFishError(
                "if_none_match / if_modified_since may only be used with a single URL"
            )

        body: dict[str, Any] = {
            "urls": urls,
            "format": fmt,
            "include_etag_and_last_modified": include_etag_and_last_modified,
            "page_metadata": page_metadata,
        }
        if purpose:
            body["purpose"] = purpose[:2000]
        if ttl is not None:
            body["ttl"] = ttl
        if if_none_match:
            body["if_none_match"] = if_none_match
        if if_modified_since:
            body["if_modified_since"] = if_modified_since
        if include_selectors:
            body["include_selectors"] = include_selectors[:20]
        if exclude_selectors:
            body["exclude_selectors"] = exclude_selectors[:20]
        if links:
            body["links"] = True
        if per_url_timeout_ms is not None:
            body["per_url_timeout_ms"] = per_url_timeout_ms

        async with httpx.AsyncClient(timeout=settings.tinyfish_timeout_seconds) as client:
            response = await client.post(
                settings.tinyfish_fetch_url.rstrip("/") + "/",
                json=body,
                headers=self._headers(json_body=True),
            )
        self._raise_for_status(response, "Fetch")
        parsed = FetchResponse.model_validate(response.json())
        logger.info(
            "TinyFish Fetch ok | requested=%d ok=%d errors=%d request_id=%s",
            len(urls),
            len(parsed.results),
            len(parsed.errors),
            parsed.request_id,
        )
        return parsed

    async def chunked_fetch(self, urls: list[str], **kwargs: Any) -> FetchResponse:
        """Fetch any number of URLs by batching into the documented max of 10."""
        merged = FetchResponse()
        # De-duplicate while preserving order to avoid paying for repeats.
        unique = list(dict.fromkeys(urls))
        for i in range(0, len(unique), 10):
            chunk = unique[i : i + 10]
            part = await self.fetch(chunk, **kwargs)
            merged.results.extend(part.results)
            merged.errors.extend(part.errors)
            merged.request_id = merged.request_id or part.request_id
        return merged

    # ---- Browser ----------------------------------------------------------
    async def create_browser_session(
        self,
        *,
        url: str | None = None,
        timeout_seconds: int | None = None,
        proxy_config: dict[str, Any] | None = None,
    ) -> BrowserSession:
        """POST https://api.browser.tinyfish.ai/ -> {session_id, cdp_url, base_url}

        Session creation takes 10-30s per the docs, so a generous client
        timeout is used. Note this browser runs in TinyFish's cloud and cannot
        reach localhost -- see docs/ARCHITECTURE.md.
        """
        body: dict[str, Any] = {}
        if url:
            body["url"] = url
        if timeout_seconds is not None:
            body["timeout_seconds"] = timeout_seconds
        if proxy_config:
            body["proxy_config"] = proxy_config

        async with httpx.AsyncClient(
            timeout=settings.tinyfish_browser_timeout_seconds
        ) as client:
            response = await client.post(
                settings.tinyfish_browser_url.rstrip("/") + "/",
                json=body,
                headers=self._headers(json_body=True),
            )
        self._raise_for_status(response, "Browser")
        session = BrowserSession.model_validate(response.json())
        # cdp_url grants control of the session, so only the host is logged.
        logger.info(
            "TinyFish Browser session created | session_id=%s cdp_host=%s",
            session.session_id,
            urlparse(session.cdp_url).hostname,
        )
        return session

    async def close_browser_session(self, session_id: str) -> bool:
        """DELETE https://api.browser.tinyfish.ai/{session_id}"""
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.delete(
                f"{settings.tinyfish_browser_url.rstrip('/')}/{session_id}",
                headers=self._headers(),
            )
        if response.status_code >= 400:
            # Teardown failure must not mask a test result; sessions also expire
            # on their own inactivity timeout.
            logger.warning(
                "Could not terminate TinyFish browser session %s (HTTP %s)",
                session_id,
                response.status_code,
            )
            return False
        logger.info("TinyFish Browser session %s terminated", session_id)
        return True

    # ---- Agent ------------------------------------------------------------
    async def agent_run_sse(
        self,
        *,
        url: str,
        goal: str,
        output_schema: dict[str, Any] | None = None,
    ):
        """POST https://agent.tinyfish.ai/v1/automation/run-sse

        Async generator of AgentEvent. Used only for navigation that Search and
        Fetch cannot do (e.g. stepping through a paginated regulator archive),
        because Agent calls are metered while Search/Fetch are free.
        """
        body: dict[str, Any] = {"url": url, "goal": goal}
        if output_schema:
            body["output_schema"] = output_schema

        endpoint = f"{settings.tinyfish_agent_url.rstrip('/')}/v1/automation/run-sse"
        headers = self._headers(json_body=True)
        headers["Accept"] = "text/event-stream"

        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream("POST", endpoint, json=body, headers=headers) as response:
                if response.status_code >= 400:
                    await response.aread()
                    self._raise_for_status(response, "Agent")
                async for line in response.aiter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    raw = line[len("data:") :].strip()
                    if not raw or raw == "[DONE]":
                        continue
                    try:
                        yield AgentEvent.model_validate(json.loads(raw))
                    except Exception as exc:  # a malformed frame must not kill the run
                        logger.warning("Skipping unparseable Agent SSE frame: %s", exc)

    async def agent_run(
        self,
        *,
        url: str,
        goal: str,
        output_schema: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """POST https://agent.tinyfish.ai/v1/automation/run (blocking)."""
        body: dict[str, Any] = {"url": url, "goal": goal}
        if output_schema:
            body["output_schema"] = output_schema
        endpoint = f"{settings.tinyfish_agent_url.rstrip('/')}/v1/automation/run"
        async with httpx.AsyncClient(timeout=None) as client:
            response = await client.post(endpoint, json=body, headers=self._headers(json_body=True))
        self._raise_for_status(response, "Agent")
        return response.json()

    # ---- Monitor ----------------------------------------------------------
    async def create_monitor(
        self,
        *,
        monitor_type: str,
        config: dict[str, Any],
        schedule_cron: str,
        name: str | None = None,
        purpose: str | None = None,
        webhook_url: str | None = None,
    ) -> MonitorCreateResponse:
        """POST https://agent.tinyfish.ai/v1/monitors

        `monitor_type` is "search" or "fetch" per the spec; `config` carries the
        product-specific parameters for the scheduled run.
        """
        if monitor_type not in ("search", "fetch"):
            raise TinyFishError(
                f"Monitor type must be 'search' or 'fetch', got {monitor_type!r}"
            )
        body: dict[str, Any] = {
            "type": monitor_type,
            "config": config,
            "schedule_cron": schedule_cron,
        }
        if name:
            body["name"] = name
        if purpose:
            body["purpose"] = purpose
        if webhook_url:
            body["webhook_url"] = webhook_url

        endpoint = f"{settings.tinyfish_agent_url.rstrip('/')}/v1/monitors"
        async with httpx.AsyncClient(timeout=settings.tinyfish_timeout_seconds) as client:
            response = await client.post(endpoint, json=body, headers=self._headers(json_body=True))
        self._raise_for_status(response, "Monitor")
        return MonitorCreateResponse.model_validate(response.json())

    async def list_monitors(self) -> list[dict[str, Any]]:
        endpoint = f"{settings.tinyfish_agent_url.rstrip('/')}/v1/monitors"
        async with httpx.AsyncClient(timeout=settings.tinyfish_timeout_seconds) as client:
            response = await client.get(endpoint, headers=self._headers())
        self._raise_for_status(response, "Monitor")
        return response.json().get("monitors", [])

    async def delete_monitor(self, monitor_id: str) -> bool:
        endpoint = f"{settings.tinyfish_agent_url.rstrip('/')}/v1/monitors/{monitor_id}"
        async with httpx.AsyncClient(timeout=settings.tinyfish_timeout_seconds) as client:
            response = await client.delete(endpoint, headers=self._headers())
        return response.status_code < 400

    # ---- Connectivity probe ----------------------------------------------
    async def healthcheck(self) -> dict[str, Any]:
        """Cheap liveness probe used by /api/system/health.

        Deliberately uses Search (free tier) with a one-word query rather than
        a metered product.
        """
        if not self.configured:
            return {"configured": False, "reachable": False, "detail": "TINYFISH_API_KEY not set"}
        try:
            result = await self.search("reserve bank of india", include_domains=["rbi.org.in"])
            return {
                "configured": True,
                "reachable": True,
                "detail": f"Search returned {len(result.results)} results",
                "request_id": result.request_id,
            }
        except TinyFishError as exc:
            return {"configured": True, "reachable": False, "detail": str(exc)[:300]}


def get_tinyfish_client() -> TinyFishClient:
    return TinyFishClient()
