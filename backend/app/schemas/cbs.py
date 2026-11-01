"""Request and response schemas for the simulated CBS API."""
from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# --- Customers -------------------------------------------------------------
class KycDocumentIn(BaseModel):
    doc_type: str = Field(description="PASSPORT, DRIVING_LICENCE, VOTER_ID, AADHAAR, PAN, ...")
    number: str | None = Field(default=None, description="Simulated document number only")
    issue_date: date | None = None
    expiry_date: date | None = None
    verified: bool = False
    # Free-form extras the UI may attach; kept so evidence is complete.
    model_config = ConfigDict(extra="allow")


class CustomerCreate(BaseModel):
    first_name: str
    last_name: str
    date_of_birth: date | None = None
    pan: str | None = None
    aadhaar_last4: str | None = Field(default=None, max_length=4)
    mobile: str | None = None
    email: str | None = None
    address_line1: str | None = None
    city: str | None = None
    state: str | None = None
    pincode: str | None = None
    country: str = "IN"
    customer_type: Literal["INDIVIDUAL", "SOLE_PROPRIETOR", "COMPANY", "TRUST"] = "INDIVIDUAL"
    customer_segment: str = "RETAIL"
    is_pep: bool = False
    expected_monthly_turnover: Decimal | None = None


class CustomerOut(ORMModel):
    id: uuid.UUID
    ucic: str
    first_name: str
    last_name: str
    date_of_birth: date | None
    pan: str | None
    aadhaar_last4: str | None
    mobile: str | None
    email: str | None
    city: str | None
    state: str | None
    pincode: str | None
    country: str
    customer_type: str
    customer_segment: str
    risk_category: str
    is_pep: bool
    kyc_status: str
    kyc_verified_at: datetime | None
    next_kyc_due_date: date | None
    status: str
    is_test_data: bool
    created_at: datetime


class CustomerListOut(BaseModel):
    items: list[CustomerOut]
    total: int
    limit: int
    offset: int


# --- KYC -------------------------------------------------------------------
class KycVerifyIn(BaseModel):
    kyc_type: Literal["CDD", "EDD", "PERIODIC_UPDATION", "RE_KYC"] = "CDD"
    documents: list[KycDocumentIn] = Field(default_factory=list)
    verification_mode: str = "IN_PERSON"
    remarks: str | None = None


class KycUpdateIn(BaseModel):
    kyc_type: Literal["PERIODIC_UPDATION", "RE_KYC", "CDD", "EDD"] = "PERIODIC_UPDATION"
    documents: list[KycDocumentIn] = Field(default_factory=list)
    # A self-declaration of "no change" is sufficient for periodic updation.
    self_declaration: bool = False
    verification_mode: str | None = None
    remarks: str | None = None


class KycRecordOut(ORMModel):
    id: uuid.UUID
    customer_id: uuid.UUID
    kyc_type: str
    status: str
    documents: list[Any] | None
    verified_document_count: int
    risk_category: str
    verification_mode: str | None
    remarks: str | None
    verified_at: datetime | None
    due_date: date | None
    version: int
    created_at: datetime


# --- Accounts --------------------------------------------------------------
class AccountCreate(BaseModel):
    customer_id: uuid.UUID
    account_type: Literal["SAVINGS", "CURRENT"] = "SAVINGS"
    product_code: str | None = Field(
        default=None, description="SB-REG, SB-SMALL, SB-BSBDA or CA-REG"
    )
    currency: str = "INR"
    initial_deposit: Decimal = Decimal("0")
    branch_code: str = "SIM001"


class AccountOut(ORMModel):
    id: uuid.UUID
    account_number: str
    customer_id: uuid.UUID
    account_type: str
    product_code: str
    currency: str
    balance: Decimal
    status: str
    restrictions: list[str] | None
    daily_debit_limit: Decimal
    per_txn_limit: Decimal
    minimum_balance: Decimal
    opened_on: date | None
    branch_code: str
    created_at: datetime


class AccountListOut(BaseModel):
    items: list[AccountOut]
    total: int
    limit: int
    offset: int


class RestrictionIn(BaseModel):
    add: list[str] = Field(default_factory=list)
    remove: list[str] = Field(default_factory=list)
    reason: str | None = None


# --- Transactions ----------------------------------------------------------
class TransactionCreate(BaseModel):
    account_id: uuid.UUID
    txn_type: Literal["DEBIT", "CREDIT", "TRANSFER"] = "DEBIT"
    amount: Decimal
    channel: str = "BRANCH"
    currency: str = "INR"
    narration: str | None = None
    counterparty_account_id: uuid.UUID | None = None


class TransactionOut(ORMModel):
    id: uuid.UUID
    reference_number: str
    account_id: uuid.UUID
    txn_type: str
    channel: str
    amount: Decimal
    currency: str
    fee: Decimal
    balance_after: Decimal | None
    narration: str | None
    status: str
    rejection_code: str | None
    rejection_reason: str | None
    aml_flagged: bool
    aml_reason: str | None
    value_date: date | None
    created_at: datetime


class TransactionListOut(BaseModel):
    items: list[TransactionOut]
    total: int
    limit: int
    offset: int


# --- Maker-checker ---------------------------------------------------------
class MakerCheckerOut(ORMModel):
    id: uuid.UUID
    operation: str
    entity_type: str
    entity_id: uuid.UUID | None
    payload: dict[str, Any] | None
    status: str
    maker_id: str
    checker_id: str | None
    maker_remarks: str | None
    checker_remarks: str | None
    decided_at: datetime | None
    created_at: datetime


class MakerCheckerListOut(BaseModel):
    items: list[MakerCheckerOut]
    total: int
    limit: int
    offset: int


class MakerCheckerDecisionIn(BaseModel):
    decision: Literal["APPROVE", "REJECT"]
    # Must differ from the maker, enforced by CBS-013.
    checker_id: str = Field(min_length=1)
    remarks: str | None = None


class AuditEventOut(ORMModel):
    id: uuid.UUID
    entity_type: str
    entity_id: uuid.UUID | None
    action: str
    actor: str
    rule_code: str | None
    before_state: dict[str, Any] | None
    after_state: dict[str, Any] | None
    message: str | None
    created_at: datetime


class AuditListOut(BaseModel):
    items: list[AuditEventOut]
    total: int
    limit: int
    offset: int
