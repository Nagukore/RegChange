"""Discovery must surface the latest instruments, and times must read as UTC."""
from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.agents.base import AgentContext
from app.agents.clause_parser import content_hash, parse_clauses, restore_paragraph_breaks
from app.agents.discovery_agent import (
    DiscoveryAgent,
    InstrumentHeader,
    _best_title,
    _detect_document_type,
    _detect_domains,
    _is_official,
    _press_release_title,
    canonical_url,
    fetchable_url,
    parse_instrument_header,
)
from app.agents.extraction_agent import ExtractionAgent
from app.agents.feed_parser import parse_feed
from app.core.config import settings
from app.integrations.tinyfish.models import FetchResponse, FetchResult, SearchResponse, SearchResult
from app.models.ops import DiscoveryRun
from app.models.regulatory import RegulatoryDocument
from app.schemas.pipeline import DiscoveryRunOut
from app.services.recovery import reconcile_interrupted_work

# The shape TinyFish Fetch really returns for RBI's feed (fmt=html): <link> tags
# gone, pubDate lower-cased, title CDATA escaped.
MANGLED_FEED = (
    '<?xml version="1.0" encoding="utf-8"?><rss><channel><title>NOTIFICATIONS FROM RBI</title>'
    "http://www.rbi.org.in<description>This is Feed from RBI for notifications.</description>"
    "<item><title>&lt;![CDATA[Implementation of Section 51A of UAPA, 1967: Updates]]&gt;</title>"
    '<description><![CDATA[<table><tr><td><p>RBI/2026-27/303<br>DoR.AML.REC.255/14.06.001/2026-27</p>'
    '<p>See <a href="https://rbi.org.in/scripts/BS_ViewMasDirections.aspx?id=13136">the Directions</a>'
    "</p></td></tr></table>]]></description>"
    "https://www.rbi.org.in/scripts/NotificationUser.aspx?Id=13749&amp;Mode=0"
    "<pubdate>Fri, 30 Oct 2026 15:15:00</pubdate></item>"
    "<item><title>&lt;![CDATA[Auction of 91-Day Treasury Bills]]&gt;</title>"
    "<description><![CDATA[<p>To the Payment Banks, on limits.</p>]]></description>"
    "https://www.rbi.org.in/scripts/NotificationUser.aspx?Id=13750&amp;Mode=0"
    "<pubdate>Fri, 30 Oct 2026 16:00:00</pubdate></item>"
    "</channel></rss>"
)

WELL_FORMED_FEED = (
    "<rss><channel><item><title><![CDATA[KYC &amp; AML update]]></title>"
    "<description><![CDATA[<p>Body</p>]]></description>"
    "<link>https://www.rbi.org.in/scripts/NotificationUser.aspx?Id=1&amp;Mode=0</link>"
    "<pubDate>Mon, 19 Oct 2026 16:50:00</pubDate></item></channel></rss>"
)


class TestFeedParser:
    def test_parses_mangled_fetch_output(self):
        first = parse_feed(MANGLED_FEED)[0]
        assert first.title == "Implementation of Section 51A of UAPA, 1967: Updates"
        assert first.published == date(2026, 10, 30)
        assert "RBI/2026-27/303" in first.text

    def test_takes_item_link_not_a_link_inside_the_description(self):
        assert parse_feed(MANGLED_FEED)[0].url == (
            "https://www.rbi.org.in/scripts/NotificationUser.aspx?Id=13749&Mode=0"
        )

    def test_parses_well_formed_rss(self):
        item = parse_feed(WELL_FORMED_FEED)[0]
        assert item.title == "KYC & AML update"
        assert item.url.endswith("Id=1&Mode=0")
        assert item.published == date(2026, 10, 19)

    def test_unparseable_date_is_left_unset(self):
        feed = WELL_FORMED_FEED.replace("Mon, 19 Oct 2026 16:50:00", "soon")
        assert parse_feed(feed)[0].published is None


class TestCanonicalUrl:
    @pytest.mark.parametrize(
        "variant",
        [
            "https://m.rbi.org.in/Scripts/NotificationUser.aspx?Id=13699&Mode=0",
            "https://www.rbi.org.in/scripts/NotificationUser.aspx?id=13699",
            "https://rbi.org.in/SCRIPTS/notificationuser.aspx?Id=13699&Mode=1",
        ],
    )
    def test_same_document_across_hosts_and_casing(self, variant):
        assert canonical_url(variant) == canonical_url(
            "https://www.rbi.org.in/Scripts/NotificationUser.aspx?Id=13699"
        )

    def test_different_documents_stay_distinct(self):
        assert canonical_url(
            "https://www.rbi.org.in/Scripts/NotificationUser.aspx?Id=1"
        ) != canonical_url("https://www.rbi.org.in/Scripts/NotificationUser.aspx?Id=2")

    def test_other_hosts_are_not_aliased(self):
        assert "rbidocs.rbi.org.in" in canonical_url(
            "https://rbidocs.rbi.org.in/rdocs/notification/PDFs/169MD.PDF"
        )


class TestClassification:
    def test_generic_title_replaced_by_instrument_name(self):
        title = _best_title(
            "Notifications| Official Website of Reserve Bank of India",
            "(1) These Directions shall be called the Reserve Bank of India (Commercial Banks "
            "� Know Your Customer) Amendment Directions, 2026. RBI/2026-27/257.",
            "https://example",
        )
        assert title == (
            "Reserve Bank of India (Commercial Banks - Know Your Customer) "
            "Amendment Directions, 2026"
        )

    def test_real_title_is_kept(self):
        assert _best_title("FAQs on Master Direction on KYC", "", "x") == (
            "FAQs on Master Direction on KYC"
        )

    def test_generic_title_kept_when_snippet_names_nothing(self):
        generic = "Official Website of Reserve Bank of India"
        assert _best_title(generic, "Unrelated text", "x") == generic

    def test_domain_keywords_match_whole_words(self):
        assert _detect_domains("the premises occupied by the Limited company") == ["CROSS_CUTTING"]
        assert "TRANSACTIONS" in _detect_domains("transfers over UPI")

    def test_document_type_priorities(self):
        assert _detect_document_type("FAQs on Master Direction on KYC", "x") == "FAQ"
        assert (
            _detect_document_type("KYC Amendment Directions, 2026", "NotificationUser.aspx")
            == "NOTIFICATION"
        )
        assert (
            _detect_document_type("Anything", "https://rbi.org.in/BS_PressReleaseDisplay.aspx")
            == "PRESS_RELEASE"
        )


class TestInstrumentHeader:
    def test_directions_have_the_title_straight_after_the_date(self):
        header = parse_instrument_header(
            "RBI/DOR/2025-26/243   DOR.AML.REC.No.162/14.01.008/2025-26  November 28, 2025  "
            "Previous Versions  Reserve Bank of India (Local Area Banks – Know Your Customer) "
            "Directions, 2025 (Updated as on October 1, 2026)  Introduction  In order to"
        )
        assert header.title.startswith("Reserve Bank of India (Local Area Banks")
        assert header.circular_number == "RBI/DOR/2025-26/243"
        assert header.issued == date(2025, 11, 28)

    def test_circulars_have_the_subject_after_the_salutation(self):
        header = parse_instrument_header(
            "|  |\n| --- |\n| RBI/2025-26/53  DOR.AML.REC.31/14.01.001/2025-26  June 12, 2025  "
            "The Chairpersons/ CEOs of all the Regulated Entities  Dear Sir/ Madam,  "
            "Updation/ Periodic Updation of KYC – Revised Instructions  Please refer to"
        )
        assert header.title == "Updation/ Periodic Updation of KYC – Revised Instructions"
        assert header.issued == date(2025, 6, 12)

    def test_subject_wrapped_over_two_lines_is_rejoined(self):
        header = parse_instrument_header(
            "RBI/2013-14/355  UBD.BPD.(PCB).Cir.No.35/13.01.000/2013-14  October 31, 2013  "
            "All Primary (Urban) Co-operative Banks  Madam / Dear Sir,  "
            "Financial Inclusion- Access to Banking Services –  "
            "Basic Savings Bank Deposit Account (BSBDA) - FAQs  Please refer"
        )
        assert header.title == (
            "Financial Inclusion- Access to Banking Services – "
            "Basic Savings Bank Deposit Account (BSBDA) - FAQs"
        )

    @pytest.mark.parametrize(
        "text",
        [
            "# Home-Reserve Bank of India\n\n## PREAMBLE\n\nto regulate the issue of Bank notes",
            "The Reserve Bank of India (RBI) has, by an order dated June 15, 2026, imposed",
            "",
        ],
    )
    def test_pages_without_a_reference_block_yield_nothing(self, text):
        assert parse_instrument_header(text) == InstrumentHeader()


class TestFetchableUrl:
    def test_mobile_host_is_replaced(self):
        assert fetchable_url(
            "https://m.rbi.org.in/Scripts/NotificationUser.aspx?Id=12008&Mode=0"
        ) == "https://www.rbi.org.in/Scripts/NotificationUser.aspx?Id=12008&Mode=0"

    def test_other_hosts_are_untouched(self):
        url = "https://rbidocs.rbi.org.in/rdocs/notification/PDFs/169MD.PDF"
        assert fetchable_url(url) == url


class _StubClient:
    configured = True

    def __init__(self, *, feed: str = MANGLED_FEED, results: list[SearchResult] | None = None):
        self.feed = feed
        self.results = results or []
        self.pages: dict[str, str] = {}
        self.search_kwargs: list[dict] = []
        self.fetch_kwargs: list[dict] = []

    async def fetch(self, urls, **kwargs):
        self.fetch_kwargs.append({"urls": urls, **kwargs})
        return FetchResponse(results=[FetchResult(url=urls[0], text=self.feed, format="html")])

    async def chunked_fetch(self, urls, **kwargs):
        return FetchResponse(
            results=[FetchResult(url=u, text=self.pages.get(u, "")) for u in urls]
        )

    async def search(self, query, **kwargs):
        self.search_kwargs.append({"query": query, **kwargs})
        return SearchResponse(query=query, results=self.results)


def _hit(url: str, *, title: str = "A title", date_: str | None = None) -> SearchResult:
    return SearchResult(position=1, site_name="RBI", snippet="", title=title, url=url, date=date_)


def _docs_for_run(db, run: DiscoveryRun) -> list[RegulatoryDocument]:
    return list(
        db.execute(
            select(RegulatoryDocument).where(RegulatoryDocument.discovery_run_id == run.id)
        ).scalars()
    )


class TestDiscoveryRun:
    async def _run(self, db, client, **kwargs) -> DiscoveryRun:
        return await DiscoveryAgent(client=client).run(db, AgentContext(actor="test"), **kwargs)

    @pytest.mark.asyncio
    async def test_searches_are_bounded_to_the_lookback_window(self, db):
        client = _StubClient()
        await self._run(db, client)
        expected = (
            datetime.now(UTC).date() - timedelta(days=settings.discovery_lookback_days)
        ).isoformat()
        assert client.search_kwargs
        assert all(k["after_date"] == expected for k in client.search_kwargs)

    @pytest.mark.asyncio
    async def test_recency_replaces_the_window(self, db):
        client = _StubClient()
        await self._run(db, client, recency_minutes=60)
        assert all(k["after_date"] is None for k in client.search_kwargs)

    @pytest.mark.asyncio
    async def test_feed_is_read_live_and_filtered_to_scope(self, db):
        client = _StubClient()
        run = await self._run(db, client)
        assert all(k["ttl"] == 0 for k in client.fetch_kwargs)
        documents = _docs_for_run(db, run)
        titles = [d.title for d in documents]
        assert "Implementation of Section 51A of UAPA, 1967: Updates" in titles
        assert not any("Treasury" in t for t in titles)
        document = next(d for d in documents if "UAPA" in d.title)
        assert document.publication_date == date(2026, 10, 30)
        assert document.circular_number == "RBI/2026-27/303"
        assert "KYC_AML" in document.banking_domains
        assert "TRANSACTIONS" not in document.banking_domains

    @pytest.mark.asyncio
    async def test_custom_queries_skip_the_feeds(self, db):
        client = _StubClient()
        await self._run(db, client, queries=[{"query": "x"}])
        assert client.fetch_kwargs == []

    @pytest.mark.asyncio
    async def test_broken_feed_is_reported_not_silently_empty(self, db):
        client = _StubClient(feed="<html>maintenance</html>")
        run = await self._run(db, client)
        assert "no items could be parsed" in (run.error_message or "")

    @pytest.mark.asyncio
    async def test_host_variants_are_one_document(self, db):
        client = _StubClient(
            results=[
                _hit("https://www.rbi.org.in/Scripts/FAQs.aspx?Id=9001"),
                _hit("https://m.rbi.org.in/scripts/faqs.aspx?id=9001&Mode=0"),
            ]
        )
        run = await self._run(db, client, queries=[{"query": "x"}])
        assert run.documents_new == 1
        assert run.documents_duplicate == 1

    @pytest.mark.asyncio
    async def test_rediscovery_fills_gaps_without_overwriting(self, db):
        url = "https://www.rbi.org.in/Scripts/NotificationUser.aspx?Id=9002"
        generic = "Notifications| Official Website of Reserve Bank of India"
        first = _StubClient(results=[_hit(url, title=generic)])
        run = await self._run(db, first, queries=[{"query": "x"}])
        document = _docs_for_run(db, run)[0]
        assert document.publication_date is None

        second = _StubClient(results=[_hit(url, title="Real Title", date_="Sep 18, 2026")])
        await self._run(db, second, queries=[{"query": "x"}])
        assert document.title == "Real Title"
        assert document.publication_date == date(2026, 9, 18)

        third = _StubClient(results=[_hit(url, title="Other Title", date_="Jan 01, 2020")])
        await self._run(db, third, queries=[{"query": "x"}])
        assert document.title == "Real Title"
        assert document.publication_date == date(2026, 9, 18)


    @pytest.mark.asyncio
    async def test_generic_titles_resolved_from_the_page(self, db):
        url = "https://m.rbi.org.in/Scripts/NotificationUser.aspx?Id=9003&Mode=0"
        client = _StubClient(
            results=[_hit(url, title="Notifications| Official Website of Reserve Bank of India")]
        )
        client.pages[fetchable_url(url)] = (
            "RBI/2026-27/9003  DOR.AML.REC.1/14.01.001/2026-27  June 1, 2026  "
            "Reserve Bank of India (Test) Directions, 2026  Introduction  Text"
        )
        run = await self._run(db, client, queries=[{"query": "x"}])
        document = _docs_for_run(db, run)[0]
        assert document.title == "Reserve Bank of India (Test) Directions, 2026"
        assert document.circular_number == "RBI/2026-27/9003"
        assert document.publication_date == date(2026, 6, 1)

    @pytest.mark.asyncio
    async def test_home_page_response_is_not_taken_for_the_document(self, db):
        url = "https://www.rbi.org.in/Scripts/NotificationUser.aspx?Id=9004"
        generic = "Notifications| Official Website of Reserve Bank of India"
        client = _StubClient(results=[_hit(url, title=generic)])
        client.pages[url] = "# Home-Reserve Bank of India\n\n## PREAMBLE\n\nto regulate"
        run = await self._run(db, client, queries=[{"query": "x"}])
        assert _docs_for_run(db, run)[0].title == generic


class TestExtractionSource:
    async def test_mobile_urls_are_fetched_from_the_main_host(self, db):
        client = _StubClient(feed="Clause text of the instrument.")
        document = RegulatoryDocument(
            title="T",
            document_type="NOTIFICATION",
            source_url="https://m.rbi.org.in/Scripts/NotificationUser.aspx?Id=9005&Mode=0",
            version=1,
            is_current=True,
            status="DISCOVERED",
            provenance="LIVE_TINYFISH",
        )
        db.add(document)
        db.flush()
        await ExtractionAgent(client=client).extract_document(
            db, AgentContext(actor="test"), document.id
        )
        assert client.fetch_kwargs[0]["urls"] == [
            "https://www.rbi.org.in/Scripts/NotificationUser.aspx?Id=9005&Mode=0"
        ]

    async def test_home_page_is_recorded_as_failure_not_stored(self, db):
        client = _StubClient(feed="# Home-Reserve Bank of India\n\n## PREAMBLE\n\nto regulate")
        document = RegulatoryDocument(
            title="T",
            document_type="NOTIFICATION",
            source_url="https://www.rbi.org.in/Scripts/NotificationUser.aspx?Id=9006",
            version=1,
            is_current=True,
            status="DISCOVERED",
            provenance="LIVE_TINYFISH",
        )
        db.add(document)
        db.flush()
        result = await ExtractionAgent(client=client).extract_document(
            db, AgentContext(actor="test"), document.id
        )
        assert document.status == "FETCH_FAILED"
        assert document.raw_text is None
        assert "redirected_to_home_page" in result.error


def _extracted_document(status: str = "ANALYSED", **overrides) -> RegulatoryDocument:
    return RegulatoryDocument(
        **{
            "title": "T",
            "document_type": "NOTIFICATION",
            "source_url": f"https://www.rbi.org.in/Scripts/NotificationUser.aspx?Id={uuid.uuid4()}",
            "version": 1,
            "is_current": True,
            "status": status,
            "provenance": "LIVE_TINYFISH",
            "raw_text": "Original text.",
            "content_hash": content_hash("Original text."),
            **overrides,
        }
    )


class _FailingClient(_StubClient):
    async def fetch(self, urls, **kwargs):
        raise RuntimeError("network down")


class TestRecheck:
    async def _recheck(self, db, client) -> list:
        return await ExtractionAgent(client=client).recheck_current(
            db, AgentContext(actor="test")
        )

    async def test_in_place_revision_becomes_a_new_version(self, db):
        document = _extracted_document()
        db.add(document)
        db.flush()
        results = await self._recheck(db, _StubClient(feed="Revised text."))
        revised = next(r for r in results if r.created_version)
        assert revised.document.version == 2
        assert revised.document.supersedes_id == document.id
        assert revised.document.status == "EXTRACTED"
        assert document.is_current is False
        # The retired row keeps its status rather than staying on FETCHING.
        assert document.status == "ANALYSED"

    async def test_unchanged_text_keeps_analysed_status(self, db):
        document = _extracted_document()
        db.add(document)
        db.flush()
        await self._recheck(db, _StubClient(feed="Original text."))
        assert document.status == "ANALYSED"
        assert document.version == 1

    async def test_fetches_are_never_served_from_cache(self, db):
        db.add(_extracted_document())
        db.flush()
        client = _StubClient(feed="Original text.")
        await self._recheck(db, client)
        assert client.fetch_kwargs
        assert all(k["ttl"] == 0 for k in client.fetch_kwargs)

    async def test_demo_fixtures_are_left_alone(self, db):
        document = _extracted_document(provenance="DEMO_FIXTURE")
        db.add(document)
        db.flush()
        results = await self._recheck(db, _StubClient(feed="Revised text."))
        assert document.id not in {r.document.id for r in results}

    async def test_network_failure_does_not_strand_the_document(self, db):
        document = _extracted_document()
        db.add(document)
        db.flush()
        await self._recheck(db, _FailingClient())
        assert document.status == "ANALYSED"
        assert document.raw_text == "Original text."

    async def test_failed_recheck_keeps_stored_text_valid(self, db):
        document = _extracted_document()
        db.add(document)
        db.flush()
        home_page = "# Home-Reserve Bank of India\n\n## PREAMBLE"
        results = await self._recheck(db, _StubClient(feed=home_page))
        assert any("redirected_to_home_page" in (r.error or "") for r in results)
        assert document.status == "ANALYSED"
        assert document.raw_text == "Original text."


class TestRecovery:
    def test_interrupted_work_is_closed(self, db):
        run = DiscoveryRun(
            status="RUNNING",
            regulatory_authority="RBI",
            institution_type="COMMERCIAL_BANK",
            provenance="LIVE_TINYFISH",
        )
        with_text = _extracted_document(status="FETCHING")
        without_text = _extracted_document(status="FETCHING", raw_text=None, content_hash=None)
        db.add_all([run, with_text, without_text])
        db.flush()

        closed = reconcile_interrupted_work(db)

        assert closed["discovery_runs"] >= 1
        assert run.status == "FAILED"
        assert "restart" in run.error_message
        assert with_text.status == "EXTRACTED"
        assert without_text.status == "DISCOVERED"


class TestSourceChecks:
    @pytest.mark.parametrize(
        ("url", "official"),
        [
            ("https://www.rbi.org.in/Scripts/X.aspx", True),
            ("https://rbidocs.rbi.org.in/rdocs/a.pdf", True),
            ("https://rbi.org.in/", True),
            ("https://evil.example/?u=//rbi.org.in", False),
            ("https://rbi.org.in.evil.example/a", False),
            ("https://notrbi.org.in/a", False),
        ],
    )
    def test_only_the_host_counts(self, url, official):
        assert _is_official(url, ["rbi.org.in"]) is official

    def test_drafts_are_typed_as_drafts(self):
        title = "Draft Reserve Bank of India (Know Your Customer) Amendment Directions, 2026"
        assert _detect_document_type(title, "x") == "DRAFT"

    def test_press_release_label_quotes_the_opening(self):
        title = _press_release_title("https://rbi.org.in/BS_PressReleaseDisplay.aspx", "A " * 200)
        assert title.startswith("Press release: A A")
        assert title.endswith("...")
        assert _press_release_title("https://rbi.org.in/NotificationUser.aspx", "Text") is None


class TestDomainFilter:
    def test_total_and_pages_describe_the_filtered_rows(self, client, live_db):
        marker = uuid.uuid4().hex
        live_db.add_all(
            [
                _extracted_document(title=f"{marker} kyc", banking_domains=["KYC_AML"]),
                _extracted_document(title=f"{marker} other", banking_domains=["TRANSACTIONS"]),
            ]
        )
        live_db.commit()
        body = client.get(
            "/api/regulations", params={"q": marker, "banking_domain": "KYC_AML"}
        ).json()
        assert body["total"] == 1
        assert [item["title"] for item in body["items"]] == [f"{marker} kyc"]


class TestTimestamps:
    def _reloaded(self, db, started: datetime) -> DiscoveryRun:
        run = DiscoveryRun(
            status="COMPLETED",
            regulatory_authority="RBI",
            institution_type="COMMERCIAL_BANK",
            provenance="LIVE_TINYFISH",
            started_at=started,
        )
        db.add(run)
        db.flush()
        db.expire(run)
        return db.get(DiscoveryRun, run.id)

    def test_loaded_values_are_utc_aware(self, db):
        loaded = self._reloaded(db, datetime(2026, 10, 31, 9, 11, tzinfo=UTC))
        assert loaded.started_at == datetime(2026, 10, 31, 9, 11, tzinfo=UTC)
        assert loaded.started_at.utcoffset() == timedelta(0)
        assert loaded.created_at.utcoffset() == timedelta(0)

    def test_other_offsets_are_converted_not_truncated(self, db):
        ist = timezone(timedelta(hours=5, minutes=30))
        loaded = self._reloaded(db, datetime(2026, 10, 31, 14, 41, tzinfo=ist))
        assert loaded.started_at == datetime(2026, 10, 31, 9, 11, tzinfo=UTC)

    def test_api_output_carries_an_offset(self, db):
        """Without one, browsers parse the value as local time."""
        loaded = self._reloaded(db, datetime(2026, 10, 31, 9, 11, tzinfo=UTC))
        payload = DiscoveryRunOut.model_validate(loaded).model_dump(mode="json")
        assert payload["started_at"] == "2026-10-31T09:11:00Z"


class TestClauseSegmentation:
    WEB_PAGE = (
        "RBI/DOR/2025-26/169  DOR.AML.REC.No.88/14.01.002/2025-26  November 28, 2025  "
        "1. These Directions shall be called the Know Your Customer Directions.  "
        "2. These Directions shall come into effect on the day they are published.  "
        "B. Applicability  3. These directions shall be applicable to Commercial Banks only.  "
        "4. Customers shall be identified before any account based relationship begins."
    )

    def test_single_line_web_pages_are_split_into_clauses(self):
        references = [c.clause_reference for c in parse_clauses(
            restore_paragraph_breaks(self.WEB_PAGE)
        )]
        assert {"Para 1", "Para 2", "Para 3", "Para 4"} <= set(references)

    def test_restoring_breaks_keeps_character_offsets(self):
        assert len(restore_paragraph_breaks(self.WEB_PAGE)) == len(self.WEB_PAGE)

    def test_text_with_line_structure_is_untouched(self):
        text = "1. First clause  with spacing\n2. Second clause  with spacing\n"
        assert restore_paragraph_breaks(text) == text

    def test_long_documents_are_not_cut_off(self):
        text = "\n".join(
            f"{n}. This numbered clause carries enough words to count." for n in range(1, 901)
        )
        assert len(parse_clauses(text)) == 900

    async def test_extraction_segments_web_pages_and_reads_applicability(self, db):
        document = _extracted_document(status="DISCOVERED", raw_text=None, content_hash=None)
        db.add(document)
        db.flush()
        await ExtractionAgent(client=_StubClient(feed=self.WEB_PAGE)).extract_document(
            db, AgentContext(actor="test"), document.id
        )
        assert len(document.clauses) >= 4
        assert "Commercial Banks" in (document.applicability_text or "")
        # What the regulator published is stored exactly as fetched.
        assert document.raw_text == self.WEB_PAGE
