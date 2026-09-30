"""Track 9: RSS/Atom feed discovery -- synthetic fixtures modeled on real
feed structure, since this environment has no live network access to
Investors.com (see the Track 9 task report for the exact observed
result). `parse_feed` must work correctly against ANY well-formed
RSS/Atom feed, not something Investors.com-specific."""
from __future__ import annotations

from app.sources.website import WebsiteSourceError, parse_feed

_RSS_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Example Trading Desk</title>
    <link>https://example.com/</link>
    <item>
      <title>We Are Buying XYZ Today</title>
      <link>https://example.com/articles/we-are-buying-xyz-today</link>
      <guid>https://example.com/articles/we-are-buying-xyz-today</guid>
      <pubDate>Mon, 01 Sep 2026 14:30:00 GMT</pubDate>
    </item>
    <item>
      <title>Market Recap</title>
      <link>https://example.com/articles/market-recap</link>
      <guid>urn:example:market-recap-1</guid>
      <pubDate>Sun, 31 Aug 2026 09:00:00 GMT</pubDate>
    </item>
  </channel>
</rss>
"""

_ATOM_FEED = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Example Trading Desk (Atom)</title>
  <link href="https://example.com/"/>
  <entry>
    <title>Conditional Setup on ABC</title>
    <link href="https://example.com/articles/conditional-setup-on-abc"/>
    <id>https://example.com/articles/conditional-setup-on-abc</id>
    <updated>2026-09-01T14:30:00Z</updated>
  </entry>
</feed>
"""


def test_parse_rss_feed_extracts_entries_with_id_link_and_timestamp():
    articles = parse_feed(_RSS_FEED, feed_url="https://example.com/feed/")
    assert len(articles) == 2
    first = articles[0]
    assert first.url == "https://example.com/articles/we-are-buying-xyz-today"
    assert first.entry_id == "https://example.com/articles/we-are-buying-xyz-today"
    assert first.published_at is not None
    assert first.published_at.year == 2026 and first.published_at.month == 9


def test_parse_atom_feed_extracts_entries():
    articles = parse_feed(_ATOM_FEED, feed_url="https://example.com/atom.xml")
    assert len(articles) == 1
    assert articles[0].url == "https://example.com/articles/conditional-setup-on-abc"
    assert articles[0].entry_id == "https://example.com/articles/conditional-setup-on-abc"


def test_parse_feed_with_zero_entries_is_not_an_error():
    empty = """<?xml version="1.0"?><rss version="2.0"><channel><title>Empty</title></channel></rss>"""
    articles = parse_feed(empty, feed_url="https://example.com/feed/")
    assert articles == []


def test_parse_feed_rejects_unrecognizable_content():
    garbage = "this is not xml or a feed at all, just plain text"
    import pytest

    with pytest.raises(WebsiteSourceError):
        parse_feed(garbage, feed_url="https://example.com/feed/")
