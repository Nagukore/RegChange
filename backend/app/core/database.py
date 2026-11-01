"""Database engine and session management.

Synchronous SQLAlchemy is used deliberately: the long-running agent work is
I/O-bound on external HTTP APIs (handled with async httpx inside the
integration layer) while persistence is short, transactional and simpler to
reason about synchronously. Blocking DB work inside async endpoints is pushed
to a threadpool by FastAPI via `def` (not `async def`) route handlers where it
matters, and by `anyio.to_thread` in the agent orchestrator.
"""
from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings


def _build_engine() -> Engine:
    url = settings.effective_database_url
    kwargs: dict = {"echo": settings.db_echo, "future": True}

    if url.startswith("sqlite"):
        # check_same_thread=False is required because FastAPI serves requests
        # from a threadpool; SQLAlchemy still pools one connection per thread.
        kwargs["connect_args"] = {"check_same_thread": False}
    else:
        # Supabase pools connections at the pgbouncer layer, so keep the local
        # pool small and recycle before the server-side idle timeout.
        kwargs.update(
            pool_pre_ping=True,
            pool_size=5,
            max_overflow=5,
            pool_recycle=1800,
        )
    return create_engine(url, **kwargs)


engine = _build_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


@event.listens_for(Engine, "connect")
def _set_sqlite_pragmas(dbapi_connection, connection_record):  # noqa: ANN001, ARG001
    """Enforce foreign keys on SQLite, which disables them by default."""
    if engine.dialect.name != "sqlite":
        return
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency yielding a request-scoped session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def session_scope() -> Generator[Session, None, None]:
    """Transactional scope for background agent work."""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
