"""Tests for clause parsing, document version comparison and traceability.

All offline: no TinyFish or Gemini credentials required. Documents and clauses
are constructed directly in the shape the extraction agent persists them.
"""
from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from app.agents.clause_parser import (
    content_hash,
    detect_applicable_entities,
    extract_applicability,
    extract_effective_date,
    parse_clauses,
    segmentation_looks_thin,
)
from app.models.enums import (
    AssertionBasis,
    ChangeType,
    DocumentStatus,
    ReviewStatus,
)
from app.models.mapping import BusinessRule, ImpactMapping
from app.models.regulatory import RegulatoryChange, RegulatoryClause, RegulatoryDocument
from app.models.testing import TestCase as TestCaseModel
from app.services.diffing import compare_clause_lists, compare_document_versions
from app.services.traceability import build_traceability

KYC_2016 = """# Master Direction - Know Your Customer Direction, 2016

## Chapter II - Customer Due Diligence

3. Customer Acceptance Policy
Banks shall frame a Customer Acceptance Policy approved by the Board.

38. Periodic updation of KYC
Banks shall carry out periodic updation of KYC records at least once every ten
years for low risk customers, once every eight years for medium risk customers
and once every two years for high risk customers.

40. Record management
Banks shall maintain records of transactions for a period of five years.
"""

KYC_2025 = """# Reserve Bank of India (Commercial Banks - Know Your Customer) Directions, 2025

## Chapter II - Customer Due Diligence

3. Customer Acceptance Policy
Banks shall frame a Customer Acceptance Policy approved by the Board.

38. Periodic updation of KYC
Banks shall carry out periodic updation of KYC records at least once every ten
years for low risk customers, once every eight years for medium risk customers
and once every two years for high risk customers. A self-declaration obtained
through an authorised business correspondent shall be acceptable where there is
no change in KYC details.

41. Digital KYC application
Banks shall develop an application for digital KYC and shall undertake KYC only
through that authenticated application.
"""


class TestClauseParsing:
    def test_numbered_clauses_use_regulator_numbering(self):
        clauses = parse_clauses(KYC_2025)
        references = [c.clause_reference for c in clauses]
        assert "Para 3" in references
        assert "Para 38" in references
        assert "Para 41" in references

    def test_bracketed_subclause_reference_is_well_formed(self):
        text = """1. Customer Identification Procedure
(a) Banks shall identify every customer using an Officially Valid Document issued by an authority.
(b) Banks shall obtain a self-attested copy along with the original for verification purposes.
"""
        references = [c.clause_reference for c in parse_clauses(text)]
        # The closing bracket must survive reference tidying.
        assert "Para (a)" in references
        assert "Para (b)" in references

    def test_clause_hash_is_whitespace_insensitive(self):
        a = parse_clauses("1. A clause with enough text to be substantive and stored properly.")
        b = parse_clauses("1. A   clause with enough text\nto be substantive and stored properly.")
        assert a[0].clause_hash == b[0].clause_hash

    def test_content_hash_ignores_reflow_and_case(self):
        assert content_hash("Banks  SHALL   verify") == content_hash("banks shall verify")

    def test_empty_document_yields_no_clauses(self):
        assert parse_clauses("") == []
        assert parse_clauses("   \n  ") == []

    def test_heading_fallback_when_no_numbering(self):
        text = """## Applicability
These directions apply to all commercial banks operating in India under licence.

## Compliance
Banks shall report compliance to the Reserve Bank within thirty days of issue.

## Repeal
The earlier circular stands repealed from the date these directions take effect.
"""
        clauses = parse_clauses(text)
        assert len(clauses) >= 2
        assert all(c.clause_reference.startswith("Section") for c in clauses)
        assert clauses[0].heading == "Applicability"

    def test_table_of_contents_noise_is_stripped(self):
        text = "Table of Contents\n......... 4\nPage 2 of 9\n\n" + KYC_2025
        clauses = parse_clauses(text)
        assert not any("Table of Contents" in c.text for c in clauses)

    def test_effective_date_extracted_only_when_stated(self):
        assert extract_effective_date(
            "These Directions shall come into force with effect from 28 November 2025."
        ) == date(2025, 11, 28)
        # Vague wording must not become a fabricated date.
        assert extract_effective_date("These Directions take effect immediately.") is None
        assert extract_effective_date("") is None

    def test_applicability_is_bounded_to_its_own_statement(self):
        text = """2. Applicability
These Directions shall apply to all Commercial Banks.

3. Customer Acceptance Policy
Banks shall frame a Customer Acceptance Policy approved by the Board of Directors.
"""
        applicability = extract_applicability(text)
        assert applicability is not None
        assert "Commercial Banks" in applicability
        # Must not bleed into the following clause.
        assert "Customer Acceptance Policy" not in applicability

    def test_entity_detection_names_only_what_is_stated(self):
        assert detect_applicable_entities(
            "These Directions shall apply to all Commercial Banks."
        ) == ["COMMERCIAL_BANK"]
        entities = detect_applicable_entities(
            "applies to commercial banks and non-banking financial companies"
        )
        assert set(entities) == {"COMMERCIAL_BANK", "NBFC"}
        # Silence means silence -- never inferred.
        assert detect_applicable_entities("Banks shall do the needful.") == []

    def test_thin_segmentation_is_detected(self):
        long_unstructured = "word " * 3000
        assert segmentation_looks_thin(long_unstructured, parse_clauses(long_unstructured)) is True
        assert segmentation_looks_thin(KYC_2025, parse_clauses(KYC_2025)) is False


def _document(db, *, title: str, text: str, version: int, url: str, supersedes=None):
    """Persist a document with parsed clauses, as the extraction agent does."""
    document = RegulatoryDocument(
        title=title,
        source_url=url,
        document_type="MASTER_DIRECTION",
        version=version,
        is_current=supersedes is None or True,
        supersedes_id=supersedes.id if supersedes else None,
        content_hash=content_hash(text),
        raw_text=text,
        char_count=len(text),
        status=DocumentStatus.EXTRACTED.value,
        publication_date=date(2016, 2, 25) if version == 1 else date(2025, 11, 28),
        fetched_at=datetime.now(UTC),
        provenance="DEMO_FIXTURE",
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
                clause_hash=parsed.clause_hash,
                provenance="DETERMINISTIC",
            )
        )
    db.flush()
    return document


class TestVersionComparison:
    def test_identical_versions_report_no_change(self, db):
        old = _document(db, title="Doc", text=KYC_2025, version=1, url="https://x.test/a")
        new = _document(db, title="Doc", text=KYC_2025, version=2, url="https://x.test/a", supersedes=old)
        result = compare_document_versions(old, new)
        assert result["modified"] == 0
        assert result["added"] == 0
        assert result["removed"] == 0
        assert result["unchanged"] > 0

    def test_amended_clause_is_reported_as_modified(self, db):
        old = _document(db, title="KYC", text=KYC_2016, version=1, url="https://x.test/kyc")
        new = _document(
            db, title="KYC", text=KYC_2025, version=2, url="https://x.test/kyc", supersedes=old
        )
        result = compare_document_versions(old, new)

        modified = [e for e in result["entries"] if e["change"] == "MODIFIED"]
        # Para 38 gained the business-correspondent self-declaration sentence.
        assert any("38" in e["clause_reference"] for e in modified)
        para38 = next(e for e in modified if "38" in e["clause_reference"])
        assert "business correspondent" in para38["current_text"]
        assert "business correspondent" not in (para38["previous_text"] or "")

    def test_new_clause_is_reported_as_added(self, db):
        old = _document(db, title="KYC", text=KYC_2016, version=1, url="https://x.test/kyc2")
        new = _document(
            db, title="KYC", text=KYC_2025, version=2, url="https://x.test/kyc2", supersedes=old
        )
        result = compare_document_versions(old, new)
        added = [e for e in result["entries"] if e["change"] == "ADDED"]
        assert any("Digital KYC" in (e["current_text"] or "") for e in added)

    def test_deleted_clause_is_reported_as_removed(self, db):
        old = _document(db, title="KYC", text=KYC_2016, version=1, url="https://x.test/kyc3")
        new = _document(
            db, title="KYC", text=KYC_2025, version=2, url="https://x.test/kyc3", supersedes=old
        )
        result = compare_document_versions(old, new)
        removed = [e for e in result["entries"] if e["change"] == "REMOVED"]
        # Para 40 (record management) is absent from the 2025 fixture.
        assert any("Record management" in (e["previous_text"] or "") for e in removed)

    def test_renumbered_clause_is_matched_not_double_counted(self):
        """A moved provision must not read as one REMOVED plus one ADDED."""

        class FakeClause:
            def __init__(self, reference, text, ordinal):
                self.clause_reference = reference
                self.heading = None
                self.text = text
                self.ordinal = ordinal
                self.clause_hash = content_hash(text)

        body = (
            "Banks shall carry out periodic updation of KYC records at least once "
            "every ten years for low risk customers and once every two years for "
            "high risk customers."
        )
        previous = [FakeClause("Para 12", body, 1)]
        current = [FakeClause("Para 38", body, 1)]

        result = compare_clause_lists(previous, current)
        assert result["added"] == 0
        assert result["removed"] == 0
        entry = result["entries"][0]
        assert "was Para 12" in entry["clause_reference"]

    def test_modified_entries_sort_before_unchanged(self, db):
        old = _document(db, title="KYC", text=KYC_2016, version=1, url="https://x.test/kyc4")
        new = _document(
            db, title="KYC", text=KYC_2025, version=2, url="https://x.test/kyc4", supersedes=old
        )
        entries = compare_document_versions(old, new)["entries"]
        labels = [e["change"] for e in entries]
        if "UNCHANGED" in labels and "MODIFIED" in labels:
            assert labels.index("MODIFIED") < labels.index("UNCHANGED")


class TestTraceability:
    def _full_chain(self, seeded_db):
        """Build document -> clause -> change -> impact -> rule -> test."""
        document = _document(
            seeded_db, title="KYC 2025", text=KYC_2025, version=1, url="https://x.test/trace"
        )
        clause = next(c for c in document.clauses if c.clause_reference == "Para 38")

        change = RegulatoryChange(
            document_id=document.id,
            clause_id=clause.id,
            requirement_code="REQ-0001",
            title="Periodic KYC updation intervals by risk category",
            requirement_text=(
                "Banks must re-verify KYC every ten years for low risk, eight for "
                "medium and two for high risk customers."
            ),
            change_type=ChangeType.AMENDED_REQUIREMENT.value,
            assertion_basis=AssertionBasis.EXPLICIT_REQUIREMENT.value,
            source_quote="Banks shall carry out periodic updation of KYC records",
            source_reference="Para 38",
            primary_banking_module="KYC_AML",
            priority="HIGH",
            confidence=0.92,
            review_status=ReviewStatus.APPROVED.value,
            provenance="DEMO_FIXTURE",
        )
        seeded_db.add(change)
        seeded_db.flush()

        rule = (
            seeded_db.query(BusinessRule).filter(BusinessRule.rule_code == "CBS-006").one()
        )
        mapping = ImpactMapping(
            change_id=change.id,
            business_rule_id=rule.id,
            impact_category="KYC_COMPLIANCE",
            banking_module="KYC_AML",
            priority="HIGH",
            rationale="The periodicity drives next_kyc_due_date computed by CBS-006.",
            assertion_basis=AssertionBasis.EXPLICIT_REQUIREMENT.value,
            regression_risk="MEDIUM",
            confidence=0.9,
            retrieval_score=1.0,
            review_status=ReviewStatus.APPROVED.value,
            provenance="DEMO_FIXTURE",
        )
        seeded_db.add(mapping)
        seeded_db.flush()

        case = TestCaseModel(
            test_code="TC-KYC-9001",
            impact_mapping_id=mapping.id,
            change_id=change.id,
            business_rule_id=rule.id,
            title="High risk customer periodicity is two years",
            objective="Verify the derived next KYC due date for a high risk customer",
            banking_module="KYC_AML",
            category="POSITIVE",
            priority="HIGH",
            automation_type="API",
            expected_result="periodicity_years equals 2",
            execution_spec={
                "driver": "PYTEST_API",
                "method": "POST",
                "path": "/api/cbs/customers/{customer_id}/risk-classification",
                "expect_status": 200,
            },
            is_state_changing=True,
            source_clause_reference="Para 38",
            source_quote=change.source_quote,
            requirement_code="REQ-0001",
            approval_status=ReviewStatus.APPROVED.value,
            provenance="DEMO_FIXTURE",
        )
        seeded_db.add(case)
        seeded_db.commit()
        return document, clause, change, mapping, rule, case

    def test_chain_spans_document_to_test_case(self, seeded_db):
        document, clause, change, mapping, rule, case = self._full_chain(seeded_db)
        trace = build_traceability(seeded_db, case.id)

        assert trace["document"].id == document.id
        assert trace["clause"].id == clause.id
        assert trace["change"].id == change.id
        assert trace["impact_mapping"].id == mapping.id
        assert trace["business_rule"].id == rule.id
        assert trace["test_case"].id == case.id

        stages = [node["extra"]["stage"] for node in trace["chain"]]
        assert stages == [
            "DOCUMENT",
            "CLAUSE",
            "REQUIREMENT",
            "IMPACT",
            "BUSINESS_RULE",
            "TEST_CASE",
        ]

    def test_chain_carries_the_verbatim_source_quote(self, seeded_db):
        *_, case = self._full_chain(seeded_db)
        trace = build_traceability(seeded_db, case.id)
        requirement_node = next(
            n for n in trace["chain"] if n["extra"]["stage"] == "REQUIREMENT"
        )
        assert "periodic updation of KYC records" in requirement_node["extra"]["source_quote"]
        assert requirement_node["extra"]["source_reference"] == "Para 38"
        assert requirement_node["extra"]["assertion_basis"] == "EXPLICIT_REQUIREMENT"

    def test_chain_reports_source_url_for_citation(self, seeded_db):
        *_, case = self._full_chain(seeded_db)
        trace = build_traceability(seeded_db, case.id)
        document_node = trace["chain"][0]
        assert document_node["extra"]["source_url"] == "https://x.test/trace"

    def test_unlinked_clause_still_appears_by_reference(self, seeded_db):
        """A test whose clause could not be matched must not break the trail."""
        case = TestCaseModel(
            test_code="TC-GEN-9002",
            title="Orphan test",
            banking_module="CROSS_CUTTING",
            category="POSITIVE",
            priority="LOW",
            automation_type="API",
            expected_result="HTTP 200",
            source_clause_reference="Para 99",
            source_quote="A quote with no stored clause row.",
            approval_status=ReviewStatus.PENDING_REVIEW.value,
            provenance="DEMO_FIXTURE",
        )
        seeded_db.add(case)
        seeded_db.commit()

        trace = build_traceability(seeded_db, case.id)
        clause_node = next(n for n in trace["chain"] if n["extra"]["stage"] == "CLAUSE")
        assert clause_node["status"] == "UNLINKED"
        assert clause_node["id"] is None

    def test_missing_test_case_raises_lookup_error(self, seeded_db):
        import uuid as _uuid

        with pytest.raises(LookupError):
            build_traceability(seeded_db, _uuid.uuid4())


class TestRealWorldDocumentParsing:
    """Regressions found against the live RBI 2025 KYC Directions PDF.

    Both bugs only surfaced on a genuine regulator document: a 400-clause PDF
    with a table of contents and an explicit carve-out in its applicability
    clause.
    """

    # Reproduces the real document's shape: a TOC entry for "Applicability"
    # appearing before the section itself.
    RBI_SHAPED = """RBI/DOR/2025-26/169
DOR.AML.REC.No.88/14.01.002/2025-26                      November 28, 2025

Reserve Bank of India (Commercial Banks - Know Your Customer) Directions, 2025

Table of Contents

Chapter I - Preliminary ................................................................ 4
A. Short Title and Commencement. ...................................................... 4
B. Applicability ....................................................................... 4
C. Definitions ......................................................................... 4

Chapter I - Preliminary

A. Short Title and Commencement
1. These directions shall be called the Reserve Bank of India (Commercial Banks -
Know Your Customer) Directions, 2025.
2. These directions shall come into effect on the day they are placed on the
official website of the RBI.

B. Applicability
3. These directions shall be applicable to Commercial Banks (hereinafter
collectively referred to as 'banks' and individually as a 'bank'). For the purpose
of these Directions, Commercial Banks mean banking companies (other than Small
Finance Banks, Payment Banks, and Local Area Banks), corresponding new banks.
"""

    def test_applicability_skips_the_table_of_contents(self):
        applicability = extract_applicability(self.RBI_SHAPED)
        assert applicability is not None
        # The TOC entry is a dotted leader plus a page number and carries no
        # substantive wording.
        assert "......" not in applicability
        assert "shall be applicable to Commercial Banks" in applicability

    def test_carve_out_entities_are_excluded_not_included(self):
        """`other than Small Finance Banks...` must not read as inclusion."""
        applicability = extract_applicability(self.RBI_SHAPED)
        entities = detect_applicable_entities(applicability)
        assert entities == ["COMMERCIAL_BANK"]
        assert "SMALL_FINANCE_BANK" not in entities
        assert "PAYMENTS_BANK" not in entities

    def test_inclusive_wording_still_detected(self):
        """The carve-out logic must not suppress a genuine inclusion."""
        text = (
            "These Directions shall apply to all Commercial Banks, including "
            "Small Finance Banks, referred to as banks."
        )
        assert detect_applicable_entities(text) == [
            "COMMERCIAL_BANK",
            "SMALL_FINANCE_BANK",
        ]

    def test_shall_not_apply_to_is_an_exclusion(self):
        text = (
            "These Directions apply to Commercial Banks. They shall not apply to "
            "Regional Rural Banks."
        )
        assert detect_applicable_entities(text) == ["COMMERCIAL_BANK"]

    def test_effective_date_absent_when_source_gives_no_date(self):
        """The real document says 'on the day they are placed on the website'."""
        assert extract_effective_date(self.RBI_SHAPED) is None

    def test_demo_fixture_marker_stripped_on_live_reextraction(self):
        from app.agents.extraction_agent import DEMO_MARKER_RE

        title = "RBI (Commercial Banks - KYC) Directions, 2025 [DEMO FIXTURE TEXT]"
        assert DEMO_MARKER_RE.sub("", title).strip() == (
            "RBI (Commercial Banks - KYC) Directions, 2025"
        )
