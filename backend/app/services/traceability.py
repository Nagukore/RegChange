"""Regulatory-to-test traceability.

Acceptance criterion: every test must be traceable back to the clause it came
from. This walks the chain in one query set and returns both the related
entities and a flat, display-ready `chain` the UI renders as a trail.

    source document -> clause -> requirement -> impact mapping
                    -> CBS business rule -> test case -> executions -> evidence
"""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.mapping import ImpactMapping
from app.models.regulatory import RegulatoryChange, RegulatoryClause, RegulatoryDocument
from app.models.testing import TestCase, TestExecution


def build_traceability(db: Session, test_case_id: uuid.UUID) -> dict:
    """Assemble the full provenance chain for one test case."""
    test_case = db.execute(
        select(TestCase)
        .where(TestCase.id == test_case_id)
        .options(
            selectinload(TestCase.business_rule),
            selectinload(TestCase.impact_mapping).selectinload(ImpactMapping.business_rule),
            selectinload(TestCase.impact_mapping).selectinload(ImpactMapping.change),
        )
    ).scalar_one_or_none()
    if test_case is None:
        raise LookupError(f"Test case {test_case_id} not found")

    mapping = test_case.impact_mapping
    change: RegulatoryChange | None = (
        mapping.change if mapping is not None else None
    )
    # A test generated before a mapping existed still carries change_id.
    if change is None and test_case.change_id:
        change = db.get(RegulatoryChange, test_case.change_id)

    clause: RegulatoryClause | None = None
    document: RegulatoryDocument | None = None
    if change is not None:
        if change.clause_id:
            clause = db.get(RegulatoryClause, change.clause_id)
        document = db.get(RegulatoryDocument, change.document_id)

    business_rule = (
        mapping.business_rule if mapping is not None else test_case.business_rule
    )

    executions = list(
        db.execute(
            select(TestExecution)
            .where(TestExecution.test_case_id == test_case.id)
            .options(
                selectinload(TestExecution.evidence),
                selectinload(TestExecution.test_case),
            )
            .order_by(TestExecution.created_at.desc())
        )
        .scalars()
        .all()
    )

    chain: list[dict] = []

    if document is not None:
        chain.append(
            {
                "id": document.id,
                "label": f"Source document: {document.title}",
                "detail": (
                    f"{document.circular_number or document.document_type} "
                    f"v{document.version} - {document.source_url}"
                ),
                "status": document.status,
                "extra": {
                    "stage": "DOCUMENT",
                    "provenance": document.provenance,
                    "publication_date": (
                        document.publication_date.isoformat()
                        if document.publication_date
                        else None
                    ),
                    "effective_date": (
                        document.effective_date.isoformat() if document.effective_date else None
                    ),
                    "source_url": document.source_url,
                },
            }
        )

    # Prefer the stored clause; fall back to the denormalised reference so the
    # trail is never broken just because clause matching failed.
    if clause is not None:
        chain.append(
            {
                "id": clause.id,
                "label": f"Clause {clause.clause_reference}",
                "detail": clause.text[:600],
                "status": None,
                "extra": {
                    "stage": "CLAUSE",
                    "heading": clause.heading,
                    "clause_hash": clause.clause_hash,
                },
            }
        )
    elif test_case.source_clause_reference:
        chain.append(
            {
                "id": None,
                "label": f"Clause {test_case.source_clause_reference}",
                "detail": test_case.source_quote or "(quote not recorded)",
                "status": "UNLINKED",
                "extra": {
                    "stage": "CLAUSE",
                    "note": (
                        "Recorded by reference only: the clause could not be matched "
                        "to a stored clause row."
                    ),
                },
            }
        )

    if change is not None:
        chain.append(
            {
                "id": change.id,
                "label": f"{change.requirement_code}: {change.title}",
                "detail": change.requirement_text[:600],
                "status": change.review_status,
                "extra": {
                    "stage": "REQUIREMENT",
                    "assertion_basis": change.assertion_basis,
                    "change_type": change.change_type,
                    "source_quote": change.source_quote,
                    "source_reference": change.source_reference,
                    "confidence": change.confidence,
                    "priority": change.priority,
                    "provenance": change.provenance,
                },
            }
        )

    if mapping is not None:
        chain.append(
            {
                "id": mapping.id,
                "label": (
                    f"Impact: {mapping.impact_category} on "
                    f"{mapping.business_rule.rule_code if mapping.business_rule else '?'}"
                ),
                "detail": mapping.rationale[:600],
                "status": mapping.review_status,
                "extra": {
                    "stage": "IMPACT",
                    "assertion_basis": mapping.assertion_basis,
                    "priority": mapping.priority,
                    "regression_risk": mapping.regression_risk,
                    "confidence": mapping.confidence,
                    "retrieval_score": mapping.retrieval_score,
                    "provenance": mapping.provenance,
                },
            }
        )

    if business_rule is not None:
        chain.append(
            {
                "id": business_rule.id,
                "label": f"CBS rule {business_rule.rule_code}: {business_rule.name}",
                "detail": business_rule.description[:600],
                "status": "ACTIVE" if business_rule.is_active else "INACTIVE",
                "extra": {
                    "stage": "BUSINESS_RULE",
                    "banking_module": business_rule.banking_module,
                    "api_endpoint": business_rule.api_endpoint,
                    "ui_route": business_rule.ui_route,
                },
            }
        )

    chain.append(
        {
            "id": test_case.id,
            "label": f"{test_case.test_code}: {test_case.title}",
            "detail": test_case.expected_result[:600],
            "status": test_case.approval_status,
            "extra": {
                "stage": "TEST_CASE",
                "category": test_case.category,
                "priority": test_case.priority,
                "automation_type": test_case.automation_type,
                "is_state_changing": test_case.is_state_changing,
                "provenance": test_case.provenance,
            },
        }
    )

    for execution in executions:
        evidence_count = len(execution.evidence)
        chain.append(
            {
                "id": execution.id,
                "label": f"Execution {execution.status} via {execution.driver}",
                "detail": (execution.actual_result or execution.failure_reason or "")[:600],
                "status": execution.status,
                "extra": {
                    "stage": "EXECUTION",
                    "duration_ms": execution.duration_ms,
                    "evidence_count": evidence_count,
                    "approved_by": execution.approved_by_email,
                    "assertions": execution.assertions,
                    "finished_at": (
                        execution.finished_at.isoformat() if execution.finished_at else None
                    ),
                },
            }
        )

    return {
        "test_case": test_case,
        "document": document,
        "clause": clause,
        "change": change,
        "impact_mapping": mapping,
        "business_rule": business_rule,
        "executions": executions,
        "chain": chain,
    }


__all__ = ["build_traceability"]
