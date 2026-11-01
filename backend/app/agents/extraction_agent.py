"""Agent 2 -- Regulatory Extraction Agent (TinyFish Fetch).

Fetches an official document, extracts clean text, segments it into
source-referenced clauses and stores it as an immutable version.

Versioning contract:
  * The content hash of the normalised text identifies a version.
  * Same hash  -> nothing stored; `fetched_at` is refreshed.
  * New hash   -> a NEW document row at version N+1, `supersedes_id` pointing at
                  the previous row, which is flipped to `is_current = False`.
    The old row and its clauses are never modified or deleted.

When the official document cannot be retrieved we record the limitation on the
row (`access_limitation`, status FETCH_FAILED) and store no text. Inventing
content is never an option.
"""
from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.base import AgentContext, AgentUnavailable, agent_activity
from app.agents.clause_parser import (
    MAX_CLAUSES_PER_DOCUMENT,
    content_hash,
    detect_applicable_entities,
    extract_applicability,
    extract_effective_date,
    parse_clauses,
    restore_paragraph_breaks,
)
from app.agents.discovery_agent import RBI_HOME_PAGE_MARKER, fetchable_url
from app.core.events import EventType
from app.core.logging import get_logger
from app.integrations.tinyfish import TinyFishClient
from app.integrations.tinyfish.models import FetchResult
from app.models.enums import AgentName, DataProvenance, DocumentStatus
from app.models.regulatory import RegulatoryClause, RegulatoryDocument

logger = get_logger(__name__)

# A demo-fixture title carries this marker. Once real content replaces the
# fixture, the marker must go with it.
DEMO_MARKER_RE = re.compile(r"\s*\[DEMO FIXTURE TEXT\]\s*", re.IGNORECASE)

# Strip site chrome so the extracted text is the instrument, not the portal.
DEFAULT_EXCLUDE_SELECTORS = [
    "nav",
    "header",
    "footer",
    "script",
    "style",
    "noscript",
    ".breadcrumb",
    "#sidebar",
]


class ExtractionResult:
    """Outcome of extracting one document."""

    def __init__(
        self,
        *,
        document: RegulatoryDocument,
        created_version: bool,
        clause_count: int,
        unchanged: bool = False,
        error: str | None = None,
    ) -> None:
        self.document = document
        self.created_version = created_version
        self.clause_count = clause_count
        self.unchanged = unchanged
        self.error = error

    def to_dict(self) -> dict:
        return {
            "document_id": str(self.document.id),
            "title": self.document.title,
            "version": self.document.version,
            "status": self.document.status,
            "created_version": self.created_version,
            "unchanged": self.unchanged,
            "clause_count": self.clause_count,
            "error": self.error,
        }


def _status_before_fetch(document: RegulatoryDocument) -> str:
    """The status to return to if this fetch changes nothing."""
    if document.status != DocumentStatus.FETCHING.value:
        return document.status
    # Left behind by an interrupted fetch, so the true prior status is lost.
    return (
        DocumentStatus.EXTRACTED.value if document.raw_text else DocumentStatus.DISCOVERED.value
    )


def _settled_status(prior_status: str) -> str:
    """Status after a successful fetch of unchanged text: analysis remains valid."""
    if prior_status == DocumentStatus.ANALYSED.value:
        return prior_status
    return DocumentStatus.EXTRACTED.value


class ExtractionAgent:
    def __init__(self, client: TinyFishClient | None = None) -> None:
        self.client = client or TinyFishClient()

    async def recheck_current(self, db: Session, ctx: AgentContext) -> list[ExtractionResult]:
        """Re-fetch every stored instrument so in-place revisions become new versions.

        RBI revises consolidated Directions at the same URL ("Updated as on ..."),
        which discovery alone cannot notice because it skips known URLs. Demo
        fixtures are skipped: their synthetic text must not be replaced unasked.
        """
        document_ids = list(
            db.execute(
                select(RegulatoryDocument.id).where(
                    RegulatoryDocument.is_current.is_(True),
                    RegulatoryDocument.raw_text.is_not(None),
                    RegulatoryDocument.provenance != DataProvenance.DEMO_FIXTURE.value,
                )
            )
            .scalars()
            .all()
        )
        return await self.extract_batch(db, ctx, document_ids)

    async def extract_document(
        self,
        db: Session,
        ctx: AgentContext,
        document_id: uuid.UUID,
        *,
        force_refetch: bool = False,
    ) -> ExtractionResult:
        """Fetch and extract one discovered document."""
        if not self.client.configured:
            raise AgentUnavailable(
                "TinyFish Fetch is not configured, so document extraction cannot run.",
                remediation=(
                    "Set TINYFISH_API_KEY in backend/.env. Fetch is free up to "
                    "1,000 URLs/day: https://agent.tinyfish.ai/api-keys"
                ),
            )

        document = db.get(RegulatoryDocument, document_id)
        if document is None:
            raise LookupError(f"Regulatory document {document_id} not found")

        ctx.emit(
            EventType.EXTRACTION_STARTED,
            {"document_id": str(document.id), "title": document.title, "url": document.source_url},
        )

        prior_status = _status_before_fetch(document)
        try:
            extraction = await self._fetch_and_store(
                db, ctx, document, prior_status, force_refetch=force_refetch
            )
        except Exception:
            db.rollback()
            document.status = prior_status
            db.commit()
            raise
        # When a newer row now holds the result, this one is history, not mid-fetch.
        if extraction.document.id != document.id:
            document.status = prior_status
            db.commit()
        return extraction

    async def _fetch_and_store(
        self,
        db: Session,
        ctx: AgentContext,
        document: RegulatoryDocument,
        prior_status: str,
        *,
        force_refetch: bool,
    ) -> ExtractionResult:
        previous_etag = document.http_etag if not force_refetch else None
        previous_modified = document.http_last_modified if not force_refetch else None

        with agent_activity(
            db,
            agent=AgentName.EXTRACTION,
            operation=f"fetch: {document.source_url}",
            ctx=ctx,
            entity_type="RegulatoryDocument",
            entity_id=document.id,
            external_service="tinyfish.fetch",
            details={"url": document.source_url, "force_refetch": force_refetch},
        ) as outcome:
            document.status = DocumentStatus.FETCHING.value
            db.commit()

            response = await self.client.fetch(
                [fetchable_url(document.source_url)],
                purpose=(
                    "Extract the full text of an official regulatory instrument for "
                    "compliance impact analysis, preserving clause numbering."
                ),
                fmt="markdown",
                # Always live: a cached copy could hide an amendment. The conditional
                # headers below still spare a re-download when nothing changed.
                ttl=0,
                include_etag_and_last_modified=True,
                if_none_match=previous_etag,
                if_modified_since=previous_modified if not previous_etag else None,
                exclude_selectors=DEFAULT_EXCLUDE_SELECTORS,
                page_metadata=True,
            )
            outcome["request_id"] = response.request_id

            if response.errors:
                error = response.errors[0]
                outcome["message"] = f"fetch error: {error.error}"
                outcome["fetch_error"] = error.model_dump()
                return self._record_failure(db, document, prior_status, error.error, error.status)

            if not response.results:
                outcome["message"] = "no result returned"
                return self._record_failure(db, document, prior_status, "empty_response", None)

            result = response.results[0]
            # Stored as the instrument, the site's home page would be analysed as law.
            if result.text_as_str.startswith(RBI_HOME_PAGE_MARKER):
                outcome["message"] = "regulator served its home page"
                return self._record_failure(
                    db, document, prior_status, "redirected_to_home_page", None
                )

            outcome["message"] = f"{len(result.text_as_str)} chars extracted"
            outcome["final_url"] = result.final_url

            # 304: the regulator confirms the document is byte-identical.
            if result.not_modified:
                document.status = _settled_status(prior_status)
                document.access_limitation = None
                document.fetched_at = datetime.now(UTC)
                db.commit()
                outcome["unchanged"] = True
                extraction = ExtractionResult(
                    document=document,
                    created_version=False,
                    clause_count=len(document.clauses),
                    unchanged=True,
                )
                self._emit_completed(ctx, extraction)
                return extraction

            extraction = self._persist_version(db, document, prior_status, result)
            outcome["created_version"] = extraction.created_version
            outcome["clause_count"] = extraction.clause_count
            outcome["version"] = extraction.document.version

        self._emit_completed(ctx, extraction)
        return extraction

    def _emit_completed(self, ctx: AgentContext, extraction: ExtractionResult) -> None:
        ctx.emit(EventType.EXTRACTION_COMPLETED, extraction.to_dict())

    def _record_failure(
        self,
        db: Session,
        document: RegulatoryDocument,
        prior_status: str,
        error_code: str,
        http_status: int | None,
    ) -> ExtractionResult:
        """Record an access limitation rather than fabricating content.

        A failed re-check leaves stored text valid, so only a document with no
        text at all becomes FETCH_FAILED.
        """
        detail = f"TinyFish Fetch could not retrieve this document (error={error_code}"
        detail += f", upstream HTTP {http_status})" if http_status else ")"
        if error_code in ("target_http_error", "blocked", "anti_bot"):
            detail += (
                ". The regulator may be blocking automated retrieval; the document "
                "must be reviewed manually at its official URL."
            )
        document.status = prior_status if document.raw_text else DocumentStatus.FETCH_FAILED.value
        document.access_limitation = detail[:2000]
        document.fetched_at = datetime.now(UTC)
        db.commit()
        logger.warning("Extraction failed for %s: %s", document.source_url, detail)
        return ExtractionResult(
            document=document, created_version=False, clause_count=0, error=detail
        )

    def _persist_version(
        self,
        db: Session,
        document: RegulatoryDocument,
        prior_status: str,
        result: FetchResult,
    ) -> ExtractionResult:
        """Store extracted text, creating a new version when content changed."""
        text = result.text_as_str
        if not text.strip():
            return self._record_failure(db, document, prior_status, "empty_extraction", None)

        new_hash = content_hash(text)
        now = datetime.now(UTC)

        # Find the newest stored version of this logical document.
        current = (
            db.execute(
                select(RegulatoryDocument)
                .where(RegulatoryDocument.source_url == document.source_url)
                .order_by(RegulatoryDocument.version.desc())
            )
            .scalars()
            .first()
        ) or document

        if current.content_hash == new_hash and current.raw_text:
            # Identical content: refresh liveness markers only.
            current.fetched_at = now
            current.status = _settled_status(
                prior_status if current.id == document.id else current.status
            )
            current.access_limitation = None
            if result.etag:
                current.http_etag = result.etag
            if result.last_modified:
                current.http_last_modified = result.last_modified
            db.commit()
            return ExtractionResult(
                document=current,
                created_version=False,
                clause_count=len(current.clauses),
                unchanged=True,
            )

        # Is this the first extraction of an as-yet-empty discovered row?
        extending_in_place = current.id == document.id and not current.content_hash

        if extending_in_place:
            target = current
        else:
            # Preserve history: new row, old row retired.
            target = RegulatoryDocument(
                source_id=current.source_id,
                discovery_run_id=current.discovery_run_id,
                title=current.title,
                circular_number=current.circular_number,
                document_type=current.document_type,
                source_url=current.source_url,
                banking_domains=current.banking_domains,
                publication_date=current.publication_date,
                version=current.version + 1,
                supersedes_id=current.id,
                is_current=True,
                provenance=DataProvenance.LIVE_TINYFISH.value,
                search_snippet=current.search_snippet,
            )
            current.is_current = False
            db.add(target)
            db.flush()

        structured = restore_paragraph_breaks(text)
        applicability = extract_applicability(structured)
        entities = detect_applicable_entities(applicability or structured[:20000])

        target.final_url = result.final_url
        target.raw_text = text
        target.content_format = result.format or "markdown"
        target.char_count = len(text)
        target.content_hash = new_hash
        target.http_etag = result.etag
        target.http_last_modified = result.last_modified
        target.effective_date = extract_effective_date(structured)
        target.applicability_text = applicability
        target.applicable_institution_types = entities
        target.status = DocumentStatus.EXTRACTED.value
        target.fetched_at = now
        target.access_limitation = None
        target.is_current = True
        if result.title and not target.title.strip():
            target.title = result.title[:1024]
        # A live fetch replacing demo-fixture content must not inherit the
        # fixture's "[DEMO FIXTURE TEXT]" marker: the text is now the
        # regulator's own and the title would misdescribe it.
        target.title = DEMO_MARKER_RE.sub("", target.title).strip()
        # Prefer a publication date the page itself declares over a search guess.
        if result.published_date and target.publication_date is None:
            parsed = extract_effective_date(f"effective from {result.published_date}")
            if parsed:
                target.publication_date = parsed

        clause_count = self.replace_clauses(db, target, structured)
        db.commit()
        db.refresh(target)

        logger.info(
            "Extracted %s v%d: %d chars, %d clauses",
            target.source_url,
            target.version,
            len(text),
            clause_count,
        )
        return ExtractionResult(
            document=target,
            created_version=not extending_in_place,
            clause_count=clause_count,
        )

    def replace_clauses(
        self, db: Session, target: RegulatoryDocument, structured_text: str
    ) -> int:
        """Re-segment one version. Prior versions keep their own clauses."""
        clauses = parse_clauses(structured_text)
        if len(clauses) >= MAX_CLAUSES_PER_DOCUMENT:
            target.access_limitation = (
                f"Clause segmentation stopped at the limit of {MAX_CLAUSES_PER_DOCUMENT} "
                "clauses; later clauses of this document are not individually citable."
            )
            logger.warning("Clause limit reached for %s", target.source_url)
        for existing in list(target.clauses):
            db.delete(existing)
        db.flush()

        for parsed in clauses:
            db.add(
                RegulatoryClause(
                    document_id=target.id,
                    clause_reference=parsed.clause_reference,
                    heading=(parsed.heading or None),
                    text=parsed.text,
                    ordinal=parsed.ordinal,
                    char_start=parsed.char_start,
                    char_end=parsed.char_end,
                    clause_hash=parsed.clause_hash,
                    banking_domains=target.banking_domains,
                    provenance=DataProvenance.DETERMINISTIC.value,
                )
            )
        return len(clauses)

    async def extract_batch(
        self,
        db: Session,
        ctx: AgentContext,
        document_ids: list[uuid.UUID],
        *,
        force_refetch: bool = False,
    ) -> list[ExtractionResult]:
        """Extract several documents, continuing past individual failures."""
        results: list[ExtractionResult] = []
        for document_id in document_ids:
            try:
                results.append(
                    await self.extract_document(
                        db, ctx, document_id, force_refetch=force_refetch
                    )
                )
            except AgentUnavailable:
                raise
            except Exception as exc:
                logger.warning("Extraction of %s failed: %s", document_id, exc)
                document = db.get(RegulatoryDocument, document_id)
                if document is not None:
                    results.append(
                        ExtractionResult(
                            document=document,
                            created_version=False,
                            clause_count=0,
                            error=str(exc)[:500],
                        )
                    )
        return results
