"""Candidate retrieval over the CBS business-rule catalogue.

Step 2 of the hybrid mapping approach: narrow 15 (eventually hundreds of) rules
down to the handful plausibly touched by a requirement, so the LLM reasons over
a short, relevant list rather than the whole catalogue.

Implementation is lexical (BM25-flavoured TF-IDF over rule keywords, name and
description). It is deterministic, needs no embedding service and is explainable
-- a reviewer can see which terms matched. `embedding` on BusinessRule is
reserved for a pgvector upgrade; `score_rules` is the single place that would
change.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass

from app.models.mapping import BusinessRule

# Regulatory prose is dense with these; they carry no discriminating signal.
STOPWORDS = frozenset(
    """
    a an the and or of to in for on at by with from as is are be been being shall
    must may should will would can could not no any all such other than then
    this that these those it its their his her they them we our you your
    bank banks banking entity entities customer customers account accounts
    regulated re requirement requirements provision provisions direction
    directions circular paragraph para section clause sub also per each every
    where which who whom whose if when while under over upon into within
    """.split()
)

TOKEN_RE = re.compile(r"[a-z][a-z0-9\-]{1,}")


def tokenise(text: str) -> list[str]:
    tokens = TOKEN_RE.findall((text or "").lower())
    return [t for t in tokens if t not in STOPWORDS and len(t) > 2]


@dataclass
class RuleCandidate:
    rule: BusinessRule
    score: float
    matched_terms: list[str]

    def to_dict(self) -> dict:
        return {
            "rule_code": self.rule.rule_code,
            "name": self.rule.name,
            "banking_module": self.rule.banking_module,
            "score": round(self.score, 4),
            "matched_terms": self.matched_terms,
        }


def _rule_document(rule: BusinessRule) -> list[str]:
    """Searchable token bag for a rule.

    Keywords are weighted by repetition because they are hand-curated and
    therefore the strongest signal of intent.
    """
    parts = [rule.name, rule.description]
    keywords = rule.keywords or []
    # Triple-weight curated keywords.
    parts.extend(" ".join(keywords) for _ in range(3))
    parts.extend(rule.affected_fields or [])
    return tokenise(" ".join(p for p in parts if p))


def score_rules(
    query_text: str,
    rules: list[BusinessRule],
    *,
    banking_module: str | None = None,
    module_boost: float = 1.35,
    top_k: int = 8,
    min_score: float = 0.0,
) -> list[RuleCandidate]:
    """Rank rules against a requirement.

    `banking_module` is the module the analysis agent assigned to the
    requirement; rules in that module get a multiplicative boost rather than a
    hard filter, so a genuinely cross-module impact can still surface.
    """
    if not rules:
        return []

    query_terms = Counter(tokenise(query_text))
    if not query_terms:
        return []

    documents = {rule.rule_code: _rule_document(rule) for rule in rules}
    total_docs = len(documents)

    # Document frequency per term, for IDF.
    doc_frequency: Counter[str] = Counter()
    for tokens in documents.values():
        for term in set(tokens):
            doc_frequency[term] += 1

    candidates: list[RuleCandidate] = []
    for rule in rules:
        tokens = documents[rule.rule_code]
        if not tokens:
            continue
        token_counts = Counter(tokens)
        length = len(tokens)

        score = 0.0
        matched: list[str] = []
        for term, query_count in query_terms.items():
            term_count = token_counts.get(term, 0)
            if not term_count:
                continue
            # Smoothed IDF: a term in every rule contributes almost nothing.
            idf = math.log(1 + (total_docs / (1 + doc_frequency[term])))
            # Length-normalised term frequency keeps verbose rules from winning
            # on sheer word count.
            tf = term_count / math.sqrt(length)
            score += idf * tf * math.log(1 + query_count)
            matched.append(term)

        if score <= min_score:
            continue
        if banking_module and rule.banking_module == banking_module:
            score *= module_boost

        candidates.append(
            RuleCandidate(
                rule=rule,
                score=score,
                # Most distinctive matches first, capped for readability.
                matched_terms=sorted(set(matched))[:12],
            )
        )

    candidates.sort(key=lambda c: c.score, reverse=True)
    return candidates[:top_k]


def normalise_scores(candidates: list[RuleCandidate]) -> list[RuleCandidate]:
    """Rescale scores to 0..1 so the UI can show a comparable bar.

    Relative within one query only -- never compare across requirements.
    """
    if not candidates:
        return candidates
    top = max(c.score for c in candidates) or 1.0
    for candidate in candidates:
        candidate.score = candidate.score / top
    return candidates


__all__ = ["RuleCandidate", "score_rules", "normalise_scores", "tokenise"]
