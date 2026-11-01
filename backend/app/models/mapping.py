"""Business-rule catalogue and the regulatory-to-CBS impact mapping.

`business_rules` is a curated catalogue of the simulated CBS. The impact engine
may only reference rule codes that already exist here -- the deterministic
validation step in app/agents/impact_agent.py rejects any invented rule ID.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    Float,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import (
    AssertionBasis,
    BankingModule,
    ImpactCategory,
    Priority,
    ReviewStatus,
)
from app.models.types import GUID, JSONBType, UTCDateTime


class BusinessRule(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One rule of the simulated core banking system, e.g. CBS-001."""

    __tablename__ = "business_rules"

    # External, human-facing identifier: "CBS-001". Unique by contract.
    rule_code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    banking_module: Mapped[str] = mapped_column(
        String(48), nullable=False, default=BankingModule.CROSS_CUTTING.value, index=True
    )
    # The CBS API surface this rule governs, e.g. "POST /cbs/customers".
    api_endpoint: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # UI route in the banking simulator, used for browser-level tests.
    ui_route: Mapped[str | None] = mapped_column(String(255), nullable=True)
    affected_fields: Mapped[list | None] = mapped_column(JSONBType, nullable=True)
    # Short keyword bag used by the lexical/semantic retrieval step.
    keywords: Mapped[list | None] = mapped_column(JSONBType, nullable=True)
    # Optional pgvector embedding; stored as a JSON array to stay portable.
    embedding: Mapped[list | None] = mapped_column(JSONBType, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    impact_mappings = relationship("ImpactMapping", back_populates="business_rule")
    test_cases = relationship("TestCase", back_populates="business_rule")


class ImpactMapping(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Links a regulatory change to a CBS business rule, pending human review."""

    __tablename__ = "impact_mappings"

    change_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("regulatory_changes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    business_rule_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("business_rules.id", ondelete="CASCADE"), nullable=False, index=True
    )

    impact_category: Mapped[str] = mapped_column(
        String(48), nullable=False, default=ImpactCategory.BUSINESS_LOGIC.value, index=True
    )
    banking_module: Mapped[str] = mapped_column(
        String(48), nullable=False, default=BankingModule.CROSS_CUTTING.value
    )
    priority: Mapped[str] = mapped_column(String(16), nullable=False, default=Priority.MEDIUM.value)
    # Why the model believes this rule is affected. Not a compliance opinion.
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    assertion_basis: Mapped[str] = mapped_column(
        String(32), nullable=False, default=AssertionBasis.AI_INTERPRETATION.value
    )
    affected_fields: Mapped[list | None] = mapped_column(JSONBType, nullable=True)
    affected_workflows: Mapped[list | None] = mapped_column(JSONBType, nullable=True)
    regression_risk: Mapped[str] = mapped_column(String(16), nullable=False, default=Priority.LOW.value)

    # Model self-reported confidence and the retrieval score that surfaced the
    # candidate rule. Both are shown to the reviewer; neither is authoritative.
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    retrieval_score: Mapped[float | None] = mapped_column(Float, nullable=True)

    review_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=ReviewStatus.PENDING_REVIEW.value, index=True
    )
    reviewed_by_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    review_notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    model_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    provenance: Mapped[str] = mapped_column(String(32), nullable=False, default="LIVE_GEMINI")

    __table_args__ = (
        UniqueConstraint("change_id", "business_rule_id", name="uq_impact_change_rule"),
        Index("ix_impact_status_priority", "review_status", "priority"),
    )

    change = relationship("RegulatoryChange", back_populates="impact_mappings")
    business_rule = relationship("BusinessRule", back_populates="impact_mappings")
    test_cases = relationship("TestCase", back_populates="impact_mapping")


__all__ = ["BusinessRule", "ImpactMapping"]
