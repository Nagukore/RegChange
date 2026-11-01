"""Application configuration.

Every credential is read from the environment. Nothing is hard-coded and no
default ever contains a real secret. See `.env.example` for the full list.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]
REPO_DIR = BACKEND_DIR.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---- Application -------------------------------------------------------
    app_name: str = "RegChange Sentinel"
    environment: str = "development"
    debug: bool = True
    api_prefix: str = "/api"
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    # ---- Database ----------------------------------------------------------
    # Supabase Postgres is the deployment target. When DATABASE_URL is unset we
    # fall back to a local SQLite file so the app is always runnable; the models
    # use portable type decorators (see app/models/types.py) to support both.
    database_url: str = Field(default="")
    db_echo: bool = False

    # ---- TinyFish (verified endpoints, see docs/TINYFISH_API.md) -----------
    tinyfish_api_key: str = Field(default="")
    tinyfish_search_url: str = "https://api.search.tinyfish.ai"
    tinyfish_fetch_url: str = "https://api.fetch.tinyfish.ai"
    tinyfish_browser_url: str = "https://api.browser.tinyfish.ai"
    tinyfish_agent_url: str = "https://agent.tinyfish.ai"
    tinyfish_timeout_seconds: float = 90.0
    tinyfish_browser_timeout_seconds: float = 120.0
    tinyfish_max_retries: int = 3

    # ---- Google Gemini -----------------------------------------------------
    gemini_api_key: str = Field(default="")
    gemini_model: str = "gemini-2.5-flash"
    gemini_pro_model: str = "gemini-2.5-pro"
    gemini_timeout_seconds: float = 180.0
    gemini_max_retries: int = 3

    # ---- Regulatory configuration -----------------------------------------
    regulatory_authority: str = "RBI"
    # The RBI KYC framework differs by regulated-entity type, so this is
    # configurable rather than assumed. See docs/REGULATORY_SCOPE.md.
    institution_type: str = "COMMERCIAL_BANK"
    regulatory_domains: str = "rbi.org.in"
    # Search ranks by relevance, not date, so an unbounded query keeps returning
    # the same old instruments. Discovery only asks for this rolling window.
    discovery_lookback_days: int = 365

    # ---- Core banking simulator -------------------------------------------
    # Local URL used by local Playwright runs.
    cbs_local_base_url: str = "http://localhost:5173"
    # Publicly reachable URL used by TinyFish's cloud browser, which cannot
    # resolve localhost. Leave empty to disable the TinyBrowser driver.
    cbs_public_base_url: str = ""

    # ---- Execution safety --------------------------------------------------
    # State-changing test execution requires an explicit human approval record.
    require_approval_for_execution: bool = True
    evidence_dir: str = str(BACKEND_DIR / "evidence")

    # ---- Auth --------------------------------------------------------------
    jwt_secret: str = Field(default="dev-only-insecure-secret-change-me")
    jwt_algorithm: str = "HS256"
    jwt_expiry_minutes: int = 720
    bootstrap_admin_email: str = "admin@regchange.local"
    bootstrap_admin_password: str = ""

    @field_validator("database_url")
    @classmethod
    def _normalise_db_url(cls, v: str) -> str:
        """Normalise Postgres URLs to the psycopg3 driver SQLAlchemy expects."""
        if not v:
            return v
        if v.startswith("postgres://"):
            v = v.replace("postgres://", "postgresql://", 1)
        if v.startswith("postgresql://"):
            v = v.replace("postgresql://", "postgresql+psycopg://", 1)
        return v

    @property
    def effective_database_url(self) -> str:
        if self.database_url:
            return self.database_url
        return f"sqlite+pysqlite:///{(BACKEND_DIR / 'regchange.db').as_posix()}"

    @property
    def is_postgres(self) -> bool:
        return self.effective_database_url.startswith("postgresql")

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def regulatory_domain_list(self) -> list[str]:
        return [d.strip() for d in self.regulatory_domains.split(",") if d.strip()]

    # ---- Capability flags: drive the honest "what is configured" UI --------
    @property
    def tinyfish_configured(self) -> bool:
        return bool(self.tinyfish_api_key)

    @property
    def gemini_configured(self) -> bool:
        return bool(self.gemini_api_key)

    @property
    def tinybrowser_cbs_configured(self) -> bool:
        return bool(self.tinyfish_api_key and self.cbs_public_base_url)


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
