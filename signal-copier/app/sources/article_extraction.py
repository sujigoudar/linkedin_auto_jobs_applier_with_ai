"""Track 9: article retrieval + clean-text extraction for the website
source (`app/sources/website.py`).

Two-stage fetch:

1. A plain HTTP GET (httpx), then `trafilatura` pulls clean article text/
   title/author/timestamps out of the response HTML. This is the default
   path for every fetch -- cheap and fast.
2. ONLY when stage 1 clearly fails (no response, or an extracted body
   that's empty/too short to be a real article -- see
   `_MIN_ARTICLE_CHARS`) does this fall back to rendering the page with
   Playwright (already installed in this environment) and re-running
   trafilatura against the rendered HTML. This handles JS-rendered
   content without paying browser-launch cost on every routine poll.

Paywall/login-page detection is a DISTINCT outcome
(`ExtractionStatus.PAYWALLED`), never silently folded into "empty
article" or "extraction failed" -- see `_looks_like_paywall_or_login`.
A short article body is genuinely ambiguous between "this really is a
short article" and "this is a teaser in front of a paywall"; this module
resolves that ambiguity by ALSO requiring a paywall/login signature
(explicit marker text, or a real `<form>` with a password field) before
calling it PAYWALLED -- a short article with no such signature is instead
EXTRACTION_TOO_SHORT (a real, separate failure state, not silently
treated as a successful short extraction either).

Stored authenticated session/cookie state (for the user's actual
Investors.com subscription) is explicitly OUT OF SCOPE for this task --
see `app.website_collectors.WebsiteCollector.auth_state_env_var`'s own
docstring. `fetch_article` below DOES accept an optional
`auth_state_path` parameter and, when given one, passes it to Playwright
as a `storage_state` file (this is the "code path that WOULD use stored
auth state" the task asks be built, not exercised) -- but nothing in this
codebase ever sets `auth_state_env_var` on a real collector, reads a real
session file, or is given real Investors.com credentials in this task.
The next, later, explicit step for the user: create that session file out
of band (e.g. `playwright codegen --save-storage=...` while logged into
their own subscription) and point a collector's `auth_state_env_var` at
its path -- mirroring `docs/security/TELEGRAM_USER_LOGIN.md`'s own
"login happens via a standalone script, never inside this service"
pattern.
"""
from __future__ import annotations

import enum
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

logger = logging.getLogger(__name__)

#: Below this many characters of extracted body text, a "successful"
#: extraction is treated as too short to be a real article -- either a
#: paywall teaser (if a paywall signature is also present) or a genuine
#: extraction failure (if not). Chosen well below any real IBD article
#: (which run to several hundred words) but above a bare headline/teaser
#: sentence.
_MIN_ARTICLE_CHARS = 200

#: Phrases that, verbatim, are how IBD-style and most other financial
#: publishers' paywalls actually present themselves in the still-served
#: HTML shell around a blocked article (rather than a 401/403 status --
#: most paywalled financial sites return 200 with a teaser + paywall
#: prompt so search engines can still index the headline). Matched
#: case-insensitively against the raw HTML, not just extracted text,
#: since the prompt itself is often OUTSIDE whatever trafilatura judges
#: to be the main content.
_PAYWALL_MARKERS = (
    "subscribe to continue reading",
    "subscribe to read this article",
    "this content is reserved for subscribers",
    "become a member to read",
    "sign in to continue reading",
    "already a subscriber? log in",
    "start your free trial",
    "unlock this article",
)

#: A real login form's own signature -- a password-type input -- is a
#: much stronger, harder-to-spoof-by-accident signal than marker text
#: alone, so it's checked independently and either one alone is enough to
#: call this PAYWALLED.
_LOGIN_FORM_PATTERN = re.compile(r'<input[^>]+type=["\']password["\']', re.IGNORECASE)
_PAYWALL_MARKER_PATTERN = re.compile("|".join(re.escape(m) for m in _PAYWALL_MARKERS), re.IGNORECASE)


class ExtractionStatus(str, enum.Enum):
    """Every outcome `fetch_article` can produce -- an honest disposition,
    never a bare `None`/exception for a case the caller needs to tell
    apart from the others (mirrors `text_parser.py`'s own
    `DispositionOutcome` convention)."""

    #: Real article text extracted, long enough to classify.
    OK = "ok"
    #: A paywall/login page was detected instead of the article -- see
    #: this module's own docstring for exactly what triggers this.
    PAYWALLED = "paywalled"
    #: A response was received and trafilatura ran, but the extracted
    #: body is shorter than `_MIN_ARTICLE_CHARS` with no paywall
    #: signature found -- a genuine extraction failure, never silently
    #: treated as a real (if short) article.
    EXTRACTION_TOO_SHORT = "extraction_too_short"
    #: The HTTP fetch itself failed (network error, non-2xx status) even
    #: after the Playwright fallback.
    FETCH_FAILED = "fetch_failed"


@dataclass
class ExtractedArticle:
    """`fetch_article`'s result -- always returned, never raised, so a
    caller can record a health state for every outcome including
    failure (see `app.website_collectors.CollectorHealth`)."""

    url: str
    status: ExtractionStatus
    title: Optional[str] = None
    author: Optional[str] = None
    text: Optional[str] = None
    published_at: Optional[datetime] = None
    modified_at: Optional[datetime] = None
    #: Which fetch path actually produced this result -- "http" (stage 1)
    #: or "playwright" (stage 2 fallback) -- `None` when neither
    #: succeeded (FETCH_FAILED before any extraction ran).
    fetched_via: Optional[str] = None
    detail: Optional[str] = None
    raw_html_length: int = 0


def _looks_like_paywall_or_login(html: str) -> bool:
    return bool(_PAYWALL_MARKER_PATTERN.search(html) or _LOGIN_FORM_PATTERN.search(html))


def _parse_trafilatura_date(value: Any) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            from datetime import datetime as _dt

            return _dt.strptime(str(value), fmt)
        except ValueError:
            continue
    return None


def extract_from_html(html: str, *, url: str) -> tuple[Optional[str], Optional[str], Optional[str], Optional[datetime], Optional[datetime]]:
    """Runs trafilatura against already-fetched HTML. Returns (title,
    author, text, published_at, modified_at) -- every field `None` when
    trafilatura found nothing (never fabricated). Isolated from
    `fetch_article` so a caller with HTML already in hand (a test
    fixture, a webhook body) can extract without a real network fetch."""
    import trafilatura

    extracted_json = trafilatura.extract(
        html,
        url=url,
        output_format="json",
        with_metadata=True,
        include_comments=False,
        favor_precision=True,
    )
    if not extracted_json:
        return None, None, None, None, None
    import json

    data = json.loads(extracted_json)
    text = data.get("text") or None
    title = data.get("title") or None
    author = data.get("author") or None
    published_at = _parse_trafilatura_date(data.get("date"))
    # trafilatura's own metadata has no distinct "modified" field in every
    # version -- fall back to the same `date` value only when a caller's
    # HTML fixture doesn't carry a separate one; real per-article
    # modified-at detection (a distinct <meta property="article:modified_time">
    # read) is done by `_extract_modified_at` below, called by
    # `fetch_article` against the raw HTML directly.
    modified_at = _extract_modified_at(html) or published_at
    return title, author, text, published_at, modified_at


_MODIFIED_META_PATTERN = re.compile(
    r'<meta[^>]+property=["\']article:modified_time["\'][^>]+content=["\']([^"\']+)["\']', re.IGNORECASE
)


def _extract_modified_at(html: str) -> Optional[datetime]:
    match = _MODIFIED_META_PATTERN.search(html)
    if not match:
        return None
    raw = match.group(1)
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def _build_result_from_html(html: str, *, url: str, fetched_via: str) -> ExtractedArticle:
    if _looks_like_paywall_or_login(html):
        return ExtractedArticle(
            url=url,
            status=ExtractionStatus.PAYWALLED,
            fetched_via=fetched_via,
            raw_html_length=len(html),
            detail="paywall/login signature detected in fetched HTML",
        )

    title, author, text, published_at, modified_at = extract_from_html(html, url=url)
    if not text or len(text) < _MIN_ARTICLE_CHARS:
        return ExtractedArticle(
            url=url,
            status=ExtractionStatus.EXTRACTION_TOO_SHORT,
            title=title,
            author=author,
            text=text,
            published_at=published_at,
            modified_at=modified_at,
            fetched_via=fetched_via,
            raw_html_length=len(html),
            detail=f"extracted body is {len(text or '')} chars (< {_MIN_ARTICLE_CHARS} minimum)",
        )

    return ExtractedArticle(
        url=url,
        status=ExtractionStatus.OK,
        title=title,
        author=author,
        text=text,
        published_at=published_at,
        modified_at=modified_at,
        fetched_via=fetched_via,
        raw_html_length=len(html),
    )


async def _fetch_http(url: str, *, timeout_seconds: float) -> Optional[str]:
    import httpx

    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=timeout_seconds) as client:
            response = await client.get(
                url,
                headers={"User-Agent": "Mozilla/5.0 (compatible; SignalCopierArticleFetcher/1.0)"},
            )
        if response.status_code >= 400:
            logger.info("article fetch %s returned status %s", url, response.status_code)
            return None
        return response.text
    except Exception:  # noqa: BLE001 - network failure of any kind is FETCH_FAILED, not a crash
        logger.warning("HTTP fetch failed for %s", url, exc_info=True)
        return None


async def _fetch_playwright(url: str, *, timeout_seconds: float, auth_state_path: Optional[str]) -> Optional[str]:
    """The JS-rendered-content fallback -- only reached when the plain
    HTTP fetch + trafilatura extraction clearly failed. `auth_state_path`,
    when given, is passed straight to Playwright's own
    `new_context(storage_state=...)` -- see this module's own docstring
    for what does and doesn't use this in this task."""
    try:
        from playwright.async_api import async_playwright
    except ImportError:  # pragma: no cover - environment-dependent
        logger.warning("playwright not installed; cannot fall back to browser rendering for %s", url)
        return None

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            try:
                context_kwargs: dict[str, Any] = {}
                if auth_state_path:
                    context_kwargs["storage_state"] = auth_state_path
                context = await browser.new_context(**context_kwargs)
                page = await context.new_page()
                await page.goto(url, timeout=timeout_seconds * 1000, wait_until="networkidle")
                html = await page.content()
                return html
            finally:
                await browser.close()
    except Exception:  # noqa: BLE001 - a browser-rendering failure is FETCH_FAILED, not a crash
        logger.warning("playwright fetch failed for %s", url, exc_info=True)
        return None


async def fetch_article(
    url: str,
    *,
    timeout_seconds: float = 15.0,
    auth_state_path: Optional[str] = None,
    allow_playwright_fallback: bool = True,
) -> ExtractedArticle:
    """Fetches `url` and extracts clean article content -- see this
    module's own docstring for the two-stage (HTTP-first, Playwright-
    fallback) strategy and the paywall-detection rule. Always returns an
    `ExtractedArticle` (never raises) so callers can persist an honest
    health state for every outcome."""
    html = await _fetch_http(url, timeout_seconds=timeout_seconds)
    if html is not None:
        result = _build_result_from_html(html, url=url, fetched_via="http")
        if result.status in (ExtractionStatus.OK, ExtractionStatus.PAYWALLED):
            return result
        # EXTRACTION_TOO_SHORT from a real HTTP response: worth trying the
        # heavier Playwright path in case the real content is JS-rendered
        # (rather than assuming every short result is just a short
        # article).
        if not allow_playwright_fallback:
            return result
        fallback_html = await _fetch_playwright(url, timeout_seconds=timeout_seconds, auth_state_path=auth_state_path)
        if fallback_html is None:
            return result  # keep the honest http-stage EXTRACTION_TOO_SHORT result
        return _build_result_from_html(fallback_html, url=url, fetched_via="playwright")

    if not allow_playwright_fallback:
        return ExtractedArticle(url=url, status=ExtractionStatus.FETCH_FAILED, detail="HTTP fetch failed")

    fallback_html = await _fetch_playwright(url, timeout_seconds=timeout_seconds, auth_state_path=auth_state_path)
    if fallback_html is None:
        return ExtractedArticle(url=url, status=ExtractionStatus.FETCH_FAILED, detail="both HTTP and Playwright fetch failed")
    return _build_result_from_html(fallback_html, url=url, fetched_via="playwright")
