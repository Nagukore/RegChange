"""Clause-level comparison between two versions of a regulatory document.

Matching is by `clause_reference` first, because a regulator's own numbering is
the stable identity of a provision. Clauses that exist in only one version are
then matched on content similarity, which catches renumbering: a provision moved
from "Para 12" to "Para 38" should read as MODIFIED (or UNCHANGED), not as one
REMOVED plus one ADDED.
"""
from __future__ import annotations

from difflib import SequenceMatcher

from app.agents.clause_parser import normalise_text
from app.models.regulatory import RegulatoryClause, RegulatoryDocument

# Above this, two clause texts are treated as the same provision.
RENUMBER_MATCH_THRESHOLD = 0.75
# Above this, a matched pair is considered textually unchanged.
UNCHANGED_THRESHOLD = 0.985


def _similarity(left: str, right: str) -> float:
    return SequenceMatcher(None, normalise_text(left), normalise_text(right)).ratio()


def _entry(
    change: str,
    reference: str,
    *,
    heading: str | None = None,
    previous_text: str | None = None,
    current_text: str | None = None,
    similarity: float | None = None,
) -> dict:
    return {
        "change": change,
        "clause_reference": reference,
        "heading": heading,
        "previous_text": previous_text,
        "current_text": current_text,
        "similarity": round(similarity, 4) if similarity is not None else None,
    }


def compare_clause_lists(
    previous: list[RegulatoryClause], current: list[RegulatoryClause]
) -> dict:
    """Diff two clause lists. Returns counts plus per-clause entries."""
    previous_by_ref = {c.clause_reference: c for c in previous}
    current_by_ref = {c.clause_reference: c for c in current}

    entries: list[dict] = []
    matched_previous: set[str] = set()
    matched_current: set[str] = set()

    # Pass 1: same reference in both versions.
    for reference, current_clause in current_by_ref.items():
        previous_clause = previous_by_ref.get(reference)
        if previous_clause is None:
            continue
        matched_previous.add(reference)
        matched_current.add(reference)

        # The stored hash is the cheap equality check.
        if previous_clause.clause_hash == current_clause.clause_hash:
            entries.append(
                _entry(
                    "UNCHANGED",
                    reference,
                    heading=current_clause.heading,
                    current_text=current_clause.text,
                    similarity=1.0,
                )
            )
            continue

        ratio = _similarity(previous_clause.text, current_clause.text)
        entries.append(
            _entry(
                "UNCHANGED" if ratio >= UNCHANGED_THRESHOLD else "MODIFIED",
                reference,
                heading=current_clause.heading,
                previous_text=previous_clause.text,
                current_text=current_clause.text,
                similarity=ratio,
            )
        )

    # Pass 2: unmatched clauses, paired on content to detect renumbering.
    leftover_previous = [c for ref, c in previous_by_ref.items() if ref not in matched_previous]
    leftover_current = [c for ref, c in current_by_ref.items() if ref not in matched_current]

    for current_clause in list(leftover_current):
        best: RegulatoryClause | None = None
        best_ratio = 0.0
        for previous_clause in leftover_previous:
            ratio = _similarity(previous_clause.text, current_clause.text)
            if ratio > best_ratio:
                best, best_ratio = previous_clause, ratio

        if best is not None and best_ratio >= RENUMBER_MATCH_THRESHOLD:
            leftover_previous.remove(best)
            leftover_current.remove(current_clause)
            renumbered = best.clause_reference != current_clause.clause_reference
            label = "UNCHANGED" if best_ratio >= UNCHANGED_THRESHOLD and not renumbered else "MODIFIED"
            reference = (
                f"{current_clause.clause_reference} (was {best.clause_reference})"
                if renumbered
                else current_clause.clause_reference
            )
            entries.append(
                _entry(
                    label,
                    reference,
                    heading=current_clause.heading,
                    previous_text=best.text,
                    current_text=current_clause.text,
                    similarity=best_ratio,
                )
            )

    # Whatever is still unpaired is a genuine addition or removal.
    for clause in leftover_current:
        entries.append(
            _entry(
                "ADDED",
                clause.clause_reference,
                heading=clause.heading,
                current_text=clause.text,
            )
        )
    for clause in leftover_previous:
        entries.append(
            _entry(
                "REMOVED",
                clause.clause_reference,
                heading=clause.heading,
                previous_text=clause.text,
            )
        )

    # Changes first, so a reviewer sees what matters without scrolling.
    order = {"MODIFIED": 0, "ADDED": 1, "REMOVED": 2, "UNCHANGED": 3}
    entries.sort(key=lambda e: (order[e["change"]], e["clause_reference"]))

    return {
        "added": sum(1 for e in entries if e["change"] == "ADDED"),
        "removed": sum(1 for e in entries if e["change"] == "REMOVED"),
        "modified": sum(1 for e in entries if e["change"] == "MODIFIED"),
        "unchanged": sum(1 for e in entries if e["change"] == "UNCHANGED"),
        "entries": entries,
    }


def compare_document_versions(
    previous: RegulatoryDocument, current: RegulatoryDocument
) -> dict:
    """Compare two stored document versions clause by clause."""
    diff = compare_clause_lists(
        sorted(previous.clauses, key=lambda c: c.ordinal),
        sorted(current.clauses, key=lambda c: c.ordinal),
    )
    return {"previous": previous, "current": current, **diff}


__all__ = ["compare_clause_lists", "compare_document_versions"]
