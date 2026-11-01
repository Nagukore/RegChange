"""Test lifecycle tables: cases, approvals, executions and evidence.

Traceability chain preserved here:
    clause -> change -> impact_mapping -> test_case -> test_execution -> evidence
Every test case therefore carries both `impact_mapping_id` and a denormalised
`source_clause_reference` so the UI can show provenance without a deep join.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
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
    AutomationType,
    BankingModule,
    EvidenceType,
    ExecutionDriver,
    ExecutionStatus,
    Priority,
    ReviewStatus,
    TestCategory,
)
from app.models.types import GUID, JSONBType, UTCDateTime


class TestCase(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "test_cases"

    # Domain class, not a pytest test class. pytest matches on the leading
    # "Test" in the class name, so opt out explicitly.
    __test__ = False

    # Human-facing stable identifier, e.g. "TC-KYC-0042".
    test_code: Mapped[str] = mapped_column(String(48), unique=True, nullable=False, index=True)

    impact_mapping_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("impact_mappings.id", ondelete="SET NULL"), nullable=True, index=True
    )
    change_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("regulatory_changes.id", ondelete="SET NULL"), nullable=True, index=True
    )
    business_rule_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("business_rules.id", ondelete="SET NULL"), nullable=True, index=True
    )

    title: Mapped[str] = mapped_column(String(512), nullable=False)
    objective: Mapped[str | None] = mapped_column(Text, nullable=True)
    banking_module: Mapped[str] = mapped_column(
        String(48), nullable=False, default=BankingModule.CROSS_CUTTING.value, index=True
    )
    category: Mapped[str] = mapped_column(
        String(32), nullable=False, default=TestCategory.POSITIVE.value, index=True
    )
    priority: Mapped[str] = mapped_column(String(16), nullable=False, default=Priority.MEDIUM.value)
    automation_type: Mapped[str] = mapped_column(
        String(16), nullable=False, default=AutomationType.API.value
    )

    preconditions: Mapped[list | None] = mapped_column(JSONBType, nullable=True)
    # Structured payload the executor feeds to the CBS simulator.
    test_data: Mapped[dict | None] = mapped_column(JSONBType, nullable=True)
    # Ordered steps: [{"step": 1, "action": "...", "expected": "..."}]
    steps: Mapped[list | None] = mapped_column(JSONBType, nullable=True)
    expected_result: Mapped[str] = mapped_column(Text, nullable=False)

    # Machine-checkable assertion contract, validated by the execution agent.
    # e.g. {"driver":"PYTEST_API","method":"POST","path":"/cbs/customers",
    #       "expect_status":422,"expect_json_contains":{"code":"KYC_REQUIRED"}}
    execution_spec: Mapped[dict | None] = mapped_column(JSONBType, nullable=True)
    is_state_changing: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Denormalised provenance for fast traceability display.
    source_clause_reference: Mapped[str | None] = mapped_column(String(256), nullable=True)
    source_quote: Mapped[str | None] = mapped_column(Text, nullable=True)
    requirement_code: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)

    approval_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=ReviewStatus.PENDING_REVIEW.value, index=True
    )
    model_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    provenance: Mapped[str] = mapped_column(String(32), nullable=False, default="LIVE_GEMINI")

    __table_args__ = (Index("ix_testcase_module_status", "banking_module", "approval_status"),)

    impact_mapping = relationship("ImpactMapping", back_populates="test_cases")
    business_rule = relationship("BusinessRule", back_populates="test_cases")
    change = relationship("RegulatoryChange")
    approvals = relationship(
        "TestApproval", back_populates="test_case", cascade="all, delete-orphan"
    )
    executions = relationship(
        "TestExecution", back_populates="test_case", cascade="all, delete-orphan"
    )


class TestApproval(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Append-only human decision log. Execution requires an APPROVED record."""

    __tablename__ = "test_approvals"

    # Domain class, not a pytest test class. pytest matches on the leading
    # "Test" in the class name, so opt out explicitly.
    __test__ = False

    test_case_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("test_cases.id", ondelete="CASCADE"), nullable=False, index=True
    )
    decision: Mapped[str] = mapped_column(String(32), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_by_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    decided_by_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    decided_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)

    test_case = relationship("TestCase", back_populates="approvals")


class TestExecutionRun(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A batch execution of one or more approved test cases."""

    __tablename__ = "test_execution_runs"

    # Domain class, not a pytest test class. pytest matches on the leading
    # "Test" in the class name, so opt out explicitly.
    __test__ = False

    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=ExecutionStatus.QUEUED.value, index=True
    )
    driver: Mapped[str] = mapped_column(
        String(32), nullable=False, default=ExecutionDriver.PYTEST_API.value
    )
    target_base_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    passed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    blocked: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    errored: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    triggered_by_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    # Populated for TinyFish Browser runs so evidence can be traced to a session.
    tinyfish_session_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    executions = relationship(
        "TestExecution", back_populates="run", cascade="all, delete-orphan"
    )


class TestExecution(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "test_executions"

    # Domain class, not a pytest test class. pytest matches on the leading
    # "Test" in the class name, so opt out explicitly.
    __test__ = False

    run_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("test_execution_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    test_case_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("test_cases.id", ondelete="CASCADE"), nullable=False, index=True
    )

    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=ExecutionStatus.QUEUED.value, index=True
    )
    driver: Mapped[str] = mapped_column(
        String(32), nullable=False, default=ExecutionDriver.PYTEST_API.value
    )
    expected_result: Mapped[str | None] = mapped_column(Text, nullable=True)
    actual_result: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Per-assertion outcomes so a reviewer sees exactly what diverged.
    assertions: Mapped[list | None] = mapped_column(JSONBType, nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    # Snapshot of the approval that authorised this execution.
    approved_by_email: Mapped[str | None] = mapped_column(String(255), nullable=True)

    __table_args__ = (
        UniqueConstraint("run_id", "test_case_id", name="uq_execution_run_case"),
        Index("ix_execution_status_created", "status", "created_at"),
    )

    run = relationship("TestExecutionRun", back_populates="executions")
    test_case = relationship("TestCase", back_populates="executions")
    evidence = relationship(
        "ExecutionEvidence", back_populates="execution", cascade="all, delete-orphan"
    )


class ExecutionEvidence(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "execution_evidence"

    execution_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("test_executions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    evidence_type: Mapped[str] = mapped_column(
        String(32), nullable=False, default=EvidenceType.LOG.value
    )
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    # Relative path under settings.evidence_dir; served read-only by the API.
    file_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    content_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Inline payload for small artefacts (API responses, logs, diffs).
    payload: Mapped[dict | None] = mapped_column(JSONBType, nullable=True)
    text_payload: Mapped[str | None] = mapped_column(Text, nullable=True)
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    captured_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)

    execution = relationship("TestExecution", back_populates="evidence")


__all__ = [
    "TestCase",
    "TestApproval",
    "TestExecutionRun",
    "TestExecution",
    "ExecutionEvidence",
]
