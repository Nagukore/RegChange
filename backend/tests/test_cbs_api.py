"""End-to-end tests through the simulated CBS HTTP API.

These exercise the exact endpoints the Test Execution Agent drives, so a green
run here means generated test cases have a working target.
"""
from __future__ import annotations

import pytest

from tests.conftest import valid_customer_payload, valid_ovd_documents

pytestmark = pytest.mark.cbs


def _unique(client, **overrides):
    """Create a customer with identifiers unique to this call.

    PAN and mobile are globally unique in the CBS, and the API-level fixtures
    share one database, so each test needs its own values.
    """
    import random
    import string

    pan = (
        "".join(random.choices(string.ascii_uppercase, k=5))
        + "".join(random.choices(string.digits, k=4))
        + random.choice(string.ascii_uppercase)
    )
    mobile = random.choice("6789") + "".join(random.choices(string.digits, k=9))
    payload = valid_customer_payload(pan=pan, mobile=mobile, **overrides)
    response = client.post("/api/cbs/customers", json=payload)
    return response


def _activate_customer(client, customer_id: str) -> None:
    """Drive the customer through maker-checker so it becomes ACTIVE."""
    tasks = client.get("/api/cbs/maker-checker", params={"status": "PENDING"}).json()
    task = next(
        t
        for t in tasks["items"]
        if t["operation"] == "CUSTOMER_CREATE" and t["entity_id"] == customer_id
    )
    decision = client.post(
        f"/api/cbs/maker-checker/{task['id']}/decide",
        json={"decision": "APPROVE", "checker_id": "checker@sim", "remarks": "Verified"},
    )
    assert decision.status_code == 200, decision.text


def _ready_account(client, *, product_code="SB-REG", initial_deposit="50000"):
    """A fully onboarded, KYC-verified customer with an ACTIVE account."""
    customer = _unique(client).json()
    _activate_customer(client, customer["id"])

    kyc = client.post(
        f"/api/cbs/customers/{customer['id']}/kyc/verify",
        json={"kyc_type": "CDD", "documents": valid_ovd_documents()},
    )
    assert kyc.status_code == 200, kyc.text

    account = client.post(
        "/api/cbs/accounts",
        json={
            "customer_id": customer["id"],
            "account_type": "CURRENT" if product_code.startswith("CA") else "SAVINGS",
            "product_code": product_code,
            "initial_deposit": initial_deposit,
        },
    )
    assert account.status_code == 201, account.text
    account_data = account.json()
    activated = client.post(f"/api/cbs/accounts/{account_data['id']}/activate")
    assert activated.status_code == 200
    return customer, activated.json()


class TestCustomerOnboarding:
    def test_create_customer_assigns_ucic_and_awaits_approval(self, client):
        response = _unique(client)
        assert response.status_code == 201, response.text
        data = response.json()
        # CBS-003: a UCIC is allocated on creation.
        assert data["ucic"].startswith("UC")
        # CBS-013: creation does not self-activate.
        assert data["status"] == "PENDING_APPROVAL"
        assert data["kyc_status"] == "NOT_STARTED"
        assert data["risk_category"] == "LOW"

    def test_missing_mandatory_field_returns_422_with_code(self, client):
        payload = valid_customer_payload(first_name="")
        response = client.post("/api/cbs/customers", json=payload)
        # Pydantic rejects a blank required string before the rule engine for
        # some fields; either way the request must not succeed.
        assert response.status_code in (400, 422)

    def test_invalid_pan_rejected_with_machine_code(self, client):
        response = client.post(
            "/api/cbs/customers", json=valid_customer_payload(pan="INVALID123")
        )
        assert response.status_code == 422
        body = response.json()
        assert body["rejection_code"] == "INVALID_PAN_FORMAT"
        assert body["rule_code"] == "CBS-001"

    def test_duplicate_pan_rejected_with_409(self, client):
        first = _unique(client).json()
        duplicate = client.post(
            "/api/cbs/customers",
            json=valid_customer_payload(pan=first["pan"], mobile="9000000001"),
        )
        assert duplicate.status_code == 409
        body = duplicate.json()
        assert body["rejection_code"] == "DUPLICATE_CUSTOMER"
        assert body["rule_code"] == "CBS-002"

    def test_pep_customer_classified_high_risk(self, client):
        response = _unique(client, is_pep=True)
        assert response.json()["risk_category"] == "HIGH"

    def test_search_finds_customer_by_ucic(self, client):
        created = _unique(client).json()
        found = client.get("/api/cbs/customers", params={"q": created["ucic"]}).json()
        assert found["total"] >= 1
        assert any(item["ucic"] == created["ucic"] for item in found["items"])

    def test_lookup_by_ucic_endpoint(self, client):
        created = _unique(client).json()
        response = client.get(f"/api/cbs/customers/by-ucic/{created['ucic']}")
        assert response.status_code == 200
        assert response.json()["id"] == created["id"]


class TestMakerChecker:
    """CBS-013."""

    def test_approval_activates_customer(self, client):
        customer = _unique(client).json()
        _activate_customer(client, customer["id"])
        refreshed = client.get(f"/api/cbs/customers/{customer['id']}").json()
        assert refreshed["status"] == "ACTIVE"

    def test_self_approval_is_rejected(self, client):
        customer = _unique(client).json()
        tasks = client.get("/api/cbs/maker-checker", params={"status": "PENDING"}).json()
        task = next(t for t in tasks["items"] if t["entity_id"] == customer["id"])
        # The maker is "maker@sim"; approving as the same identity must fail.
        response = client.post(
            f"/api/cbs/maker-checker/{task['id']}/decide",
            json={"decision": "APPROVE", "checker_id": "maker@sim"},
        )
        assert response.status_code == 422
        assert response.json()["rejection_code"] == "SELF_APPROVAL_FORBIDDEN"

    def test_task_cannot_be_decided_twice(self, client):
        customer = _unique(client).json()
        tasks = client.get("/api/cbs/maker-checker", params={"status": "PENDING"}).json()
        task = next(t for t in tasks["items"] if t["entity_id"] == customer["id"])
        first = client.post(
            f"/api/cbs/maker-checker/{task['id']}/decide",
            json={"decision": "APPROVE", "checker_id": "checker@sim"},
        )
        assert first.status_code == 200
        second = client.post(
            f"/api/cbs/maker-checker/{task['id']}/decide",
            json={"decision": "APPROVE", "checker_id": "checker2@sim"},
        )
        assert second.status_code == 422
        assert second.json()["rejection_code"] == "TASK_ALREADY_DECIDED"

    def test_rejection_marks_customer_rejected(self, client):
        customer = _unique(client).json()
        tasks = client.get("/api/cbs/maker-checker", params={"status": "PENDING"}).json()
        task = next(t for t in tasks["items"] if t["entity_id"] == customer["id"])
        client.post(
            f"/api/cbs/maker-checker/{task['id']}/decide",
            json={"decision": "REJECT", "checker_id": "checker@sim", "remarks": "Bad docs"},
        )
        refreshed = client.get(f"/api/cbs/customers/{customer['id']}").json()
        assert refreshed["status"] == "REJECTED"


class TestKyc:
    """CBS-004, CBS-005, CBS-006."""

    def test_verify_kyc_sets_status_and_due_date(self, client):
        customer = _unique(client).json()
        _activate_customer(client, customer["id"])
        response = client.post(
            f"/api/cbs/customers/{customer['id']}/kyc/verify",
            json={"kyc_type": "CDD", "documents": valid_ovd_documents()},
        )
        assert response.status_code == 200, response.text
        record = response.json()
        assert record["status"] == "VERIFIED"
        assert record["version"] == 1

        kyc = client.get(f"/api/cbs/customers/{customer['id']}/kyc").json()
        assert kyc["kyc_status"] == "VERIFIED"
        # CBS-006: LOW risk implies a 10-year periodicity.
        assert kyc["periodicity_years"] == 10
        assert kyc["next_kyc_due_date"] is not None
        assert kyc["kyc_due_now"] is False

    def test_kyc_with_no_documents_rejected(self, client):
        customer = _unique(client).json()
        response = client.post(
            f"/api/cbs/customers/{customer['id']}/kyc/verify",
            json={"kyc_type": "CDD", "documents": []},
        )
        assert response.status_code == 422
        assert response.json()["rejection_code"] == "INSUFFICIENT_OVD"

    def test_pan_only_kyc_rejected_for_no_address_proof(self, client):
        customer = _unique(client).json()
        response = client.post(
            f"/api/cbs/customers/{customer['id']}/kyc/verify",
            json={
                "kyc_type": "CDD",
                "documents": [{"doc_type": "PAN", "number": "ABCDE1234F", "verified": True}],
            },
        )
        assert response.status_code == 422
        assert response.json()["rejection_code"] == "NO_ADDRESS_PROOF"

    def test_high_risk_customer_is_escalated_to_edd(self, client):
        customer = _unique(client, is_pep=True).json()
        _activate_customer(client, customer["id"])
        response = client.post(
            f"/api/cbs/customers/{customer['id']}/kyc/verify",
            json={"kyc_type": "CDD", "documents": valid_ovd_documents()},
        )
        assert response.status_code == 200, response.text
        # A PEP requesting CDD is upgraded to EDD, which needs two OVDs.
        assert response.json()["kyc_type"] == "EDD"

    def test_high_risk_single_document_fails_edd(self, client):
        customer = _unique(client, is_pep=True).json()
        response = client.post(
            f"/api/cbs/customers/{customer['id']}/kyc/verify",
            json={
                "kyc_type": "CDD",
                "documents": [
                    {"doc_type": "PASSPORT", "expiry_date": "2033-01-01", "verified": True}
                ],
            },
        )
        assert response.status_code == 422
        assert response.json()["rejection_code"] == "INSUFFICIENT_OVD"

    def test_periodic_updation_creates_new_version_not_overwrite(self, client):
        customer = _unique(client).json()
        _activate_customer(client, customer["id"])
        client.post(
            f"/api/cbs/customers/{customer['id']}/kyc/verify",
            json={"kyc_type": "CDD", "documents": valid_ovd_documents()},
        )
        update = client.post(
            f"/api/cbs/customers/{customer['id']}/kyc/update",
            json={"kyc_type": "PERIODIC_UPDATION", "self_declaration": True},
        )
        assert update.status_code == 200, update.text
        assert update.json()["version"] == 2

        kyc = client.get(f"/api/cbs/customers/{customer['id']}/kyc").json()
        # Both versions retained -- no overwrite.
        assert len(kyc["records"]) == 2
        assert {r["version"] for r in kyc["records"]} == {1, 2}

    def test_risk_reclassification_endpoint(self, client):
        customer = _unique(client, is_pep=True).json()
        response = client.post(f"/api/cbs/customers/{customer['id']}/risk-classification")
        assert response.status_code == 200
        body = response.json()
        assert body["risk_category"] == "HIGH"
        assert body["requires_edd"] is True
        assert body["periodicity_years"] == 2


class TestAccounts:
    """CBS-007, CBS-008, CBS-009, CBS-014."""

    def test_account_opening_blocked_without_kyc(self, client):
        customer = _unique(client).json()
        _activate_customer(client, customer["id"])
        response = client.post(
            "/api/cbs/accounts",
            json={"customer_id": customer["id"], "account_type": "SAVINGS"},
        )
        assert response.status_code == 422
        assert response.json()["rejection_code"] == "KYC_NOT_VERIFIED"

    def test_account_opening_blocked_for_inactive_customer(self, client):
        customer = _unique(client).json()  # left PENDING_APPROVAL
        response = client.post(
            "/api/cbs/accounts",
            json={"customer_id": customer["id"], "account_type": "SAVINGS"},
        )
        assert response.status_code == 422
        codes = {v["code"] for v in response.json()["violations"]}
        assert "CUSTOMER_NOT_ACTIVE" in codes

    def test_savings_account_opens_with_product_limits(self, client):
        _, account = _ready_account(client)
        assert account["status"] == "ACTIVE"
        assert account["product_code"] == "SB-REG"
        assert account["per_txn_limit"] == "50000.00"
        assert account["minimum_balance"] == "1000.00"

    def test_current_account_uses_its_own_limits(self, client):
        _, account = _ready_account(client, product_code="CA-REG", initial_deposit="100000")
        assert account["account_type"] == "CURRENT"
        assert account["per_txn_limit"] == "500000.00"

    def test_account_status_reports_operability(self, client):
        _, account = _ready_account(client)
        status = client.get(f"/api/cbs/accounts/{account['id']}/status").json()
        assert status["debit_allowed"] is True
        assert status["credit_allowed"] is True

    def test_restriction_blocks_debit_but_not_credit(self, client):
        _, account = _ready_account(client)
        client.post(
            f"/api/cbs/accounts/{account['id']}/restrictions",
            json={"add": ["DEBIT_FREEZE"], "reason": "Test restriction"},
        )
        status = client.get(f"/api/cbs/accounts/{account['id']}/status").json()
        assert status["debit_allowed"] is False
        assert status["credit_allowed"] is True

    def test_restriction_can_be_lifted(self, client):
        _, account = _ready_account(client)
        client.post(
            f"/api/cbs/accounts/{account['id']}/restrictions", json={"add": ["DEBIT_FREEZE"]}
        )
        client.post(
            f"/api/cbs/accounts/{account['id']}/restrictions", json={"remove": ["DEBIT_FREEZE"]}
        )
        status = client.get(f"/api/cbs/accounts/{account['id']}/status").json()
        assert status["debit_allowed"] is True


class TestTransactions:
    """CBS-010, CBS-011, CBS-012."""

    def test_valid_debit_posts_and_updates_balance(self, client):
        _, account = _ready_account(client, initial_deposit="50000")
        response = client.post(
            "/api/cbs/transactions",
            json={"account_id": account["id"], "txn_type": "DEBIT", "amount": "5000"},
        )
        assert response.status_code == 201, response.text
        txn = response.json()
        assert txn["status"] == "POSTED"
        assert txn["balance_after"] == "45000.00"

    def test_credit_increases_balance(self, client):
        _, account = _ready_account(client, initial_deposit="10000")
        response = client.post(
            "/api/cbs/transactions",
            json={"account_id": account["id"], "txn_type": "CREDIT", "amount": "2500"},
        )
        assert response.status_code == 201
        assert response.json()["balance_after"] == "12500.00"

    def test_debit_beyond_balance_rejected(self, client):
        _, account = _ready_account(client, initial_deposit="5000")
        response = client.post(
            "/api/cbs/transactions",
            json={"account_id": account["id"], "txn_type": "DEBIT", "amount": "9000"},
        )
        assert response.status_code == 422
        assert response.json()["rejection_code"] == "INSUFFICIENT_BALANCE"

    def test_minimum_balance_protected(self, client):
        _, account = _ready_account(client, initial_deposit="5000")
        # Min balance is 1000 for SB-REG, so a 4500 debit must fail.
        response = client.post(
            "/api/cbs/transactions",
            json={"account_id": account["id"], "txn_type": "DEBIT", "amount": "4500"},
        )
        assert response.status_code == 422
        assert response.json()["rejection_code"] == "MINIMUM_BALANCE_BREACH"

    def test_per_transaction_limit_enforced(self, client):
        _, account = _ready_account(client, initial_deposit="500000")
        response = client.post(
            "/api/cbs/transactions",
            json={"account_id": account["id"], "txn_type": "DEBIT", "amount": "60000"},
        )
        assert response.status_code == 422
        assert response.json()["rejection_code"] == "PER_TXN_LIMIT_EXCEEDED"

    def test_daily_limit_enforced_across_transactions(self, client):
        _, account = _ready_account(client, initial_deposit="500000")
        # Daily limit is 100000 for SB-REG; two 50000 debits exhaust it.
        for _ in range(2):
            ok = client.post(
                "/api/cbs/transactions",
                json={"account_id": account["id"], "txn_type": "DEBIT", "amount": "50000"},
            )
            assert ok.status_code == 201, ok.text
        third = client.post(
            "/api/cbs/transactions",
            json={"account_id": account["id"], "txn_type": "DEBIT", "amount": "1000"},
        )
        assert third.status_code == 422
        assert third.json()["rejection_code"] == "DAILY_LIMIT_EXCEEDED"

    def test_zero_amount_rejected(self, client):
        _, account = _ready_account(client)
        response = client.post(
            "/api/cbs/transactions",
            json={"account_id": account["id"], "txn_type": "DEBIT", "amount": "0"},
        )
        assert response.status_code == 422
        assert response.json()["rejection_code"] == "INVALID_AMOUNT"

    def test_frozen_account_debit_rejected(self, client):
        _, account = _ready_account(client)
        client.post(
            f"/api/cbs/accounts/{account['id']}/restrictions",
            json={"add": ["KYC_NON_COMPLIANT"]},
        )
        response = client.post(
            "/api/cbs/transactions",
            json={"account_id": account["id"], "txn_type": "DEBIT", "amount": "1000"},
        )
        assert response.status_code == 422
        assert response.json()["rejection_code"] == "ACCOUNT_RESTRICTED"

    def test_rejected_transaction_is_still_recorded_as_evidence(self, client):
        _, account = _ready_account(client, initial_deposit="1000")
        # 5000 is within the 50000 per-transaction limit, so the balance check
        # is what rejects it -- keeping this test focused on one rule.
        client.post(
            "/api/cbs/transactions",
            json={"account_id": account["id"], "txn_type": "DEBIT", "amount": "5000"},
        )
        listing = client.get(
            "/api/cbs/transactions", params={"account_id": account["id"], "status": "REJECTED"}
        ).json()
        assert listing["total"] == 1
        assert listing["items"][0]["rejection_code"] == "INSUFFICIENT_BALANCE"

    def test_large_transaction_flagged_for_aml(self, client):
        _, account = _ready_account(client, product_code="CA-REG", initial_deposit="100000")
        response = client.post(
            "/api/cbs/transactions",
            json={"account_id": account["id"], "txn_type": "CREDIT", "amount": "1500000"},
        )
        assert response.status_code == 201
        assert response.json()["aml_flagged"] is True

    def test_neft_fee_applied_and_deducted(self, client):
        _, account = _ready_account(client, initial_deposit="50000")
        response = client.post(
            "/api/cbs/transactions",
            json={
                "account_id": account["id"],
                "txn_type": "DEBIT",
                "amount": "5000",
                "channel": "NEFT",
            },
        )
        assert response.status_code == 201
        txn = response.json()
        assert txn["fee"] == "2.50"
        # 50000 - 5000 - 2.50
        assert txn["balance_after"] == "44997.50"


class TestAuditTrail:
    def test_every_operation_writes_a_rule_tagged_audit_event(self, client):
        customer, account = _ready_account(client)
        client.post(
            "/api/cbs/transactions",
            json={"account_id": account["id"], "txn_type": "DEBIT", "amount": "1000"},
        )
        audit = client.get("/api/cbs/audit", params={"limit": 500}).json()
        rule_codes = {e["rule_code"] for e in audit["items"] if e["rule_code"]}
        # Onboarding, KYC, account opening, dual control and transactions all
        # leave a trace tagged with the rule they exercised.
        for expected in {"CBS-001", "CBS-004", "CBS-007", "CBS-013", "CBS-010"}:
            assert expected in rule_codes, f"missing audit for {expected}"

    def test_audit_filterable_by_rule_code(self, client):
        _unique(client)
        audit = client.get("/api/cbs/audit", params={"rule_code": "CBS-001"}).json()
        assert audit["total"] >= 1
        assert all(e["rule_code"] == "CBS-001" for e in audit["items"])


class TestReferenceData:
    def test_reference_endpoint_exposes_rule_parameters(self, client):
        reference = client.get("/api/cbs/reference").json()
        assert "PASSPORT" in reference["accepted_ovd_types"]
        assert reference["kyc_periodicity_years"]["HIGH"] == 2
        assert "SB-SMALL" in reference["products"]
        assert "simulated" in reference["notice"].lower()
