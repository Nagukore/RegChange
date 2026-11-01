"""Simulated core banking system tables.

SIMULATION ONLY. These tables model an Indian CBS closely enough to execute
meaningful compliance tests. Nothing here connects to a real banking system and
no real customer data should ever be loaded.

All CBS tables are prefixed `cbs_` to keep them visibly separate from the
regulatory-intelligence schema.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.types import GUID, JSONBType, UTCDateTime


class CbsCustomer(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "cbs_customers"

    # Unique Customer Identification Code -- one per customer across the bank.
    ucic: Mapped[str] = mapped_column(String(24), unique=True, nullable=False, index=True)
    first_name: Mapped[str] = mapped_column(String(128), nullable=False)
    last_name: Mapped[str] = mapped_column(String(128), nullable=False)
    date_of_birth: Mapped[date | None] = mapped_column(Date, nullable=True)
    # Simulated identifiers only. Never load real PAN/Aadhaar values.
    pan: Mapped[str | None] = mapped_column(String(10), nullable=True, index=True)
    # Last 4 digits only, mirroring the RBI redaction expectation.
    aadhaar_last4: Mapped[str | None] = mapped_column(String(4), nullable=True)
    mobile: Mapped[str | None] = mapped_column(String(16), nullable=True, index=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    address_line1: Mapped[str | None] = mapped_column(String(255), nullable=True)
    city: Mapped[str | None] = mapped_column(String(128), nullable=True)
    state: Mapped[str | None] = mapped_column(String(128), nullable=True)
    pincode: Mapped[str | None] = mapped_column(String(6), nullable=True)
    country: Mapped[str] = mapped_column(String(64), nullable=False, default="IN")

    customer_type: Mapped[str] = mapped_column(String(32), nullable=False, default="INDIVIDUAL")
    customer_segment: Mapped[str] = mapped_column(String(32), nullable=False, default="RETAIL")
    # LOW | MEDIUM | HIGH -- drives CDD depth and periodic-updation interval.
    risk_category: Mapped[str] = mapped_column(String(16), nullable=False, default="LOW", index=True)
    is_pep: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # NOT_STARTED | PENDING | VERIFIED | REJECTED | EXPIRED
    kyc_status: Mapped[str] = mapped_column(
        String(24), nullable=False, default="NOT_STARTED", index=True
    )
    kyc_verified_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    next_kyc_due_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    # DRAFT | PENDING_APPROVAL | ACTIVE | REJECTED | FROZEN | CLOSED
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="DRAFT", index=True)
    # Marks rows created by the test-data seeder so a demo can be reset safely.
    is_test_data: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    __table_args__ = (Index("ix_cbs_cust_dedupe", "pan", "mobile", "date_of_birth"),)

    kyc_records = relationship(
        "CbsKycRecord", back_populates="customer", cascade="all, delete-orphan"
    )
    accounts = relationship("CbsAccount", back_populates="customer", cascade="all, delete-orphan")


class CbsKycRecord(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A Customer Due Diligence record, including periodic re-KYC cycles."""

    __tablename__ = "cbs_kyc_records"

    customer_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("cbs_customers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # CDD | EDD | PERIODIC_UPDATION | RE_KYC
    kyc_type: Mapped[str] = mapped_column(String(32), nullable=False, default="CDD")
    # NOT_STARTED | PENDING | VERIFIED | REJECTED | EXPIRED
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="PENDING", index=True)
    # OVD list: [{"doc_type":"PASSPORT","number":"X1234567","verified":true,...}]
    documents: Mapped[list | None] = mapped_column(JSONBType, nullable=True)
    # Officially Valid Document count that passed validation.
    verified_document_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    risk_category: Mapped[str] = mapped_column(String(16), nullable=False, default="LOW")
    verification_mode: Mapped[str | None] = mapped_column(String(32), nullable=True)
    remarks: Mapped[str | None] = mapped_column(Text, nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    customer = relationship("CbsCustomer", back_populates="kyc_records")


class CbsAccount(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "cbs_accounts"

    account_number: Mapped[str] = mapped_column(String(20), unique=True, nullable=False, index=True)
    customer_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("cbs_customers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # SAVINGS | CURRENT
    account_type: Mapped[str] = mapped_column(String(24), nullable=False, default="SAVINGS")
    # Small accounts (BSBDA-small) carry regulatory balance/turnover caps.
    product_code: Mapped[str] = mapped_column(String(32), nullable=False, default="SB-REG")
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="INR")
    balance: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False, default=0)
    # ACTIVE | INACTIVE | DORMANT | FROZEN | CLOSED | PENDING_APPROVAL
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="PENDING_APPROVAL", index=True)
    # Restriction codes, e.g. ["DEBIT_FREEZE","KYC_NON_COMPLIANT"].
    restrictions: Mapped[list | None] = mapped_column(JSONBType, nullable=True)
    daily_debit_limit: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False, default=100000)
    per_txn_limit: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False, default=50000)
    minimum_balance: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False, default=0)
    opened_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    branch_code: Mapped[str] = mapped_column(String(16), nullable=False, default="SIM001")
    is_test_data: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    customer = relationship("CbsCustomer", back_populates="accounts")
    # cbs_transactions has two FKs to this table (account_id and
    # counterparty_account_id), so the join column must be explicit.
    transactions = relationship(
        "CbsTransaction",
        back_populates="account",
        cascade="all, delete-orphan",
        foreign_keys="CbsTransaction.account_id",
    )


class CbsTransaction(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "cbs_transactions"

    reference_number: Mapped[str] = mapped_column(String(32), unique=True, nullable=False, index=True)
    account_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("cbs_accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    counterparty_account_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("cbs_accounts.id", ondelete="SET NULL"), nullable=True
    )
    # DEBIT | CREDIT | TRANSFER
    txn_type: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    channel: Mapped[str] = mapped_column(String(24), nullable=False, default="BRANCH")
    amount: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="INR")
    fee: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False, default=0)
    balance_after: Mapped[float | None] = mapped_column(Numeric(18, 2), nullable=True)
    narration: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # POSTED | REJECTED | PENDING_APPROVAL | REVERSED
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="POSTED", index=True)
    rejection_code: Mapped[str | None] = mapped_column(String(48), nullable=True)
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Set when the txn trips an AML monitoring threshold.
    aml_flagged: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    aml_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    value_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    is_test_data: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    account = relationship(
        "CbsAccount", back_populates="transactions", foreign_keys=[account_id]
    )


class CbsMakerCheckerTask(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Dual-control workflow: a maker proposes, a different checker decides."""

    __tablename__ = "cbs_maker_checker_tasks"

    # CUSTOMER_CREATE | ACCOUNT_OPEN | KYC_UPDATE | TXN_APPROVE | RESTRICTION_APPLY
    operation: Mapped[str] = mapped_column(String(48), nullable=False, index=True)
    entity_type: Mapped[str] = mapped_column(String(48), nullable=False)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True, index=True)
    # The proposed state change, applied verbatim on approval.
    payload: Mapped[dict | None] = mapped_column(JSONBType, nullable=True)
    # PENDING | APPROVED | REJECTED
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="PENDING", index=True)
    maker_id: Mapped[str] = mapped_column(String(64), nullable=False)
    checker_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    maker_remarks: Mapped[str | None] = mapped_column(Text, nullable=True)
    checker_remarks: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)

    __table_args__ = (
        UniqueConstraint("operation", "entity_id", "status", name="uq_mc_open_task"),
    )


class CbsAuditEvent(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Immutable CBS audit trail, queried by compliance evidence tests."""

    __tablename__ = "cbs_audit_events"

    entity_type: Mapped[str] = mapped_column(String(48), nullable=False, index=True)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    actor: Mapped[str] = mapped_column(String(64), nullable=False, default="system")
    before_state: Mapped[dict | None] = mapped_column(JSONBType, nullable=True)
    after_state: Mapped[dict | None] = mapped_column(JSONBType, nullable=True)
    # CBS rule code this event exercises, e.g. "CBS-013".
    rule_code: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)


__all__ = [
    "CbsCustomer",
    "CbsKycRecord",
    "CbsAccount",
    "CbsTransaction",
    "CbsMakerCheckerTask",
    "CbsAuditEvent",
]
