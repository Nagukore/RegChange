"""The CBS API contract handed to the test-generation model.

Kept as data in one place so the prompt, the execution validator and the docs
cannot drift apart. `ENDPOINTS` is the allow-list: a generated test whose
`http_path` is not here is rejected before execution.
"""
from __future__ import annotations

from app.cbs.rules import RejectionCode


class Endpoint:
    def __init__(
        self,
        *,
        method: str,
        path: str,
        rule_codes: list[str],
        description: str,
        request_fields: dict[str, str] | None = None,
        success_status: int = 200,
        state_changing: bool = False,
    ) -> None:
        self.method = method
        self.path = path
        self.rule_codes = rule_codes
        self.description = description
        self.request_fields = request_fields or {}
        self.success_status = success_status
        self.state_changing = state_changing

    @property
    def key(self) -> str:
        return f"{self.method} {self.path}"


# Path templates use {placeholder}; the executor substitutes from test_data.
ENDPOINTS: list[Endpoint] = [
    Endpoint(
        method="POST",
        path="/api/cbs/customers",
        rule_codes=["CBS-001", "CBS-002", "CBS-003"],
        description=(
            "Create an individual customer. Validates mandatory fields and formats, "
            "checks for a duplicate, allots a UCIC and leaves the record in "
            "PENDING_APPROVAL for a checker."
        ),
        request_fields={
            "first_name": "string, mandatory",
            "last_name": "string, mandatory",
            "date_of_birth": "ISO date YYYY-MM-DD, mandatory, must be 18+ and not future",
            "pan": "string, optional, format AAAAA9999A",
            "aadhaar_last4": "string, optional, 4 digits",
            "mobile": "string, mandatory, 10 digits starting 6-9",
            "email": "string, optional",
            "address_line1": "string, optional",
            "city": "string, optional",
            "state": "string, optional",
            "pincode": "string, optional, 6 digits not starting with 0",
            "country": "2-letter code, default IN",
            "customer_type": "INDIVIDUAL | SOLE_PROPRIETOR | COMPANY | TRUST",
            "is_pep": "boolean as string, 'true' or 'false'",
        },
        success_status=201,
        state_changing=True,
    ),
    Endpoint(
        method="GET",
        path="/api/cbs/customers/{customer_id}",
        rule_codes=["CBS-001"],
        description="Retrieve a customer by id.",
        request_fields={"customer_id": "UUID of an existing customer"},
    ),
    Endpoint(
        method="GET",
        path="/api/cbs/customers/by-ucic/{ucic}",
        rule_codes=["CBS-003"],
        description="Retrieve a customer by UCIC, verifying unique-identifier allotment.",
        request_fields={"ucic": "UCIC string, format UC + 10 digits"},
    ),
    Endpoint(
        method="POST",
        path="/api/cbs/customers/{customer_id}/risk-classification",
        rule_codes=["CBS-015"],
        description=(
            "Recompute the customer risk category (LOW/MEDIUM/HIGH) and derive the "
            "periodic KYC updation due date from it. Returns risk_category, "
            "requires_edd, periodicity_years and next_kyc_due_date."
        ),
        request_fields={"customer_id": "UUID of an existing customer"},
        state_changing=True,
    ),
    Endpoint(
        method="GET",
        path="/api/cbs/customers/{customer_id}/kyc",
        rule_codes=["CBS-005"],
        description=(
            "Retrieve current and historical KYC for a customer: kyc_status, "
            "risk_category, next_kyc_due_date, kyc_due_now, periodicity_years, "
            "requires_edd and a versioned list of records."
        ),
        request_fields={"customer_id": "UUID of an existing customer"},
    ),
    Endpoint(
        method="POST",
        path="/api/cbs/customers/{customer_id}/kyc/verify",
        rule_codes=["CBS-004"],
        description=(
            "Perform Customer Due Diligence. Requires at least one verified, "
            "unexpired Officially Valid Document, and at least one of them must "
            "evidence address (PAN alone is insufficient). A HIGH-risk or PEP "
            "customer is escalated to EDD, which requires two verified documents."
        ),
        request_fields={
            "customer_id": "UUID of an existing customer",
            "kyc_type": "CDD | EDD | PERIODIC_UPDATION | RE_KYC",
            "documents": (
                "JSON array as a string, each item "
                '{"doc_type":"PASSPORT","number":"X1","expiry_date":"2033-01-01",'
                '"verified":true}. Accepted doc_type: PASSPORT, DRIVING_LICENCE, '
                "VOTER_ID, AADHAAR, NREGA_JOB_CARD, NPR_LETTER, PAN"
            ),
            "verification_mode": "IN_PERSON | V_CIP | DIGITAL_KYC | SELF_DECLARATION",
        },
        state_changing=True,
    ),
    Endpoint(
        method="POST",
        path="/api/cbs/customers/{customer_id}/kyc/update",
        rule_codes=["CBS-006"],
        description=(
            "Periodic KYC updation / re-KYC. Creates a NEW version and never "
            "overwrites a prior record. A self-declaration of no change is "
            "accepted when self_declaration is true and no documents are supplied."
        ),
        request_fields={
            "customer_id": "UUID of an existing customer",
            "kyc_type": "PERIODIC_UPDATION | RE_KYC | CDD | EDD",
            "documents": "JSON array as a string, same shape as kyc/verify",
            "self_declaration": "'true' or 'false'",
        },
        state_changing=True,
    ),
    Endpoint(
        method="POST",
        path="/api/cbs/accounts",
        rule_codes=["CBS-007", "CBS-008"],
        description=(
            "Open an account. Requires the customer to be ACTIVE with KYC VERIFIED "
            "and not lapsed; a HIGH-risk or PEP customer additionally requires a "
            "completed EDD record. The account is created in PENDING_APPROVAL."
        ),
        request_fields={
            "customer_id": "UUID of an ACTIVE, KYC-verified customer",
            "account_type": "SAVINGS | CURRENT",
            "product_code": "SB-REG | SB-SMALL | SB-BSBDA | CA-REG",
            "initial_deposit": "decimal as string",
            "branch_code": "string, default SIM001",
        },
        success_status=201,
        state_changing=True,
    ),
    Endpoint(
        method="GET",
        path="/api/cbs/accounts/{account_id}/status",
        rule_codes=["CBS-014"],
        description=(
            "Effective operability of an account. Returns debit_allowed, "
            "credit_allowed, status_operable and blocking_restrictions."
        ),
        request_fields={"account_id": "UUID of an existing account"},
    ),
    Endpoint(
        method="POST",
        path="/api/cbs/accounts/{account_id}/restrictions",
        rule_codes=["CBS-009"],
        description=(
            "Apply or lift restriction codes. DEBIT_FREEZE and KYC_NON_COMPLIANT "
            "block debits; TOTAL_FREEZE and COURT_ORDER block both directions."
        ),
        request_fields={
            "account_id": "UUID of an existing account",
            "add": "JSON array as a string, e.g. [\"DEBIT_FREEZE\"]",
            "remove": "JSON array as a string",
            "reason": "string",
        },
        state_changing=True,
    ),
    Endpoint(
        method="POST",
        path="/api/cbs/accounts/{account_id}/activate",
        rule_codes=["CBS-014"],
        description="Activate a PENDING_APPROVAL account. Test-setup convenience.",
        request_fields={"account_id": "UUID of an existing account"},
        state_changing=True,
    ),
    Endpoint(
        method="POST",
        path="/api/cbs/transactions",
        rule_codes=["CBS-010", "CBS-011", "CBS-012"],
        description=(
            "Post a transaction. A debit requires an ACTIVE account with no "
            "debit-blocking restriction, sufficient balance net of fees and the "
            "minimum balance, and compliance with the per-transaction and "
            "cumulative daily debit limits. A credit respects the product balance "
            "ceiling. Transactions at or above the monitoring threshold are "
            "aml_flagged. Rejected transactions are still persisted as evidence."
        ),
        request_fields={
            "account_id": "UUID of an ACTIVE account",
            "txn_type": "DEBIT | CREDIT | TRANSFER",
            "amount": "decimal as string, must be greater than zero",
            "channel": "BRANCH | NEFT | RTGS | IMPS | UPI | CASH | ATM",
            "narration": "string, optional",
        },
        success_status=201,
        state_changing=True,
    ),
    Endpoint(
        method="GET",
        path="/api/cbs/maker-checker",
        rule_codes=["CBS-013"],
        description="List maker-checker tasks, filterable by status.",
        request_fields={"status": "PENDING | APPROVED | REJECTED"},
    ),
    Endpoint(
        method="POST",
        path="/api/cbs/maker-checker/{task_id}/decide",
        rule_codes=["CBS-013"],
        description=(
            "Checker decision under dual control. The checker_id must differ from "
            "the maker, and a task can only be decided once. Approval applies the "
            "proposed state change."
        ),
        request_fields={
            "task_id": "UUID of a PENDING maker-checker task",
            "decision": "APPROVE | REJECT",
            "checker_id": "string, must differ from the maker id",
            "remarks": "string, optional",
        },
        state_changing=True,
    ),
    Endpoint(
        method="GET",
        path="/api/cbs/reference",
        rule_codes=["CBS-012"],
        description=(
            "Read-only rule parameters: accepted OVD types, KYC periodicity by "
            "risk category, product limits and restriction codes. Useful for a "
            "regression test that asserts a configured threshold has not drifted."
        ),
        request_fields={},
    ),
    Endpoint(
        method="GET",
        path="/api/cbs/audit",
        rule_codes=["CBS-013"],
        description=(
            "Immutable CBS audit trail, filterable by rule_code or entity_id. "
            "Used to evidence that a control actually fired."
        ),
        request_fields={"rule_code": "e.g. CBS-004", "entity_id": "UUID"},
    ),
]

ENDPOINTS_BY_KEY: dict[str, Endpoint] = {e.key: e for e in ENDPOINTS}
PATHS: frozenset[str] = frozenset(e.path for e in ENDPOINTS)
REJECTION_CODES: list[str] = sorted(str(code) for code in RejectionCode)


def endpoints_for_rule(rule_code: str) -> list[Endpoint]:
    return [e for e in ENDPOINTS if rule_code in e.rule_codes]


def render_contract(rule_code: str | None = None) -> str:
    """Human/model readable contract text, optionally focused on one rule.

    The rule's own endpoints are listed first and in full; the rest are listed
    compactly so the model can still build an INTEGRATION test across modules.
    """
    primary = endpoints_for_rule(rule_code) if rule_code else []
    primary_keys = {e.key for e in primary}
    secondary = [e for e in ENDPOINTS if e.key not in primary_keys]

    lines: list[str] = []
    if primary:
        lines.append("PRIMARY ENDPOINTS FOR THIS RULE")
        for endpoint in primary:
            lines.append(_render_endpoint(endpoint, full=True))
        lines.append("")
        lines.append("OTHER AVAILABLE ENDPOINTS (for setup and integration flows)")
    else:
        lines.append("AVAILABLE ENDPOINTS")
    for endpoint in secondary:
        lines.append(_render_endpoint(endpoint, full=not primary))
    return "\n".join(lines)


def _render_endpoint(endpoint: Endpoint, *, full: bool) -> str:
    header = (
        f"- {endpoint.method} {endpoint.path}  "
        f"(rules: {', '.join(endpoint.rule_codes)}; success: {endpoint.success_status})"
    )
    if not full:
        return f"{header}\n    {endpoint.description[:160]}"
    field_lines = "\n".join(
        f"      {name}: {spec}" for name, spec in endpoint.request_fields.items()
    )
    return f"{header}\n    {endpoint.description}\n    request fields:\n{field_lines}"


__all__ = [
    "Endpoint",
    "ENDPOINTS",
    "ENDPOINTS_BY_KEY",
    "PATHS",
    "REJECTION_CODES",
    "endpoints_for_rule",
    "render_contract",
]
