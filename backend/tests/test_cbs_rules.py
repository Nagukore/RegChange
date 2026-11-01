"""Unit tests for the deterministic CBS rule engine."""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.cbs import rules as R


class TestCustomerValidation:
    """CBS-001."""

    def test_valid_payload_passes(self):
        from tests.conftest import valid_customer_payload

        assert R.validate_customer_payload(valid_customer_payload()).ok

    @pytest.mark.parametrize("missing", ["first_name", "last_name", "date_of_birth", "mobile"])
    def test_missing_mandatory_field_rejected(self, missing):
        from tests.conftest import valid_customer_payload

        payload = valid_customer_payload()
        payload[missing] = ""
        result = R.validate_customer_payload(payload)
        assert not result.ok
        codes = {v.code for v in result.violations}
        assert R.RejectionCode.MISSING_MANDATORY_FIELD in codes

    @pytest.mark.parametrize(
        "pan,expected",
        [
            ("ABCDE1234F", True),
            ("abcde1234f", True),   # normalised to upper before matching
            ("ABCD1234F", False),   # only four leading letters
            ("ABCDE12345", False),  # trailing char must be a letter
            ("ABCDEF1234", False),
            ("", False),
        ],
    )
    def test_pan_format(self, pan, expected):
        assert R.validate_pan(pan) is expected

    @pytest.mark.parametrize(
        "mobile,expected",
        [
            ("9876543210", True),
            ("+919876543210", True),
            ("5876543210", False),  # must start 6-9
            ("98765432", False),
            ("98765432101", False),
        ],
    )
    def test_mobile_format(self, mobile, expected):
        assert R.validate_mobile(mobile) is expected

    def test_pincode_cannot_start_with_zero(self):
        assert R.validate_pincode("682001") is True
        assert R.validate_pincode("082001") is False

    def test_future_date_of_birth_rejected(self):
        from tests.conftest import valid_customer_payload

        payload = valid_customer_payload(
            date_of_birth=(date.today() + timedelta(days=1)).isoformat()
        )
        result = R.validate_customer_payload(payload)
        assert not result.ok
        assert result.first.code == R.RejectionCode.INVALID_DATE_OF_BIRTH

    def test_minor_rejected_for_regular_individual_account(self):
        from tests.conftest import valid_customer_payload

        dob = date.today().replace(year=date.today().year - 10)
        result = R.validate_customer_payload(
            valid_customer_payload(date_of_birth=dob.isoformat())
        )
        assert not result.ok
        assert R.RejectionCode.CUSTOMER_UNDERAGE in {v.code for v in result.violations}


class TestKycDocuments:
    """CBS-004."""

    def test_two_verified_ovds_pass(self):
        from tests.conftest import valid_ovd_documents

        assert R.validate_kyc_documents(valid_ovd_documents(), "CDD").ok

    def test_no_documents_rejected(self):
        result = R.validate_kyc_documents([], "CDD")
        assert not result.ok
        assert result.first.code == R.RejectionCode.INSUFFICIENT_OVD

    def test_pan_alone_is_not_address_proof(self):
        result = R.validate_kyc_documents(
            [{"doc_type": "PAN", "number": "ABCDE1234F", "verified": True}], "CDD"
        )
        assert not result.ok
        assert R.RejectionCode.NO_ADDRESS_PROOF in {v.code for v in result.violations}

    def test_expired_document_rejected(self):
        expired = (date.today() - timedelta(days=30)).isoformat()
        result = R.validate_kyc_documents(
            [{"doc_type": "PASSPORT", "expiry_date": expired, "verified": True}], "CDD"
        )
        assert not result.ok
        assert R.RejectionCode.EXPIRED_DOCUMENT in {v.code for v in result.violations}

    def test_unaccepted_document_type_rejected(self):
        result = R.validate_kyc_documents(
            [{"doc_type": "GYM_MEMBERSHIP_CARD", "verified": True}], "CDD"
        )
        assert not result.ok
        assert R.RejectionCode.UNACCEPTED_OVD_TYPE in {v.code for v in result.violations}

    def test_edd_requires_two_documents(self):
        single = [
            {"doc_type": "PASSPORT", "expiry_date": "2033-01-01", "verified": True},
        ]
        assert R.validate_kyc_documents(single, "CDD").ok
        edd_result = R.validate_kyc_documents(single, "EDD")
        assert not edd_result.ok
        assert R.RejectionCode.INSUFFICIENT_OVD in {v.code for v in edd_result.violations}

    def test_unverified_documents_do_not_count(self):
        result = R.validate_kyc_documents(
            [{"doc_type": "PASSPORT", "expiry_date": "2033-01-01", "verified": False}], "CDD"
        )
        assert not result.ok
        assert R.RejectionCode.INSUFFICIENT_OVD in {v.code for v in result.violations}


class TestRiskClassification:
    """CBS-015."""

    def test_plain_resident_individual_is_low(self):
        category, reasons = R.classify_customer_risk(is_pep=False, country="IN")
        assert category == "LOW"
        assert reasons

    def test_pep_is_high(self):
        category, _ = R.classify_customer_risk(is_pep=True, country="IN")
        assert category == "HIGH"

    def test_high_risk_country_is_high(self):
        category, _ = R.classify_customer_risk(is_pep=False, country="KP")
        assert category == "HIGH"

    def test_non_individual_is_medium(self):
        category, _ = R.classify_customer_risk(
            is_pep=False, country="IN", customer_type="COMPANY"
        )
        assert category == "MEDIUM"

    def test_edd_required_for_high_and_pep(self):
        assert R.requires_enhanced_due_diligence("HIGH", False) is True
        assert R.requires_enhanced_due_diligence("LOW", True) is True
        assert R.requires_enhanced_due_diligence("LOW", False) is False

    @pytest.mark.parametrize(
        "category,years", [("LOW", 10), ("MEDIUM", 8), ("HIGH", 2)]
    )
    def test_periodicity_by_risk(self, category, years):
        base = date(2025, 6, 15)
        assert R.next_kyc_due_date(category, base) == date(2025 + years, 6, 15)

    def test_leap_day_due_date_does_not_crash(self):
        # 29 Feb + 2 years lands on a non-leap year.
        assert R.next_kyc_due_date("HIGH", date(2024, 2, 29)) == date(2026, 2, 28)


class TestAccountOpening:
    """CBS-007 / CBS-008."""

    def _base(self, **kw):
        args = {
            "customer_status": "ACTIVE",
            "kyc_status": "VERIFIED",
            "next_kyc_due": date.today() + timedelta(days=365),
            "account_type": "SAVINGS",
            "product_code": "SB-REG",
            "risk_category": "LOW",
            "is_pep": False,
            "edd_completed": False,
        }
        args.update(kw)
        return R.validate_account_opening(**args)

    def test_happy_path(self):
        assert self._base().ok

    def test_unverified_kyc_blocks_opening(self):
        result = self._base(kyc_status="PENDING")
        assert not result.ok
        assert R.RejectionCode.KYC_NOT_VERIFIED in {v.code for v in result.violations}

    def test_inactive_customer_blocks_opening(self):
        result = self._base(customer_status="PENDING_APPROVAL")
        assert R.RejectionCode.CUSTOMER_NOT_ACTIVE in {v.code for v in result.violations}

    def test_lapsed_kyc_blocks_opening(self):
        result = self._base(next_kyc_due=date.today() - timedelta(days=1))
        assert R.RejectionCode.KYC_EXPIRED in {v.code for v in result.violations}

    def test_high_risk_without_edd_blocks_opening(self):
        result = self._base(risk_category="HIGH", edd_completed=False)
        assert R.RejectionCode.EDD_REQUIRED in {v.code for v in result.violations}

    def test_high_risk_with_edd_passes(self):
        assert self._base(risk_category="HIGH", edd_completed=True).ok

    def test_unknown_product_rejected(self):
        result = self._base(product_code="SB-NOPE")
        assert R.RejectionCode.UNKNOWN_PRODUCT in {v.code for v in result.violations}


class TestTransactionValidation:
    """CBS-010 / CBS-011 / CBS-012."""

    def _debit(self, **kw):
        args = {
            "txn_type": "DEBIT",
            "amount": Decimal("1000"),
            "account_status": "ACTIVE",
            "restrictions": [],
            "balance": Decimal("50000"),
            "minimum_balance": Decimal("1000"),
            "per_txn_limit": Decimal("50000"),
            "daily_debit_limit": Decimal("100000"),
            "debits_today": Decimal("0"),
            "max_balance": Decimal("0"),
            "fee": Decimal("0"),
        }
        args.update(kw)
        return R.validate_transaction(**args)

    def test_valid_debit_passes(self):
        assert self._debit().ok

    def test_zero_amount_rejected(self):
        result = self._debit(amount=Decimal("0"))
        assert result.first.code == R.RejectionCode.INVALID_AMOUNT

    def test_negative_amount_rejected(self):
        assert self._debit(amount=Decimal("-5")).first.code == R.RejectionCode.INVALID_AMOUNT

    def test_insufficient_balance_rejected(self):
        result = self._debit(amount=Decimal("90000"), per_txn_limit=Decimal("100000"))
        assert R.RejectionCode.INSUFFICIENT_BALANCE in {v.code for v in result.violations}

    def test_minimum_balance_breach_rejected(self):
        # 50000 balance, min 1000 -- a 49500 debit leaves 500.
        result = self._debit(amount=Decimal("49500"))
        assert R.RejectionCode.MINIMUM_BALANCE_BREACH in {v.code for v in result.violations}

    def test_boundary_exactly_at_minimum_balance_passes(self):
        # Leaves exactly the minimum balance, which is permitted.
        assert self._debit(amount=Decimal("49000")).ok

    def test_per_transaction_limit_exceeded(self):
        result = self._debit(amount=Decimal("60000"), balance=Decimal("500000"))
        assert R.RejectionCode.PER_TXN_LIMIT_EXCEEDED in {v.code for v in result.violations}

    def test_boundary_exactly_at_per_txn_limit_passes(self):
        assert self._debit(
            amount=Decimal("50000"), balance=Decimal("500000"), minimum_balance=Decimal("0")
        ).ok

    def test_daily_limit_exceeded(self):
        result = self._debit(
            amount=Decimal("20000"),
            debits_today=Decimal("90000"),
            balance=Decimal("500000"),
            minimum_balance=Decimal("0"),
        )
        assert R.RejectionCode.DAILY_LIMIT_EXCEEDED in {v.code for v in result.violations}

    def test_debit_freeze_blocks_debit(self):
        result = self._debit(restrictions=["DEBIT_FREEZE"])
        assert R.RejectionCode.ACCOUNT_RESTRICTED in {v.code for v in result.violations}

    def test_kyc_non_compliant_blocks_debit(self):
        result = self._debit(restrictions=["KYC_NON_COMPLIANT"])
        assert R.RejectionCode.ACCOUNT_RESTRICTED in {v.code for v in result.violations}

    def test_debit_freeze_does_not_block_credit(self):
        result = R.validate_transaction(
            txn_type="CREDIT",
            amount=Decimal("1000"),
            account_status="ACTIVE",
            restrictions=["DEBIT_FREEZE"],
            balance=Decimal("0"),
            minimum_balance=Decimal("0"),
            per_txn_limit=Decimal("50000"),
            daily_debit_limit=Decimal("100000"),
            debits_today=Decimal("0"),
        )
        assert result.ok

    def test_total_freeze_blocks_credit(self):
        result = R.validate_transaction(
            txn_type="CREDIT",
            amount=Decimal("1000"),
            account_status="ACTIVE",
            restrictions=["TOTAL_FREEZE"],
            balance=Decimal("0"),
            minimum_balance=Decimal("0"),
            per_txn_limit=Decimal("50000"),
            daily_debit_limit=Decimal("100000"),
            debits_today=Decimal("0"),
        )
        assert R.RejectionCode.ACCOUNT_RESTRICTED in {v.code for v in result.violations}

    def test_small_account_balance_ceiling_enforced(self):
        result = R.validate_transaction(
            txn_type="CREDIT",
            amount=Decimal("10000"),
            account_status="ACTIVE",
            restrictions=[],
            balance=Decimal("45000"),
            minimum_balance=Decimal("0"),
            per_txn_limit=Decimal("10000"),
            daily_debit_limit=Decimal("10000"),
            debits_today=Decimal("0"),
            max_balance=Decimal("50000"),
        )
        assert R.RejectionCode.MAX_BALANCE_EXCEEDED in {v.code for v in result.violations}

    def test_dormant_account_cannot_transact(self):
        result = self._debit(account_status="DORMANT")
        assert R.RejectionCode.ACCOUNT_NOT_ACTIVE in {v.code for v in result.violations}

    def test_fee_counts_towards_available_balance(self):
        # 50000 balance, min 0, debit 50000 + 15 fee must fail.
        result = self._debit(
            amount=Decimal("50000"), minimum_balance=Decimal("0"), fee=Decimal("15")
        )
        assert R.RejectionCode.INSUFFICIENT_BALANCE in {v.code for v in result.violations}


class TestAmlMonitoring:
    def test_large_amount_flagged(self):
        flagged, reason = R.evaluate_aml_monitoring(
            amount=Decimal("1000000"), channel="BRANCH", risk_category="LOW"
        )
        assert flagged and reason

    def test_small_amount_not_flagged(self):
        flagged, _ = R.evaluate_aml_monitoring(
            amount=Decimal("5000"), channel="BRANCH", risk_category="LOW"
        )
        assert flagged is False

    def test_high_risk_customer_lower_threshold(self):
        flagged, _ = R.evaluate_aml_monitoring(
            amount=Decimal("250000"), channel="BRANCH", risk_category="HIGH"
        )
        assert flagged is True

    def test_cash_channel_threshold(self):
        flagged, _ = R.evaluate_aml_monitoring(
            amount=Decimal("600000"), channel="CASH", risk_category="LOW"
        )
        assert flagged is True


class TestMakerChecker:
    """CBS-013."""

    def test_different_checker_allowed(self):
        assert R.validate_checker_decision(
            maker_id="maker@bank", checker_id="checker@bank", current_status="PENDING"
        ).ok

    def test_self_approval_rejected(self):
        result = R.validate_checker_decision(
            maker_id="maker@bank", checker_id="maker@bank", current_status="PENDING"
        )
        assert R.RejectionCode.SELF_APPROVAL_FORBIDDEN in {v.code for v in result.violations}

    def test_self_approval_case_insensitive(self):
        result = R.validate_checker_decision(
            maker_id="Maker@Bank", checker_id="maker@bank", current_status="PENDING"
        )
        assert not result.ok

    def test_already_decided_rejected(self):
        result = R.validate_checker_decision(
            maker_id="maker@bank", checker_id="checker@bank", current_status="APPROVED"
        )
        assert R.RejectionCode.TASK_ALREADY_DECIDED in {v.code for v in result.violations}


class TestAccountOperability:
    """CBS-014."""

    def test_active_unrestricted_is_fully_operable(self):
        state = R.resolve_account_operability(
            account_status="ACTIVE", restrictions=[], kyc_status="VERIFIED"
        )
        assert state["debit_allowed"] and state["credit_allowed"]

    def test_debit_freeze_allows_credit_only(self):
        state = R.resolve_account_operability(
            account_status="ACTIVE", restrictions=["DEBIT_FREEZE"], kyc_status="VERIFIED"
        )
        assert state["debit_allowed"] is False
        assert state["credit_allowed"] is True

    def test_closed_account_is_inoperable(self):
        state = R.resolve_account_operability(
            account_status="CLOSED", restrictions=[], kyc_status="VERIFIED"
        )
        assert state["debit_allowed"] is False and state["credit_allowed"] is False


class TestFeesAndIdentifiers:
    def test_credit_has_no_fee(self):
        assert R.compute_transaction_fee(
            txn_type="CREDIT", amount=Decimal("100000"), channel="NEFT"
        ) == Decimal("0")

    def test_neft_fee_tiers(self):
        assert R.compute_transaction_fee(
            txn_type="DEBIT", amount=Decimal("5000"), channel="NEFT"
        ) == Decimal("2.50")
        assert R.compute_transaction_fee(
            txn_type="DEBIT", amount=Decimal("50000"), channel="NEFT"
        ) == Decimal("5.00")
        assert R.compute_transaction_fee(
            txn_type="DEBIT", amount=Decimal("500000"), channel="RTGS"
        ) == Decimal("15.00")

    def test_branch_channel_has_no_fee(self):
        assert R.compute_transaction_fee(
            txn_type="DEBIT", amount=Decimal("5000"), channel="BRANCH"
        ) == Decimal("0")

    def test_ucic_format_and_uniqueness(self):
        codes = {R.generate_ucic() for _ in range(200)}
        assert len(codes) > 190  # collisions must be rare
        for code in codes:
            assert code.startswith("UC") and len(code) == 12 and code[2:].isdigit()

    def test_account_number_includes_branch_prefix(self):
        number = R.generate_account_number("SIM002")
        assert number.startswith("SIM002") and len(number) == 14
