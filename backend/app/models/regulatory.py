"""Regulatory intelligence tables.

Versioning rule: a regulatory document is never overwritten. A re-fetch whose
content hash differs creates a NEW row with version = previous + 1, linked via
`supersedes_id`. `is_current` marks the live version of a logical document
(identified by `source_url`).
"""
from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import (
    AssertionBasis,
    BankingModule,
    ChangeType,
    DocumentStatus,
    Priority,
    ReviewStatus,
)
from app.models.types import GUID, JSONBType, UTCDateTime


class RegulatorySource(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A publisher of regulation, e.g. the RBI notifications index."""

    __tablename__ = "regulatory_sources"

    authority: Mapped[str] = mapped_column(String(64), nullable=False, default="RBI", index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    base_url: Mapped[str] = mapped_column(String(1024), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Which regulated-entity types this source governs. Empty list => unverified.
    applicable_institution_types: Mapped[list | None] = mapped_column(JSONBType, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)

    __table_args__ = (UniqueConstraint("authority", "base_url", name="uq_source_authority_url"),)

    documents = relationship("RegulatoryDocument", back_populates="source")


class RegulatoryDocument(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "regulatory_documents"

    source_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("regulatory_sources.id", ondelete="SET NULL"), nullable=True
    )
    discovery_run_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("discovery_runs.id", ondelete="SET NULL"), nullable=True
    )

    title: Mapped[str] = mapped_column(String(1024), nullable=False)
    # Regulator-assigned identifier, e.g. "RBI/2024-25/xx DOR.AML.REC.xx".
    circular_number: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    document_type: Mapped[str] = mapped_column(String(64), nullable=False, default="CIRCULAR")
    source_url: Mapped[str] = mapped_column(String(2048), nullable=False, index=True)
    final_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)

    publication_date: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    effective_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    # Verbatim applicability text from the document (its "Applicability" para).
    applicability_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    applicable_institution_types: Mapped[list | None] = mapped_column(JSONBType, nullable=True)
    banking_domains: Mapped[list | None] = mapped_column(JSONBType, nullable=True)

    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("regulatory_documents.id", ondelete="SET NULL"), nullable=True
    )
    # SHA-256 of the normalised extracted text; drives change detection.
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    http_etag: Mapped[str | None] = mapped_column(String(255), nullable=True)
    http_last_modified: Mapped[str | None] = mapped_column(String(255), nullable=True)

    raw_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_format: Mapped[str] = mapped_column(String(16), nullable=False, default="markdown")
    char_count: Mapped[int | None] = mapped_column(Integer, nullable=True)

    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=DocumentStatus.DISCOVERED.value, index=True
    )
    provenance: Mapped[str] = mapped_column(String(32), nullable=False, default="LIVE_TINYFISH")
    # Populated when the official document could not be retrieved; we record the
    # limitation rather than inventing content.
    access_limitation: Mapped[str | None] = mapped_column(Text, nullable=True)
    search_snippet: Mapped[str | None] = mapped_column(Text, nullable=True)
    fetched_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)

    __table_args__ = (
        UniqueConstraint("source_url", "version", name="uq_document_url_version"),
        Index("ix_documents_current_pubdate", "is_current", "publication_date"),
    )

    source = relationship("RegulatorySource", back_populates="documents")
    discovery_run = relationship("DiscoveryRun", back_populates="documents")
    clauses = relationship(
        "RegulatoryClause", back_populates="document", cascade="all, delete-orphan"
    )
    changes = relationship(
        "RegulatoryChange",
        back_populates="document",
        cascade="all, delete-orphan",
        foreign_keys="RegulatoryChange.document_id",
    )


class RegulatoryClause(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """An addressable fragment of a document, preserving its source reference."""

    __tablename__ = "regulatory_clauses"

    document_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("regulatory_documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # The regulator's own numbering, e.g. "Para 38(a)" or "Section 5.2".
    clause_reference: Mapped[str] = mapped_column(String(128), nullable=False)
    heading: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    # Position within the document, used to show stable ordering in the UI.
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    char_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    char_end: Mapped[int | None] = mapped_column(Integer, nullable=True)
    clause_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    banking_domains: Mapped[list | None] = mapped_column(JSONBType, nullable=True)
    provenance: Mapped[str] = mapped_column(String(32), nullable=False, default="DETERMINISTIC")

    __table_args__ = (
        UniqueConstraint("document_id", "clause_reference", "ordinal", name="uq_clause_doc_ref"),
    )

    document = relationship("RegulatoryDocument", back_populates="clauses")
    changes = relationship("RegulatoryChange", back_populates="clause")


class RegulatoryChange(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A requirement identified by the Analysis Agent, always source-anchored."""

    __tablename__ = "regulatory_changes"

    document_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("regulatory_documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    clause_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("regulatory_clauses.id", ondelete="SET NULL"), nullable=True
    )
    compared_to_document_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("regulatory_documents.id", ondelete="SET NULL"), nullable=True
    )

    # Stable human-facing requirement handle, e.g. "REQ-0007".
    requirement_code: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    requirement_text: Mapped[str] = mapped_column(Text, nullable=False)
    change_type: Mapped[str] = mapped_column(
        String(32), nullable=False, default=ChangeType.NEW_REQUIREMENT.value
    )
    # Mandatory provenance label: explicit requirement vs AI reading vs proposal.
    assertion_basis: Mapped[str] = mapped_column(
        String(32), nullable=False, default=AssertionBasis.AI_INTERPRETATION.value, index=True
    )
    # Verbatim quote from the source document supporting this requirement.
    source_quote: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_reference: Mapped[str | None] = mapped_column(String(256), nullable=True)
    previous_text: Mapped[str | None] = mapped_column(Text, nullable=True)

    applicable_entities: Mapped[list | None] = mapped_column(JSONBType, nullable=True)
    primary_banking_module: Mapped[str] = mapped_column(
        String(48), nullable=False, default=BankingModule.CROSS_CUTTING.value
    )
    effective_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    priority: Mapped[str] = mapped_column(String(16), nullable=False, default=Priority.MEDIUM.value)
    # Model self-reported confidence, 0..1. Never treated as a compliance score.
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)

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
        UniqueConstraint("document_id", "requirement_code", name="uq_change_doc_code"),
        Index("ix_changes_module_priority", "primary_banking_module", "priority"),
    )

    document = relationship(
        "RegulatoryDocument", back_populates="changes", foreign_keys=[document_id]
    )
    clause = relationship("RegulatoryClause", back_populates="changes")
    impact_mappings = relationship(
        "ImpactMapping", back_populates="change", cascade="all, delete-orphan"
    )


__all__ = [
    "RegulatorySource",
    "RegulatoryDocument",
    "RegulatoryClause",
    "RegulatoryChange",
]
