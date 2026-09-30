"""Track 9: WebsiteSource end-to-end (feed discovery -> fetch/extract ->
classify -> candidate/signal handoff), driven entirely by injected fakes
-- no real network. Confirms: a resolved actionable article reaches
on_signal; an unresolved/multi-leg-unresolved or paywalled/uncertain
article never does; every poll outcome reports a health state."""
from __future__ import annotations

import pytest

from app.sources.article_extraction import ExtractedArticle, ExtractionStatus
from app.sources.website import WebsiteSource, WebsiteSourceError
from app.website_collectors import CollectorHealth, SiteFormat

_RSS_ONE_NEW_ARTICLE = """<?xml version="1.0"?>
<rss version="2.0"><channel><title>Feed</title>
<item>
  <title>We Are Buying XYZ Today</title>
  <link>https://example.com/articles/we-are-buying-xyz</link>
  <guid>https://example.com/articles/we-are-buying-xyz</guid>
</item>
</channel></rss>
"""

_ACTIONABLE_TEXT = (
    "We are buying XYZ today right here at the pivot after it cleared a clean base "
    "on strong volume. This is a real trade we are putting on now, with a stop at $48.00 "
    "and a target of $62.00." * 1
)


class _Recorder:
    def __init__(self):
        self.signals = []
        self.candidates = []
        self.health = []

    async def on_signal(self, signal):
        self.signals.append(signal)

    async def on_candidate(self, candidate):
        self.candidates.append(candidate)

    async def on_health(self, state, detail):
        self.health.append((state, detail))


def _make_source(*, fetch_feed_or_list, fetch_article_fn, recorder: _Recorder, **kwargs) -> WebsiteSource:
    return WebsiteSource(
        recorder.on_signal,
        site_id="investors_com_swingtrader",
        site_format=SiteFormat.FEED,
        feed_url="https://example.com/feed/",
        fetch_feed_or_list=fetch_feed_or_list,
        fetch_article_fn=fetch_article_fn,
        on_candidate=recorder.on_candidate,
        on_health=recorder.on_health,
        **kwargs,
    )


@pytest.mark.asyncio
async def test_actionable_article_reaches_on_signal_and_reports_healthy():
    recorder = _Recorder()

    async def fake_fetch_feed(url):
        return _RSS_ONE_NEW_ARTICLE

    async def fake_fetch_article(url, *, auth_state_path, allow_playwright_fallback):
        return ExtractedArticle(
            url=url,
            status=ExtractionStatus.OK,
            title="We Are Buying XYZ Today",
            text=_ACTIONABLE_TEXT,
            fetched_via="http",
        )

    source = _make_source(fetch_feed_or_list=fake_fetch_feed, fetch_article_fn=fake_fetch_article, recorder=recorder)
    candidates = await source.poll_once()

    assert len(candidates) == 1
    assert candidates[0].resolved is True
    assert len(recorder.signals) == 1
    assert recorder.signals[0].symbol  # a real symbol was extracted
    assert recorder.signals[0].channel_id == "investors_com_swingtrader"
    assert recorder.signals[0].message_id == "https://example.com/articles/we-are-buying-xyz"
    assert (CollectorHealth.HEALTHY_QUALIFIED.value, None) in recorder.health


@pytest.mark.asyncio
async def test_paywalled_article_never_reaches_on_signal_and_reports_access_denied():
    recorder = _Recorder()

    async def fake_fetch_feed(url):
        return _RSS_ONE_NEW_ARTICLE

    async def fake_fetch_article(url, *, auth_state_path, allow_playwright_fallback):
        return ExtractedArticle(url=url, status=ExtractionStatus.PAYWALLED, detail="paywall detected")

    source = _make_source(fetch_feed_or_list=fake_fetch_feed, fetch_article_fn=fake_fetch_article, recorder=recorder)
    candidates = await source.poll_once()

    assert candidates == []
    assert recorder.signals == []
    assert (CollectorHealth.ACCESS_DENIED_OR_PAYWALLED.value, "paywall detected") in recorder.health


@pytest.mark.asyncio
async def test_ambiguous_article_reports_classifier_uncertain_and_never_routes():
    recorder = _Recorder()

    async def fake_fetch_feed(url):
        return _RSS_ONE_NEW_ARTICLE

    ambiguous_text = (
        "We are buying XYZ today, much like we bought ABC last week which had a similar setup. "
        "In the past, this pattern has been a reliable one for the desk."
    )

    async def fake_fetch_article(url, *, auth_state_path, allow_playwright_fallback):
        return ExtractedArticle(url=url, status=ExtractionStatus.OK, text=ambiguous_text, fetched_via="http")

    source = _make_source(fetch_feed_or_list=fake_fetch_feed, fetch_article_fn=fake_fetch_article, recorder=recorder)
    candidates = await source.poll_once()

    assert candidates == []
    assert recorder.signals == []
    assert any(state == CollectorHealth.CLASSIFIER_UNCERTAIN.value for state, _ in recorder.health)


@pytest.mark.asyncio
async def test_second_poll_never_reprocesses_an_already_seen_article():
    recorder = _Recorder()
    call_count = {"n": 0}

    async def fake_fetch_feed(url):
        return _RSS_ONE_NEW_ARTICLE

    async def fake_fetch_article(url, *, auth_state_path, allow_playwright_fallback):
        call_count["n"] += 1
        return ExtractedArticle(url=url, status=ExtractionStatus.OK, text=_ACTIONABLE_TEXT, fetched_via="http")

    source = _make_source(fetch_feed_or_list=fake_fetch_feed, fetch_article_fn=fake_fetch_article, recorder=recorder)
    first = await source.poll_once()
    second = await source.poll_once()

    assert len(first) == 1
    assert second == []
    assert call_count["n"] == 1
    assert (CollectorHealth.NO_NEW_ARTICLES_OBSERVED.value, None) in recorder.health


@pytest.mark.asyncio
async def test_unresolved_multi_leg_candidate_reaches_on_candidate_but_never_on_signal():
    recorder = _Recorder()
    spread_text = (
        "We are buying a bull call spread on XYZ today: buying the $50 call and selling the $55 call, "
        "for a net debit of $2.10 per spread."
    )

    async def fake_fetch_feed(url):
        return _RSS_ONE_NEW_ARTICLE

    async def fake_fetch_article(url, *, auth_state_path, allow_playwright_fallback):
        return ExtractedArticle(url=url, status=ExtractionStatus.OK, text=spread_text, fetched_via="http")

    source = _make_source(fetch_feed_or_list=fake_fetch_feed, fetch_article_fn=fake_fetch_article, recorder=recorder)
    candidates = await source.poll_once()
    # let the fire-and-forget on_candidate task run
    if source._in_flight_tasks:
        import asyncio

        await asyncio.gather(*source._in_flight_tasks)

    assert len(candidates) == 1
    assert candidates[0].resolved is False
    assert len(recorder.candidates) == 1
    assert recorder.signals == []


@pytest.mark.asyncio
async def test_unrecognizable_feed_content_reports_unsupported_format():
    recorder = _Recorder()

    async def fake_fetch_feed(url):
        return "not a feed at all"

    async def fake_fetch_article(url, *, auth_state_path, allow_playwright_fallback):  # pragma: no cover - unreached
        raise AssertionError("should not fetch an article when feed parsing itself fails")

    source = _make_source(fetch_feed_or_list=fake_fetch_feed, fetch_article_fn=fake_fetch_article, recorder=recorder)
    with pytest.raises(WebsiteSourceError):
        await source.poll_once()

    assert any(state == CollectorHealth.UNSUPPORTED_FORMAT_ENCOUNTERED.value for state, _ in recorder.health)


def test_website_source_requires_feed_url_for_feed_format():
    recorder = _Recorder()
    with pytest.raises(WebsiteSourceError):
        WebsiteSource(
            recorder.on_signal,
            site_id="x",
            site_format=SiteFormat.FEED,
            feed_url=None,
        )


def test_website_source_requires_article_list_url_for_article_list_format():
    recorder = _Recorder()
    with pytest.raises(WebsiteSourceError):
        WebsiteSource(
            recorder.on_signal,
            site_id="x",
            site_format=SiteFormat.ARTICLE_LIST,
            article_list_url=None,
        )
