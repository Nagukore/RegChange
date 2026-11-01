"""Parse regulator RSS feeds as returned by TinyFish Fetch.

Fetch runs the feed through an HTML parser, so the XML does not arrive intact:
`<link>` loses its tags (the URL becomes bare text after `</description>`),
`<pubDate>` is lower-cased and the title's CDATA is entity-escaped. Regexes that
tolerate both this and well-formed RSS are used instead of an XML parser, which
would reject the former.
"""
from __future__ import annotations

import html
import re
from dataclasses import dataclass
from datetime import date, datetime

_ITEM_RE = re.compile(r"<item\b[^>]*>(.*?)</item>", re.IGNORECASE | re.DOTALL)
_TITLE_RE = re.compile(r"<title\b[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_DESCRIPTION_RE = re.compile(r"<description\b[^>]*>(.*?)</description>", re.IGNORECASE | re.DOTALL)
_PUBDATE_RE = re.compile(r"<pubdate\b[^>]*>(.*?)</pubdate>", re.IGNORECASE | re.DOTALL)
_LINK_RE = re.compile(r"<link\b[^>]*>(.*?)</link>", re.IGNORECASE | re.DOTALL)
_BARE_URL_RE = re.compile(r"https?://[^\s<]+", re.IGNORECASE)
_CDATA_RE = re.compile(r"<!\[CDATA\[(.*?)\]\]>", re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")
_SPACE_RE = re.compile(r"\s+")

# RBI omits the timezone from pubDate; some feeds use a standard RFC 822 suffix.
_PUBDATE_FORMATS = ("%a, %d %b %Y %H:%M:%S", "%a, %d %b %Y")


@dataclass
class FeedItem:
    title: str
    url: str
    published: date | None
    text: str


def _unwrap(raw: str) -> str:
    """Unescape, remove the CDATA wrapper, and unescape what it contained."""
    value = html.unescape(raw.strip())
    match = _CDATA_RE.search(value)
    return html.unescape(match.group(1)) if match else value


def _plain_text(markup: str) -> str:
    return _SPACE_RE.sub(" ", html.unescape(_TAG_RE.sub(" ", markup))).strip()


def _parse_pubdate(raw: str) -> date | None:
    # Returning None rather than guessing, as with search result dates.
    value = raw.strip()
    for fmt, length in zip(_PUBDATE_FORMATS, (25, 16), strict=True):
        try:
            return datetime.strptime(value[:length].strip(), fmt).date()
        except ValueError:
            continue
    return None


def _item_url(body: str) -> str | None:
    link = _LINK_RE.search(body)
    if link:
        return html.unescape(link.group(1)).strip() or None
    # Mangled form: the URL is whatever bare text follows the description. URLs
    # inside the description are ignored, as they point at other instruments.
    tail = body.rsplit("</description>", 1)[-1] if "</description>" in body.lower() else body
    bare = _BARE_URL_RE.search(tail)
    return html.unescape(bare.group(0)) if bare else None


def parse_feed(text: str) -> list[FeedItem]:
    """Extract items from feed text. Items with no title or link are skipped."""
    items: list[FeedItem] = []
    for body in _ITEM_RE.findall(text or ""):
        title_match = _TITLE_RE.search(body)
        title = _plain_text(_unwrap(title_match.group(1))) if title_match else ""
        url = _item_url(body)
        if not title or not url:
            continue
        description = _DESCRIPTION_RE.search(body)
        pubdate = _PUBDATE_RE.search(body)
        items.append(
            FeedItem(
                title=title,
                url=url,
                published=_parse_pubdate(pubdate.group(1)) if pubdate else None,
                text=_plain_text(_unwrap(description.group(1))) if description else "",
            )
        )
    return items


__all__ = ["FeedItem", "parse_feed"]
