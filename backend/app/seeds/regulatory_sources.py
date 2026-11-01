"""Official regulatory sources and the search queries used to discover them.

Every URL here was verified against rbi.org.in on 2026-10-31. These are entry
points for TinyFish Search/Fetch -- the pipeline still fetches live content; we
do not ship cached regulatory text as if it were freshly retrieved.

Applicability note: the RBI KYC framework is entity-type specific. The 2025
Directions at RBI/DOR/2025-26/169 are titled "Commercial Banks", so they are
recorded as applying to COMMERCIAL_BANK only. Other regulated entities
(co-operative banks, NBFCs, payments banks) are governed by their own
directions, and this project does not assume otherwise. See
docs/REGULATORY_SCOPE.md.
"""
from __future__ import annotations

from typing import TypedDict


class SourceSeed(TypedDict):
    authority: str
    name: str
    base_url: str
    description: str
    applicable_institution_types: list[str]


REGULATORY_SOURCES: list[SourceSeed] = [
    {
        "authority": "RBI",
        "name": "RBI Master Directions index",
        "base_url": "https://www.rbi.org.in/Scripts/BS_ViewMasterDirections.aspx",
        "description": (
            "Index of all RBI Master Directions. The consolidated, currently "
            "applicable instrument for each subject area is published here."
        ),
        "applicable_institution_types": [],
    },
    {
        "authority": "RBI",
        "name": "RBI Notifications",
        "base_url": "https://www.rbi.org.in/Scripts/NotificationUser.aspx",
        "description": "Circulars and notifications as issued, newest first.",
        "applicable_institution_types": [],
    },
    {
        "authority": "RBI",
        "name": "RBI Press Releases",
        "base_url": "https://www.rbi.org.in/Scripts/BS_PressReleaseDisplay.aspx",
        "description": "Press releases, often the first signal of a forthcoming direction.",
        "applicable_institution_types": [],
    },
]

# Seed documents used as discovery anchors and as the old/new pair for version
# comparison. `is_superseded` is recorded from the regulator's own statement,
# not inferred.
ANCHOR_DOCUMENTS: list[dict] = [
    {
        "title": "Reserve Bank of India (Commercial Banks - Know Your Customer) Directions, 2025",
        "circular_number": "RBI/DOR/2025-26/169",
        "document_type": "MASTER_DIRECTION",
        "source_url": "https://rbidocs.rbi.org.in/rdocs/notification/PDFs/169MD.PDF",
        "applicable_institution_types": ["COMMERCIAL_BANK"],
        "banking_domains": ["KYC_AML", "CUSTOMER_ONBOARDING", "ACCOUNT_MANAGEMENT"],
        "note": (
            "Issued 28 November 2025. Replaces the 2016 KYC Master Direction for "
            "commercial banks. Amended by the Know Your Customer Amendment "
            "Directions, 2025 dated 29 December 2025."
        ),
        "is_superseded": False,
    },
    {
        "title": "Master Direction - Know Your Customer (KYC) Direction, 2016",
        "circular_number": "RBI/DBR/2015-16/18",
        "document_type": "MASTER_DIRECTION",
        "source_url": "https://www.rbi.org.in/Scripts/BS_ViewMasDirections.aspx?id=11566",
        "applicable_institution_types": [
            "COMMERCIAL_BANK",
            "CO_OPERATIVE_BANK",
            "NBFC",
            "PAYMENT_SYSTEM_PROVIDER",
        ],
        "banking_domains": ["KYC_AML", "CUSTOMER_ONBOARDING", "ACCOUNT_MANAGEMENT"],
        "note": (
            "The long-standing consolidated KYC framework. Superseded for "
            "commercial banks by RBI/DOR/2025-26/169; retained here as the prior "
            "version for old-versus-new clause comparison."
        ),
        "is_superseded": True,
    },
]

# Queries issued to TinyFish Search, scoped to official domains via
# `include_domains`. Each is tagged with the banking domain it targets so
# discovered documents can be categorised without guessing.
DISCOVERY_QUERIES: list[dict] = [
    # The two queries that track change. Drafts run first: a draft and its later
    # final text read alike in snippets, and the first title seen is kept.
    {
        "query": "draft Know Your Customer amendment public comments",
        "banking_domains": ["KYC_AML"],
        "purpose": "Find draft RBI KYC amendments issued for public comment.",
    },
    {
        "query": "Know Your Customer Amendment Directions",
        "banking_domains": ["KYC_AML"],
        "purpose": "Find the latest amendments to the RBI KYC directions.",
    },
    {
        "query": "Know Your Customer Directions commercial banks",
        "banking_domains": ["KYC_AML", "CUSTOMER_ONBOARDING"],
        "purpose": (
            "Find the currently applicable RBI KYC directions for commercial banks, "
            "including the consolidated master direction and any amendments."
        ),
    },
    {
        "query": "periodic updation of KYC existing customers",
        "banking_domains": ["KYC_AML"],
        "purpose": "Find RBI instructions on periodic KYC updation and re-KYC timelines.",
    },
    {
        "query": "Customer Due Diligence officially valid document requirements",
        "banking_domains": ["KYC_AML", "CUSTOMER_ONBOARDING"],
        "purpose": "Find RBI requirements on CDD and acceptable officially valid documents.",
    },
    {
        "query": "customer risk categorisation enhanced due diligence politically exposed persons",
        "banking_domains": ["KYC_AML"],
        "purpose": "Find RBI requirements on risk categorisation and enhanced due diligence.",
    },
    {
        "query": "unique customer identification code UCIC banks",
        "banking_domains": ["CUSTOMER_ONBOARDING"],
        "purpose": "Find RBI instructions on allotment of a unique customer identification code.",
    },
    {
        "query": "small account basic savings bank deposit account limits",
        "banking_domains": ["ACCOUNT_MANAGEMENT", "TRANSACTIONS"],
        "purpose": "Find RBI limits applicable to small accounts and BSBDA.",
    },
    {
        "query": "Central KYC Records Registry CKYCR upload download requirements",
        "banking_domains": ["KYC_AML"],
        "purpose": "Find RBI requirements on uploading and relying upon CKYCR records.",
    },
    {
        "query": "video based customer identification process V-CIP",
        "banking_domains": ["KYC_AML", "CUSTOMER_ONBOARDING"],
        "purpose": "Find RBI requirements governing video-based customer identification.",
    },
]

# The regulator's own newest-first RSS feeds (latest 10 items each). They catch
# what was issued this week, before a search index has picked it up.
DISCOVERY_FEEDS: list[str] = [
    "https://www.rbi.org.in/notifications_rss.xml",
    "https://www.rbi.org.in/pressreleases_rss.xml",
]

# Entity types the institution_type setting accepts. Used by the Settings screen
# and to flag documents whose applicability does not cover the configured bank.
INSTITUTION_TYPES: list[str] = [
    "COMMERCIAL_BANK",
    "SMALL_FINANCE_BANK",
    "PAYMENTS_BANK",
    "CO_OPERATIVE_BANK",
    "REGIONAL_RURAL_BANK",
    "NBFC",
    "PAYMENT_SYSTEM_PROVIDER",
]

__all__ = [
    "REGULATORY_SOURCES",
    "ANCHOR_DOCUMENTS",
    "DISCOVERY_QUERIES",
    "DISCOVERY_FEEDS",
    "INSTITUTION_TYPES",
    "SourceSeed",
]
