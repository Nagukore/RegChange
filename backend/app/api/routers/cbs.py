"""Simulated core banking API.

Deterministic endpoints the test-execution agent drives. Rule rejections are
returned as HTTP 422 (or 409 for a duplicate) with a stable `rejection_code`,
so a generated negative test asserts on a machine-checkable field.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.cbs import service as svc
from app.cbs.rules import ACCEPTED_OVD_TYPES, KYC_PERIODICITY_YEARS, PRODUCT_LIMITS
from app.cbs.service import CbsRuleError
from app.core.database import get_db
from app.schemas.cbs import (
    AccountCreate,
    AccountListOut,
    AccountOut,
    AuditListOut,
    CustomerCreate,
    CustomerListOut,
    CustomerOut,
    KycRecordOut,
    KycUpdateIn,
    KycVerifyIn,
    MakerCheckerDecisionIn,
    MakerCheckerListOut,
    MakerCheckerOut,
    RestrictionIn,
    TransactionCreate,
    TransactionListOut,
    TransactionOut,
)

router = APIRouter(prefix="/cbs", tags=["core banking simulator"])


def _rule_error_response(exc: CbsRuleError) -> JSONResponse:
    return JSONResponse(status_code=exc.http_status, content=exc.to_payload())


# ---------------------------------------------------------------------------
# Reference data
# ---------------------------------------------------------------------------
@router.get("/reference")
def reference_data() -> dict:
    """Rule parameters the UI and generated tests need to stay in step."""
    return {
        "accepted_ovd_types": sorted(ACCEPTED_OVD_TYPES),
        "kyc_periodicity_years": KYC_PERIODICITY_YEARS,
        "products": {
            code: {k: str(v) for k, v in limits.items()}
            for code, limits in PRODUCT_LIMITS.items()
        },
        "restriction_codes": [
            "DEBIT_FREEZE",
            "CREDIT_FREEZE",
            "TOTAL_FREEZE",
            "KYC_NON_COMPLIANT",
            "COURT_ORDER",
        ],
        "notice": "Simulated core banking environment. No real banking system is contacted.",
    }


# ---------------------------------------------------------------------------
# Customers -- CBS-001, CBS-002, CBS-003, CBS-015
# ---------------------------------------------------------------------------
@router.post("/customers", response_model=CustomerOut, status_code=status.HTTP_201_CREATED)
def create_customer(payload: CustomerCreate, db: Session = Depends(get_db)):
    try:
        return svc.create_customer(db, payload.model_dump(mode="json"))
    except CbsRuleError as exc:
        return _rule_error_response(exc)


@router.get("/customers", response_model=CustomerListOut)
def list_customers(
    q: str | None = Query(default=None, description="Name, UCIC, PAN or mobile"),
    kyc_status: str | None = None,
    account_status: str | None = Query(default=None, alias="status"),
    risk_category: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    items, total = svc.search_customers(
        db,
        query=q,
        kyc_status=kyc_status,
        status=account_status,
        risk_category=risk_category,
        limit=limit,
        offset=offset,
    )
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@router.get("/customers/{customer_id}", response_model=CustomerOut)
def get_customer(customer_id: uuid.UUID, db: Session = Depends(get_db)):
    customer = svc.get_customer(db, customer_id)
    if customer is None:
        raise HTTPException(status_code=404, detail="Customer not found")
    return customer


@router.get("/customers/by-ucic/{ucic}", response_model=CustomerOut)
def get_customer_by_ucic(ucic: str, db: Session = Depends(get_db)):
    customer = svc.get_customer_by_ucic(db, ucic)
    if customer is None:
        raise HTTPException(status_code=404, detail="Customer not found")
    return customer


@router.post("/customers/{customer_id}/risk-classification")
def risk_classification(customer_id: uuid.UUID, db: Session = Depends(get_db)):
    """CBS-015."""
    try:
        return svc.classify_risk(db, customer_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# KYC -- CBS-004, CBS-005, CBS-006
# ---------------------------------------------------------------------------
@router.get("/customers/{customer_id}/kyc")
def get_kyc(customer_id: uuid.UUID, db: Session = Depends(get_db)):
    """CBS-005."""
    try:
        return svc.get_kyc(db, customer_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/customers/{customer_id}/kyc/verify", response_model=KycRecordOut)
def verify_kyc(customer_id: uuid.UUID, payload: KycVerifyIn, db: Session = Depends(get_db)):
    """CBS-004."""
    try:
        return svc.verify_kyc(db, customer_id, payload.model_dump(mode="json"))
    except CbsRuleError as exc:
        return _rule_error_response(exc)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/customers/{customer_id}/kyc/update", response_model=KycRecordOut)
def update_kyc(customer_id: uuid.UUID, payload: KycUpdateIn, db: Session = Depends(get_db)):
    """CBS-006."""
    try:
        return svc.update_kyc(db, customer_id, payload.model_dump(mode="json"))
    except CbsRuleError as exc:
        return _rule_error_response(exc)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# Accounts -- CBS-007, CBS-008, CBS-009, CBS-014
# ---------------------------------------------------------------------------
@router.post("/accounts", response_model=AccountOut, status_code=status.HTTP_201_CREATED)
def open_account(payload: AccountCreate, db: Session = Depends(get_db)):
    """CBS-007 / CBS-008."""
    try:
        return svc.open_account(db, payload.model_dump(mode="json"))
    except CbsRuleError as exc:
        return _rule_error_response(exc)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/accounts", response_model=AccountListOut)
def list_accounts(
    customer_id: uuid.UUID | None = None,
    account_status: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    items, total = svc.list_accounts(
        db, customer_id=customer_id, status=account_status, limit=limit, offset=offset
    )
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@router.get("/accounts/{account_id}", response_model=AccountOut)
def get_account(account_id: uuid.UUID, db: Session = Depends(get_db)):
    account = svc.get_account(db, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="Account not found")
    return account


@router.get("/accounts/{account_id}/status")
def account_status_endpoint(account_id: uuid.UUID, db: Session = Depends(get_db)):
    """CBS-014."""
    try:
        return svc.account_status(db, account_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/accounts/{account_id}/restrictions", response_model=AccountOut)
def apply_restrictions(
    account_id: uuid.UUID, payload: RestrictionIn, db: Session = Depends(get_db)
):
    """CBS-009."""
    try:
        return svc.apply_restrictions(
            db, account_id, add=payload.add, remove=payload.remove, reason=payload.reason
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/accounts/{account_id}/activate", response_model=AccountOut)
def activate_account(account_id: uuid.UUID, db: Session = Depends(get_db)):
    """Convenience endpoint for test setup; normally done via maker-checker."""
    try:
        return svc.activate_account(db, account_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# Transactions -- CBS-010, CBS-011, CBS-012
# ---------------------------------------------------------------------------
@router.post("/transactions", response_model=TransactionOut, status_code=status.HTTP_201_CREATED)
def create_transaction(payload: TransactionCreate, db: Session = Depends(get_db)):
    """CBS-010 / CBS-011 / CBS-012."""
    try:
        return svc.create_transaction(db, payload.model_dump(mode="json"))
    except CbsRuleError as exc:
        return _rule_error_response(exc)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/transactions", response_model=TransactionListOut)
def list_transactions(
    account_id: uuid.UUID | None = None,
    txn_status: str | None = Query(default=None, alias="status"),
    aml_flagged: bool | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    items, total = svc.list_transactions(
        db,
        account_id=account_id,
        status=txn_status,
        aml_flagged=aml_flagged,
        limit=limit,
        offset=offset,
    )
    return {"items": items, "total": total, "limit": limit, "offset": offset}


# ---------------------------------------------------------------------------
# Maker-checker -- CBS-013
# ---------------------------------------------------------------------------
@router.get("/maker-checker", response_model=MakerCheckerListOut)
def list_tasks(
    task_status: str | None = Query(default="PENDING", alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    items, total = svc.list_maker_checker_tasks(
        db, status=task_status, limit=limit, offset=offset
    )
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@router.post("/maker-checker/{task_id}/decide", response_model=MakerCheckerOut)
def decide_task(
    task_id: uuid.UUID, payload: MakerCheckerDecisionIn, db: Session = Depends(get_db)
):
    """CBS-013. Self-approval is rejected."""
    try:
        return svc.decide_maker_checker(
            db,
            task_id,
            decision=payload.decision,
            checker_id=payload.checker_id,
            remarks=payload.remarks,
        )
    except CbsRuleError as exc:
        return _rule_error_response(exc)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/audit", response_model=AuditListOut)
def list_audit(
    entity_id: uuid.UUID | None = None,
    rule_code: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    items, total = svc.list_audit_events(
        db, entity_id=entity_id, rule_code=rule_code, limit=limit, offset=offset
    )
    return {"items": items, "total": total, "limit": limit, "offset": offset}
