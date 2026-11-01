"""Operational tables: users, discovery runs, agent activity, notifications."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import ActivityStatus, AgentName, DiscoveryStatus, UserRole
from app.models.types import GUID, JSONBType, UTCDateTime


class User(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False, default=UserRole.VIEWER.value)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class DiscoveryRun(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One invocation of the Regulatory Discovery Agent (TinyFish Search)."""

    __tablename__ = "discovery_runs"

    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=DiscoveryStatus.PENDING.value, index=True
    )
    regulatory_authority: Mapped[str] = mapped_column(String(64), nullable=False, default="RBI")
    institution_type: Mapped[str] = mapped_column(String(64), nullable=False, default="COMMERCIAL_BANK")
    queries: Mapped[list | None] = mapped_column(JSONBType, nullable=True)
    search_domains: Mapped[list | None] = mapped_column(JSONBType, nullable=True)

    documents_found: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    documents_new: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    documents_duplicate: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    provenance: Mapped[str] = mapped_column(String(32), nullable=False, default="LIVE_TINYFISH")
    tinyfish_request_ids: Mapped[list | None] = mapped_column(JSONBType, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    triggered_by_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    documents = relationship("RegulatoryDocument", back_populates="discovery_run")


class AgentActivityLog(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Append-only trace of every agent operation -- the audit backbone."""

    __tablename__ = "agent_activity_logs"

    agent_name: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    operation: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default=ActivityStatus.STARTED.value)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Free-form structured context (never contains credentials; see redaction in
    # app/core/logging.py).
    details: Mapped[dict | None] = mapped_column(JSONBType, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    external_service: Mapped[str | None] = mapped_column(String(64), nullable=True)
    external_request_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # Loose correlation handles -- deliberately not FKs so a log line survives
    # deletion of the entity it describes.
    correlation_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True, index=True)
    entity_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True)

    __table_args__ = (Index("ix_agent_logs_agent_created", "agent_name", "created_at"),)


class Notification(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "notifications"

    title: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    severity: Mapped[str] = mapped_column(String(32), nullable=False, default="INFO")
    category: Mapped[str] = mapped_column(String(64), nullable=False, default="GENERAL")
    is_read: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    entity_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="CASCADE"), nullable=True
    )


__all__ = ["User", "DiscoveryRun", "AgentActivityLog", "Notification", "AgentName"]
