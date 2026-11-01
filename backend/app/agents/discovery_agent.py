"""Agent 1 -- Regulatory Discovery Agent (TinyFish Search + Fetch).

Reads the regulator's newest-first RSS feeds, then issues date-bounded,
domain-scoped searches, and persists each distinct document as a DISCOVERED row.
Instrument content is not fetched here; that is Agent 2's job.

De-duplication is by canonical URL, since RBI serves one document from several
hosts and casings. A known document is not re-inserted, but a later sighting may
fill in a publication date or a real title it was missing.
"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.base import AgentContext, AgentUnavailable, agent_activity
from app.agents.feed_parser import FeedItem, parse_feed
from app.core.config import settings
from app.core.events import EventType
from app.core.logging import get_logger
from app.integrations.tinyfish import TinyFishClient, TinyFishNotConfigured
from app.integrations.tinyfish.models import SearchResult
from app.models.enums import AgentName, DataProvenance, DiscoveryStatus, DocumentStatus
from app.models.ops import DiscoveryRun
from app.models.regulatory import RegulatoryDocument, RegulatorySource
from app.seeds.regulatory_sources import DISCOVERY_FEEDS, DISCOVERY_QUERIES

logger = get_logger(__name__)

# Hosts that serve the same RBI pages; `Mode` only selects a page layout.
RBI_HOST_ALIASES = {"rbi.org.in", "www.rbi.org.in", "m.rbi.org.in"}
IGNORED_QUERY_PARAMS = {"mode"}

# Search returns the site's page title for many documents, which says nothing.
GENERIC_TITLE_RE = re.compile(
    r"^(?:[\w\s]{0,30}\|\s*)?(?:official website of )?reserve bank(?: of india)?(?:\s*\.{3})?$",
    re.IGNORECASE,
)
# Instruments name themselves in their opening clause, which snippets often quote.
# "Draft" is captured so that a draft can never read as a final instrument.
INSTRUMENT_NAME_RE = re.compile(
    r"(?:Draft\s+)?Reserve Bank of India\s*\([^)]{3,160}\)[^.;]{0,80}?Directions,?\s*\d{4}"
)
# What RBI's site serves for some URLs instead of the requested document.
RBI_HOME_PAGE_MARKER = "# Home-Reserve Bank of India"
# Each discovery run resolves at most this many generic titles (one Fetch per URL).
MAX_TITLES_RESOLVED_PER_RUN = 40
PRESS_TITLE_LENGTH = 140

_SALUTATION_RE = re.compile(r"\b(?:dear sir|madam|dear madam)\b", re.IGNORECASE)
_TABLE_NOISE_RE = re.compile(r"(?:\|\s*-{3}\s*)+\||\|")

# Feeds carry everything the regulator issues (auctions, bond prices). Only items
# naming a topic in scope are kept, so these are deliberately specific terms:
# bare "payment" would match every "Payment Banks" addressee line.
FEED_RELEVANCE_RE = re.compile(
    r"\b(?:know your customer|kyc|aml|money laundering|due diligence|ckycr?|v-cip|ucic"
    r"|officially valid document|politically exposed|customer identification|bsbda"
    r"|basic savings|small account|account opening|neft|rtgs|imps|upi|remittance"
    r"|fund transfer)\b",
    re.IGNORECASE,
)

# An RBI circular number looks like "RBI/2025-26/169" or
# "RBI/DOR/2025-26/169", sometimes followed by a departmental reference.
CIRCULAR_NUMBER_RE = re.compile(r"\bRBI/(?:[A-Z]{2,6}/)?\d{4}-\d{2,4}/\d+\b", re.IGNORECASE)

# Document-type hints, matched against the title in priority order.
DOCUMENT_TYPE_HINTS: list[tuple[str, str]] = [
    # First, as a draft of any type is not binding and must not read as issued.
    ("draft ", "DRAFT"),
    ("faq", "FAQ"),
    ("press release", "PRESS_RELEASE"),
    ("pressrelease", "PRESS_RELEASE"),
    ("amendment direction", "NOTIFICATION"),
    ("master direction", "MASTER_DIRECTION"),
    ("directions", "MASTER_DIRECTION"),
    ("master circular", "MASTER_CIRCULAR"),
    ("notification", "NOTIFICATION"),
    ("circular", "CIRCULAR"),
    ("guidelines", "GUIDELINE"),
]

# Keyword -> banking domain, used to categorise without guessing.
DOMAIN_KEYWORDS: dict[str, list[str]] = {
    "KYC_AML": [
        "know your customer", "kyc", "due diligence", "cdd", "aml",
        "money laundering", "ckycr", "v-cip", "video based", "risk categorisation",
        "politically exposed", "officially valid document", "periodic updation",
    ],
    "CUSTOMER_ONBOARDING": [
        "customer identification", "account opening", "onboarding", "ucic",
        "unique customer identification", "customer acceptance",
    ],
    "ACCOUNT_MANAGEMENT": [
        "savings account", "current account", "bsbda", "small account",
        "basic savings", "deposit account", "account closure", "freeze",
    ],
    "TRANSACTIONS": [
        "transaction", "payment system", "digital payment", "neft", "rtgs", "imps",
        "upi", "remittance", "fund transfer", "cash withdrawal",
    ],
}


@dataclass
class Sighting:
    """One document seen by discovery, from Search or from a feed."""

    url: str
    title: str
    snippet: str
    published: date | None


def canonical_url(url: str) -> str:
    """A comparison key for a URL, never stored or fetched.

    RBI's ASP.NET site ignores case and serves the same page from several hosts,
    so these are all one document to a compliance reader.
    """
    parts = urlsplit(url.strip())
    host = parts.netloc.lower()
    if host in RBI_HOST_ALIASES:
        host = "www.rbi.org.in"
    params = sorted(
        (key.lower(), value.lower())
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key.lower() not in IGNORED_QUERY_PARAMS
    )
    return f"{host}{parts.path.lower().rstrip('/')}?{urlencode(params)}"


def _best_title(title: str, snippet: str, fallback: str) -> str:
    """The result's title, or the instrument's own name if the title is generic."""
    cleaned = (title or "").strip()
    if cleaned and not _is_generic_title(cleaned):
        return cleaned
    # Search snippets carry U+FFFD where RBI's pages use an en dash.
    named = INSTRUMENT_NAME_RE.search((snippet or "").replace("�", "-"))
    if named:
        return " ".join(named.group(0).split())
    return cleaned or fallback


def _is_generic_title(title: str) -> bool:
    return bool(GENERIC_TITLE_RE.match(title.strip()))


@dataclass
class InstrumentHeader:
    """What an RBI instrument page states about itself in its opening lines."""

    title: str | None = None
    circular_number: str | None = None
    issued: date | None = None


def parse_instrument_header(text: str) -> InstrumentHeader:
    """Read an instrument's reference block: reference, date, addressee, subject.

    Fields not clearly stated stay None. A wrong title on a regulatory instrument
    is worse than a vague one, so pages without this structure yield nothing.
    """
    head = _TABLE_NOISE_RE.sub(" ", (text or "")[:2000]).replace("\xa0", " ")
    segments = [s.strip() for s in re.split(r"\s{2,}|\n", head) if s.strip()]
    date_index = next((i for i, s in enumerate(segments[:6]) if _parse_header_date(s)), None)
    if date_index is None or not segments[0].upper().startswith("RBI/"):
        return InstrumentHeader()

    rest = [s for s in segments[date_index + 1 :] if s.lower() != "previous versions"]
    # Circulars are addressed before the subject line; Directions have neither.
    salutation = next((i for i, s in enumerate(rest[:8]) if _SALUTATION_RE.search(s)), None)
    subject = rest[(salutation + 1 if salutation is not None else 0) :]

    title = subject[0] if subject else None
    # A subject wrapped over two lines leaves its first line ending in a dash.
    if title and title[-1] in "-–" and len(subject) > 1:
        title = f"{title} {subject[1]}"
    return InstrumentHeader(
        title=title,
        circular_number=_extract_circular_number(segments[0]),
        issued=_parse_header_date(segments[date_index]),
    )


def _press_release_title(url: str, text: str) -> str | None:
    """A label quoting a press release's opening, which has no title block."""
    if "pressrelease" not in url.lower():
        return None
    opening = " ".join(_TABLE_NOISE_RE.sub(" ", text[:600]).split())
    if not opening:
        return None
    if len(opening) > PRESS_TITLE_LENGTH:
        opening = opening[:PRESS_TITLE_LENGTH].rsplit(" ", 1)[0] + "..."
    return f"Press release: {opening}"


def _parse_header_date(value: str) -> date | None:
    try:
        return datetime.strptime(value, "%B %d, %Y").date()
    except ValueError:
        return None


def fetchable_url(url: str) -> str:
    """The form of a URL to give Fetch: RBI's mobile host can serve its home page."""
    parts = urlsplit(url.strip())
    if parts.netloc.lower() not in RBI_HOST_ALIASES:
        return url
    return urlunsplit(("https", "www.rbi.org.in", parts.path, parts.query, ""))


def _detect_document_type(title: str, url: str) -> str:
    haystack = f"{title} {url}".lower()
    for needle, doc_type in DOCUMENT_TYPE_HINTS:
        if needle in haystack:
            return doc_type
    return "CIRCULAR"


def _detect_domains(text: str) -> list[str]:
    # Whole words only: as substrings, "upi" matches "occupied".
    found = [
        domain
        for domain, keywords in DOMAIN_KEYWORDS.items()
        if any(
            re.search(rf"\b{re.escape(keyword)}\b", text, re.IGNORECASE)
            for keyword in keywords
        )
    ]
    return found or ["CROSS_CUTTING"]


def _extract_circular_number(text: str) -> str | None:
    match = CIRCULAR_NUMBER_RE.search(text or "")
    return match.group(0).upper() if match else None


def _parse_result_date(result: SearchResult) -> date | None:
    """Parse the publication date Search returns, when it returns one.

    Returns None rather than guessing: an invented publication date would
    corrupt the regulatory timeline.
    """
    raw = (result.date or "").strip()
    if not raw:
        return None
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%b %d, %Y", "%d %b %Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(raw[:20], fmt).date()
        except ValueError:
            continue
    # ISO-8601 with a time component.
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).date()
    except ValueError:
        logger.debug("Unparseable search result date %r; leaving unset", raw)
        return None


def _is_official(url: str, allowed_domains: list[str]) -> bool:
    """Keep only results actually hosted on an allowed regulator domain.

    Search is already scoped via include_domains, but this guards against a
    result whose URL is off-domain (e.g. an aggregator mirror).
    """
    # The parsed host, not the whole string: a query like ?u=//rbi.org.in must not
    # let another site's page be recorded as an official regulatory source.
    host = (urlsplit(url.strip()).hostname or "").lower()
    return any(host == domain or host.endswith(f".{domain}") for domain in allowed_domains)


class DiscoveryAgent:
    """Discovers regulatory publications using TinyFish Search."""

    def __init__(self, client: TinyFishClient | None = None) -> None:
        self.client = client or TinyFishClient()

    async def run(
        self,
        db: Session,
        ctx: AgentContext,
        *,
        queries: list[dict] | None = None,
        institution_type: str | None = None,
        recency_minutes: int | None = None,
        max_results_per_query: int = 10,
    ) -> DiscoveryRun:
        """Execute a discovery run and persist the documents found.

        Caller-supplied `queries` are a targeted lookup, so the feeds are only
        read for the default run. `recency_minutes` replaces the default
        lookback window, because Search rejects the two together.
        """
        if not self.client.configured:
            raise AgentUnavailable(
                "TinyFish Search is not configured, so regulatory discovery cannot run.",
                remediation=(
                    "Set TINYFISH_API_KEY in backend/.env. Search is free up to "
                    "12,000 requests/day: https://agent.tinyfish.ai/api-keys"
                ),
            )

        query_set = queries or DISCOVERY_QUERIES
        feeds = [] if queries else DISCOVERY_FEEDS
        domains = settings.regulatory_domain_list
        institution = institution_type or settings.institution_type
        after_date = (
            None
            if recency_minutes is not None
            else (datetime.now(UTC).date() - timedelta(days=settings.discovery_lookback_days))
            .isoformat()
        )

        run = DiscoveryRun(
            status=DiscoveryStatus.RUNNING.value,
            regulatory_authority=settings.regulatory_authority,
            institution_type=institution,
            queries=[*feeds, *(q["query"] for q in query_set)],
            search_domains=domains,
            provenance=DataProvenance.LIVE_TINYFISH.value,
            started_at=datetime.now(UTC),
            tinyfish_request_ids=[],
        )
        db.add(run)
        db.commit()
        db.refresh(run)

        ctx.emit(
            EventType.DISCOVERY_STARTED,
            {
                "discovery_run_id": str(run.id),
                "queries": run.queries,
                "domains": domains,
                "institution_type": institution,
            },
        )

        source = self._default_source(db)
        request_ids: list[str] = []
        found = new = duplicate = 0
        failures: list[str] = []
        known = self._known_documents(db)

        # Feeds first: their titles are the regulator's own, so a search hit on
        # the same document in this run cannot leave it with a generic one.
        for feed_url in feeds:
            try:
                with agent_activity(
                    db,
                    agent=AgentName.DISCOVERY,
                    operation=f"feed: {feed_url}",
                    ctx=ctx,
                    entity_type="DiscoveryRun",
                    entity_id=run.id,
                    external_service="tinyfish.fetch",
                    details={"feed": feed_url},
                ) as outcome:
                    items = await self._read_feed(feed_url)
                    outcome["result_count"] = len(items)
                    outcome["message"] = f"{len(items)} relevant items"

                for item in items:
                    if not _is_official(item.url, domains):
                        logger.debug("Skipping off-domain feed item %s", item.url)
                        continue
                    found += 1
                    created = self._persist_sighting(
                        db,
                        sighting=Sighting(
                            url=item.url,
                            title=item.title,
                            snippet=item.text,
                            published=item.published,
                        ),
                        run=run,
                        source=source,
                        declared_domains=[],
                        known=known,
                    )
                    if created:
                        new += 1
                    else:
                        duplicate += 1

            except TinyFishNotConfigured:
                raise
            except Exception as exc:
                # A feed outage must not stop the searches from running.
                failures.append(f"{feed_url}: {str(exc)[:200]}")
                logger.warning("Discovery feed failed (%s): %s", feed_url, exc)

        for entry in query_set:
            query = entry["query"]
            try:
                with agent_activity(
                    db,
                    agent=AgentName.DISCOVERY,
                    operation=f"search: {query}",
                    ctx=ctx,
                    entity_type="DiscoveryRun",
                    entity_id=run.id,
                    external_service="tinyfish.search",
                    details={"query": query, "include_domains": domains, "after_date": after_date},
                ) as outcome:
                    response = await self.client.search(
                        query,
                        purpose=entry.get("purpose"),
                        include_domains=domains,
                        recency_minutes=recency_minutes,
                        after_date=after_date,
                    )
                    outcome["request_id"] = response.request_id
                    outcome["result_count"] = len(response.results)
                    outcome["message"] = f"{len(response.results)} results"

                if response.request_id:
                    request_ids.append(response.request_id)

                for result in response.results[:max_results_per_query]:
                    if not _is_official(result.url, domains):
                        logger.debug("Skipping off-domain result %s", result.url)
                        continue
                    found += 1
                    created = self._persist_sighting(
                        db,
                        sighting=Sighting(
                            url=result.url,
                            title=result.title,
                            snippet=result.snippet,
                            published=_parse_result_date(result),
                        ),
                        run=run,
                        source=source,
                        declared_domains=entry.get("banking_domains") or [],
                        known=known,
                    )
                    if created:
                        new += 1
                    else:
                        duplicate += 1

            except TinyFishNotConfigured:
                raise
            except Exception as exc:
                # One failing query must not abandon the whole run.
                failures.append(f"{query}: {str(exc)[:200]}")
                logger.warning("Discovery query failed (%s): %s", query, exc)

        db.commit()

        await self._resolve_generic_titles(db, ctx, run, known)

        run.documents_found = found
        run.documents_new = new
        run.documents_duplicate = duplicate
        run.tinyfish_request_ids = request_ids
        run.finished_at = datetime.now(UTC)
        if failures and new == 0:
            run.status = DiscoveryStatus.FAILED.value
            run.error_message = "; ".join(failures)[:2000]
        elif failures:
            run.status = DiscoveryStatus.PARTIAL.value
            run.error_message = "; ".join(failures)[:2000]
        else:
            run.status = DiscoveryStatus.COMPLETED.value
        db.commit()
        db.refresh(run)

        ctx.emit(
            EventType.DOCUMENTS_FOUND,
            {
                "discovery_run_id": str(run.id),
                "status": run.status,
                "found": found,
                "new": new,
                "duplicate": duplicate,
                "failed_queries": len(failures),
            },
        )
        return run

    # ---- persistence helpers ---------------------------------------------
    def _default_source(self, db: Session) -> RegulatorySource | None:
        return (
            db.execute(
                select(RegulatorySource)
                .where(RegulatorySource.authority == settings.regulatory_authority)
                .order_by(RegulatorySource.created_at)
            )
            .scalars()
            .first()
        )

    def _known_documents(self, db: Session) -> dict[str, RegulatoryDocument]:
        """Current version of every stored document, by canonical URL."""
        rows = (
            db.execute(select(RegulatoryDocument).order_by(RegulatoryDocument.version))
            .scalars()
            .all()
        )
        # Ordered by version, so the newest row for a URL wins.
        return {canonical_url(row.source_url): row for row in rows}

    async def _read_feed(self, feed_url: str) -> list[FeedItem]:
        """Read one RSS feed, returning only the items within scope."""
        # html, not markdown: the markdown conversion strips the item structure.
        # ttl=0 because a cached copy of a "what's new" feed defeats its purpose.
        response = await self.client.fetch(
            [feed_url],
            purpose="Read the regulator's latest publications feed.",
            fmt="html",
            ttl=0,
            page_metadata=False,
        )
        if response.errors:
            raise RuntimeError(f"fetch error: {response.errors[0].error}")
        items = parse_feed(response.results[0].text_as_str if response.results else "")
        if not items:
            # An empty parse means the feed's shape changed, not that the regulator
            # went quiet; reporting success here would hide that we went blind.
            raise RuntimeError("no items could be parsed from the feed")
        return [
            item
            for item in items
            if FEED_RELEVANCE_RE.search(f"{item.title} {item.text}")
        ]

    async def _resolve_generic_titles(
        self,
        db: Session,
        ctx: AgentContext,
        run: DiscoveryRun,
        known: dict[str, RegulatoryDocument],
    ) -> None:
        """Replace site-title placeholders using each page's own reference block.

        Best-effort: a document keeping its generic title is not a discovery
        failure, so problems here are logged and never fail the run.
        """
        targets = [
            doc for doc in known.values() if doc.is_current and _is_generic_title(doc.title)
        ]
        targets.sort(key=lambda d: d.publication_date or date.min, reverse=True)
        targets = targets[:MAX_TITLES_RESOLVED_PER_RUN]
        if not targets:
            return

        by_url = {fetchable_url(doc.source_url): doc for doc in targets}
        try:
            with agent_activity(
                db,
                agent=AgentName.DISCOVERY,
                operation="resolve document titles",
                ctx=ctx,
                entity_type="DiscoveryRun",
                entity_id=run.id,
                external_service="tinyfish.fetch",
                details={"urls": len(by_url)},
            ) as outcome:
                response = await self.client.chunked_fetch(
                    list(by_url),
                    purpose="Read the title block of an official regulatory instrument.",
                    fmt="markdown",
                    page_metadata=False,
                )
                resolved = 0
                for result in response.results:
                    document = by_url.get(result.url)
                    text = result.text_as_str
                    if document is None or text.startswith(RBI_HOME_PAGE_MARKER):
                        continue
                    header = parse_instrument_header(text)
                    title = header.title or _press_release_title(document.source_url, text)
                    if title:
                        document.title = title[:1024]
                        resolved += 1
                    document.circular_number = (
                        document.circular_number or header.circular_number
                    )
                    document.publication_date = document.publication_date or header.issued
                outcome["message"] = f"{resolved} of {len(by_url)} titles resolved"
            db.commit()
        except TinyFishNotConfigured:
            raise
        except Exception as exc:
            db.rollback()
            logger.warning("Could not resolve document titles: %s", exc)

    def _persist_sighting(
        self,
        db: Session,
        *,
        sighting: Sighting,
        run: DiscoveryRun,
        source: RegulatorySource | None,
        declared_domains: list[str],
        known: dict[str, RegulatoryDocument],
    ) -> bool:
        """Insert a discovered document. Returns False if already known."""
        key = canonical_url(sighting.url)
        title = _best_title(sighting.title, sighting.snippet, sighting.url)

        existing = known.get(key)
        if existing is not None:
            # Only fill gaps. A stated date or a real title is never overwritten.
            if existing.publication_date is None and sighting.published is not None:
                existing.publication_date = sighting.published
            if _is_generic_title(existing.title) and not _is_generic_title(title):
                existing.title = title[:1024]
            return False

        haystack = f"{title} {sighting.snippet or ''}"
        domains = sorted(set(declared_domains) | set(_detect_domains(haystack)))

        document = RegulatoryDocument(
            source_id=source.id if source else None,
            discovery_run_id=run.id,
            title=title[:1024],
            circular_number=_extract_circular_number(haystack),
            document_type=_detect_document_type(title, sighting.url),
            source_url=sighting.url,
            publication_date=sighting.published,
            banking_domains=domains,
            version=1,
            is_current=True,
            status=DocumentStatus.DISCOVERED.value,
            provenance=DataProvenance.LIVE_TINYFISH.value,
            search_snippet=(sighting.snippet or "")[:4000] or None,
        )
        db.add(document)
        # Flush per row so a constraint violation is attributable, and so later
        # results in the same run can still be inserted.
        try:
            db.flush()
        except Exception as exc:
            db.rollback()
            logger.warning("Could not persist discovered document %s: %s", sighting.url, exc)
            return False
        known[key] = document
        return True


async def get_discovery_run(db: Session, run_id: uuid.UUID) -> DiscoveryRun | None:
    return db.get(DiscoveryRun, run_id)
