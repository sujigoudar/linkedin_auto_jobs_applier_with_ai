"""Track 9: website/article-based signal source (e.g. Investors.com --
IBD SwingTrader, Leaderboard, the options column), designed to extend to
other sites later.

Articles are prose, not short structured alerts -- this is a
fundamentally different kind of source from the chat/email adapters
(`app/sources/telegram.py`, `app/sources/slack.py`, ...). A discovered
article is never trusted directly: it goes through
`app/sources/article_extraction.py` (clean text + honest paywall/failure
detection) and then `app/sources/article_classifier.py` (a distinct
classification-before-extraction step) before anything resembling a
trade is built, and even a fully "resolved" trade candidate is still just
an ordinary `Signal` handed to `self.on_signal` -- routing, the qualified-
route gate (`app.engine.SignalCopierEngine._check_route_qualified`), and
every other safety check downstream are completely unchanged and untouched
by this adapter. Nothing here builds a second, parallel routing/allow
mechanism.

New-article discovery, two modes (per `app.website_collectors.SiteFormat`,
mirroring the registry's own vocabulary):

- FEED: polls a configured `feed_url` with `feedparser` -- an entry's
  `id` (falling back to its `link`) is its stable identifier, `link` is
  the canonical article URL, `published_parsed`/`updated_parsed` gives
  its timestamp. This is a real RSS/Atom parse against ANY well-formed
  feed -- nothing here is specific to Investors.com's actual feed shape
  (which this environment has no live network access to verify -- see
  this module's own test suite's fixture feed, and the Track 9 task
  report for the exact live-network result observed).
- ARTICLE_LIST: no usable feed configured. Polls a configured
  `article_list_url` (a plain article-index HTML page), extracts every
  `<a href=...>` link matching a simple "looks like an article path"
  heuristic, and diffs against a persisted "seen URL set" checkpoint.
  Deliberately NOT a general-purpose visual-diff/changedetection.io-style
  tool (no DOM-structure tracking, no arbitrary CSS-selector
  configuration) -- exactly the bounded scope the Track 9 brief asks for.

Every collector's `feed_url`/`article_list_url` is EXPLICITLY configured
per collector (see `app.website_collectors.WebsiteCollector`) -- this
adapter never guesses or scans arbitrary URLs on the operator's behalf.

Dedup / revision handling: a candidate is keyed by
`(channel_id, message_id) == (site_id, canonical_article_url)` -- the
same `SignalStore.find_signal_id_by_provider_identity`-style provider-
identity dedup Track 5 established for Telegram, just keyed on this
source's own identity fields (see `app/db.py`'s `find_website_candidate_
by_url`/`upsert_website_candidate`). A later revision of the SAME URL
(detected by a newer `modified_at` than what's already stored) updates
the EXISTING candidate row rather than creating a duplicate.
"""
from __future__ import annotations

import asyncio
import functools
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Optional

from app.sources.article_classifier import (
    ArticleClassification,
    build_trade_candidate,
    classify_article,
)
from app.sources.article_extraction import ExtractionStatus, fetch_article
from app.sources.base import SourceAdapter
from app.website_collectors import CollectorHealth, SiteFormat

logger = logging.getLogger(__name__)

#: A simple "does this href look like an article permalink, not a
#: category/tag/nav/asset link" heuristic for the ARTICLE_LIST fallback --
#: deliberately conservative (a few false negatives are fine; a false
#: positive means fetching a non-article page, which
#: `article_extraction.py` will then correctly report as
#: EXTRACTION_TOO_SHORT rather than misclassify).
_LIKELY_ARTICLE_PATH = re.compile(r"/[\w-]{8,}(?:/|\.html?)?$")
_SKIP_EXTENSIONS = re.compile(r"\.(?:jpg|jpeg|png|gif|svg|css|js|pdf|xml)$", re.IGNORECASE)


@dataclass
class DiscoveredArticle:
    """One new article this poll cycle found, from either discovery
    mode -- the adapter's own internal, mode-independent shape (feed
    entries and article-list links are normalized into this before
    anything downstream cares which mode produced it)."""

    url: str
    entry_id: Optional[str] = None
    published_at: Optional[datetime] = None


class WebsiteSourceError(RuntimeError):
    """Raised for a configuration/format failure this adapter can't
    recover from on its own (see `CollectorHealth.MISSING_CONFIGURATION`/
    `UNSUPPORTED_FORMAT_ENCOUNTERED`) -- never silently swallowed."""


def parse_feed(raw_feed_bytes_or_text: Any, *, feed_url: str) -> list[DiscoveredArticle]:
    """Parses RSS/Atom feed content with `feedparser` into
    `DiscoveredArticle` entries, newest-unspecified (caller sorts/diffs).
    Raises `WebsiteSourceError` when feedparser can't recognize the
    content as a feed at all (a real, distinct
    `UNSUPPORTED_FORMAT_ENCOUNTERED` case) -- a feed with zero entries is
    NOT an error (a genuinely quiet source is `NO_NEW_ARTICLES_OBSERVED`,
    handled by the caller, not here)."""
    import feedparser

    parsed = feedparser.parse(raw_feed_bytes_or_text)
    # feedparser is deliberately lenient (it will "parse" almost
    # anything into an empty feed rather than raising) -- `bozo` plus a
    # completely absent feed title/version is the honest signal that
    # this wasn't real feed content at all.
    if parsed.get("bozo") and not parsed.get("entries") and not parsed.get("feed", {}).get("title"):
        raise WebsiteSourceError(
            f"content at {feed_url!r} does not parse as a recognizable RSS/Atom feed: "
            f"{parsed.get('bozo_exception')!r}"
        )

    articles: list[DiscoveredArticle] = []
    for entry in parsed.get("entries", []):
        link = entry.get("link")
        if not link:
            continue
        entry_id = entry.get("id") or link
        published_at = None
        for field_name in ("published_parsed", "updated_parsed"):
            struct_time = entry.get(field_name)
            if struct_time:
                import time as _time

                published_at = datetime.fromtimestamp(_time.mktime(struct_time), tz=timezone.utc)
                break
        articles.append(DiscoveredArticle(url=link, entry_id=entry_id, published_at=published_at))
    return articles


def parse_article_list(html: str, *, base_url: str, list_url: str) -> list[DiscoveredArticle]:
    """Extracts candidate article links out of a plain article-index HTML
    page -- a simple, bounded "seen URL set" diff source, not a general
    page-structure scraper. Raises `WebsiteSourceError` only when the
    content isn't parseable HTML at all (e.g. empty); an HTML page with
    zero matching links is a legitimate (if unusual) empty result, left
    to the caller's `NO_NEW_ARTICLES_OBSERVED` handling."""
    if not html or not html.strip():
        raise WebsiteSourceError(f"content at {list_url!r} is empty -- not a usable article-list page")

    from urllib.parse import urljoin

    href_pattern = re.compile(r'<a\s[^>]*href=["\']([^"\']+)["\']', re.IGNORECASE)
    seen: set[str] = set()
    articles: list[DiscoveredArticle] = []
    for match in href_pattern.finditer(html):
        href = match.group(1)
        if href.startswith(("#", "mailto:", "javascript:")):
            continue
        absolute = urljoin(base_url, href)
        if _SKIP_EXTENSIONS.search(absolute):
            continue
        if not _LIKELY_ARTICLE_PATH.search(absolute):
            continue
        if absolute in seen:
            continue
        seen.add(absolute)
        articles.append(DiscoveredArticle(url=absolute))
    return articles


class WebsiteSource(SourceAdapter):
    """One collector's worth of polling -- one `WebsiteSource` instance
    per `app.website_collectors.WebsiteCollector` row (mirrors
    `app.sources.telegram_user.TelegramUserSource`'s one-instance-per-
    registry-row convention). The engine/`app/main.py` is responsible for
    constructing one of these per registered, enabled collector (same as
    Telegram's own collector-registry startup loop) -- this module itself
    does not read the registry.
    """

    name = "website"

    def __init__(
        self,
        on_signal,
        *,
        site_id: str,
        site_format: SiteFormat | str,
        feed_url: Optional[str] = None,
        article_list_url: Optional[str] = None,
        analyst: Optional[str] = None,
        poll_interval_seconds: float = 300.0,
        auth_state_path: Optional[str] = None,
        allow_playwright_fallback: bool = True,
        on_source_event=None,
        # Injection points for tests -- default to the real network/db
        # calls; a test replaces these with fakes instead of monkeypatching
        # module-level functions.
        fetch_feed_or_list: Optional[Callable[[str], Awaitable[str]]] = None,
        fetch_article_fn: Optional[Callable[..., Awaitable[Any]]] = None,
        on_candidate: Optional[Callable[[Any], Awaitable[Any]]] = None,
        on_health: Optional[Callable[[str, Optional[str]], Awaitable[Any]]] = None,
        checkpoint_seen_urls: Optional[set[str]] = None,
    ):
        super().__init__(on_signal, on_source_event=on_source_event)
        self.site_id = site_id
        self.site_format = SiteFormat(site_format) if isinstance(site_format, str) else site_format
        self.feed_url = feed_url
        self.article_list_url = article_list_url
        self.analyst = analyst
        self.poll_interval_seconds = poll_interval_seconds
        self.auth_state_path = auth_state_path
        self.allow_playwright_fallback = allow_playwright_fallback
        self._fetch_feed_or_list = fetch_feed_or_list or self._default_fetch_text
        self._fetch_article_fn = fetch_article_fn or fetch_article
        #: Called with EVERY built `TradeCandidate` (resolved or not) --
        #: the caller decides persistence/dedup/revision-update (see
        #: `app/db.py`'s `upsert_website_candidate`); this adapter never
        #: touches storage directly, mirroring every other adapter's
        #: separation from `SignalStore`.
        self.on_candidate = on_candidate
        #: Called with `(health_state, detail)` on every poll outcome --
        #: see `app.website_collectors.CollectorHealth`. `None` (the
        #: default) means no health sink is wired, a real no-op (same
        #: convention as `SourceAdapter._emit_source_event`).
        self.on_health = on_health
        self._seen_urls: set[str] = checkpoint_seen_urls if checkpoint_seen_urls is not None else set()
        self._poll_task: Optional[asyncio.Task] = None
        self._in_flight_tasks: set[asyncio.Task] = set()

        if self.site_format is SiteFormat.FEED and not self.feed_url:
            raise WebsiteSourceError("site_format='feed' requires feed_url")
        if self.site_format is SiteFormat.ARTICLE_LIST and not self.article_list_url:
            raise WebsiteSourceError("site_format='article_list' requires article_list_url")

    async def _default_fetch_text(self, url: str) -> str:
        import httpx

        async with httpx.AsyncClient(follow_redirects=True, timeout=15.0) as client:
            response = await client.get(url, headers={"User-Agent": "Mozilla/5.0 (compatible; SignalCopierFeedFetcher/1.0)"})
            response.raise_for_status()
            return response.text

    async def start(self) -> None:
        self._poll_task = asyncio.create_task(self._poll_loop())

    async def stop(self) -> None:
        if self._poll_task is not None:
            self._poll_task.cancel()
        if self._in_flight_tasks:
            await asyncio.gather(*self._in_flight_tasks, return_exceptions=True)

    async def _poll_loop(self) -> None:
        while True:
            try:
                await self.poll_once()
            except Exception:  # noqa: BLE001 - one bad poll cycle must not kill the loop
                logger.exception("website collector %s: poll cycle failed", self.site_id)
                await self._report_health(CollectorHealth.PARSER_FAILURE, "unexpected exception during poll cycle")
            await asyncio.sleep(self.poll_interval_seconds)

    async def _report_health(self, health: CollectorHealth, detail: Optional[str]) -> None:
        if self.on_health is not None:
            await self.on_health(health.value, detail)

    async def _discover_new_articles(self) -> list[DiscoveredArticle]:
        url = self.feed_url if self.site_format is SiteFormat.FEED else self.article_list_url
        assert url is not None  # enforced in __init__
        try:
            content = await self._fetch_feed_or_list(url)
        except Exception as exc:  # noqa: BLE001 - a fetch failure is a real, honest health state
            raise WebsiteSourceError(f"could not fetch {url!r}: {exc}") from exc

        if self.site_format is SiteFormat.FEED:
            discovered = parse_feed(content, feed_url=url)
        else:
            discovered = parse_article_list(content, base_url=url, list_url=url)

        return [a for a in discovered if a.url not in self._seen_urls]

    async def poll_once(self) -> list[Any]:
        """One full poll cycle: discover new articles, fetch+extract+
        classify each, and hand off any built `TradeCandidate` to
        `on_candidate`. Returns the list of candidates built this cycle
        (empty list, not `None`, when there was genuinely nothing new --
        distinguishable from a raised `WebsiteSourceError` on real
        failure)."""
        try:
            new_articles = await self._discover_new_articles()
        except WebsiteSourceError as exc:
            await self._report_health(CollectorHealth.UNSUPPORTED_FORMAT_ENCOUNTERED, str(exc))
            raise

        if not new_articles:
            await self._report_health(CollectorHealth.NO_NEW_ARTICLES_OBSERVED, None)
            return []

        candidates: list[Any] = []
        for article in new_articles:
            self._seen_urls.add(article.url)
            candidate = await self._process_article(article)
            if candidate is not None:
                candidates.append(candidate)
        return candidates

    async def _process_article(self, article: DiscoveredArticle):
        extracted = await self._fetch_article_fn(
            article.url,
            auth_state_path=self.auth_state_path,
            allow_playwright_fallback=self.allow_playwright_fallback,
        )

        if extracted.status is ExtractionStatus.PAYWALLED:
            await self._report_health(CollectorHealth.ACCESS_DENIED_OR_PAYWALLED, extracted.detail)
            return None
        if extracted.status in (ExtractionStatus.FETCH_FAILED, ExtractionStatus.EXTRACTION_TOO_SHORT):
            await self._report_health(CollectorHealth.UNSUPPORTED_FORMAT_ENCOUNTERED, extracted.detail)
            return None

        classification = classify_article(extracted.text or "")
        if classification.classification == ArticleClassification.NEEDS_HUMAN_REVIEW:
            await self._report_health(CollectorHealth.CLASSIFIER_UNCERTAIN, classification.detail)

        candidate = None
        if classification.classification in (
            ArticleClassification.ACTIONABLE_RECOMMENDATION,
            ArticleClassification.CONDITIONAL_SETUP,
        ):
            candidate = build_trade_candidate(
                extracted.text or "",
                channel_id=self.site_id,
                message_id=article.url,
                classification=classification,
                analyst=self.analyst,
                raw_source_event={
                    "url": article.url,
                    "title": extracted.title,
                    "author": extracted.author,
                    "published_at": extracted.published_at.isoformat() if extracted.published_at else None,
                    "modified_at": extracted.modified_at.isoformat() if extracted.modified_at else None,
                    "fetched_via": extracted.fetched_via,
                },
            )
            if candidate is None:
                await self._report_health(
                    CollectorHealth.CLASSIFIER_UNCERTAIN,
                    "classified as actionable/conditional but no symbol/side could be extracted",
                )

        if candidate is not None:
            if self.on_candidate is not None:
                task = asyncio.create_task(self.on_candidate(candidate))
                self._in_flight_tasks.add(task)
                task.add_done_callback(functools.partial(self._on_candidate_task_done, url=article.url))
            if candidate.resolved:
                signal = candidate.to_signal(source=self.name)
                if signal is not None:
                    await self.on_signal(signal)
            await self._report_health(CollectorHealth.HEALTHY_QUALIFIED, None)
        else:
            await self._report_health(CollectorHealth.HEALTHY_QUALIFIED, None)

        return candidate

    def _on_candidate_task_done(self, task: asyncio.Task, *, url: str) -> None:
        self._in_flight_tasks.discard(task)
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.error("processing website candidate for %s failed", url, exc_info=exc)
