"""Track 9: the ARTICLE_LIST fallback -- a simple 'seen URL set' diff
over a plain article-index HTML page, used only when no feed is
configured. Deliberately NOT a general-purpose visual-diff tool -- see
app/sources/website.py's module docstring."""
from __future__ import annotations

import pytest

from app.sources.website import WebsiteSourceError, parse_article_list

_LIST_HTML = """<!doctype html>
<html>
<body>
<nav><a href="/">Home</a> <a href="/about">About</a></nav>
<div class="article-list">
  <a href="/articles/we-are-buying-xyz-today">We Are Buying XYZ Today</a>
  <a href="/articles/market-recap-for-the-week">Market Recap For The Week</a>
  <a href="https://example.com/articles/conditional-setup-on-abc">Conditional Setup on ABC</a>
  <a href="/static/logo.png">logo</a>
  <a href="#top">Back to top</a>
  <a href="mailto:tips@example.com">Send tips</a>
</div>
</body>
</html>
"""


def test_parse_article_list_extracts_article_links_and_skips_nav_assets():
    articles = parse_article_list(_LIST_HTML, base_url="https://example.com/section/", list_url="https://example.com/section/")
    urls = {a.url for a in articles}
    assert "https://example.com/articles/we-are-buying-xyz-today" in urls
    assert "https://example.com/articles/market-recap-for-the-week" in urls
    assert "https://example.com/articles/conditional-setup-on-abc" in urls
    assert not any(u.endswith(".png") for u in urls)
    assert not any(u.startswith("mailto:") for u in urls)
    assert not any("#top" in u for u in urls)


def test_parse_article_list_rejects_empty_content():
    with pytest.raises(WebsiteSourceError):
        parse_article_list("", base_url="https://example.com/", list_url="https://example.com/section/")


def test_seen_url_set_diff_only_reports_new_links():
    first_pass = parse_article_list(_LIST_HTML, base_url="https://example.com/", list_url="https://example.com/section/")
    seen = {a.url for a in first_pass}

    updated_html = _LIST_HTML.replace(
        '<div class="article-list">',
        '<div class="article-list"><a href="/articles/brand-new-article-just-posted">Brand New Article</a>',
    )
    second_pass = parse_article_list(updated_html, base_url="https://example.com/", list_url="https://example.com/section/")
    new_only = [a for a in second_pass if a.url not in seen]

    assert len(new_only) == 1
    assert new_only[0].url == "https://example.com/articles/brand-new-article-just-posted"
