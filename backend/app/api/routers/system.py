"""System health, capability reporting and the SSE stream.

The capability report is deliberately blunt: it tells the UI exactly which
external services are configured and reachable, so the interface can state what
is live and what is unavailable instead of implying everything works.
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session
from sse_starlette.sse import EventSourceResponse

from app.core.config import settings
from app.core.database import get_db
from app.core.events import bus
from app.core.logging import get_logger
from app.integrations.gemini import get_gemini_client
from app.integrations.tinyfish import get_tinyfish_client
from app.models.mapping import BusinessRule
from app.models.regulatory import RegulatoryDocument
from app.seeds.regulatory_sources import INSTITUTION_TYPES

logger = get_logger(__name__)
router = APIRouter(tags=["system"])


@router.get("/health")
def health(db: Session = Depends(get_db)) -> dict:
    """Liveness probe. Does not call external services."""
    try:
        db.execute(text("SELECT 1"))
        db_ok = True
        db_detail = "connected"
    except Exception as exc:
        db_ok = False
        db_detail = str(exc)[:200]

    return {
        "status": "ok" if db_ok else "degraded",
        "app": settings.app_name,
        "environment": settings.environment,
        "database": {
            "ok": db_ok,
            "dialect": "postgresql" if settings.is_postgres else "sqlite",
            "detail": db_detail,
        },
    }


@router.get("/system/capabilities")
def capabilities(db: Session = Depends(get_db)) -> dict:
    """What this deployment can actually do, given its configuration.

    Used by the Settings screen. Reports configuration only -- no outbound
    calls, so it stays fast and free.
    """
    rule_count = db.execute(select(func.count()).select_from(BusinessRule)).scalar() or 0
    doc_count = db.execute(select(func.count()).select_from(RegulatoryDocument)).scalar() or 0

    return {
        "regulatory_authority": settings.regulatory_authority,
        "institution_type": settings.institution_type,
        "institution_types_available": INSTITUTION_TYPES,
        "regulatory_domains": settings.regulatory_domain_list,
        "database": {
            "dialect": "postgresql" if settings.is_postgres else "sqlite",
            "configured": bool(settings.database_url),
            "business_rules": rule_count,
            "regulatory_documents": doc_count,
        },
        "integrations": {
            "tinyfish_search": {
                "configured": settings.tinyfish_configured,
                "endpoint": settings.tinyfish_search_url,
                "required_for": "Regulatory Discovery Agent",
            },
            "tinyfish_fetch": {
                "configured": settings.tinyfish_configured,
                "endpoint": settings.tinyfish_fetch_url,
                "required_for": "Regulatory Extraction Agent",
            },
            "tinyfish_agent": {
                "configured": settings.tinyfish_configured,
                "endpoint": f"{settings.tinyfish_agent_url}/v1/automation/run-sse",
                "required_for": "Complex regulator-site navigation (metered)",
            },
            "tinyfish_browser": {
                "configured": settings.tinyfish_configured,
                "endpoint": settings.tinyfish_browser_url,
                "required_for": "Cloud browser execution driver",
            },
            "tinyfish_monitor": {
                "configured": settings.tinyfish_configured,
                "endpoint": f"{settings.tinyfish_agent_url}/v1/monitors",
                "required_for": "Scheduled regulatory change monitoring",
            },
            "gemini": {
                "configured": settings.gemini_configured,
                "model": settings.gemini_model,
                "required_for": "Analysis, impact mapping and test generation",
            },
        },
        "execution_drivers": {
            "PYTEST_API": {
                "available": True,
                "detail": "In-process calls against the simulated CBS API",
            },
            "PLAYWRIGHT_LOCAL": {
                "available": True,
                "detail": f"Local browser against {settings.cbs_local_base_url}",
            },
            "TINYFISH_BROWSER": {
                "available": settings.tinybrowser_cbs_configured,
                "detail": (
                    f"TinyFish cloud browser against {settings.cbs_public_base_url}"
                    if settings.tinybrowser_cbs_configured
                    else "Unavailable: needs TINYFISH_API_KEY and a publicly reachable "
                    "CBS_PUBLIC_BASE_URL, because the TinyFish cloud browser cannot "
                    "resolve localhost"
                ),
            },
        },
        "safety": {
            "require_approval_for_execution": settings.require_approval_for_execution,
            "simulated_cbs_only": True,
        },
    }


@router.get("/system/health/external")
async def external_health() -> dict:
    """Probe TinyFish and Gemini. Makes real (free-tier) calls, so it is slow."""
    tinyfish = get_tinyfish_client()
    gemini = get_gemini_client()
    tf_result, gm_result = await asyncio.gather(
        tinyfish.healthcheck(), gemini.healthcheck(), return_exceptions=True
    )

    def normalise(value: object, name: str) -> dict:
        if isinstance(value, BaseException):
            return {"configured": True, "reachable": False, "detail": f"{name}: {value}"[:300]}
        return value  # type: ignore[return-value]

    return {
        "tinyfish": normalise(tf_result, "tinyfish"),
        "gemini": normalise(gm_result, "gemini"),
    }


@router.get("/events/stream")
async def events_stream():
    """Server-Sent Events stream of pipeline progress.

    Emits the workflow events listed in the brief (discovery_started,
    documents_found, extraction_completed, ...) plus periodic heartbeats.
    """

    async def generator():
        async for event in bus.subscribe():
            yield event.to_sse_dict()

    return EventSourceResponse(generator())
