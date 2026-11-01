"""Tests for Agent 6 (Test Execution) using the in-process API driver.

These need no external credentials: the test cases are constructed directly,
exactly as the generation agent would persist them, and executed against the
simulated CBS over ASGI. That makes the whole execution path -- approval gate,
fixture provisioning, assertion checking, evidence capture -- verifiable
offline.
"""
from __future__ import annotations

import pytest

from app.agents.base import AgentContext
from app.agents.execution_agent import ExecutionAgent, record_test_approval
from app.models.enums import (
    AutomationType,
    EvidenceType,
    ExecutionDriver,
    ExecutionStatus,
    Priority,
    ReviewStatus,
)

# Aliased: pytest tries to collect any imported class named Test*, which these
# are not -- they are an enum and a SQLAlchemy model.
from app.models.enums import TestCategory as Category
from app.models.mapping import BusinessRule
from app.models.testing import ExecutionEvidence
from app.models.testing import TestCase as TestCaseModel
from tests.conftest import unique_identifiers

pytestmark = pytest.mark.cbs


def _make_test_case(
    db,
    *,
    test_code: str,
    title: str,
    method: str,
    path: str,
    expect_status: int,
    test_data: dict | None = None,
    expect_json_path: str | None = None,
    expect_json_value: str | None = None,
    category: Category = Category.POSITIVE,
    is_state_changing: bool = True,
    rule_code: str = "CBS-001",
) -> TestCaseModel:
    """Persist a test case in the exact shape Agent 5 produces."""
    rule = db.query(BusinessRule).filter(BusinessRule.rule_code == rule_code).one_or_none()
    case = TestCaseModel(
        test_code=test_code,
        business_rule_id=rule.id if rule else None,
        title=title,
        objective=f"Verify {title}",
        banking_module=rule.banking_module if rule else "CROSS_CUTTING",
        category=category.value,
        priority=Priority.HIGH.value,
        automation_type=AutomationType.API.value,
        preconditions=[],
        test_data=test_data or {},
        steps=[{"step": 1, "action": f"{method} {path}", "expected": str(expect_status)}],
        expected_result=f"HTTP {expect_status}",
        execution_spec={
            "driver": "PYTEST_API",
            "method": method,
            "path": path,
            "expect_status": expect_status,
            "expect_json_path": expect_json_path,
            "expect_json_value": expect_json_value,
        },
        is_state_changing=is_state_changing,
        requirement_code="REQ-TEST",
        source_clause_reference="Para 38",
        source_quote="Banks shall carry out periodic updation of KYC records.",
        approval_status=ReviewStatus.PENDING_REVIEW.value,
        provenance="DETERMINISTIC",
    )
    db.add(case)
    db.commit()
    db.refresh(case)
    return case


def _approve(db, case: TestCaseModel) -> None:
    record_test_approval(
        db,
        case.id,
        decision="APPROVE",
        reviewer_id=None,
        reviewer_email="reviewer@test.local",
        notes="Approved for automated test",
    )


class TestApprovalGate:
    """Brief requirements 5.6 / 12.12: approval before state-changing execution."""

    async def test_state_changing_test_blocked_without_approval(self, live_db):
        case = _make_test_case(
            live_db,
            test_code="TC-GATE-0001",
            title="Create customer without approval",
            method="POST",
            path="/api/cbs/customers",
            expect_status=201,
            test_data={
                "first_name": "Blocked",
                "last_name": "Customer",
                "date_of_birth": "1990-01-01",
                "mobile": "9811111111",
            },
            is_state_changing=True,
        )
        agent = ExecutionAgent()
        run = await agent.run_batch(live_db, AgentContext(), [case.id])

        assert run.blocked == 1
        assert run.passed == 0
        execution = run.executions[0]
        assert execution.status == ExecutionStatus.BLOCKED.value
        assert "approval" in (execution.failure_reason or "").lower()

    async def test_approved_test_executes(self, live_db):
        case = _make_test_case(
            live_db,
            test_code="TC-GATE-0002",
            title="Create customer with approval",
            method="POST",
            path="/api/cbs/customers",
            expect_status=201,
            test_data={
                "first_name": "Approved",
                "last_name": "Customer",
                "date_of_birth": "1990-01-01",
                **unique_identifiers(),
            },
            is_state_changing=True,
        )
        _approve(live_db, case)
        agent = ExecutionAgent()
        run = await agent.run_batch(live_db, AgentContext(), [case.id])

        assert run.blocked == 0
        assert run.passed == 1, run.executions[0].failure_reason
        execution = run.executions[0]
        assert execution.status == ExecutionStatus.PASSED.value
        # The authorising approval is snapshotted onto the execution.
        assert execution.approved_by_email == "reviewer@test.local"

    async def test_read_only_test_runs_without_approval(self, live_db):
        case = _make_test_case(
            live_db,
            test_code="TC-GATE-0003",
            title="Read maker-checker queue",
            method="GET",
            path="/api/cbs/maker-checker",
            expect_status=200,
            is_state_changing=False,
        )
        agent = ExecutionAgent()
        run = await agent.run_batch(live_db, AgentContext(), [case.id])
        assert run.blocked == 0
        assert run.passed == 1


class TestAssertionChecking:
    async def test_negative_test_asserting_rejection_code_passes(self, live_db):
        """A correctly-failing CBS call makes the negative test PASS."""
        case = _make_test_case(
            live_db,
            test_code="TC-NEG-0001",
            title="Invalid PAN is rejected",
            method="POST",
            path="/api/cbs/customers",
            expect_status=422,
            test_data={
                "first_name": "Bad",
                "last_name": "Pan",
                "date_of_birth": "1990-01-01",
                "mobile": "9833333333",
                "pan": "NOTAPAN",
            },
            expect_json_path="rejection_code",
            expect_json_value="INVALID_PAN_FORMAT",
            category=Category.NEGATIVE,
        )
        _approve(live_db, case)
        run = await ExecutionAgent().run_batch(live_db, AgentContext(), [case.id])

        assert run.passed == 1, run.executions[0].failure_reason
        execution = run.executions[0]
        assert len(execution.assertions) == 2
        assert all(a["passed"] for a in execution.assertions)

    async def test_wrong_expectation_makes_test_fail(self, live_db):
        """A test asserting the wrong code must FAIL, not silently pass."""
        case = _make_test_case(
            live_db,
            test_code="TC-NEG-0002",
            title="Invalid PAN misattributed to the wrong code",
            method="POST",
            path="/api/cbs/customers",
            expect_status=422,
            test_data={
                "first_name": "Bad",
                "last_name": "Pan",
                "date_of_birth": "1990-01-01",
                "mobile": "9844444444",
                "pan": "NOTAPAN",
            },
            expect_json_path="rejection_code",
            expect_json_value="INSUFFICIENT_BALANCE",  # wrong on purpose
            category=Category.NEGATIVE,
        )
        _approve(live_db, case)
        run = await ExecutionAgent().run_batch(live_db, AgentContext(), [case.id])

        assert run.failed == 1
        execution = run.executions[0]
        assert execution.status == ExecutionStatus.FAILED.value
        assert "INSUFFICIENT_BALANCE" in execution.failure_reason

    async def test_status_mismatch_fails(self, live_db):
        case = _make_test_case(
            live_db,
            test_code="TC-NEG-0003",
            title="Expect 201 from an invalid payload",
            method="POST",
            path="/api/cbs/customers",
            expect_status=201,
            test_data={
                "first_name": "Bad",
                "last_name": "Pan",
                "date_of_birth": "1990-01-01",
                "mobile": "9855555555",
                "pan": "NOTAPAN",
            },
        )
        _approve(live_db, case)
        run = await ExecutionAgent().run_batch(live_db, AgentContext(), [case.id])
        assert run.failed == 1

    async def test_test_with_no_assertion_cannot_pass(self, live_db):
        """An unassertable test must not be reported as a pass."""
        case = _make_test_case(
            live_db,
            test_code="TC-NEG-0004",
            title="No expectation declared",
            method="GET",
            path="/api/cbs/maker-checker",
            expect_status=200,
            is_state_changing=False,
        )
        # Strip every expectation, as a poor generation might.
        case.execution_spec = {
            "driver": "PYTEST_API",
            "method": "GET",
            "path": "/api/cbs/maker-checker",
            "expect_status": None,
            "expect_json_path": None,
            "expect_json_value": None,
        }
        live_db.commit()

        run = await ExecutionAgent().run_batch(live_db, AgentContext(), [case.id])
        assert run.failed == 1
        assertions = run.executions[0].assertions
        assert assertions[0]["name"] == "assertable_expectation"
        assert assertions[0]["passed"] is False


class TestFixtureProvisioning:
    """The executor seeds entities a generated test cannot know the ids of."""

    async def test_customer_id_placeholder_is_provisioned(self, live_db):
        case = _make_test_case(
            live_db,
            test_code="TC-FIX-0001",
            title="Retrieve KYC for a provisioned customer",
            method="GET",
            path="/api/cbs/customers/{customer_id}/kyc",
            expect_status=200,
            expect_json_path="kyc_status",
            expect_json_value="NOT_STARTED",
            is_state_changing=False,
            rule_code="CBS-005",
        )
        run = await ExecutionAgent().run_batch(live_db, AgentContext(), [case.id])
        assert run.passed == 1, run.executions[0].failure_reason

    async def test_account_id_placeholder_provisions_full_onboarding(self, live_db):
        """An {account_id} test needs customer + approval + KYC + account."""
        case = _make_test_case(
            live_db,
            test_code="TC-FIX-0002",
            title="Account status for a provisioned account",
            method="GET",
            path="/api/cbs/accounts/{account_id}/status",
            expect_status=200,
            expect_json_path="debit_allowed",
            expect_json_value="True",
            is_state_changing=False,
            rule_code="CBS-014",
        )
        run = await ExecutionAgent().run_batch(live_db, AgentContext(), [case.id])
        assert run.passed == 1, run.executions[0].failure_reason

    async def test_transaction_against_provisioned_account(self, live_db):
        case = _make_test_case(
            live_db,
            test_code="TC-FIX-0003",
            title="Debit beyond the per-transaction limit is rejected",
            method="POST",
            path="/api/cbs/transactions",
            expect_status=422,
            test_data={"txn_type": "DEBIT", "amount": "60000", "channel": "BRANCH"},
            expect_json_path="rejection_code",
            expect_json_value="PER_TXN_LIMIT_EXCEEDED",
            category=Category.BOUNDARY,
            rule_code="CBS-012",
        )
        _approve(live_db, case)
        run = await ExecutionAgent().run_batch(live_db, AgentContext(), [case.id])
        assert run.passed == 1, run.executions[0].failure_reason

    async def test_documents_json_string_is_coerced(self, live_db):
        """Gemini emits `documents` as a JSON string; it must still work."""
        case = _make_test_case(
            live_db,
            test_code="TC-FIX-0004",
            title="KYC verification with documents supplied as a JSON string",
            method="POST",
            path="/api/cbs/customers/{customer_id}/kyc/verify",
            expect_status=200,
            test_data={
                "kyc_type": "CDD",
                "documents": (
                    '[{"doc_type":"PASSPORT","number":"X1","expiry_date":"2033-01-01",'
                    '"verified":true},{"doc_type":"PAN","number":"ABCDE1234F","verified":true}]'
                ),
                "verification_mode": "IN_PERSON",
            },
            expect_json_path="status",
            expect_json_value="VERIFIED",
            rule_code="CBS-004",
        )
        _approve(live_db, case)
        run = await ExecutionAgent().run_batch(live_db, AgentContext(), [case.id])
        assert run.passed == 1, run.executions[0].failure_reason


class TestEvidenceCapture:
    async def test_api_execution_stores_request_and_response_evidence(self, live_db):
        case = _make_test_case(
            live_db,
            test_code="TC-EV-0001",
            title="Evidence is captured for an API execution",
            method="GET",
            path="/api/cbs/reference",
            expect_status=200,
            is_state_changing=False,
        )
        run = await ExecutionAgent().run_batch(live_db, AgentContext(), [case.id])
        execution = run.executions[0]

        evidence = (
            live_db.query(ExecutionEvidence)
            .filter(ExecutionEvidence.execution_id == execution.id)
            .all()
        )
        assert len(evidence) >= 1
        api_evidence = next(
            e for e in evidence if e.evidence_type == EvidenceType.API_RESPONSE.value
        )
        assert api_evidence.payload["response"]["status_code"] == 200
        assert api_evidence.sha256 and len(api_evidence.sha256) == 64
        assert api_evidence.captured_at is not None

    async def test_blocked_execution_records_reason_as_evidence(self, live_db):
        case = _make_test_case(
            live_db,
            test_code="TC-EV-0002",
            title="Blocked execution leaves an evidence trail",
            method="POST",
            path="/api/cbs/customers",
            expect_status=201,
            test_data={"first_name": "No", "last_name": "Approval", "mobile": "9866666666"},
            is_state_changing=True,
        )
        run = await ExecutionAgent().run_batch(live_db, AgentContext(), [case.id])
        execution = run.executions[0]
        evidence = (
            live_db.query(ExecutionEvidence)
            .filter(ExecutionEvidence.execution_id == execution.id)
            .all()
        )
        assert any("blocked" in e.label.lower() for e in evidence)


class TestBatchExecution:
    async def test_mixed_batch_reports_accurate_totals(self, live_db):
        passing = _make_test_case(
            live_db,
            test_code="TC-BATCH-0001",
            title="Reference data is readable",
            method="GET",
            path="/api/cbs/reference",
            expect_status=200,
            is_state_changing=False,
        )
        failing = _make_test_case(
            live_db,
            test_code="TC-BATCH-0002",
            title="Reference data wrongly expected to 404",
            method="GET",
            path="/api/cbs/reference",
            expect_status=404,
            is_state_changing=False,
        )
        blocked = _make_test_case(
            live_db,
            test_code="TC-BATCH-0003",
            title="Unapproved state change",
            method="POST",
            path="/api/cbs/customers",
            expect_status=201,
            test_data={"first_name": "A", "last_name": "B", "mobile": "9877777777"},
            is_state_changing=True,
        )
        run = await ExecutionAgent().run_batch(
            live_db,
            AgentContext(),
            [passing.id, failing.id, blocked.id],
            driver=ExecutionDriver.PYTEST_API.value,
        )
        assert run.total == 3
        assert run.passed == 1
        assert run.failed == 1
        assert run.blocked == 1
        assert run.status == ExecutionStatus.FAILED.value
        assert run.finished_at is not None

    async def test_empty_batch_is_rejected(self, live_db):
        from app.agents.base import AgentUnavailable

        with pytest.raises(AgentUnavailable):
            await ExecutionAgent().run_batch(live_db, AgentContext(), [])


class TestDriverAvailability:
    async def test_tinyfish_browser_driver_reports_missing_public_url(self, live_db):
        """TinyBrowser cannot reach localhost, and says so rather than failing oddly."""
        from app.agents.base import AgentUnavailable

        case = _make_test_case(
            live_db,
            test_code="TC-DRV-0001",
            title="Cloud browser driver without a public URL",
            method="GET",
            path="/api/cbs/reference",
            expect_status=200,
            is_state_changing=False,
        )
        with pytest.raises(AgentUnavailable) as exc_info:
            await ExecutionAgent().run_batch(
                live_db,
                AgentContext(),
                [case.id],
                driver=ExecutionDriver.TINYFISH_BROWSER.value,
            )
        message = str(exc_info.value).lower()
        assert "localhost" in message or "publicly reachable" in message
