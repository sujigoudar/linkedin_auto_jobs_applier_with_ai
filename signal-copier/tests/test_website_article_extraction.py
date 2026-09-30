"""Track 9: article extraction (app/sources/article_extraction.py) --
fixture HTML modeled on real article-page structure, including a
synthetic paywall/login page, so the paywall/access-denied state is
tested explicitly and never silently folded into 'empty article'."""
from __future__ import annotations

import pytest

from app.sources.article_extraction import (
    ExtractionStatus,
    _build_result_from_html,
    _looks_like_paywall_or_login,
)

_REAL_ARTICLE_HTML = """<!doctype html>
<html>
<head>
<title>We Are Buying XYZ Today</title>
<meta property="article:modified_time" content="2026-09-02T10:00:00Z">
</head>
<body>
<article>
<h1>We Are Buying XYZ Today</h1>
<p class="byline">By Jane Analyst</p>
<p>""" + (
    "We are buying XYZ today after it cleared a clean base breakout on strong volume. "
    "This is a real trade we are putting on right now, not a hypothetical. "
) * 6 + """</p>
</article>
</body>
</html>
"""

_PAYWALL_HTML = """<!doctype html>
<html>
<head><title>Subscriber Content</title></head>
<body>
<article>
<h1>Exclusive: Our Latest Options Trade</h1>
<p>Here is a short teaser of the article before the fold.</p>
<div class="paywall-prompt">
  <p>Subscribe to continue reading this exclusive analysis.</p>
  <form>
    <input type="email" name="email" />
    <input type="password" name="password" />
    <button>Log In</button>
  </form>
</div>
</article>
</body>
</html>
"""

_TOO_SHORT_HTML = """<!doctype html>
<html>
<head><title>Quick Note</title></head>
<body><article><p>Short update, nothing more to say here today.</p></article></body>
</html>
"""


def test_looks_like_paywall_detects_marker_phrase_and_login_form():
    assert _looks_like_paywall_or_login(_PAYWALL_HTML) is True


def test_looks_like_paywall_false_for_real_article():
    assert _looks_like_paywall_or_login(_REAL_ARTICLE_HTML) is False


def test_extraction_of_real_article_returns_ok_with_title_author_and_timestamps():
    result = _build_result_from_html(_REAL_ARTICLE_HTML, url="https://example.com/a", fetched_via="http")
    assert result.status is ExtractionStatus.OK
    assert result.title and "Buying XYZ" in result.title
    assert result.text is not None and len(result.text) > 200
    assert result.modified_at is not None
    assert result.modified_at.year == 2026


def test_extraction_of_paywall_page_is_a_distinct_paywalled_status_not_empty_article():
    result = _build_result_from_html(_PAYWALL_HTML, url="https://example.com/paywalled", fetched_via="http")
    assert result.status is ExtractionStatus.PAYWALLED
    # Must never be silently treated as an empty/short article instead.
    assert result.status is not ExtractionStatus.EXTRACTION_TOO_SHORT


def test_extraction_too_short_when_no_paywall_signature_present():
    result = _build_result_from_html(_TOO_SHORT_HTML, url="https://example.com/short", fetched_via="http")
    assert result.status is ExtractionStatus.EXTRACTION_TOO_SHORT
    assert result.status is not ExtractionStatus.PAYWALLED


@pytest.mark.asyncio
async def test_fetch_article_reports_fetch_failed_when_http_and_playwright_both_fail(monkeypatch):
    from app.sources import article_extraction

    async def _fail_http(url, *, timeout_seconds):
        return None

    async def _fail_playwright(url, *, timeout_seconds, auth_state_path):
        return None

    monkeypatch.setattr(article_extraction, "_fetch_http", _fail_http)
    monkeypatch.setattr(article_extraction, "_fetch_playwright", _fail_playwright)

    result = await article_extraction.fetch_article("https://example.com/unreachable")
    assert result.status is ExtractionStatus.FETCH_FAILED


@pytest.mark.asyncio
async def test_fetch_article_falls_back_to_playwright_only_when_http_result_is_too_short(monkeypatch):
    from app.sources import article_extraction

    calls = {"playwright": 0}

    async def _short_http(url, *, timeout_seconds):
        return _TOO_SHORT_HTML

    async def _rich_playwright(url, *, timeout_seconds, auth_state_path):
        calls["playwright"] += 1
        return _REAL_ARTICLE_HTML

    monkeypatch.setattr(article_extraction, "_fetch_http", _short_http)
    monkeypatch.setattr(article_extraction, "_fetch_playwright", _rich_playwright)

    result = await article_extraction.fetch_article("https://example.com/js-rendered")
    assert result.status is ExtractionStatus.OK
    assert result.fetched_via == "playwright"
    assert calls["playwright"] == 1


@pytest.mark.asyncio
async def test_fetch_article_does_not_use_playwright_when_http_already_succeeds(monkeypatch):
    from app.sources import article_extraction

    calls = {"playwright": 0}

    async def _ok_http(url, *, timeout_seconds):
        return _REAL_ARTICLE_HTML

    async def _should_not_be_called(url, *, timeout_seconds, auth_state_path):
        calls["playwright"] += 1
        return _REAL_ARTICLE_HTML

    monkeypatch.setattr(article_extraction, "_fetch_http", _ok_http)
    monkeypatch.setattr(article_extraction, "_fetch_playwright", _should_not_be_called)

    result = await article_extraction.fetch_article("https://example.com/fine")
    assert result.status is ExtractionStatus.OK
    assert result.fetched_via == "http"
    assert calls["playwright"] == 0
