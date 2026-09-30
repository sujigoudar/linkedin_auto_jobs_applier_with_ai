"""Track 9: the persistent website/article collector registry.

Mirrors `app/telegram_collectors.py`'s own shape and reasoning field for
field -- see that module's docstring for the full rationale this repeats
here rather than re-deriving: a real, persisted registry (`website_
collectors` table, `app/db.py`'s `SignalStore`) of every site/section this
deployment polls for articles, instead of a hardcoded URL somewhere in
adapter code.

A website collector is deliberately narrower than the Telegram registry
in one respect: there is no bot/user-account connection-mode choice here
-- every collector either polls a configured RSS/Atom `feed_url`, or (when
no usable feed exists) diffs a configured `article_list_url`'s links
against a seen-URL checkpoint. `site_format` records which of the two this
collector actually uses; see `SiteFormat`'s own docstring.

Security note (SECURITY, hard requirement) -- identical to Telegram's:
this table stores ONLY a REFERENCE to where an (optional) authenticated
session/cookie state for paywalled content lives --
`auth_state_env_var` names an environment variable pointing to a session
state FILE PATH, never a token/cookie/session VALUE itself. `None` (the
default) means this collector fetches with no stored auth at all --
plain, unauthenticated HTTP -- which is the ONLY mode this task actually
exercises; see `app/sources/article_extraction.py`'s module docstring for
exactly what "build the code path that WOULD use it" does and does not
mean here.

Private-ingestion / commercial-redistribution isolation (point 10, same
as Telegram): `allowed_uses` defaults to `["private_trading"]` ONLY.
Registering a collector here never grants, and cannot by itself imply,
eligibility for `signal-portfolio-commercial`'s customer-facing
publication pipeline.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional


class SiteFormat(str, enum.Enum):
    """How this collector actually discovers new articles -- see this
    module's own docstring. A collector's registration must specify
    `feed_url` (FEED) or `article_list_url` (ARTICLE_LIST), never both
    left unset -- see `validate_registration`."""

    #: Polls a configured RSS/Atom `feed_url` (feedparser) -- entry id/
    #: link/published timestamp drive new-article detection. Preferred
    #: whenever a real feed exists for the site/section.
    FEED = "feed"
    #: No usable feed: polls a configured `article_list_url` (a plain
    #: article-index HTML page) and diffs its extracted links against a
    #: "seen URL set" checkpoint. Deliberately NOT a general-purpose
    #: visual-diff/changedetection.io-style tool -- see
    #: `app/sources/website.py`'s module docstring.
    ARTICLE_LIST = "article_list"


class AllowedUse(str, enum.Enum):
    """Identical vocabulary and isolation guarantee to
    `app.telegram_collectors.AllowedUse` -- see that enum's own
    docstring. Kept as a separate enum (not imported from there) so this
    registry's own persisted values are never accidentally coupled to
    changes made to the Telegram registry's vocabulary."""

    PRIVATE_TRADING = "private_trading"
    COMMERCIAL_REDISTRIBUTION = "commercial_redistribution"


DEFAULT_ALLOWED_USES: list[str] = [AllowedUse.PRIVATE_TRADING.value]


class CollectorHealth(str, enum.Enum):
    """Point 4/8 of the Track 9 brief: the explicit, honest incident/
    health states every website collector must be surfaceable as --
    never silently held with no dashboard signal. `UNQUALIFIED` (not
    `HEALTHY_QUALIFIED`) is the default for a freshly registered row, same
    reasoning as `app.telegram_collectors.CollectorHealth`."""

    #: Registered with neither a usable `feed_url` nor `article_list_url`
    #: (should be unreachable through `validate_registration`, but also
    #: covers a collector whose ONLY configured URL later turns out to be
    #: blank/unparseable at poll time).
    MISSING_CONFIGURATION = "missing_configuration"
    #: The feed/article-list URL was polled successfully, but no entry the
    #: registry hasn't already seen was found this cycle -- routine, not
    #: an error; distinguishes "genuinely quiet source" from a real
    #: failure.
    NO_NEW_ARTICLES_OBSERVED = "no_new_articles_observed"
    #: A fetch returned what `article_extraction.py` recognizes as a
    #: paywall/login page rather than the article body -- see that
    #: module's `ExtractionStatus.PAYWALLED`.
    ACCESS_DENIED_OR_PAYWALLED = "access_denied_or_paywalled"
    #: The feed/article-list URL returned content this adapter's parser
    #: could not recognize as RSS/Atom or as HTML with extractable links
    #: (e.g. a non-2xx response, a JSON API, garbage bytes).
    UNSUPPORTED_FORMAT_ENCOUNTERED = "unsupported_format_encountered"
    #: An article was fetched and extracted, but `article_classifier.py`
    #: could not confidently place it in any of the six categories (or
    #: confidently place an actionable/conditional article's trade
    #: structure) -- a DISTINCT, visible "needs human review" state, never
    #: silently held with no signal at all. See
    #: `app/sources/article_classifier.py`'s `ArticleClassification.
    #: NEEDS_HUMAN_REVIEW`.
    CLASSIFIER_UNCERTAIN = "classifier_uncertain"
    #: A real fetch/extract succeeded but the classifier or candidate-
    #: builder raised in a way distinct from routine non-actionable
    #: classification (a genuine bug/crash on real content).
    PARSER_FAILURE = "parser_failure"
    #: The default, honest starting state -- registered, but no evidence
    #: yet that a real article has ever been fetched and processed.
    UNQUALIFIED = "unqualified"
    #: Real evidence recorded (see
    #: `SignalStore.record_website_collector_qualification_evidence`) that
    #: this collector is genuinely fetching and processing real articles
    #: -- the only state a dashboard may render as green.
    HEALTHY_QUALIFIED = "healthy_qualified"


class WebsiteCollectorError(ValueError):
    """Raised for a registration/update that fails this registry's own
    validation -- never a silent best-effort acceptance."""


@dataclass
class WebsiteCollector:
    """One row of the `website_collectors` registry -- mirrors
    `app.telegram_collectors.TelegramCollector`'s shape."""

    id: str
    site_format: SiteFormat
    #: The site/section identity (e.g. "investors_com_swingtrader") --
    #: this source's `channel_id` on every `Signal`/candidate it produces.
    site_id: str
    provider_name: str
    feed_url: Optional[str] = None
    article_list_url: Optional[str] = None
    analyst: Optional[str] = None
    #: The name of an environment variable pointing to a stored auth-
    #: state FILE PATH (e.g. a Playwright `storage_state` JSON file) for
    #: paywalled content -- NEVER a session/cookie value itself. `None`
    #: (the default) means this collector fetches with no stored auth.
    #: See this module's own docstring and
    #: `app/sources/article_extraction.py` for exactly how/when it would
    #: be used.
    auth_state_env_var: Optional[str] = None
    allowed_uses: list[str] = field(default_factory=lambda: list(DEFAULT_ALLOWED_USES))
    last_qualified_at: Optional[datetime] = None
    qualification_evidence: dict[str, Any] = field(default_factory=dict)
    #: Point 7 (same convention as Telegram's `checkpoint_message_id`):
    #: the last article URL this collector has admitted to live routing --
    #: `None` for a collector that has never processed a live article.
    checkpoint_article_url: Optional[str] = None
    checkpoint_updated_at: Optional[datetime] = None
    health_state: CollectorHealth = CollectorHealth.UNQUALIFIED
    health_detail: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    def __post_init__(self) -> None:
        if isinstance(self.site_format, str):
            self.site_format = SiteFormat(self.site_format)
        if isinstance(self.health_state, str):
            self.health_state = CollectorHealth(self.health_state)


def validate_registration(
    *,
    collector_id: str,
    site_format: str,
    site_id: str,
    provider_name: str,
    feed_url: str | None,
    article_list_url: str | None,
    allowed_uses: list[str] | None,
) -> tuple[SiteFormat, list[str]]:
    """Shared validation for a new/updated registry row -- raises
    `WebsiteCollectorError` for anything that would leave the registry in
    a state the Track 9 brief doesn't allow:

    - an unrecognized `site_format`.
    - `site_format == "feed"` with no `feed_url` set (or vice versa for
      `"article_list"`/`article_list_url`) -- the collector must specify
      the EXACT feed or article-list URL to poll; this registry never
      guesses/scans arbitrary URLs on the operator's behalf.
    - any `allowed_uses` entry that isn't a real `AllowedUse` value.
    - any required identity field left blank.
    """
    if not collector_id or not site_id or not provider_name:
        raise WebsiteCollectorError("collector_id, site_id and provider_name are all required")
    try:
        fmt = SiteFormat(site_format)
    except ValueError as exc:
        raise WebsiteCollectorError(
            f"site_format must be one of {[f.value for f in SiteFormat]}, got {site_format!r}"
        ) from exc
    if fmt is SiteFormat.FEED and not feed_url:
        raise WebsiteCollectorError("site_format='feed' requires a non-empty feed_url")
    if fmt is SiteFormat.ARTICLE_LIST and not article_list_url:
        raise WebsiteCollectorError("site_format='article_list' requires a non-empty article_list_url")
    uses = list(allowed_uses) if allowed_uses is not None else list(DEFAULT_ALLOWED_USES)
    for use in uses:
        try:
            AllowedUse(use)
        except ValueError as exc:
            raise WebsiteCollectorError(
                f"allowed_uses entries must be one of {[u.value for u in AllowedUse]}, got {use!r}"
            ) from exc
    return fmt, uses


def now_utc() -> datetime:
    return datetime.now(timezone.utc)
