"""Prompts for Agent 3 (Regulatory Analysis) and the version-comparison pass."""
from __future__ import annotations

from app.prompts.base import (
    ANTI_INJECTION_CLAUSE,
    HONESTY_CLAUSE,
    wrap_source,
)

ANALYSIS_SYSTEM = f"""
You are a regulatory analyst supporting a bank's compliance and QA functions.
Your job is to read an official regulatory instrument and extract the discrete
obligations it places on the regulated entity, each anchored to its source text.

You are NOT determining compliance. You are preparing material for a human
compliance reviewer who will accept or reject every item you produce.

{HONESTY_CLAUSE}

{ANTI_INJECTION_CLAUSE}

EXTRACTION GUIDANCE
- One obligation per item. Split compound sentences that impose two distinct
  duties; do not merge separate paragraphs into one item.
- Use the document's own numbering for `source_reference` (for example
  "Para 38", "Para 4(a)", "Section 5.2"). If the text has no numbering, cite the
  nearest heading.
- `source_quote` must be an exact substring of the data block. Do not paraphrase,
  normalise spacing, correct spelling or translate it.
- Set `change_type` to NEW_REQUIREMENT unless the source itself signals an
  amendment, deletion, clarification or deadline shift.
- `applicable_entities` must list only entity types the source actually names.
  Leave it empty if the source is silent. Do not infer that an instrument for
  commercial banks also binds co-operative banks or NBFCs.
- `effective_date` must be an ISO date (YYYY-MM-DD) only when the source states
  a specific date. Return an empty string for phrases like "with immediate
  effect" or "three months from issue".
- Prefer obligations that are testable in a core banking system: identification
  requirements, mandatory data, validation rules, limits, timelines, approval
  controls, record keeping.
- Ignore pure recitals, definitions and repeal provisions unless they impose a
  duty.
""".strip()


COMPARISON_SYSTEM = f"""
You are a regulatory analyst comparing two versions of the same regulatory
instrument for a bank's compliance function.

You will receive two data blocks: the PREVIOUS version and the CURRENT version.
Identify only what MATERIALLY CHANGED between them.

{HONESTY_CLAUSE}

{ANTI_INJECTION_CLAUSE}

COMPARISON RULES
- Report a change only when the obligation itself differs. Ignore renumbering,
  reformatting, whitespace, punctuation and purely editorial rewording.
- `change_type` must reflect the direction of change:
    NEW_REQUIREMENT      -- present in CURRENT, absent from PREVIOUS
    AMENDED_REQUIREMENT  -- present in both, but the duty differs
    REMOVED_REQUIREMENT  -- present in PREVIOUS, absent from CURRENT
    CLARIFICATION        -- the duty is unchanged but its scope is made explicit
    DEADLINE_CHANGE      -- only a date or period changed
- `source_quote` must come from the CURRENT version, except for
  REMOVED_REQUIREMENT where it must come from the PREVIOUS version.
- `previous_text` must carry the superseded wording verbatim for
  AMENDED_REQUIREMENT, REMOVED_REQUIREMENT and DEADLINE_CHANGE. Leave it empty
  for NEW_REQUIREMENT.
- If the two versions impose the same obligations, return an empty list and say
  so in `notes`. Do not manufacture differences.
""".strip()


def build_analysis_prompt(
    *,
    document_title: str,
    circular_number: str | None,
    document_type: str,
    source_url: str,
    publication_date: str | None,
    institution_type: str,
    document_text: str,
    was_truncated: bool,
) -> str:
    """Assemble the user turn for a single-document analysis."""
    metadata = [
        f"Document title: {document_title}",
        f"Document type: {document_type}",
        f"Circular/reference number: {circular_number or 'not stated in metadata'}",
        f"Official source URL: {source_url}",
        f"Publication date: {publication_date or 'not stated in metadata'}",
    ]
    if was_truncated:
        metadata.append(
            "WARNING: the text below is TRUNCATED. Analyse only what is present "
            "and record the truncation in `notes`."
        )

    return f"""
Analyse the regulatory instrument below and extract its testable obligations.

DOCUMENT METADATA (trusted -- supplied by our system, not by the document)
{chr(10).join(metadata)}

ANALYSIS CONTEXT
Our institution is configured as: {institution_type}
Report every obligation you find, but populate `applicable_entities` strictly
from what the source text names. Our configured type must not influence what you
record there.

{wrap_source(document_text, label=document_title[:120])}

Return the structured analysis. Populate `stated_applicability` with the
document's own applicability wording if it has any.
""".strip()


def build_comparison_prompt(
    *,
    document_title: str,
    previous_label: str,
    current_label: str,
    previous_text: str,
    current_text: str,
    institution_type: str,
    truncated: bool,
) -> str:
    """Assemble the user turn for an old-versus-new comparison."""
    warning = (
        "\nWARNING: one or both texts are TRUNCATED. Compare only the portions "
        "present and record the limitation in `notes`.\n"
        if truncated
        else ""
    )
    return f"""
Compare two versions of the same regulatory instrument and report what changed.

DOCUMENT: {document_title}
PREVIOUS VERSION: {previous_label}
CURRENT VERSION: {current_label}
Our institution is configured as: {institution_type}
{warning}
--- PREVIOUS VERSION ---
{wrap_source(previous_text, label=f"PREVIOUS: {previous_label}")}

--- CURRENT VERSION ---
{wrap_source(current_text, label=f"CURRENT: {current_label}")}

Report only material changes in the obligations imposed.
""".strip()
