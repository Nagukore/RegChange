"""Deterministic clause segmentation for regulatory documents.

Regulators number their text, and that numbering is the citation a compliance
reviewer needs. So clauses are split by the document's own structure rather than
by a fixed character window: the resulting `clause_reference` is a real locator
("Para 38", "Section 5.2"), not an artefact of chunking.

This runs before any LLM involvement. Gemini is only asked to segment when the
deterministic pass yields implausibly few clauses for the document length.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

# Markdown heading, e.g. "## 5. Customer Due Diligence"
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)

# A numbered clause at the start of a line. Covers the forms RBI actually uses:
#   "5. ", "5.2 ", "5.2.1 ", "(a) ", "(iv) ", "38(a) "
CLAUSE_START_RE = re.compile(
    r"""^[ \t]*(?P<ref>
          (?:\d+(?:\.\d+){0,3})            # 5 | 5.2 | 5.2.1
          (?:\s*\([a-z0-9ivx]{1,4}\))?     # optional (a) / (iv)
        | \([a-z0-9ivx]{1,4}\)             # bare (a) / (iv)
        )
        [.)]?[ \t]+
        (?=\S)""",
    re.MULTILINE | re.VERBOSE | re.IGNORECASE,
)

# Tables of contents and boilerplate we do not want as clauses.
NOISE_PATTERNS = [
    re.compile(r"^\s*(table of contents|contents|index)\s*$", re.IGNORECASE),
    re.compile(r"^\s*page\s+\d+\s*(of\s+\d+)?\s*$", re.IGNORECASE),
    re.compile(r"^\s*\.{3,}\s*\d+\s*$"),  # dotted TOC leader line
]

# Low enough to keep a numbered parent clause that only carries a title and
# whose substance sits in its (a)/(b) sub-clauses, e.g.
# "4. Customer Identification Procedure".
MIN_CLAUSE_CHARS = 25
MAX_CLAUSE_CHARS = 8000
# A guard against pathological input, far above any real instrument: the 2025
# KYC Directions alone have about 700. Dropping real clauses would be silent
# data loss, so extraction reports whenever a document reaches this.
MAX_CLAUSES_PER_DOCUMENT = 5000


def restore_paragraph_breaks(text: str) -> str:
    """Give single-line text its paragraphs back, keeping every character offset.

    Fetch returns web pages as one line with runs of spaces between paragraphs,
    but clause markers are recognised at the start of a line. Text that already
    has line structure, such as a PDF, is returned unchanged.
    """
    if text.count("\n") >= len(text) / 2000:
        return text
    return re.sub(r"[ \t]{2,}", lambda m: "\n" + " " * (len(m.group(0)) - 1), text)


@dataclass
class ParsedClause:
    clause_reference: str
    heading: str | None
    text: str
    ordinal: int
    char_start: int
    char_end: int

    @property
    def clause_hash(self) -> str:
        """Hash of the normalised text; drives clause-level change detection."""
        return hashlib.sha256(normalise_text(self.text).encode("utf-8")).hexdigest()


def normalise_text(text: str) -> str:
    """Collapse whitespace so cosmetic reflow does not look like a change."""
    return re.sub(r"\s+", " ", (text or "")).strip().lower()


def content_hash(text: str) -> str:
    return hashlib.sha256(normalise_text(text).encode("utf-8")).hexdigest()


def _tidy_reference(raw: str) -> str:
    """Normalise a captured clause marker into a citable reference.

    Keeps bracketed forms intact ("(a)" must not become "(a") while dropping a
    trailing sentence period from numeric forms ("5." -> "5").
    """
    reference = (raw or "").strip()
    reference = reference.rstrip(".")
    # Re-close a bracket the strip or the source may have left dangling.
    if reference.startswith("(") and not reference.endswith(")"):
        reference = f"{reference})"
    return reference.strip()


def _is_noise(line: str) -> bool:
    return any(pattern.match(line) for pattern in NOISE_PATTERNS)


def _strip_noise(text: str) -> str:
    return "\n".join(line for line in text.splitlines() if not _is_noise(line))


def _heading_at_or_above(headings: list[tuple[int, str]], position: int) -> str | None:
    """The nearest heading preceding `position`."""
    current = None
    for offset, title in headings:
        if offset <= position:
            current = title
        else:
            break
    return current


def parse_clauses(
    text: str, *, max_clauses: int = MAX_CLAUSES_PER_DOCUMENT
) -> list[ParsedClause]:
    """Split document text into addressable clauses.

    Strategy, in order of preference:
      1. Split on the document's own numbered clause markers.
      2. Fall back to markdown headings.
      3. Fall back to paragraph blocks (reference recorded as "Para N").
    """
    if not text or not text.strip():
        return []

    cleaned = _strip_noise(text)
    headings = [(m.start(), m.group(2).strip()) for m in HEADING_RE.finditer(cleaned)]

    matches = list(CLAUSE_START_RE.finditer(cleaned))
    clauses: list[ParsedClause] = []

    if len(matches) >= 3:
        for index, match in enumerate(matches):
            start = match.start()
            end = matches[index + 1].start() if index + 1 < len(matches) else len(cleaned)
            body = cleaned[start:end].strip()
            if len(body) < MIN_CLAUSE_CHARS:
                # Too short to be a substantive clause on its own -- usually a
                # list label whose content follows; merge it into the previous.
                if clauses:
                    previous = clauses[-1]
                    merged = f"{previous.text}\n{body}"[:MAX_CLAUSE_CHARS]
                    clauses[-1] = ParsedClause(
                        clause_reference=previous.clause_reference,
                        heading=previous.heading,
                        text=merged,
                        ordinal=previous.ordinal,
                        char_start=previous.char_start,
                        char_end=end,
                    )
                continue
            reference = _tidy_reference(match.group("ref"))
            clauses.append(
                ParsedClause(
                    clause_reference=f"Para {reference}",
                    heading=_heading_at_or_above(headings, start),
                    text=body[:MAX_CLAUSE_CHARS],
                    ordinal=len(clauses) + 1,
                    char_start=start,
                    char_end=end,
                )
            )
            if len(clauses) >= max_clauses:
                break
        if clauses:
            return clauses

    if len(headings) >= 2:
        for index, (offset, title) in enumerate(headings):
            end = headings[index + 1][0] if index + 1 < len(headings) else len(cleaned)
            body = cleaned[offset:end].strip()
            if len(body) < MIN_CLAUSE_CHARS:
                continue
            clauses.append(
                ParsedClause(
                    clause_reference=f"Section {index + 1}",
                    heading=title,
                    text=body[:MAX_CLAUSE_CHARS],
                    ordinal=len(clauses) + 1,
                    char_start=offset,
                    char_end=end,
                )
            )
            if len(clauses) >= max_clauses:
                break
        if clauses:
            return clauses

    # Last resort: paragraph blocks. The reference is explicitly positional so
    # a reviewer can tell it is not the regulator's own numbering.
    cursor = 0
    for block in re.split(r"\n\s*\n", cleaned):
        stripped = block.strip()
        start = cleaned.find(stripped, cursor) if stripped else cursor
        cursor = start + len(stripped)
        if len(stripped) < MIN_CLAUSE_CHARS:
            continue
        clauses.append(
            ParsedClause(
                clause_reference=f"Para {len(clauses) + 1}",
                heading=_heading_at_or_above(headings, start),
                text=stripped[:MAX_CLAUSE_CHARS],
                ordinal=len(clauses) + 1,
                char_start=start,
                char_end=cursor,
            )
        )
        if len(clauses) >= max_clauses:
            break
    return clauses


def segmentation_looks_thin(text: str, clauses: list[ParsedClause]) -> bool:
    """True when the deterministic pass probably missed the structure.

    A long document that yields only a couple of clauses usually means the
    markdown lost its numbering, which is when an LLM segmentation assist earns
    its cost.
    """
    if not text:
        return False
    if len(text) > 8000 and len(clauses) < 3:
        return True
    return bool(clauses) and (len(text) / max(len(clauses), 1)) > 12000


# --- Metadata extraction ---------------------------------------------------
EFFECTIVE_DATE_PATTERNS = [
    re.compile(
        r"(?:come into force|shall come into effect|effective from|with effect from|"
        r"shall be effective)\D{0,40}?(\d{1,2}\s+\w+,?\s+\d{4})",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:come into force|effective from|with effect from)\D{0,40}?(\d{4}-\d{2}-\d{2})",
        re.IGNORECASE,
    ),
]

APPLICABILITY_HEADING_RE = re.compile(
    r"(?:^|\n)[ \t]*(?:#+[ \t]*)?"
    # Section label: numeric ("2.", "5.1") or alphabetic ("B."). RBI uses
    # lettered subsections inside numbered chapters, so both are needed.
    r"(?:[A-Za-z]\.[ \t]*|\d+(?:\.\d+)*\.?[ \t]*)?"
    r"applicability\b[:.\s]*",
    re.IGNORECASE,
)

DATE_FORMATS = ("%d %B %Y", "%d %B, %Y", "%d %b %Y", "%d %b, %Y", "%Y-%m-%d")


def extract_effective_date(text: str):
    """Find a stated effective date. Returns None when the source gives none."""
    from datetime import datetime

    for pattern in EFFECTIVE_DATE_PATTERNS:
        match = pattern.search(text or "")
        if not match:
            continue
        raw = match.group(1).strip().replace(",", "")
        for fmt in ("%d %B %Y", "%d %b %Y", "%Y-%m-%d"):
            try:
                return datetime.strptime(raw, fmt).date()
            except ValueError:
                continue
    return None


# A table-of-contents line: dotted leader followed by a page number, e.g.
# "B. Applicability ............ 4". The real section must be found instead.
TOC_LEADER_RE = re.compile(r"\.{4,}\s*\d+")


def extract_applicability(text: str) -> str | None:
    """Return the document's own applicability wording, verbatim.

    Real regulator PDFs carry a table of contents, so the first match for
    "Applicability" is usually a TOC entry rather than the section itself.
    Every match is considered and TOC-shaped ones are skipped.

    The snippet is bounded by the next clause or heading so it is the
    applicability statement alone, not the clauses that follow it.
    """
    if not text:
        return None

    for match in APPLICABILITY_HEADING_RE.finditer(text):
        remainder = text[match.end() : match.end() + 4000]

        # Stop at whichever boundary comes first: the next numbered clause or
        # the next markdown heading.
        end = len(remainder)
        for pattern in (CLAUSE_START_RE, HEADING_RE):
            found = pattern.search(remainder)
            if found and found.start() > 0:
                end = min(end, found.start())

        snippet = re.sub(r"\s+", " ", remainder[:end]).strip()

        # A TOC entry is a dotted leader and a page number, and carries no
        # substantive wording.
        if TOC_LEADER_RE.search(snippet[:200]) or len(snippet) < 40:
            continue
        return snippet[:2000]

    return None


# Entity types named in applicability text, in the regulator's own vocabulary.
ENTITY_PATTERNS: dict[str, list[str]] = {
    "COMMERCIAL_BANK": ["commercial bank", "scheduled commercial bank", "all banks"],
    "SMALL_FINANCE_BANK": ["small finance bank"],
    "PAYMENTS_BANK": ["payments bank", "payment bank"],
    "CO_OPERATIVE_BANK": ["co-operative bank", "cooperative bank", "urban co-operative"],
    "REGIONAL_RURAL_BANK": ["regional rural bank"],
    "NBFC": ["non-banking financial", "nbfc"],
    "PAYMENT_SYSTEM_PROVIDER": ["payment system provider", "payment system participant"],
}


# Carve-outs. The RBI 2025 KYC Directions, for example, apply to commercial
# banks "(other than Small Finance Banks, Payment Banks, and Local Area Banks)".
# Naming an entity inside such a clause EXCLUDES it, and reading that as
# inclusion would overstate the scope of an obligation -- the exact mistake a
# compliance tool must not make.
EXCLUSION_RE = re.compile(
    r"(?:other than|excluding|except\s+for|save\s+for|"
    r"(?:shall|do|does)\s+not\s+apply\s+to)\b([^.;)\n]{0,220})",
    re.IGNORECASE,
)


def _entities_in(fragment: str) -> set[str]:
    lowered = (fragment or "").lower()
    return {
        entity
        for entity, needles in ENTITY_PATTERNS.items()
        if any(needle in lowered for needle in needles)
    }


def detect_applicable_entities(text: str) -> list[str]:
    """Entity types the text explicitly names as in scope.

    Entities named only inside an exclusion clause are removed. An empty list
    means the source did not state its scope -- it is never inferred.
    """
    if not text:
        return []

    included = _entities_in(text)
    if not included:
        return []

    excluded: set[str] = set()
    for match in EXCLUSION_RE.finditer(text):
        excluded |= _entities_in(match.group(1))

    # An entity named both in scope and in a carve-out stays excluded: the
    # narrower reading is the safe one to put in front of a reviewer.
    return sorted(included - excluded)


__all__ = [
    "ParsedClause",
    "MAX_CLAUSES_PER_DOCUMENT",
    "parse_clauses",
    "restore_paragraph_breaks",
    "segmentation_looks_thin",
    "content_hash",
    "normalise_text",
    "extract_effective_date",
    "extract_applicability",
    "detect_applicable_entities",
]
