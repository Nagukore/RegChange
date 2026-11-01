"""Idempotent seeding of reference data.

Runs on every startup. Only reference data is seeded here -- the business-rule
catalogue, regulatory source entry points and (optionally) a bootstrap admin.
No regulatory document text is seeded; that is always fetched live.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import get_logger
from app.core.security import hash_password
from app.models.enums import UserRole
from app.models.mapping import BusinessRule
from app.models.ops import User
from app.models.regulatory import RegulatorySource
from app.seeds.business_rules import BUSINESS_RULES
from app.seeds.regulatory_sources import REGULATORY_SOURCES

logger = get_logger(__name__)


def seed_business_rules(db: Session) -> tuple[int, int]:
    """Upsert the CBS rule catalogue by rule_code. Returns (created, updated)."""
    created = updated = 0
    for seed in BUSINESS_RULES:
        existing = db.execute(
            select(BusinessRule).where(BusinessRule.rule_code == seed["rule_code"])
        ).scalar_one_or_none()
        if existing is None:
            db.add(
                BusinessRule(
                    rule_code=seed["rule_code"],
                    name=seed["name"],
                    description=seed["description"],
                    banking_module=seed["banking_module"],
                    api_endpoint=seed["api_endpoint"],
                    ui_route=seed["ui_route"],
                    affected_fields=seed["affected_fields"],
                    keywords=seed["keywords"],
                )
            )
            created += 1
            continue
        # Keep the catalogue text in sync with the code without touching the
        # row identity, so existing impact mappings stay valid.
        changed = False
        for field in ("name", "description", "banking_module", "api_endpoint", "ui_route"):
            if getattr(existing, field) != seed[field]:
                setattr(existing, field, seed[field])
                changed = True
        if existing.affected_fields != seed["affected_fields"]:
            existing.affected_fields = seed["affected_fields"]
            changed = True
        if existing.keywords != seed["keywords"]:
            existing.keywords = seed["keywords"]
            changed = True
        if changed:
            updated += 1
    return created, updated


def seed_regulatory_sources(db: Session) -> int:
    created = 0
    for seed in REGULATORY_SOURCES:
        existing = db.execute(
            select(RegulatorySource).where(
                RegulatorySource.authority == seed["authority"],
                RegulatorySource.base_url == seed["base_url"],
            )
        ).scalar_one_or_none()
        if existing is None:
            db.add(
                RegulatorySource(
                    authority=seed["authority"],
                    name=seed["name"],
                    base_url=seed["base_url"],
                    description=seed["description"],
                    applicable_institution_types=seed["applicable_institution_types"],
                )
            )
            created += 1
    return created


def seed_bootstrap_admin(db: Session) -> bool:
    """Create the first admin only when a password is explicitly configured.

    There is deliberately no default password: an unconfigured deployment ends
    up with no accounts rather than a known-credential account.
    """
    if not settings.bootstrap_admin_password:
        return False
    email = settings.bootstrap_admin_email.lower().strip()
    existing = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
    if existing is not None:
        return False
    db.add(
        User(
            email=email,
            full_name="Bootstrap Administrator",
            hashed_password=hash_password(settings.bootstrap_admin_password),
            role=UserRole.ADMIN.value,
            is_active=True,
        )
    )
    logger.info("Bootstrap admin created for %s", email)
    return True


def run_all_seeds(db: Session) -> dict[str, int | bool]:
    rules_created, rules_updated = seed_business_rules(db)
    sources_created = seed_regulatory_sources(db)
    admin_created = seed_bootstrap_admin(db)
    db.commit()
    result = {
        "business_rules_created": rules_created,
        "business_rules_updated": rules_updated,
        "regulatory_sources_created": sources_created,
        "bootstrap_admin_created": admin_created,
    }
    logger.info("Seeding complete: %s", result)
    return result
