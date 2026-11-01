"""Simulated core banking services.

Each public function corresponds to one or more catalogued CBS rules and writes
an audit event tagged with the rule code it exercised, which is what lets a
compliance test produce real evidence.

SIMULATION ONLY. Nothing here contacts a banking system.
"""
from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.cbs import rules as R
from app.models.cbs import (
    CbsAccount,
    CbsAuditEvent,
    CbsCustomer,
    CbsKycRecord,
    CbsMakerCheckerTask,
    CbsTransaction,
)


class CbsRuleError(Exception):
    """Raised when a CBS rule rejects an operation.

    Carries the structured RuleResult so the API layer can return machine
    assertable codes rather than prose.
    """

    def __init__(self, result: R.RuleResult, *, http_status: int = 422):
        first = result.first
        super().__init__(first.message if first else "CBS rule violation")
        self.result = result
        self.http_status = http_status

    def to_payload(self) -> dict:
        first = self.result.first
        return {
            "error": "cbs_rule_violation",
            # Primary code, so a test can assert one stable field.
            "rejection_code": str(first.code) if first else None,
            "rule_code": first.rule_code if first else None,
            "message": first.message if first else "CBS rule violation",
            "violations": self.result.to_dict()["violations"],
        }


def _now() -> datetime:
    return datetime.now(UTC)


def _dec(value, field_name: str = "amount") -> Decimal:  # noqa: ANN001
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} is not a valid decimal number") from exc


def _audit(
    db: Session,
    *,
    entity_type: str,
    entity_id: uuid.UUID | None,
    action: str,
    rule_code: str | None,
    actor: str = "system",
    before: dict | None = None,
    after: dict | None = None,
    message: str | None = None,
) -> None:
    db.add(
        CbsAuditEvent(
            entity_type=entity_type,
            entity_id=entity_id,
            action=action,
            actor=actor,
            rule_code=rule_code,
            before_state=before,
            after_state=after,
            message=message,
        )
    )


# ---------------------------------------------------------------------------
# Customers -- CBS-001, CBS-002, CBS-003
# ---------------------------------------------------------------------------
def find_duplicate_customer(db: Session, payload: dict) -> CbsCustomer | None:
    """CBS-002: match on PAN, or on mobile + date of birth."""
    pan = (payload.get("pan") or "").strip().upper() or None
    mobile = (payload.get("mobile") or "").strip() or None
    dob = R._coerce_date(payload.get("date_of_birth")) if payload.get("date_of_birth") else None

    if pan:
        hit = db.execute(select(CbsCustomer).where(CbsCustomer.pan == pan)).scalars().first()
        if hit:
            return hit
    if mobile and dob:
        hit = (
            db.execute(
                select(CbsCustomer).where(
                    CbsCustomer.mobile == mobile, CbsCustomer.date_of_birth == dob
                )
            )
            .scalars()
            .first()
        )
        if hit:
            return hit
    return None


def create_customer(db: Session, payload: dict, *, actor: str = "maker@sim") -> CbsCustomer:
    """CBS-001 + CBS-002 + CBS-003.

    The customer is created in PENDING_APPROVAL with a maker-checker task, so
    dual control (CBS-013) governs activation.
    """
    result = R.validate_customer_payload(payload)
    if not result.ok:
        raise CbsRuleError(result)

    duplicate = find_duplicate_customer(db, payload)
    if duplicate is not None:
        dup = R.RuleResult()
        dup.add(
            R.RejectionCode.DUPLICATE_CUSTOMER,
            f"An existing customer already matches these details (UCIC {duplicate.ucic})",
            "CBS-002",
            "pan",
        )
        raise CbsRuleError(dup, http_status=409)

    risk_category, risk_reasons = R.classify_customer_risk(
        is_pep=bool(payload.get("is_pep", False)),
        country=payload.get("country", "IN") or "IN",
        customer_type=payload.get("customer_type", "INDIVIDUAL"),
        expected_monthly_turnover=payload.get("expected_monthly_turnover"),
    )

    customer = CbsCustomer(
        ucic=R.generate_ucic(),  # CBS-003
        first_name=payload["first_name"].strip(),
        last_name=payload["last_name"].strip(),
        date_of_birth=R._coerce_date(payload.get("date_of_birth")),
        pan=(payload.get("pan") or "").strip().upper() or None,
        aadhaar_last4=(payload.get("aadhaar_last4") or "").strip() or None,
        mobile=(payload.get("mobile") or "").strip() or None,
        email=(payload.get("email") or "").strip() or None,
        address_line1=payload.get("address_line1"),
        city=payload.get("city"),
        state=payload.get("state"),
        pincode=(payload.get("pincode") or "").strip() or None,
        country=(payload.get("country") or "IN").upper(),
        customer_type=payload.get("customer_type", "INDIVIDUAL"),
        customer_segment=payload.get("customer_segment", "RETAIL"),
        risk_category=risk_category,
        is_pep=bool(payload.get("is_pep", False)),
        kyc_status="NOT_STARTED",
        status="PENDING_APPROVAL",
        is_test_data=True,
    )
    db.add(customer)
    db.flush()

    db.add(
        CbsMakerCheckerTask(
            operation="CUSTOMER_CREATE",
            entity_type="CbsCustomer",
            entity_id=customer.id,
            payload={"ucic": customer.ucic, "name": f"{customer.first_name} {customer.last_name}"},
            status="PENDING",
            maker_id=actor,
            maker_remarks="Customer created via simulator, awaiting checker approval",
        )
    )
    _audit(
        db,
        entity_type="CbsCustomer",
        entity_id=customer.id,
        action="CREATE",
        rule_code="CBS-001",
        actor=actor,
        after={"ucic": customer.ucic, "status": customer.status, "risk_category": risk_category},
        message=f"Risk classified {risk_category}: {'; '.join(risk_reasons)}",
    )
    db.commit()
    db.refresh(customer)
    return customer


def get_customer(db: Session, customer_id: uuid.UUID) -> CbsCustomer | None:
    return db.get(CbsCustomer, customer_id)


def get_customer_by_ucic(db: Session, ucic: str) -> CbsCustomer | None:
    return db.execute(select(CbsCustomer).where(CbsCustomer.ucic == ucic)).scalars().first()


def search_customers(
    db: Session,
    *,
    query: str | None = None,
    kyc_status: str | None = None,
    status: str | None = None,
    risk_category: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[CbsCustomer], int]:
    stmt = select(CbsCustomer)
    count_stmt = select(func.count()).select_from(CbsCustomer)

    if query:
        pattern = f"%{query.strip()}%"
        condition = (
            CbsCustomer.first_name.ilike(pattern)
            | CbsCustomer.last_name.ilike(pattern)
            | CbsCustomer.ucic.ilike(pattern)
            | CbsCustomer.pan.ilike(pattern)
            | CbsCustomer.mobile.ilike(pattern)
        )
        stmt = stmt.where(condition)
        count_stmt = count_stmt.where(condition)
    for column, value in (
        (CbsCustomer.kyc_status, kyc_status),
        (CbsCustomer.status, status),
        (CbsCustomer.risk_category, risk_category),
    ):
        if value:
            stmt = stmt.where(column == value)
            count_stmt = count_stmt.where(column == value)

    total = db.execute(count_stmt).scalar() or 0
    rows = (
        db.execute(stmt.order_by(CbsCustomer.created_at.desc()).limit(limit).offset(offset))
        .scalars()
        .all()
    )
    return list(rows), total


def classify_risk(
    db: Session, customer_id: uuid.UUID, *, actor: str = "system"
) -> dict:
    """CBS-015: recompute the risk category and the periodic KYC due date."""
    customer = db.get(CbsCustomer, customer_id)
    if customer is None:
        raise LookupError("Customer not found")

    before = {"risk_category": customer.risk_category, "next_kyc_due_date": str(customer.next_kyc_due_date)}
    category, reasons = R.classify_customer_risk(
        is_pep=customer.is_pep,
        country=customer.country,
        customer_type=customer.customer_type,
    )
    customer.risk_category = category
    # Re-base the due date only once KYC is actually verified.
    if customer.kyc_verified_at is not None:
        customer.next_kyc_due_date = R.next_kyc_due_date(
            category, customer.kyc_verified_at.date()
        )

    _audit(
        db,
        entity_type="CbsCustomer",
        entity_id=customer.id,
        action="RISK_CLASSIFY",
        rule_code="CBS-015",
        actor=actor,
        before=before,
        after={"risk_category": category, "next_kyc_due_date": str(customer.next_kyc_due_date)},
        message="; ".join(reasons),
    )
    db.commit()
    return {
        "customer_id": str(customer.id),
        "ucic": customer.ucic,
        "risk_category": category,
        "reasons": reasons,
        "requires_edd": R.requires_enhanced_due_diligence(category, customer.is_pep),
        "periodicity_years": R.KYC_PERIODICITY_YEARS.get(category, 10),
        "next_kyc_due_date": customer.next_kyc_due_date.isoformat()
        if customer.next_kyc_due_date
        else None,
    }


# ---------------------------------------------------------------------------
# KYC -- CBS-004, CBS-005, CBS-006
# ---------------------------------------------------------------------------
def get_kyc(db: Session, customer_id: uuid.UUID) -> dict:
    """CBS-005: retrieve current and historical KYC information."""
    customer = db.get(CbsCustomer, customer_id)
    if customer is None:
        raise LookupError("Customer not found")
    records = (
        db.execute(
            select(CbsKycRecord)
            .where(CbsKycRecord.customer_id == customer_id)
            .order_by(CbsKycRecord.version.desc())
        )
        .scalars()
        .all()
    )
    return {
        "customer_id": str(customer.id),
        "ucic": customer.ucic,
        "kyc_status": customer.kyc_status,
        "risk_category": customer.risk_category,
        "kyc_verified_at": customer.kyc_verified_at.isoformat() if customer.kyc_verified_at else None,
        "next_kyc_due_date": customer.next_kyc_due_date.isoformat()
        if customer.next_kyc_due_date
        else None,
        "kyc_due_now": R.kyc_is_due(customer.next_kyc_due_date),
        "periodicity_years": R.KYC_PERIODICITY_YEARS.get(customer.risk_category, 10),
        "requires_edd": R.requires_enhanced_due_diligence(customer.risk_category, customer.is_pep),
        "records": [
            {
                "id": str(r.id),
                "version": r.version,
                "kyc_type": r.kyc_type,
                "status": r.status,
                "verified_document_count": r.verified_document_count,
                "documents": r.documents or [],
                "verification_mode": r.verification_mode,
                "remarks": r.remarks,
                "verified_at": r.verified_at.isoformat() if r.verified_at else None,
                "due_date": r.due_date.isoformat() if r.due_date else None,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in records
        ],
    }


def verify_kyc(
    db: Session,
    customer_id: uuid.UUID,
    payload: dict,
    *,
    actor: str = "maker@sim",
) -> CbsKycRecord:
    """CBS-004: Customer Due Diligence against submitted OVDs."""
    customer = db.get(CbsCustomer, customer_id)
    if customer is None:
        raise LookupError("Customer not found")

    kyc_type = (payload.get("kyc_type") or "CDD").upper()
    documents = payload.get("documents") or []
    # High-risk / PEP customers need the deeper EDD document set.
    if R.requires_enhanced_due_diligence(customer.risk_category, customer.is_pep) and kyc_type == "CDD":
        kyc_type = "EDD"

    result = R.validate_kyc_documents(documents, kyc_type)
    verified_count = sum(1 for d in documents if d.get("verified") is True)

    next_version = (
        db.execute(
            select(func.coalesce(func.max(CbsKycRecord.version), 0)).where(
                CbsKycRecord.customer_id == customer_id
            )
        ).scalar()
        or 0
    ) + 1

    record = CbsKycRecord(
        customer_id=customer.id,
        kyc_type=kyc_type,
        status="VERIFIED" if result.ok else "REJECTED",
        documents=documents,
        verified_document_count=verified_count,
        risk_category=customer.risk_category,
        verification_mode=payload.get("verification_mode", "IN_PERSON"),
        remarks=payload.get("remarks")
        or ("Verification successful" if result.ok else result.first.message),
        version=next_version,
    )

    before = {"kyc_status": customer.kyc_status}
    if result.ok:
        now = _now()
        record.verified_at = now
        record.due_date = R.next_kyc_due_date(customer.risk_category, now.date())
        customer.kyc_status = "VERIFIED"
        customer.kyc_verified_at = now
        customer.next_kyc_due_date = record.due_date
    else:
        customer.kyc_status = "REJECTED"

    db.add(record)
    _audit(
        db,
        entity_type="CbsKycRecord",
        entity_id=customer.id,
        action="KYC_VERIFY",
        rule_code="CBS-004",
        actor=actor,
        before=before,
        after={"kyc_status": customer.kyc_status, "kyc_type": kyc_type, "version": next_version},
        message=record.remarks,
    )
    db.commit()
    db.refresh(record)

    if not result.ok:
        # Persist the rejected attempt for evidence, then surface the violation.
        raise CbsRuleError(result)
    return record


def update_kyc(
    db: Session, customer_id: uuid.UUID, payload: dict, *, actor: str = "maker@sim"
) -> CbsKycRecord:
    """CBS-006: periodic updation / re-KYC. Never overwrites a prior version."""
    customer = db.get(CbsCustomer, customer_id)
    if customer is None:
        raise LookupError("Customer not found")

    kyc_type = (payload.get("kyc_type") or "PERIODIC_UPDATION").upper()
    documents = payload.get("documents") or []
    self_declaration = bool(payload.get("self_declaration", False))

    next_version = (
        db.execute(
            select(func.coalesce(func.max(CbsKycRecord.version), 0)).where(
                CbsKycRecord.customer_id == customer_id
            )
        ).scalar()
        or 0
    ) + 1

    # A self-declaration of "no change" is accepted for periodic updation; a
    # change of details requires documents to be re-validated.
    if self_declaration and not documents:
        result = R.RuleResult()
        result.warnings.append("Accepted on self-declaration of no change in KYC details")
    else:
        result = R.validate_kyc_documents(documents, kyc_type)

    record = CbsKycRecord(
        customer_id=customer.id,
        kyc_type=kyc_type,
        status="VERIFIED" if result.ok else "REJECTED",
        documents=documents,
        verified_document_count=sum(1 for d in documents if d.get("verified") is True),
        risk_category=customer.risk_category,
        verification_mode=payload.get(
            "verification_mode", "SELF_DECLARATION" if self_declaration else "IN_PERSON"
        ),
        remarks=payload.get("remarks")
        or ("Periodic updation recorded" if result.ok else result.first.message),
        version=next_version,
    )

    before = {"kyc_status": customer.kyc_status, "next_kyc_due_date": str(customer.next_kyc_due_date)}
    if result.ok:
        now = _now()
        record.verified_at = now
        record.due_date = R.next_kyc_due_date(customer.risk_category, now.date())
        customer.kyc_status = "VERIFIED"
        customer.kyc_verified_at = now
        customer.next_kyc_due_date = record.due_date

    db.add(record)
    _audit(
        db,
        entity_type="CbsKycRecord",
        entity_id=customer.id,
        action="KYC_UPDATE",
        rule_code="CBS-006",
        actor=actor,
        before=before,
        after={
            "kyc_status": customer.kyc_status,
            "version": next_version,
            "next_kyc_due_date": str(customer.next_kyc_due_date),
        },
        message=record.remarks,
    )
    db.commit()
    db.refresh(record)
    if not result.ok:
        raise CbsRuleError(result)
    return record


# ---------------------------------------------------------------------------
# Accounts -- CBS-007, CBS-008, CBS-009, CBS-014
# ---------------------------------------------------------------------------
def open_account(db: Session, payload: dict, *, actor: str = "maker@sim") -> CbsAccount:
    """CBS-007 / CBS-008: open a savings or current account."""
    customer_id = payload.get("customer_id")
    customer = db.get(CbsCustomer, uuid.UUID(str(customer_id))) if customer_id else None
    if customer is None:
        raise LookupError("Customer not found")

    account_type = (payload.get("account_type") or "SAVINGS").upper()
    default_product = "CA-REG" if account_type == "CURRENT" else "SB-REG"
    product_code = payload.get("product_code") or default_product

    result = R.validate_account_opening(
        customer_status=customer.status,
        kyc_status=customer.kyc_status,
        next_kyc_due=customer.next_kyc_due_date,
        account_type=account_type,
        product_code=product_code,
        risk_category=customer.risk_category,
        is_pep=customer.is_pep,
        edd_completed=_has_completed_edd(db, customer.id),
    )
    if not result.ok:
        raise CbsRuleError(result)

    limits = R.product_limits(product_code)
    account = CbsAccount(
        account_number=R.generate_account_number(payload.get("branch_code", "SIM001")),
        customer_id=customer.id,
        account_type=account_type,
        product_code=product_code,
        currency=payload.get("currency", "INR"),
        balance=Decimal(str(payload.get("initial_deposit", 0) or 0)),
        status="PENDING_APPROVAL",
        restrictions=[],
        daily_debit_limit=limits["daily_debit_limit"],
        per_txn_limit=limits["per_txn_limit"],
        minimum_balance=limits["minimum_balance"],
        opened_on=date.today(),
        branch_code=payload.get("branch_code", "SIM001"),
        is_test_data=True,
    )
    db.add(account)
    db.flush()

    db.add(
        CbsMakerCheckerTask(
            operation="ACCOUNT_OPEN",
            entity_type="CbsAccount",
            entity_id=account.id,
            payload={
                "account_number": account.account_number,
                "account_type": account_type,
                "product_code": product_code,
            },
            status="PENDING",
            maker_id=actor,
            maker_remarks="Account opened via simulator, awaiting checker approval",
        )
    )
    _audit(
        db,
        entity_type="CbsAccount",
        entity_id=account.id,
        action="OPEN",
        rule_code="CBS-008" if account_type == "CURRENT" else "CBS-007",
        actor=actor,
        after={"account_number": account.account_number, "status": account.status},
    )
    db.commit()
    db.refresh(account)
    return account


def _has_completed_edd(db: Session, customer_id: uuid.UUID) -> bool:
    return (
        db.execute(
            select(CbsKycRecord.id).where(
                CbsKycRecord.customer_id == customer_id,
                CbsKycRecord.kyc_type == "EDD",
                CbsKycRecord.status == "VERIFIED",
            )
        ).first()
        is not None
    )


def get_account(db: Session, account_id: uuid.UUID) -> CbsAccount | None:
    return db.get(CbsAccount, account_id)


def list_accounts(
    db: Session,
    *,
    customer_id: uuid.UUID | None = None,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[CbsAccount], int]:
    stmt = select(CbsAccount)
    count_stmt = select(func.count()).select_from(CbsAccount)
    if customer_id:
        stmt = stmt.where(CbsAccount.customer_id == customer_id)
        count_stmt = count_stmt.where(CbsAccount.customer_id == customer_id)
    if status:
        stmt = stmt.where(CbsAccount.status == status)
        count_stmt = count_stmt.where(CbsAccount.status == status)
    total = db.execute(count_stmt).scalar() or 0
    rows = (
        db.execute(stmt.order_by(CbsAccount.created_at.desc()).limit(limit).offset(offset))
        .scalars()
        .all()
    )
    return list(rows), total


def account_status(db: Session, account_id: uuid.UUID) -> dict:
    """CBS-014: effective operability of an account."""
    account = db.get(CbsAccount, account_id)
    if account is None:
        raise LookupError("Account not found")
    customer = db.get(CbsCustomer, account.customer_id)
    operability = R.resolve_account_operability(
        account_status=account.status,
        restrictions=account.restrictions,
        kyc_status=customer.kyc_status if customer else "UNKNOWN",
    )
    return {
        "account_id": str(account.id),
        "account_number": account.account_number,
        "balance": str(account.balance),
        **operability,
    }


def apply_restrictions(
    db: Session,
    account_id: uuid.UUID,
    *,
    add: list[str] | None = None,
    remove: list[str] | None = None,
    reason: str | None = None,
    actor: str = "maker@sim",
) -> CbsAccount:
    """CBS-009: apply or lift restriction codes."""
    account = db.get(CbsAccount, account_id)
    if account is None:
        raise LookupError("Account not found")

    current = set(account.restrictions or [])
    before = sorted(current)
    current |= {r.strip().upper() for r in (add or []) if r.strip()}
    current -= {r.strip().upper() for r in (remove or []) if r.strip()}
    # Reassign rather than mutate: JSON columns are not change-tracked in place.
    account.restrictions = sorted(current)

    _audit(
        db,
        entity_type="CbsAccount",
        entity_id=account.id,
        action="RESTRICTION_CHANGE",
        rule_code="CBS-009",
        actor=actor,
        before={"restrictions": before},
        after={"restrictions": account.restrictions},
        message=reason,
    )
    db.commit()
    db.refresh(account)
    return account


def activate_account(db: Session, account_id: uuid.UUID, *, actor: str = "checker@sim") -> CbsAccount:
    account = db.get(CbsAccount, account_id)
    if account is None:
        raise LookupError("Account not found")
    before = {"status": account.status}
    account.status = "ACTIVE"
    _audit(
        db,
        entity_type="CbsAccount",
        entity_id=account.id,
        action="ACTIVATE",
        rule_code="CBS-014",
        actor=actor,
        before=before,
        after={"status": "ACTIVE"},
    )
    db.commit()
    db.refresh(account)
    return account


# ---------------------------------------------------------------------------
# Transactions -- CBS-010, CBS-011, CBS-012
# ---------------------------------------------------------------------------
def _debits_today(db: Session, account_id: uuid.UUID) -> Decimal:
    total = db.execute(
        select(func.coalesce(func.sum(CbsTransaction.amount), 0)).where(
            CbsTransaction.account_id == account_id,
            CbsTransaction.txn_type.in_(["DEBIT", "TRANSFER"]),
            CbsTransaction.status == "POSTED",
            CbsTransaction.value_date == date.today(),
        )
    ).scalar()
    return Decimal(str(total or 0))


def create_transaction(db: Session, payload: dict, *, actor: str = "maker@sim") -> CbsTransaction:
    """CBS-010 / CBS-011 / CBS-012 with AML monitoring.

    A rejected transaction is still persisted with status REJECTED so that the
    rejection itself is auditable evidence.
    """
    account_id = payload.get("account_id")
    account = db.get(CbsAccount, uuid.UUID(str(account_id))) if account_id else None
    if account is None:
        raise LookupError("Account not found")

    txn_type = (payload.get("txn_type") or "DEBIT").upper()
    channel = (payload.get("channel") or "BRANCH").upper()
    amount = _dec(payload.get("amount", 0))
    customer = db.get(CbsCustomer, account.customer_id)
    limits = R.product_limits(account.product_code)

    fee = R.compute_transaction_fee(txn_type=txn_type, amount=amount, channel=channel)
    result = R.validate_transaction(
        txn_type=txn_type,
        amount=amount,
        account_status=account.status,
        restrictions=account.restrictions,
        balance=Decimal(str(account.balance)),
        minimum_balance=Decimal(str(account.minimum_balance)),
        per_txn_limit=Decimal(str(account.per_txn_limit)),
        daily_debit_limit=Decimal(str(account.daily_debit_limit)),
        debits_today=_debits_today(db, account.id),
        max_balance=limits.get("max_balance", Decimal("0")),
        fee=fee,
    )

    aml_flagged, aml_reason = R.evaluate_aml_monitoring(
        amount=amount,
        channel=channel,
        risk_category=customer.risk_category if customer else "LOW",
    )

    txn = CbsTransaction(
        reference_number=R.generate_txn_reference(),
        account_id=account.id,
        txn_type=txn_type,
        channel=channel,
        amount=amount,
        currency=payload.get("currency", "INR"),
        fee=fee,
        narration=payload.get("narration"),
        status="POSTED" if result.ok else "REJECTED",
        aml_flagged=aml_flagged,
        aml_reason=aml_reason,
        value_date=date.today(),
        is_test_data=True,
    )

    if result.ok:
        balance = Decimal(str(account.balance))
        if txn_type in ("DEBIT", "TRANSFER"):
            balance = balance - amount - fee
        else:
            balance = balance + amount
        account.balance = balance
        txn.balance_after = balance
    else:
        violation = result.first
        txn.rejection_code = str(violation.code)
        txn.rejection_reason = violation.message

    db.add(txn)
    _audit(
        db,
        entity_type="CbsTransaction",
        entity_id=account.id,
        action=f"{txn_type}_{'POSTED' if result.ok else 'REJECTED'}",
        rule_code="CBS-010" if txn_type in ("DEBIT", "TRANSFER") else "CBS-011",
        actor=actor,
        after={
            "reference_number": txn.reference_number,
            "amount": str(amount),
            "status": txn.status,
            "rejection_code": txn.rejection_code,
            "aml_flagged": aml_flagged,
        },
        message=txn.rejection_reason or aml_reason,
    )
    db.commit()
    db.refresh(txn)

    if not result.ok:
        raise CbsRuleError(result)
    return txn


def list_transactions(
    db: Session,
    *,
    account_id: uuid.UUID | None = None,
    status: str | None = None,
    aml_flagged: bool | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[CbsTransaction], int]:
    stmt = select(CbsTransaction)
    count_stmt = select(func.count()).select_from(CbsTransaction)
    if account_id:
        stmt = stmt.where(CbsTransaction.account_id == account_id)
        count_stmt = count_stmt.where(CbsTransaction.account_id == account_id)
    if status:
        stmt = stmt.where(CbsTransaction.status == status)
        count_stmt = count_stmt.where(CbsTransaction.status == status)
    if aml_flagged is not None:
        stmt = stmt.where(CbsTransaction.aml_flagged == aml_flagged)
        count_stmt = count_stmt.where(CbsTransaction.aml_flagged == aml_flagged)
    total = db.execute(count_stmt).scalar() or 0
    rows = (
        db.execute(stmt.order_by(CbsTransaction.created_at.desc()).limit(limit).offset(offset))
        .scalars()
        .all()
    )
    return list(rows), total


# ---------------------------------------------------------------------------
# Maker-checker -- CBS-013
# ---------------------------------------------------------------------------
def list_maker_checker_tasks(
    db: Session, *, status: str | None = "PENDING", limit: int = 50, offset: int = 0
) -> tuple[list[CbsMakerCheckerTask], int]:
    stmt = select(CbsMakerCheckerTask)
    count_stmt = select(func.count()).select_from(CbsMakerCheckerTask)
    if status:
        stmt = stmt.where(CbsMakerCheckerTask.status == status)
        count_stmt = count_stmt.where(CbsMakerCheckerTask.status == status)
    total = db.execute(count_stmt).scalar() or 0
    rows = (
        db.execute(
            stmt.order_by(CbsMakerCheckerTask.created_at.desc()).limit(limit).offset(offset)
        )
        .scalars()
        .all()
    )
    return list(rows), total


def decide_maker_checker(
    db: Session,
    task_id: uuid.UUID,
    *,
    decision: str,
    checker_id: str,
    remarks: str | None = None,
) -> CbsMakerCheckerTask:
    """CBS-013: checker approves or rejects, and the change is applied."""
    task = db.get(CbsMakerCheckerTask, task_id)
    if task is None:
        raise LookupError("Maker-checker task not found")

    result = R.validate_checker_decision(
        maker_id=task.maker_id, checker_id=checker_id, current_status=task.status
    )
    if not result.ok:
        raise CbsRuleError(result)

    decision_upper = decision.upper()
    if decision_upper not in ("APPROVE", "REJECT"):
        raise ValueError("decision must be APPROVE or REJECT")

    task.status = "APPROVED" if decision_upper == "APPROVE" else "REJECTED"
    task.checker_id = checker_id
    task.checker_remarks = remarks
    task.decided_at = _now()

    # Apply the proposed state change only on approval.
    if task.status == "APPROVED" and task.entity_id is not None:
        if task.operation == "CUSTOMER_CREATE":
            customer = db.get(CbsCustomer, task.entity_id)
            if customer is not None:
                customer.status = "ACTIVE"
        elif task.operation == "ACCOUNT_OPEN":
            account = db.get(CbsAccount, task.entity_id)
            if account is not None:
                account.status = "ACTIVE"
    elif task.status == "REJECTED" and task.entity_id is not None:
        if task.operation == "CUSTOMER_CREATE":
            customer = db.get(CbsCustomer, task.entity_id)
            if customer is not None:
                customer.status = "REJECTED"
        elif task.operation == "ACCOUNT_OPEN":
            account = db.get(CbsAccount, task.entity_id)
            if account is not None:
                account.status = "CLOSED"

    _audit(
        db,
        entity_type="CbsMakerCheckerTask",
        entity_id=task.entity_id,
        action=f"CHECKER_{task.status}",
        rule_code="CBS-013",
        actor=checker_id,
        after={"operation": task.operation, "status": task.status},
        message=remarks,
    )
    db.commit()
    db.refresh(task)
    return task


def list_audit_events(
    db: Session,
    *,
    entity_id: uuid.UUID | None = None,
    rule_code: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[CbsAuditEvent], int]:
    stmt = select(CbsAuditEvent)
    count_stmt = select(func.count()).select_from(CbsAuditEvent)
    if entity_id:
        stmt = stmt.where(CbsAuditEvent.entity_id == entity_id)
        count_stmt = count_stmt.where(CbsAuditEvent.entity_id == entity_id)
    if rule_code:
        stmt = stmt.where(CbsAuditEvent.rule_code == rule_code)
        count_stmt = count_stmt.where(CbsAuditEvent.rule_code == rule_code)
    total = db.execute(count_stmt).scalar() or 0
    rows = (
        db.execute(stmt.order_by(CbsAuditEvent.created_at.desc()).limit(limit).offset(offset))
        .scalars()
        .all()
    )
    return list(rows), total
