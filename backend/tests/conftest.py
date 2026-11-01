"""Shared test fixtures.

Database strategy
-----------------
`DATABASE_URL` is pointed at a throwaway SQLite file *before* app.core.config is
imported, so the application engine, the TestClient and the execution agent all
target the same test database. This matters for Agent 6: it drives the CBS by
calling our own FastAPI app over ASGI, which resolves `get_db` through the real
engine. Without this the agent would write to the developer's database while the
test read from a different one.

Two session fixtures, for two different needs:
  * `db` / `seeded_db` -- wrapped in a transaction that is rolled back after the
    test. Fast and isolated; correct for anything that only touches the ORM.
  * `live_db`           -- commits for real. Required whenever the code under
    test performs its own writes through a separate connection (the execution
    agent), because a rolled-back outer transaction is invisible to it.
"""
from __future__ import annotations

import os
import tempfile
from collections.abc import Generator
from pathlib import Path

import pytest

_TMP_DIR = Path(tempfile.mkdtemp(prefix="regchange-tests-"))
_TEST_DB = _TMP_DIR / "regchange-test.db"

# Set before any `app.*` import so Settings picks these up on first construction.
os.environ["DATABASE_URL"] = f"sqlite+pysqlite:///{_TEST_DB.as_posix()}"
os.environ["ENVIRONMENT"] = "test"
# Keep test runs from spending real API quota or hitting external services.
os.environ["TINYFISH_API_KEY"] = ""
os.environ["GEMINI_API_KEY"] = ""
os.environ["CBS_PUBLIC_BASE_URL"] = ""
os.environ["EVIDENCE_DIR"] = str(_TMP_DIR / "evidence")


@pytest.fixture(scope="session", autouse=True)
def _create_schema():
    """Create the schema once for the whole session."""
    from app.core.database import engine
    from app.models import Base

    Base.metadata.create_all(engine)
    yield
    engine.dispose()


@pytest.fixture(scope="session")
def engine():
    from app.core.database import engine as app_engine

    return app_engine


@pytest.fixture()
def db(engine) -> Generator:
    """Session inside a transaction that is rolled back after the test."""
    from sqlalchemy.orm import sessionmaker

    connection = engine.connect()
    transaction = connection.begin()
    SessionTesting = sessionmaker(bind=connection, autoflush=False, expire_on_commit=False)
    session = SessionTesting()
    try:
        yield session
    finally:
        session.close()
        if transaction.is_active:
            transaction.rollback()
        connection.close()


@pytest.fixture()
def seeded_db(db):
    """Rolled-back session with the rule catalogue and sources seeded."""
    from app.seeds.runner import seed_business_rules, seed_regulatory_sources

    seed_business_rules(db)
    seed_regulatory_sources(db)
    db.flush()
    return db


@pytest.fixture()
def live_db() -> Generator:
    """Committing session sharing the application engine.

    Use for code that writes through its own connection. Rows persist for the
    session; the whole database file is discarded at the end of the run.
    """
    from app.core.database import SessionLocal
    from app.seeds.runner import seed_business_rules, seed_regulatory_sources

    session = SessionLocal()
    try:
        seed_business_rules(session)
        seed_regulatory_sources(session)
        session.commit()
        yield session
    finally:
        session.close()


@pytest.fixture()
def client(engine) -> Generator:
    """TestClient against the same test database."""
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


# --- Helpers ---------------------------------------------------------------
def unique_identifiers() -> dict[str, str]:
    """A PAN and mobile unique to this call.

    Both are globally unique in the CBS, and `live_db` tests share one database
    for the whole session, so fixed literals would collide.
    """
    import random
    import string

    pan = (
        "".join(random.choices(string.ascii_uppercase, k=5))
        + "".join(random.choices(string.digits, k=4))
        + random.choice(string.ascii_uppercase)
    )
    mobile = random.choice("6789") + "".join(random.choices(string.digits, k=9))
    return {"pan": pan, "mobile": mobile}


def valid_customer_payload(**overrides) -> dict:
    """A customer payload that passes every CBS-001 validation."""
    payload = {
        "first_name": "Asha",
        "last_name": "Menon",
        "date_of_birth": "1988-04-12",
        "pan": "ABCDE1234F",
        "mobile": "9876543210",
        "email": "asha.menon@example.invalid",
        "address_line1": "14 MG Road",
        "city": "Kochi",
        "state": "Kerala",
        "pincode": "682001",
        "country": "IN",
        "customer_type": "INDIVIDUAL",
        "is_pep": False,
    }
    payload.update(overrides)
    return payload


def valid_ovd_documents() -> list[dict]:
    """Two verified, unexpired OVDs including address-capable proof."""
    return [
        {
            "doc_type": "PASSPORT",
            "number": "SIMPASS001",
            "expiry_date": "2033-01-01",
            "verified": True,
        },
        {"doc_type": "PAN", "number": "ABCDE1234F", "verified": True},
    ]
