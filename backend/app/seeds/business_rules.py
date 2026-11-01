"""The CBS business-rule catalogue.

This is the authoritative list of rules the simulated core banking system
implements. The impact engine may ONLY map a regulatory requirement onto a rule
code that appears here -- `app/agents/impact_agent.py` drops anything else, so
the model cannot invent a CBS rule ID.

Each entry declares the API endpoint and UI route it governs, which is what
makes a generated test executable rather than merely descriptive.
"""
from __future__ import annotations

from typing import TypedDict

from app.models.enums import BankingModule


class RuleSeed(TypedDict):
    rule_code: str
    name: str
    description: str
    banking_module: str
    api_endpoint: str | None
    ui_route: str | None
    affected_fields: list[str]
    keywords: list[str]


BUSINESS_RULES: list[RuleSeed] = [
    {
        "rule_code": "CBS-001",
        "name": "Create individual customer",
        "description": (
            "Creates an individual customer (party) record capturing name, date of birth, "
            "address and contact details. Mandatory-field validation and format checks for "
            "PAN, mobile and pincode are applied before the record is persisted in DRAFT."
        ),
        "banking_module": BankingModule.CUSTOMER_ONBOARDING.value,
        "api_endpoint": "POST /api/cbs/customers",
        "ui_route": "/simulator/customers/new",
        "affected_fields": [
            "first_name", "last_name", "date_of_birth", "pan", "aadhaar_last4",
            "mobile", "address_line1", "city", "state", "pincode", "customer_type",
        ],
        "keywords": [
            "customer", "individual", "onboarding", "account opening", "party",
            "identification", "proof of identity", "proof of address", "ovd",
            "officially valid document", "name", "address", "date of birth",
        ],
    },
    {
        "rule_code": "CBS-002",
        "name": "Validate duplicate customer",
        "description": (
            "Detects an existing customer before a new record is created, matching on PAN, "
            "or on the mobile plus date-of-birth combination. A match blocks creation so one "
            "customer cannot hold multiple identities in the bank."
        ),
        "banking_module": BankingModule.CUSTOMER_ONBOARDING.value,
        "api_endpoint": "POST /api/cbs/customers",
        "ui_route": "/simulator/customers",
        "affected_fields": ["pan", "mobile", "date_of_birth", "ucic"],
        "keywords": [
            "duplicate", "unique", "de-duplication", "existing customer", "single identifier",
            "multiple accounts", "same customer", "ucic", "one customer one identifier",
        ],
    },
    {
        "rule_code": "CBS-003",
        "name": "Assign UCIC",
        "description": (
            "Assigns a Unique Customer Identification Code on customer creation. Exactly one "
            "UCIC exists per customer across the bank and it is immutable once issued."
        ),
        "banking_module": BankingModule.CUSTOMER_ONBOARDING.value,
        "api_endpoint": "POST /api/cbs/customers",
        "ui_route": "/simulator/customers",
        "affected_fields": ["ucic"],
        "keywords": [
            "ucic", "unique customer identification code", "single customer view",
            "customer identifier", "unique identifier", "one ucic",
        ],
    },
    {
        "rule_code": "CBS-004",
        "name": "Verify customer identity",
        "description": (
            "Performs Customer Due Diligence by validating submitted Officially Valid "
            "Documents, recording the verification mode and setting KYC status to VERIFIED "
            "only when the required number of valid documents is present and unexpired."
        ),
        "banking_module": BankingModule.KYC_AML.value,
        "api_endpoint": "POST /api/cbs/customers/{customer_id}/kyc/verify",
        "ui_route": "/simulator/kyc",
        "affected_fields": [
            "kyc_status", "documents", "verified_document_count", "verification_mode",
            "kyc_verified_at",
        ],
        "keywords": [
            "customer due diligence", "cdd", "verification", "identity", "ovd",
            "officially valid document", "passport", "aadhaar", "voter id", "driving licence",
            "video kyc", "v-cip", "authentication", "verify",
        ],
    },
    {
        "rule_code": "CBS-005",
        "name": "Retrieve existing KYC information",
        "description": (
            "Returns the current and historical KYC records for a customer, including document "
            "status, risk category, verification dates and the next periodic-updation due date."
        ),
        "banking_module": BankingModule.KYC_AML.value,
        "api_endpoint": "GET /api/cbs/customers/{customer_id}/kyc",
        "ui_route": "/simulator/kyc",
        "affected_fields": ["kyc_status", "documents", "next_kyc_due_date", "risk_category"],
        "keywords": [
            "existing kyc", "retrieve", "kyc records", "ckyc", "central kyc registry",
            "kyc identifier", "fetch kyc", "existing customer kyc",
        ],
    },
    {
        "rule_code": "CBS-006",
        "name": "Update KYC information",
        "description": (
            "Handles periodic KYC updation and re-KYC. Creates a new versioned KYC record, "
            "recomputes the next due date from the customer risk category and never overwrites "
            "a prior KYC version."
        ),
        "banking_module": BankingModule.KYC_AML.value,
        "api_endpoint": "POST /api/cbs/customers/{customer_id}/kyc/update",
        "ui_route": "/simulator/kyc",
        "affected_fields": [
            "kyc_status", "documents", "next_kyc_due_date", "version", "kyc_type",
        ],
        "keywords": [
            "periodic updation", "re-kyc", "update kyc", "refresh", "ten years", "eight years",
            "two years", "periodicity", "self-declaration", "no change in kyc",
            "fresh kyc", "due date",
        ],
    },
    {
        "rule_code": "CBS-007",
        "name": "Open savings account",
        "description": (
            "Opens a savings account for an existing customer. KYC must be VERIFIED and the "
            "customer ACTIVE; the account is created in PENDING_APPROVAL awaiting dual control."
        ),
        "banking_module": BankingModule.ACCOUNT_MANAGEMENT.value,
        "api_endpoint": "POST /api/cbs/accounts",
        "ui_route": "/simulator/accounts/new",
        "affected_fields": [
            "account_number", "account_type", "product_code", "status", "minimum_balance",
            "branch_code",
        ],
        "keywords": [
            "savings account", "sb account", "open account", "bsbda", "small account",
            "account opening", "deposit account",
        ],
    },
    {
        "rule_code": "CBS-008",
        "name": "Open current account",
        "description": (
            "Opens a current account. Requires VERIFIED KYC and, for non-individual or "
            "high-risk customers, additional due-diligence evidence before approval."
        ),
        "banking_module": BankingModule.ACCOUNT_MANAGEMENT.value,
        "api_endpoint": "POST /api/cbs/accounts",
        "ui_route": "/simulator/accounts/new",
        "affected_fields": ["account_number", "account_type", "product_code", "status"],
        "keywords": [
            "current account", "ca", "open account", "business account", "non-individual",
            "proprietorship", "account opening",
        ],
    },
    {
        "rule_code": "CBS-009",
        "name": "Apply account restrictions",
        "description": (
            "Applies or lifts restriction codes on an account, for example DEBIT_FREEZE or "
            "KYC_NON_COMPLIANT. Restrictions are enforced by the transaction validation rules."
        ),
        "banking_module": BankingModule.ACCOUNT_MANAGEMENT.value,
        "api_endpoint": "POST /api/cbs/accounts/{account_id}/restrictions",
        "ui_route": "/simulator/accounts",
        "affected_fields": ["restrictions", "status"],
        "keywords": [
            "restriction", "freeze", "debit freeze", "partial freeze", "block", "suspend",
            "non-compliant", "disallow", "cease operations", "account closure",
        ],
    },
    {
        "rule_code": "CBS-010",
        "name": "Validate debit transaction",
        "description": (
            "Validates a debit before posting: account ACTIVE, no debit-blocking restriction, "
            "sufficient balance net of minimum balance, and within per-transaction and daily "
            "limits."
        ),
        "banking_module": BankingModule.TRANSACTIONS.value,
        "api_endpoint": "POST /api/cbs/transactions",
        "ui_route": "/simulator/transactions/new",
        "affected_fields": [
            "amount", "balance", "status", "rejection_code", "restrictions", "minimum_balance",
        ],
        "keywords": [
            "debit", "withdrawal", "insufficient balance", "transaction", "payment",
            "outward", "funds transfer", "limit",
        ],
    },
    {
        "rule_code": "CBS-011",
        "name": "Validate credit transaction",
        "description": (
            "Validates a credit before posting: account must accept credits and not be CLOSED "
            "or fully frozen. Credits into KYC non-compliant accounts follow the configured "
            "restriction behaviour."
        ),
        "banking_module": BankingModule.TRANSACTIONS.value,
        "api_endpoint": "POST /api/cbs/transactions",
        "ui_route": "/simulator/transactions/new",
        "affected_fields": ["amount", "balance", "status", "rejection_code", "restrictions"],
        "keywords": [
            "credit", "deposit", "inward", "remittance", "transaction", "receive funds",
        ],
    },
    {
        "rule_code": "CBS-012",
        "name": "Apply transaction limits",
        "description": (
            "Enforces per-transaction and cumulative daily debit limits, including the reduced "
            "caps applicable to small accounts and KYC-incomplete accounts."
        ),
        "banking_module": BankingModule.TRANSACTIONS.value,
        "api_endpoint": "POST /api/cbs/transactions",
        "ui_route": "/simulator/transactions",
        "affected_fields": ["per_txn_limit", "daily_debit_limit", "amount", "rejection_code"],
        "keywords": [
            "limit", "threshold", "cap", "ceiling", "maximum", "aggregate", "turnover",
            "fifty thousand", "one lakh", "small account", "monthly", "annual",
        ],
    },
    {
        "rule_code": "CBS-013",
        "name": "Maker-checker approval",
        "description": (
            "Dual control: a maker proposes a state change and a different checker approves or "
            "rejects it. Self-approval is rejected and every decision is written to the audit "
            "trail."
        ),
        "banking_module": BankingModule.CROSS_CUTTING.value,
        "api_endpoint": "POST /api/cbs/maker-checker/{task_id}/decide",
        "ui_route": "/simulator/maker-checker",
        "affected_fields": ["status", "maker_id", "checker_id", "decided_at"],
        "keywords": [
            "maker checker", "dual control", "four eyes", "authorisation", "approval",
            "segregation of duties", "senior management", "designated director", "sign off",
        ],
    },
    {
        "rule_code": "CBS-014",
        "name": "Validate account status",
        "description": (
            "Resolves the effective operability of an account from its status, restrictions and "
            "the owning customer KYC status. Used as a precondition by transaction processing."
        ),
        "banking_module": BankingModule.ACCOUNT_MANAGEMENT.value,
        "api_endpoint": "GET /api/cbs/accounts/{account_id}/status",
        "ui_route": "/simulator/accounts",
        "affected_fields": ["status", "restrictions", "kyc_status"],
        "keywords": [
            "account status", "dormant", "inactive", "active", "closed", "operable",
            "status check", "frozen",
        ],
    },
    {
        "rule_code": "CBS-015",
        "name": "Perform customer risk classification",
        "description": (
            "Assigns a LOW, MEDIUM or HIGH risk category from customer attributes including PEP "
            "status, and derives the periodic KYC updation interval from that category."
        ),
        "banking_module": BankingModule.KYC_AML.value,
        "api_endpoint": "POST /api/cbs/customers/{customer_id}/risk-classification",
        "ui_route": "/simulator/kyc",
        "affected_fields": ["risk_category", "is_pep", "next_kyc_due_date"],
        "keywords": [
            "risk categorisation", "risk classification", "high risk", "low risk",
            "medium risk", "pep", "politically exposed person", "enhanced due diligence",
            "edd", "risk profile", "risk based approach",
        ],
    },
]

RULE_CODES: frozenset[str] = frozenset(r["rule_code"] for r in BUSINESS_RULES)


def rule_by_code(code: str) -> RuleSeed | None:
    for rule in BUSINESS_RULES:
        if rule["rule_code"] == code:
            return rule
    return None


__all__ = ["BUSINESS_RULES", "RULE_CODES", "RuleSeed", "rule_by_code"]
