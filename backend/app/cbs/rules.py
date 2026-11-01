"""Deterministic business-rule engine for the simulated CBS.

SIMULATION. These rules encode a plausible Indian core banking ruleset so that
generated compliance tests have something real to assert against. They are NOT
a statement of what regulation requires -- the mapping from regulation to rule
is produced by the impact engine and reviewed by a human.

Every rejection carries a stable machine code (`RejectionCode`) so a generated
test can assert on behaviour rather than on prose.
"""
from __future__ import annotations

import random
import re
import string
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import StrEnum

# --- Rule parameters -------------------------------------------------------
# Periodic KYC updation interval by risk category, in years.
KYC_PERIODICITY_YEARS: dict[str, int] = {"LOW": 10, "MEDIUM": 8, "HIGH": 2}

# Minimum count of verified Officially Valid Documents by KYC depth.
MIN_VERIFIED_OVD: dict[str, int] = {"CDD": 1, "EDD": 2, "PERIODIC_UPDATION": 1, "RE_KYC": 1}

ACCEPTED_OVD_TYPES: frozenset[str] = frozenset(
    {
        "PASSPORT",
        "DRIVING_LICENCE",
        "VOTER_ID",
        "AADHAAR",
        "NREGA_JOB_CARD",
        "NPR_LETTER",
        "PAN",  # identity only; not accepted as proof of address
    }
)
# PAN proves identity but not address, so it alone cannot complete CDD.
ADDRESS_CAPABLE_OVD: frozenset[str] = ACCEPTED_OVD_TYPES - {"PAN"}

# Product-level caps. Small accounts carry reduced limits.
PRODUCT_LIMITS: dict[str, dict[str, Decimal]] = {
    "SB-REG": {
        "per_txn_limit": Decimal("50000"),
        "daily_debit_limit": Decimal("100000"),
        "minimum_balance": Decimal("1000"),
        "max_balance": Decimal("0"),  # 0 == uncapped
    },
    "SB-SMALL": {
        "per_txn_limit": Decimal("10000"),
        "daily_debit_limit": Decimal("10000"),
        "minimum_balance": Decimal("0"),
        "max_balance": Decimal("50000"),
    },
    "SB-BSBDA": {
        "per_txn_limit": Decimal("25000"),
        "daily_debit_limit": Decimal("50000"),
        "minimum_balance": Decimal("0"),
        "max_balance": Decimal("0"),
    },
    "CA-REG": {
        "per_txn_limit": Decimal("500000"),
        "daily_debit_limit": Decimal("2000000"),
        "minimum_balance": Decimal("10000"),
        "max_balance": Decimal("0"),
    },
}

# Transactions at or above this value are flagged for AML review.
AML_CASH_THRESHOLD = Decimal("1000000")

# Restrictions that block debits / credits respectively.
DEBIT_BLOCKING_RESTRICTIONS: frozenset[str] = frozenset(
    {"DEBIT_FREEZE", "TOTAL_FREEZE", "KYC_NON_COMPLIANT", "COURT_ORDER"}
)
CREDIT_BLOCKING_RESTRICTIONS: frozenset[str] = frozenset({"TOTAL_FREEZE", "COURT_ORDER"})

OPERABLE_ACCOUNT_STATUSES: frozenset[str] = frozenset({"ACTIVE"})


class RejectionCode(StrEnum):
    """Stable codes a generated test can assert against."""

    MISSING_MANDATORY_FIELD = "MISSING_MANDATORY_FIELD"
    INVALID_PAN_FORMAT = "INVALID_PAN_FORMAT"
    INVALID_MOBILE_FORMAT = "INVALID_MOBILE_FORMAT"
    INVALID_PINCODE_FORMAT = "INVALID_PINCODE_FORMAT"
    INVALID_DATE_OF_BIRTH = "INVALID_DATE_OF_BIRTH"
    CUSTOMER_UNDERAGE = "CUSTOMER_UNDERAGE"
    DUPLICATE_CUSTOMER = "DUPLICATE_CUSTOMER"
    KYC_NOT_VERIFIED = "KYC_NOT_VERIFIED"
    KYC_EXPIRED = "KYC_EXPIRED"
    INSUFFICIENT_OVD = "INSUFFICIENT_OVD"
    UNACCEPTED_OVD_TYPE = "UNACCEPTED_OVD_TYPE"
    NO_ADDRESS_PROOF = "NO_ADDRESS_PROOF"
    EXPIRED_DOCUMENT = "EXPIRED_DOCUMENT"
    EDD_REQUIRED = "EDD_REQUIRED"
    CUSTOMER_NOT_ACTIVE = "CUSTOMER_NOT_ACTIVE"
    ACCOUNT_NOT_ACTIVE = "ACCOUNT_NOT_ACTIVE"
    ACCOUNT_RESTRICTED = "ACCOUNT_RESTRICTED"
    INSUFFICIENT_BALANCE = "INSUFFICIENT_BALANCE"
    MINIMUM_BALANCE_BREACH = "MINIMUM_BALANCE_BREACH"
    PER_TXN_LIMIT_EXCEEDED = "PER_TXN_LIMIT_EXCEEDED"
    DAILY_LIMIT_EXCEEDED = "DAILY_LIMIT_EXCEEDED"
    MAX_BALANCE_EXCEEDED = "MAX_BALANCE_EXCEEDED"
    INVALID_AMOUNT = "INVALID_AMOUNT"
    SELF_APPROVAL_FORBIDDEN = "SELF_APPROVAL_FORBIDDEN"
    TASK_ALREADY_DECIDED = "TASK_ALREADY_DECIDED"
    UNKNOWN_PRODUCT = "UNKNOWN_PRODUCT"


@dataclass
class RuleViolation:
    code: RejectionCode
    message: str
    # CBS rule this violation belongs to, e.g. "CBS-010".
    rule_code: str
    field: str | None = None


@dataclass
class RuleResult:
    """Outcome of evaluating a rule set. `ok` is False if any violation exists."""

    violations: list[RuleViolation] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.violations

    @property
    def first(self) -> RuleViolation | None:
        return self.violations[0] if self.violations else None

    def add(self, code: RejectionCode, message: str, rule_code: str, field_name: str | None = None) -> None:
        self.violations.append(
            RuleViolation(code=code, message=message, rule_code=rule_code, field=field_name)
        )

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "violations": [
                {
                    "code": str(v.code),
                    "message": v.message,
                    "rule_code": v.rule_code,
                    "field": v.field,
                }
                for v in self.violations
            ],
            "warnings": self.warnings,
        }


# --- Format validators -----------------------------------------------------
# PAN: five letters, four digits, one letter.
PAN_RE = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")
# Indian mobile: 10 digits starting 6-9, optionally +91 prefixed.
MOBILE_RE = re.compile(r"^(?:\+?91)?[6-9][0-9]{9}$")
PINCODE_RE = re.compile(r"^[1-9][0-9]{5}$")


def validate_pan(value: str | None) -> bool:
    return bool(value and PAN_RE.match(value.strip().upper()))


def validate_mobile(value: str | None) -> bool:
    return bool(value and MOBILE_RE.match(value.strip().replace(" ", "")))


def validate_pincode(value: str | None) -> bool:
    return bool(value and PINCODE_RE.match(value.strip()))


# --- CBS-001 / CBS-002 / CBS-003 : customer creation -----------------------
MANDATORY_CUSTOMER_FIELDS = ("first_name", "last_name", "date_of_birth", "mobile")


def validate_customer_payload(payload: dict) -> RuleResult:
    """CBS-001: mandatory fields and format validation for a new customer."""
    result = RuleResult()

    for field_name in MANDATORY_CUSTOMER_FIELDS:
        value = payload.get(field_name)
        if value is None or (isinstance(value, str) and not value.strip()):
            result.add(
                RejectionCode.MISSING_MANDATORY_FIELD,
                f"{field_name} is mandatory for individual customer creation",
                "CBS-001",
                field_name,
            )

    if payload.get("pan") and not validate_pan(str(payload["pan"])):
        result.add(
            RejectionCode.INVALID_PAN_FORMAT,
            "PAN must match five letters, four digits, then one letter",
            "CBS-001",
            "pan",
        )
    if payload.get("mobile") and not validate_mobile(str(payload["mobile"])):
        result.add(
            RejectionCode.INVALID_MOBILE_FORMAT,
            "Mobile must be a 10-digit Indian number beginning 6-9",
            "CBS-001",
            "mobile",
        )
    if payload.get("pincode") and not validate_pincode(str(payload["pincode"])):
        result.add(
            RejectionCode.INVALID_PINCODE_FORMAT,
            "Pincode must be six digits and must not start with zero",
            "CBS-001",
            "pincode",
        )

    dob = payload.get("date_of_birth")
    if dob:
        parsed = _coerce_date(dob)
        if parsed is None:
            result.add(
                RejectionCode.INVALID_DATE_OF_BIRTH,
                "date_of_birth must be an ISO date (YYYY-MM-DD)",
                "CBS-001",
                "date_of_birth",
            )
        elif parsed > date.today():
            result.add(
                RejectionCode.INVALID_DATE_OF_BIRTH,
                "date_of_birth cannot be in the future",
                "CBS-001",
                "date_of_birth",
            )
        elif _age_years(parsed) < 18 and payload.get("customer_type", "INDIVIDUAL") == "INDIVIDUAL":
            # A minor account is a different product with a guardian mandate,
            # which this simulator does not model.
            result.add(
                RejectionCode.CUSTOMER_UNDERAGE,
                "Customer must be at least 18 years old for a regular individual account",
                "CBS-001",
                "date_of_birth",
            )
    return result


def _coerce_date(value) -> date | None:  # noqa: ANN001
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value).strip()[:10])
    except (ValueError, TypeError):
        return None


def _age_years(dob: date) -> int:
    today = date.today()
    return today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))


def generate_ucic() -> str:
    """CBS-003: allocate a UCIC. Format UC + 10 digits (simulated)."""
    return "UC" + "".join(random.choices(string.digits, k=10))


def generate_account_number(branch_code: str = "SIM001") -> str:
    """Simulated 14-char account number: branch prefix + 8 digits."""
    return f"{branch_code}{''.join(random.choices(string.digits, k=8))}"


def generate_txn_reference() -> str:
    return "TXN" + "".join(random.choices(string.digits + string.ascii_uppercase, k=12))


# --- CBS-004 / CBS-006 : KYC verification ----------------------------------
def validate_kyc_documents(documents: list[dict] | None, kyc_type: str = "CDD") -> RuleResult:
    """CBS-004: validate submitted OVDs for a CDD / EDD / periodic cycle."""
    result = RuleResult()
    docs = documents or []
    required = MIN_VERIFIED_OVD.get(kyc_type, 1)

    if not docs:
        result.add(
            RejectionCode.INSUFFICIENT_OVD,
            f"{kyc_type} requires at least {required} verified officially valid document(s)",
            "CBS-004",
            "documents",
        )
        return result

    verified_count = 0
    has_address_proof = False
    today = date.today()

    for index, doc in enumerate(docs):
        doc_type = str(doc.get("doc_type", "")).upper().strip()
        if doc_type not in ACCEPTED_OVD_TYPES:
            result.add(
                RejectionCode.UNACCEPTED_OVD_TYPE,
                f"Document type {doc_type or '(blank)'} is not an accepted officially valid document",
                "CBS-004",
                f"documents[{index}].doc_type",
            )
            continue

        expiry = _coerce_date(doc.get("expiry_date")) if doc.get("expiry_date") else None
        if expiry is not None and expiry < today:
            result.add(
                RejectionCode.EXPIRED_DOCUMENT,
                f"{doc_type} expired on {expiry.isoformat()}",
                "CBS-004",
                f"documents[{index}].expiry_date",
            )
            continue

        if doc.get("verified") is True:
            verified_count += 1
            if doc_type in ADDRESS_CAPABLE_OVD:
                has_address_proof = True

    if verified_count < required:
        result.add(
            RejectionCode.INSUFFICIENT_OVD,
            f"{kyc_type} requires {required} verified document(s); found {verified_count}",
            "CBS-004",
            "documents",
        )
    if verified_count and not has_address_proof:
        result.add(
            RejectionCode.NO_ADDRESS_PROOF,
            "At least one verified document must also evidence address; PAN alone is insufficient",
            "CBS-004",
            "documents",
        )
    return result


def next_kyc_due_date(risk_category: str, from_date: date | None = None) -> date:
    """CBS-006 / CBS-015: periodic updation due date derived from risk."""
    base = from_date or date.today()
    years = KYC_PERIODICITY_YEARS.get(risk_category.upper(), 10)
    try:
        return base.replace(year=base.year + years)
    except ValueError:
        # 29 February in a non-leap target year.
        return base.replace(year=base.year + years, day=28)


# --- CBS-015 : risk classification ----------------------------------------
HIGH_RISK_COUNTRIES: frozenset[str] = frozenset({"KP", "IR", "MM"})


def classify_customer_risk(
    *,
    is_pep: bool,
    country: str = "IN",
    customer_type: str = "INDIVIDUAL",
    expected_monthly_turnover: Decimal | float | int | None = None,
) -> tuple[str, list[str]]:
    """CBS-015: derive LOW / MEDIUM / HIGH plus the reasons behind it."""
    reasons: list[str] = []
    score = 0

    if is_pep:
        score += 3
        reasons.append("Customer is a politically exposed person")
    if (country or "IN").upper() in HIGH_RISK_COUNTRIES:
        score += 3
        reasons.append(f"Country {country} is on the high-risk list")
    if customer_type.upper() not in ("INDIVIDUAL",):
        score += 1
        reasons.append(f"Non-individual customer type {customer_type}")
    if expected_monthly_turnover is not None:
        turnover = Decimal(str(expected_monthly_turnover))
        if turnover >= Decimal("1000000"):
            score += 2
            reasons.append("Expected monthly turnover at or above INR 10,00,000")
        elif turnover >= Decimal("200000"):
            score += 1
            reasons.append("Expected monthly turnover at or above INR 2,00,000")

    if score >= 3:
        category = "HIGH"
    elif score >= 1:
        category = "MEDIUM"
    else:
        category = "LOW"
        reasons.append("No elevated risk indicators present")
    return category, reasons


def requires_enhanced_due_diligence(risk_category: str, is_pep: bool) -> bool:
    return risk_category.upper() == "HIGH" or is_pep


# --- CBS-007 / CBS-008 / CBS-014 : accounts --------------------------------
def validate_account_opening(
    *,
    customer_status: str,
    kyc_status: str,
    next_kyc_due: date | None,
    account_type: str,
    product_code: str,
    risk_category: str,
    is_pep: bool,
    edd_completed: bool,
) -> RuleResult:
    """CBS-007 / CBS-008: preconditions for opening an account."""
    result = RuleResult()
    rule = "CBS-008" if account_type.upper() == "CURRENT" else "CBS-007"

    if product_code not in PRODUCT_LIMITS:
        result.add(
            RejectionCode.UNKNOWN_PRODUCT,
            f"Unknown product code {product_code}",
            rule,
            "product_code",
        )
    if customer_status.upper() != "ACTIVE":
        result.add(
            RejectionCode.CUSTOMER_NOT_ACTIVE,
            f"Customer must be ACTIVE to open an account; current status is {customer_status}",
            rule,
            "customer_status",
        )
    if kyc_status.upper() != "VERIFIED":
        result.add(
            RejectionCode.KYC_NOT_VERIFIED,
            f"KYC must be VERIFIED before account opening; current status is {kyc_status}",
            rule,
            "kyc_status",
        )
    elif next_kyc_due is not None and next_kyc_due < date.today():
        result.add(
            RejectionCode.KYC_EXPIRED,
            f"KYC lapsed on {next_kyc_due.isoformat()}; periodic updation is due",
            rule,
            "next_kyc_due_date",
        )

    if requires_enhanced_due_diligence(risk_category, is_pep) and not edd_completed:
        result.add(
            RejectionCode.EDD_REQUIRED,
            "Enhanced due diligence must be completed for a high-risk or PEP customer",
            rule,
            "risk_category",
        )
    return result


def resolve_account_operability(
    *, account_status: str, restrictions: list[str] | None, kyc_status: str
) -> dict:
    """CBS-014: effective operability of an account."""
    restriction_set = set(restrictions or [])
    debit_blocked = bool(restriction_set & DEBIT_BLOCKING_RESTRICTIONS)
    credit_blocked = bool(restriction_set & CREDIT_BLOCKING_RESTRICTIONS)
    status_ok = account_status.upper() in OPERABLE_ACCOUNT_STATUSES

    return {
        "account_status": account_status,
        "kyc_status": kyc_status,
        "restrictions": sorted(restriction_set),
        "status_operable": status_ok,
        "debit_allowed": status_ok and not debit_blocked,
        "credit_allowed": status_ok and not credit_blocked,
        "blocking_restrictions": sorted(
            restriction_set & (DEBIT_BLOCKING_RESTRICTIONS | CREDIT_BLOCKING_RESTRICTIONS)
        ),
    }


# --- CBS-010 / CBS-011 / CBS-012 : transactions ----------------------------
def validate_transaction(
    *,
    txn_type: str,
    amount: Decimal,
    account_status: str,
    restrictions: list[str] | None,
    balance: Decimal,
    minimum_balance: Decimal,
    per_txn_limit: Decimal,
    daily_debit_limit: Decimal,
    debits_today: Decimal,
    max_balance: Decimal = Decimal("0"),
    fee: Decimal = Decimal("0"),
) -> RuleResult:
    """CBS-010 / CBS-011 / CBS-012: full pre-posting validation."""
    result = RuleResult()
    is_debit = txn_type.upper() in ("DEBIT", "TRANSFER")
    rule = "CBS-010" if is_debit else "CBS-011"
    restriction_set = set(restrictions or [])

    if amount <= 0:
        result.add(
            RejectionCode.INVALID_AMOUNT, "Transaction amount must be greater than zero", rule, "amount"
        )
        return result  # further checks are meaningless

    if account_status.upper() not in OPERABLE_ACCOUNT_STATUSES:
        result.add(
            RejectionCode.ACCOUNT_NOT_ACTIVE,
            f"Account status {account_status} does not permit transactions",
            "CBS-014",
            "account_status",
        )

    blocking = restriction_set & (
        DEBIT_BLOCKING_RESTRICTIONS if is_debit else CREDIT_BLOCKING_RESTRICTIONS
    )
    if blocking:
        result.add(
            RejectionCode.ACCOUNT_RESTRICTED,
            f"Account carries blocking restriction(s): {', '.join(sorted(blocking))}",
            "CBS-009",
            "restrictions",
        )

    if is_debit:
        if amount > per_txn_limit:
            result.add(
                RejectionCode.PER_TXN_LIMIT_EXCEEDED,
                f"Amount {amount} exceeds the per-transaction limit of {per_txn_limit}",
                "CBS-012",
                "amount",
            )
        if debits_today + amount > daily_debit_limit:
            result.add(
                RejectionCode.DAILY_LIMIT_EXCEEDED,
                f"Amount {amount} would take today's debits to {debits_today + amount}, "
                f"above the daily limit of {daily_debit_limit}",
                "CBS-012",
                "amount",
            )
        total_outflow = amount + fee
        if total_outflow > balance:
            result.add(
                RejectionCode.INSUFFICIENT_BALANCE,
                f"Balance {balance} is insufficient for a debit of {total_outflow} including fees",
                "CBS-010",
                "amount",
            )
        elif balance - total_outflow < minimum_balance:
            result.add(
                RejectionCode.MINIMUM_BALANCE_BREACH,
                f"Debit of {total_outflow} would leave {balance - total_outflow}, "
                f"below the minimum balance of {minimum_balance}",
                "CBS-010",
                "amount",
            )
    else:
        # Small accounts carry a balance ceiling; 0 means uncapped.
        if max_balance > 0 and balance + amount > max_balance:
            result.add(
                RejectionCode.MAX_BALANCE_EXCEEDED,
                f"Credit of {amount} would take the balance to {balance + amount}, "
                f"above the product ceiling of {max_balance}",
                "CBS-012",
                "amount",
            )
    return result


def evaluate_aml_monitoring(
    *, amount: Decimal, channel: str, risk_category: str
) -> tuple[bool, str | None]:
    """Transaction monitoring: flag for AML review. Simulated thresholds."""
    if amount >= AML_CASH_THRESHOLD:
        return True, f"Amount at or above the INR {AML_CASH_THRESHOLD:,} monitoring threshold"
    if risk_category.upper() == "HIGH" and amount >= Decimal("200000"):
        return True, "High-risk customer transacting at or above INR 2,00,000"
    if channel.upper() == "CASH" and amount >= Decimal("500000"):
        return True, "Cash transaction at or above INR 5,00,000"
    return False, None


def compute_transaction_fee(*, txn_type: str, amount: Decimal, channel: str) -> Decimal:
    """Simulated fee schedule, deliberately simple and deterministic."""
    if txn_type.upper() == "CREDIT":
        return Decimal("0")
    if channel.upper() in ("NEFT", "RTGS", "IMPS"):
        if amount <= Decimal("10000"):
            return Decimal("2.50")
        if amount <= Decimal("100000"):
            return Decimal("5.00")
        return Decimal("15.00")
    return Decimal("0")


def product_limits(product_code: str) -> dict[str, Decimal]:
    return PRODUCT_LIMITS.get(product_code, PRODUCT_LIMITS["SB-REG"])


# --- CBS-013 : maker-checker ----------------------------------------------
def validate_checker_decision(*, maker_id: str, checker_id: str, current_status: str) -> RuleResult:
    """CBS-013: dual control. The checker must differ from the maker."""
    result = RuleResult()
    if current_status.upper() != "PENDING":
        result.add(
            RejectionCode.TASK_ALREADY_DECIDED,
            f"Task is already {current_status} and cannot be decided again",
            "CBS-013",
            "status",
        )
    if maker_id and checker_id and maker_id.strip().lower() == checker_id.strip().lower():
        result.add(
            RejectionCode.SELF_APPROVAL_FORBIDDEN,
            "The checker must be a different user from the maker",
            "CBS-013",
            "checker_id",
        )
    return result


def days_until(target: date | None) -> int | None:
    if target is None:
        return None
    return (target - date.today()).days


def kyc_is_due(next_due: date | None, within_days: int = 0) -> bool:
    remaining = days_until(next_due)
    return remaining is not None and remaining <= within_days


__all__ = [
    "RejectionCode",
    "RuleResult",
    "RuleViolation",
    "KYC_PERIODICITY_YEARS",
    "PRODUCT_LIMITS",
    "ACCEPTED_OVD_TYPES",
    "AML_CASH_THRESHOLD",
    "validate_customer_payload",
    "validate_kyc_documents",
    "validate_account_opening",
    "validate_transaction",
    "validate_checker_decision",
    "resolve_account_operability",
    "classify_customer_risk",
    "requires_enhanced_due_diligence",
    "next_kyc_due_date",
    "evaluate_aml_monitoring",
    "compute_transaction_fee",
    "product_limits",
    "generate_ucic",
    "generate_account_number",
    "generate_txn_reference",
    "validate_pan",
    "validate_mobile",
    "validate_pincode",
    "kyc_is_due",
    "days_until",
]
