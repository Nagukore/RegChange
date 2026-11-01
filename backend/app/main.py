"""RegChange Sentinel API entrypoint."""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.core.database import SessionLocal, engine
from app.core.logging import configure_logging, get_logger, redact
from app.integrations.gemini import GeminiError, GeminiNotConfigured
from app.integrations.tinyfish import TinyFishError, TinyFishNotConfigured
from app.seeds.runner import run_all_seeds
from app.services.recovery import reconcile_interrupted_work

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    logger.info(
        "Starting %s | env=%s | db=%s",
        settings.app_name,
        settings.environment,
        "postgresql" if settings.is_postgres else "sqlite",
    )
    # Reference data is seeded on every boot; the operation is idempotent.
    with SessionLocal() as db:
        try:
            run_all_seeds(db)
        except Exception as exc:
            # A seeding failure must not prevent the API from serving; the
            # Settings screen will show the resulting empty catalogue.
            logger.error("Seeding failed: %s", exc)
        try:
            reconcile_interrupted_work(db)
        except Exception as exc:
            logger.error("Could not close interrupted work: %s", exc)

    if not settings.tinyfish_configured:
        logger.warning(
            "TINYFISH_API_KEY is not set -- regulatory discovery and extraction "
            "are disabled. Get a free key at https://agent.tinyfish.ai/api-keys"
        )
    if not settings.gemini_configured:
        logger.warning(
            "GEMINI_API_KEY is not set -- analysis, impact mapping and test "
            "generation are disabled. Get a key at https://aistudio.google.com/apikey"
        )
    yield
    engine.dispose()
    logger.info("Shutdown complete")


app = FastAPI(
    title=settings.app_name,
    description=(
        "From regulatory intelligence to automated banking compliance testing.\n\n"
        "All banking operations run against a SIMULATED core banking system. "
        "No real banking system is ever contacted."
    ),
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs",
    openapi_url="/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- Exception handlers: translate integration failures into honest HTTP ----
@app.exception_handler(TinyFishNotConfigured)
async def _tinyfish_unconfigured(request: Request, exc: TinyFishNotConfigured):
    return JSONResponse(
        status_code=503,
        content={
            "error": "tinyfish_not_configured",
            "detail": str(exc),
            "remediation": "Set TINYFISH_API_KEY in backend/.env and restart the API.",
        },
    )


@app.exception_handler(GeminiNotConfigured)
async def _gemini_unconfigured(request: Request, exc: GeminiNotConfigured):
    return JSONResponse(
        status_code=503,
        content={
            "error": "gemini_not_configured",
            "detail": str(exc),
            "remediation": "Set GEMINI_API_KEY in backend/.env and restart the API.",
        },
    )


@app.exception_handler(TinyFishError)
async def _tinyfish_error(request: Request, exc: TinyFishError):
    logger.warning("TinyFish failure on %s: %s", request.url.path, exc)
    return JSONResponse(
        status_code=502,
        content={
            "error": "tinyfish_request_failed",
            "detail": redact(str(exc))[:600],
            "status_code": exc.status_code,
            "request_id": exc.request_id,
        },
    )


@app.exception_handler(GeminiError)
async def _gemini_error(request: Request, exc: GeminiError):
    logger.warning("Gemini failure on %s: %s", request.url.path, exc)
    return JSONResponse(
        status_code=502,
        content={"error": "gemini_request_failed", "detail": redact(str(exc))[:600]},
    )


# --- Routers ---------------------------------------------------------------
from app.api.routers import cbs, dashboard, pipeline, system  # noqa: E402

app.include_router(system.router, prefix=settings.api_prefix)
app.include_router(pipeline.router, prefix=settings.api_prefix)
app.include_router(dashboard.router, prefix=settings.api_prefix)
app.include_router(cbs.router, prefix=settings.api_prefix)


@app.get("/", tags=["system"])
def root() -> dict:
    return {
        "name": settings.app_name,
        "tagline": "From Regulatory Intelligence to Automated Banking Compliance Testing",
        "docs": "/docs",
        "api_prefix": settings.api_prefix,
        "notice": "Banking operations are simulated. No real banking system is contacted.",
    }
