"""Clearly-labelled demo fixtures for running the pipeline without credentials.

WHY THIS EXISTS
TinyFish and Gemini both need API keys. Without them, discovery, extraction,
analysis, impact mapping and test generation cannot run, and the brief is
explicit that mocked results must never be presented as real agent output.
This module takes the other honest route it allows: clearly labelled demo
fixtures.

WHAT IS AND IS NOT REAL HERE
  * Document metadata (title, circular number, source URL, dates) is REAL and
    was verified against rbi.org.in on 2026-10-31.
  * The document BODY TEXT below is SYNTHETIC. It is written in the style of a
    KYC direction to exercise the parser and make the pipeline demonstrable.
    It is NOT the regulator's wording and must never be cited as such.
  * Clause segmentation, hashing, version diffing, candidate-rule retrieval and
    test execution are all REAL code paths running on this synthetic input.

Everything created here is stamped `provenance = DEMO_FIXTURE`, which the UI
renders with a distinct "Demo fixture" badge on every screen, and each document
carries an `access_limitation` naming the credential needed for the real text.
"""
from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.clause_parser import (
    content_hash,
    detect_applicable_entities,
    extract_applicability,
    extract_effective_date,
    parse_clauses,
)
from app.agents.retrieval import normalise_scores, score_rules
from app.models.enums import (
    AssertionBasis,
    AutomationType,
    ChangeType,
    DataProvenance,
    DocumentStatus,
    ImpactCategory,
    Priority,
    ReviewStatus,
    TestCategory,
)
from app.models.mapping import BusinessRule, ImpactMapping
from app.models.regulatory import RegulatoryChange, RegulatoryClause, RegulatoryDocument
from app.models.testing import TestCase

# A banner prepended to every fixture document body so the synthetic nature of
# the text is visible even if someone reads the raw record directly.
SYNTHETIC_BANNER = (
    "> [DEMO FIXTURE] The body text below is SYNTHETIC, written to exercise this\n"
    "> pipeline without API credentials. It is NOT the wording of any real\n"
    "> regulatory instrument and must not be cited as such. Configure\n"
    "> TINYFISH_API_KEY to retrieve the authentic document from the official\n"
    "> source URL recorded on this record.\n\n"
)

PRIOR_VERSION_BODY = """# Know Your Customer Direction (illustrative prior version)

## Chapter I - Preliminary

1. Short title and commencement
This illustrative direction shall come into force with effect from 25 February 2016.

2. Applicability
This illustrative direction applies to all Commercial Banks, Co-operative Banks and
Non-Banking Financial Companies.

## Chapter II - Customer Due Diligence

3. Customer Acceptance Policy
Regulated entities shall frame a Customer Acceptance Policy approved by the Board of
Directors. No account shall be opened in an anonymous or fictitious name.

4. Customer Identification Procedure
Regulated entities shall identify every customer and verify that identity using an
Officially Valid Document. A self-attested copy shall be obtained along with the
original for verification.

5. Unique Customer Identification Code
Regulated entities shall allot a Unique Customer Identification Code to each customer
so that a single customer is not assigned multiple identifiers across the entity.

## Chapter III - Ongoing Due Diligence

38. Periodic updation of KYC
Regulated entities shall carry out periodic updation of KYC records at least once
every ten years for low risk customers, once every eight years for medium risk
customers and once every two years for high risk customers.

39. Risk categorisation
Regulated entities shall categorise each customer as low, medium or high risk based on
the customer profile, and shall apply enhanced due diligence to high risk customers.

40. Record management
Regulated entities shall maintain records of all transactions for a period of five
years from the date of the transaction.
"""

CURRENT_VERSION_BODY = """# Commercial Banks Know Your Customer Directions (illustrative current version)

## Chapter I - Preliminary

1. Short title and commencement
This illustrative direction shall come into force with effect from 28 November 2025.

2. Applicability
This illustrative direction applies to all Commercial Banks, including Small Finance
Banks, referred to in this direction as banks.

## Chapter II - Customer Due Diligence

3. Customer Acceptance Policy
Banks shall frame a Customer Acceptance Policy approved by the Board of Directors. No
account shall be opened in an anonymous or fictitious name.

4. Customer Identification Procedure
Banks shall identify every customer and verify that identity using an Officially Valid
Document. A self-attested copy shall be obtained along with the original for
verification. At least one document relied upon shall evidence the current address of
the customer.

5. Unique Customer Identification Code
Banks shall allot a Unique Customer Identification Code to each customer so that a
single customer is not assigned multiple identifiers across the bank.

## Chapter III - Ongoing Due Diligence

38. Periodic updation of KYC
Banks shall carry out periodic updation of KYC records at least once every ten years
for low risk customers, once every eight years for medium risk customers and once every
two years for high risk customers. Where there is no change in KYC details, a
self-declaration obtained through an authorised business correspondent shall be
acceptable, and the bank shall remain ultimately responsible for the updation.

39. Risk categorisation
Banks shall categorise each customer as low, medium or high risk based on the customer
profile, and shall apply enhanced due diligence to high risk customers and to
politically exposed persons.

41. Digital KYC application
Banks shall develop an application for digital KYC and shall undertake the customer
identification process only through that authenticated application.

42. Transaction monitoring threshold
Banks shall monitor and record transactions of ten lakh rupees and above for the
purpose of ongoing due diligence.
"""

# Requirements the fixture presents. Each `source_quote` is copied verbatim from
# the synthetic bodies above, so quote verification behaves exactly as it does
# on live text.
FIXTURE_REQUIREMENTS: list[dict] = [
    {
        "title": "Periodic KYC updation intervals by risk category",
        "requirement_text": (
            "Banks must re-verify KYC records at defined intervals determined by the "
            "customer risk category: ten years for low risk, eight years for medium "
            "risk and two years for high risk."
        ),
        "change_type": ChangeType.AMENDED_REQUIREMENT.value,
        "assertion_basis": AssertionBasis.EXPLICIT_REQUIREMENT.value,
        "source_quote": (
            "Banks shall carry out periodic updation of KYC records at least once every "
            "ten years\nfor low risk customers, once every eight years for medium risk "
            "customers and once every\ntwo years for high risk customers."
        ),
        "source_reference": "Para 38",
        "previous_text": (
            "Regulated entities shall carry out periodic updation of KYC records at least "
            "once\nevery ten years for low risk customers, once every eight years for "
            "medium risk\ncustomers and once every two years for high risk customers."
        ),
        "banking_module": "KYC_AML",
        "priority": Priority.HIGH.value,
        "confidence": 0.94,
    },
    {
        "title": "Self-declaration via business correspondent for unchanged KYC",
        "requirement_text": (
            "Where KYC details are unchanged, a self-declaration collected through an "
            "authorised business correspondent is acceptable for periodic updation, with "
            "the bank remaining ultimately responsible."
        ),
        "change_type": ChangeType.NEW_REQUIREMENT.value,
        "assertion_basis": AssertionBasis.EXPLICIT_REQUIREMENT.value,
        "source_quote": (
            "Where there is no change in KYC details, a\nself-declaration obtained through "
            "an authorised business correspondent shall be\nacceptable, and the bank shall "
            "remain ultimately responsible for the updation."
        ),
        "source_reference": "Para 38",
        "previous_text": "",
        "banking_module": "KYC_AML",
        "priority": Priority.MEDIUM.value,
        "confidence": 0.88,
    },
    {
        "title": "At least one document must evidence current address",
        "requirement_text": (
            "During customer identification, at least one Officially Valid Document relied "
            "upon must evidence the customer's current address."
        ),
        "change_type": ChangeType.NEW_REQUIREMENT.value,
        "assertion_basis": AssertionBasis.EXPLICIT_REQUIREMENT.value,
        "source_quote": (
            "At least one document relied upon shall evidence the current address of\nthe "
            "customer."
        ),
        "source_reference": "Para 4",
        "previous_text": "",
        "banking_module": "CUSTOMER_ONBOARDING",
        "priority": Priority.HIGH.value,
        "confidence": 0.91,
    },
    {
        "title": "Enhanced due diligence extended to politically exposed persons",
        "requirement_text": (
            "Enhanced due diligence must be applied to high risk customers and, newly, to "
            "politically exposed persons."
        ),
        "change_type": ChangeType.AMENDED_REQUIREMENT.value,
        "assertion_basis": AssertionBasis.EXPLICIT_REQUIREMENT.value,
        "source_quote": (
            "shall apply enhanced due diligence to high risk customers and to\npolitically "
            "exposed persons."
        ),
        "source_reference": "Para 39",
        "previous_text": (
            "shall apply enhanced due diligence to high risk customers."
        ),
        "banking_module": "KYC_AML",
        "priority": Priority.HIGH.value,
        "confidence": 0.9,
    },
    {
        "title": "Transaction monitoring threshold of ten lakh rupees",
        "requirement_text": (
            "Transactions of ten lakh rupees and above must be monitored and recorded for "
            "ongoing due diligence."
        ),
        "change_type": ChangeType.NEW_REQUIREMENT.value,
        "assertion_basis": AssertionBasis.EXPLICIT_REQUIREMENT.value,
        "source_quote": (
            "Banks shall monitor and record transactions of ten lakh rupees and above for "
            "the\npurpose of ongoing due diligence."
        ),
        "source_reference": "Para 42",
        "previous_text": "",
        "banking_module": "TRANSACTIONS",
        "priority": Priority.MEDIUM.value,
        "confidence": 0.86,
    },
]

# Executable tests the fixture creates. These are REAL: they run against the
# live simulated CBS through the normal execution agent.
FIXTURE_TESTS: list[dict] = [
    {
        "requirement_index": 0,
        "rule_code": "CBS-015",
        # The executor provisions a plain resident individual, which classifies
        # LOW, so the derived periodicity is the ten-year band.
        "title": "Low risk customer receives a ten year KYC periodicity",
        "objective": (
            "Verify the periodic updation interval derived by risk classification for a "
            "low risk individual customer."
        ),
        "category": TestCategory.POSITIVE.value,
        "priority": Priority.HIGH.value,
        "method": "POST",
        "path": "/api/cbs/customers/{customer_id}/risk-classification",
        "expect_status": 200,
        "expect_json_path": "periodicity_years",
        "expect_json_value": "10",
        "test_data": {},
        "expected_result": "HTTP 200 with periodicity_years reflecting the customer risk band",
        "state_changing": True,
        "steps": [
            {"step": 1, "action": "Provision a simulated customer", "expected": "Customer created"},
            {
                "step": 2,
                "action": "POST to the risk-classification endpoint",
                "expected": "HTTP 200 with risk_category and periodicity_years",
            },
        ],
    },
    {
        "requirement_index": 2,
        "rule_code": "CBS-004",
        "title": "KYC with PAN alone is rejected for lacking address proof",
        "objective": (
            "Verify that a document set evidencing identity but not address fails CDD."
        ),
        "category": TestCategory.NEGATIVE.value,
        "priority": Priority.HIGH.value,
        "method": "POST",
        "path": "/api/cbs/customers/{customer_id}/kyc/verify",
        "expect_status": 422,
        "expect_json_path": "rejection_code",
        "expect_json_value": "NO_ADDRESS_PROOF",
        "test_data": {
            "kyc_type": "CDD",
            "documents": '[{"doc_type":"PAN","number":"ABCDE1234F","verified":true}]',
            "verification_mode": "IN_PERSON",
        },
        "expected_result": "HTTP 422 with rejection_code NO_ADDRESS_PROOF",
        "state_changing": True,
        "steps": [
            {"step": 1, "action": "Provision a simulated customer", "expected": "Customer created"},
            {
                "step": 2,
                "action": "Submit a verified PAN as the only document",
                "expected": "Rejected: PAN evidences identity but not address",
            },
        ],
    },
    {
        "requirement_index": 2,
        "rule_code": "CBS-004",
        "title": "KYC with passport plus PAN is accepted",
        "objective": "Verify the compliant document set completes Customer Due Diligence.",
        "category": TestCategory.POSITIVE.value,
        "priority": Priority.HIGH.value,
        "method": "POST",
        "path": "/api/cbs/customers/{customer_id}/kyc/verify",
        "expect_status": 200,
        "expect_json_path": "status",
        "expect_json_value": "VERIFIED",
        "test_data": {
            "kyc_type": "CDD",
            "documents": (
                '[{"doc_type":"PASSPORT","number":"SIMPASS1","expiry_date":"2033-01-01",'
                '"verified":true},{"doc_type":"PAN","number":"ABCDE1234F","verified":true}]'
            ),
            "verification_mode": "IN_PERSON",
        },
        "expected_result": "HTTP 200 with the KYC record status VERIFIED",
        "state_changing": True,
        "steps": [
            {"step": 1, "action": "Provision a simulated customer", "expected": "Customer created"},
            {
                "step": 2,
                "action": "Submit a verified passport and PAN",
                "expected": "KYC verified and a next due date derived",
            },
        ],
    },
    {
        "requirement_index": 4,
        "rule_code": "CBS-012",
        "title": "Debit above the per-transaction limit is rejected",
        "objective": "Verify the per-transaction ceiling is enforced before posting.",
        "category": TestCategory.BOUNDARY.value,
        "priority": Priority.HIGH.value,
        "method": "POST",
        "path": "/api/cbs/transactions",
        "expect_status": 422,
        "expect_json_path": "rejection_code",
        "expect_json_value": "PER_TXN_LIMIT_EXCEEDED",
        "test_data": {"txn_type": "DEBIT", "amount": "60000", "channel": "BRANCH"},
        "expected_result": "HTTP 422 with rejection_code PER_TXN_LIMIT_EXCEEDED",
        "state_changing": True,
        "steps": [
            {
                "step": 1,
                "action": "Provision an ACTIVE savings account with a funded balance",
                "expected": "Account open with SB-REG limits",
            },
            {
                "step": 2,
                "action": "Post a 60,000 debit against a 50,000 per-transaction limit",
                "expected": "Rejected with PER_TXN_LIMIT_EXCEEDED",
            },
        ],
    },
    {
        "requirement_index": 4,
        "rule_code": "CBS-012",
        "title": "Configured KYC periodicity has not drifted",
        "objective": (
            "Regression guard: the rule parameters the CBS exposes must still match the "
            "intervals this requirement depends on."
        ),
        "category": TestCategory.REGRESSION.value,
        "priority": Priority.MEDIUM.value,
        "method": "GET",
        "path": "/api/cbs/reference",
        "expect_status": 200,
        "expect_json_path": "kyc_periodicity_years.HIGH",
        "expect_json_value": "2",
        "test_data": {},
        "expected_result": "HTTP 200 with the HIGH risk periodicity still set to 2 years",
        "state_changing": False,
        "steps": [
            {
                "step": 1,
                "action": "Read the CBS reference parameters",
                "expected": "HIGH risk periodicity is 2 years",
            },
        ],
    },
]

DEMO_SOURCE_URL_CURRENT = "https://rbidocs.rbi.org.in/rdocs/notification/PDFs/169MD.PDF"
DEMO_SOURCE_URL_PRIOR = "https://www.rbi.org.in/Scripts/BS_ViewMasDirections.aspx?id=11566"

ACCESS_LIMITATION = (
    "DEMO FIXTURE: the stored body text is synthetic, not the regulator's wording. "
    "Set TINYFISH_API_KEY and re-extract this document to retrieve the authentic text "
    "from the official source URL."
)


def _create_document(
    db: Session,
    *,
    title: str,
    circular_number: str,
    source_url: str,
    body: str,
    publication_date: date,
    version: int,
    is_current: bool,
    supersedes: RegulatoryDocument | None = None,
) -> RegulatoryDocument:
    text = SYNTHETIC_BANNER + body
    applicability = extract_applicability(body)

    document = RegulatoryDocument(
        title=title,
        circular_number=circular_number,
        document_type="MASTER_DIRECTION",
        source_url=source_url,
        version=version,
        is_current=is_current,
        supersedes_id=supersedes.id if supersedes else None,
        publication_date=publication_date,
        effective_date=extract_effective_date(body),
        applicability_text=applicability,
        applicable_institution_types=detect_applicable_entities(applicability or body[:20000]),
        banking_domains=["KYC_AML", "CUSTOMER_ONBOARDING", "ACCOUNT_MANAGEMENT"],
        content_hash=content_hash(text),
        raw_text=text,
        content_format="markdown",
        char_count=len(text),
        status=DocumentStatus.ANALYSED.value,
        provenance=DataProvenance.DEMO_FIXTURE.value,
        access_limitation=ACCESS_LIMITATION,
        fetched_at=datetime.now(UTC),
    )
    db.add(document)
    db.flush()

    for parsed in parse_clauses(text):
        db.add(
            RegulatoryClause(
                document_id=document.id,
                clause_reference=parsed.clause_reference,
                heading=parsed.heading,
                text=parsed.text,
                ordinal=parsed.ordinal,
                char_start=parsed.char_start,
                char_end=parsed.char_end,
                clause_hash=parsed.clause_hash,
                banking_domains=document.banking_domains,
                provenance=DataProvenance.DETERMINISTIC.value,
            )
        )
    db.flush()
    return document


def seed_demo_fixture(db: Session, *, reset: bool = False) -> dict:
    """Create the labelled demo dataset. Idempotent unless `reset` is set."""
    existing = db.execute(
        select(RegulatoryDocument).where(
            RegulatoryDocument.source_url == DEMO_SOURCE_URL_CURRENT,
            RegulatoryDocument.provenance == DataProvenance.DEMO_FIXTURE.value,
        )
    ).scalars().first()

    if reset:
        # Always clean up on reset, even when no demo document remains: an
        # interrupted previous reset can leave orphaned demo test cases whose
        # unique `test_code` would otherwise collide here.
        _delete_demo_records(db)
        db.flush()
    elif existing is not None:
        return {
            "created": False,
            "reason": "Demo fixture already present. Pass reset=true to rebuild it.",
            "document_id": str(existing.id),
        }

    prior = _create_document(
        db,
        title="Master Direction - Know Your Customer (KYC) Direction, 2016 [DEMO FIXTURE TEXT]",
        circular_number="RBI/DBR/2015-16/18",
        source_url=DEMO_SOURCE_URL_PRIOR,
        body=PRIOR_VERSION_BODY,
        publication_date=date(2016, 2, 25),
        version=1,
        is_current=True,
    )
    current = _create_document(
        db,
        title=(
            "Reserve Bank of India (Commercial Banks - Know Your Customer) Directions, "
            "2025 [DEMO FIXTURE TEXT]"
        ),
        circular_number="RBI/DOR/2025-26/169",
        source_url=DEMO_SOURCE_URL_CURRENT,
        body=CURRENT_VERSION_BODY,
        publication_date=date(2025, 11, 28),
        version=1,
        is_current=True,
    )

    clauses = {clause.clause_reference: clause for clause in current.clauses}
    rules = list(
        db.execute(select(BusinessRule).where(BusinessRule.is_active.is_(True))).scalars().all()
    )

    changes: list[RegulatoryChange] = []
    for index, entry in enumerate(FIXTURE_REQUIREMENTS, start=1):
        clause = clauses.get(entry["source_reference"])
        change = RegulatoryChange(
            document_id=current.id,
            clause_id=clause.id if clause else None,
            compared_to_document_id=prior.id,
            requirement_code=f"REQ-D{index:03d}",
            title=entry["title"],
            requirement_text=entry["requirement_text"],
            change_type=entry["change_type"],
            assertion_basis=entry["assertion_basis"],
            source_quote=entry["source_quote"],
            source_reference=entry["source_reference"],
            previous_text=entry["previous_text"] or None,
            applicable_entities=["COMMERCIAL_BANK", "SMALL_FINANCE_BANK"],
            primary_banking_module=entry["banking_module"],
            effective_date=date(2025, 11, 28),
            priority=entry["priority"],
            confidence=entry["confidence"],
            review_status=ReviewStatus.PENDING_REVIEW.value,
            model_name=None,
            provenance=DataProvenance.DEMO_FIXTURE.value,
        )
        db.add(change)
        changes.append(change)
    db.flush()

    # Candidate retrieval is the real deterministic engine, so the scores shown
    # in the UI are genuine for this input.
    mappings: list[ImpactMapping] = []
    for change, entry in zip(changes, FIXTURE_REQUIREMENTS, strict=True):
        query = f"{change.title} {change.requirement_text} {change.source_quote}"
        candidates = normalise_scores(
            score_rules(query, rules, banking_module=change.primary_banking_module, top_k=3)
        )
        for rank, candidate in enumerate(candidates[:2]):
            mapping = ImpactMapping(
                change_id=change.id,
                business_rule_id=candidate.rule.id,
                impact_category=_category_for(candidate.rule.banking_module),
                banking_module=candidate.rule.banking_module,
                priority=entry["priority"],
                rationale=(
                    f"Lexical retrieval matched this requirement to {candidate.rule.rule_code} "
                    f"({candidate.rule.name}) on the terms "
                    f"{', '.join(candidate.matched_terms[:6]) or 'n/a'}. The rule governs "
                    f"{candidate.rule.description[:160]}"
                ),
                assertion_basis=(
                    AssertionBasis.AI_INTERPRETATION.value
                    if rank == 0
                    else AssertionBasis.PROPOSED_CONTROL.value
                ),
                affected_fields=candidate.rule.affected_fields or [],
                affected_workflows=[candidate.rule.ui_route] if candidate.rule.ui_route else [],
                regression_risk=Priority.MEDIUM.value if rank == 0 else Priority.LOW.value,
                confidence=round(0.6 + 0.3 * candidate.score, 3),
                retrieval_score=candidate.score,
                review_status=ReviewStatus.PENDING_REVIEW.value,
                model_name=None,
                provenance=DataProvenance.DEMO_FIXTURE.value,
            )
            db.add(mapping)
            mappings.append(mapping)
    db.flush()

    # Index mappings by (requirement, rule) so fixture tests attach correctly.
    by_key = {
        (mapping.change_id, mapping.business_rule.rule_code): mapping
        for mapping in mappings
        if mapping.business_rule
    }

    # A fixture test names the rule it exercises. Retrieval does not always rank
    # that rule in its top two, so ensure the mapping exists -- otherwise the
    # traceability chain would skip the impact hop for that test.
    for entry in FIXTURE_TESTS:
        change = changes[entry["requirement_index"]]
        rule = next((r for r in rules if r.rule_code == entry["rule_code"]), None)
        if rule is None or (change.id, rule.rule_code) in by_key:
            continue
        mapping = ImpactMapping(
            change_id=change.id,
            business_rule_id=rule.id,
            impact_category=_category_for(rule.banking_module),
            banking_module=rule.banking_module,
            priority=change.priority,
            rationale=(
                f"{rule.rule_code} ({rule.name}) implements the control this requirement "
                f"governs: {rule.description[:180]}"
            ),
            assertion_basis=AssertionBasis.AI_INTERPRETATION.value,
            affected_fields=rule.affected_fields or [],
            affected_workflows=[rule.ui_route] if rule.ui_route else [],
            regression_risk=Priority.MEDIUM.value,
            confidence=0.78,
            retrieval_score=None,
            review_status=ReviewStatus.PENDING_REVIEW.value,
            model_name=None,
            provenance=DataProvenance.DEMO_FIXTURE.value,
        )
        db.add(mapping)
        db.flush()
        mappings.append(mapping)
        by_key[(change.id, rule.rule_code)] = mapping

    test_cases: list[TestCase] = []
    for index, entry in enumerate(FIXTURE_TESTS, start=1):
        change = changes[entry["requirement_index"]]
        rule = next((r for r in rules if r.rule_code == entry["rule_code"]), None)
        mapping = by_key.get((change.id, entry["rule_code"]))

        case = TestCase(
            test_code=f"TC-DEMO-{index:04d}",
            impact_mapping_id=mapping.id if mapping else None,
            change_id=change.id,
            business_rule_id=rule.id if rule else None,
            title=entry["title"],
            objective=entry["objective"],
            banking_module=rule.banking_module if rule else "CROSS_CUTTING",
            category=entry["category"],
            priority=entry["priority"],
            automation_type=AutomationType.API.value,
            preconditions=["A simulated customer or account is provisioned by the executor"],
            test_data=entry["test_data"],
            steps=entry["steps"],
            expected_result=entry["expected_result"],
            execution_spec={
                "driver": "PYTEST_API",
                "method": entry["method"],
                "path": entry["path"],
                "expect_status": entry["expect_status"],
                "expect_json_path": entry["expect_json_path"],
                "expect_json_value": entry["expect_json_value"],
            },
            is_state_changing=entry["state_changing"],
            source_clause_reference=change.source_reference,
            source_quote=change.source_quote,
            requirement_code=change.requirement_code,
            approval_status=ReviewStatus.PENDING_REVIEW.value,
            model_name=None,
            provenance=DataProvenance.DEMO_FIXTURE.value,
        )
        db.add(case)
        test_cases.append(case)

    db.commit()

    return {
        "created": True,
        "notice": (
            "Demo fixture loaded. Document metadata and URLs are real; the body text is "
            "synthetic and labelled DEMO_FIXTURE throughout the UI. Clause parsing, "
            "retrieval scoring and test execution are real code paths."
        ),
        "documents": 2,
        "clauses": len(current.clauses) + len(prior.clauses),
        "requirements": len(changes),
        "impact_mappings": len(mappings),
        "test_cases": len(test_cases),
        "current_document_id": str(current.id),
        "prior_document_id": str(prior.id),
    }


def _category_for(banking_module: str) -> str:
    return {
        "KYC_AML": ImpactCategory.KYC_COMPLIANCE.value,
        "CUSTOMER_ONBOARDING": ImpactCategory.CUSTOMER_ONBOARDING.value,
        "ACCOUNT_MANAGEMENT": ImpactCategory.ACCOUNT_MANAGEMENT.value,
        "TRANSACTIONS": ImpactCategory.TRANSACTION_PROCESSING.value,
    }.get(banking_module, ImpactCategory.BUSINESS_LOGIC.value)


def _delete_demo_records(db: Session) -> int:
    """Delete demo test cases, then demo documents.

    Order matters. Deleting an ImpactMapping sets `test_cases.impact_mapping_id`
    to NULL rather than removing the test case, so the test rows must go first
    or their unique `test_code` values would collide on the next seed.
    """
    for case in (
        db.execute(
            select(TestCase).where(TestCase.provenance == DataProvenance.DEMO_FIXTURE.value)
        )
        .scalars()
        .all()
    ):
        db.delete(case)
    db.flush()

    documents = list(
        db.execute(
            select(RegulatoryDocument).where(
                RegulatoryDocument.provenance == DataProvenance.DEMO_FIXTURE.value
            )
        )
        .scalars()
        .all()
    )
    # Cascades from the document remove its clauses, changes and mappings.
    for document in documents:
        db.delete(document)
    return len(documents)


def clear_demo_fixture(db: Session) -> int:
    """Remove every DEMO_FIXTURE document and its dependent records."""
    count = _delete_demo_records(db)
    db.commit()
    return count
