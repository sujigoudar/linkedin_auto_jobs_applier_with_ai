"""Track 24: `RssSourceAdapter` -- the ONE real, testable example this
track wires against the new adapter boundary (`app/sources/
adapter_contract.py`). A direct RSS/Atom source, using `feedparser`
directly (same library `app/sources/website.py`'s pre-existing
`WebsiteSource` FEED mode already uses -- see this module's own
docstring section below for exactly how the two relate), with NO
dependency on Agent Reach or any external account/credential.

Relationship to the pre-existing `app.sources.website.WebsiteSource`
(verified before writing a single line here, per this track's brief):
`WebsiteSource`'s FEED mode is a REAL, already-implemented RSS/Atom
adapter wired into `app/connection_catalog.py`'s `"rss"`/`"website"`
catalog entries and the TR-17 onboarding wizard -- it is NOT a
placeholder. This module does not replace or duplicate that wiring; it
is a SEPARATE, parallel adapter demonstrating the NEW `probe`/`fetch`/
`poll`/`normalize` contract and writing to the NEW `source_observations`
table, which `WebsiteSource` (built before this track, against its own
`TradeCandidate`/`on_candidate` pipeline and the `collectors` table's
checkpoint) does not do. `app/connection_catalog.py`'s `"rss"` entry
correctly continues to point at `WebsiteSource` -- no catalog change was
needed for this track (see that module's own `verified_against` list,
confirmed accurate by reading `app/sources/website.py` first).

Checkpoint storage: `sources.acquisition_checkpoint` (see that column's
own comment in `app/db.py`) -- NOT a new `collectors` row, because
`SignalStore.register_collector` enforces a CLOSED `CollectorKind` enum
(telegram/pull/email/website) this adapter does not belong to, and
widening that enum for an adjacent, not-yet-integrated adapter would be
a wider blast radius than one nullable column on the `sources` row this
adapter already requires (Track 14's provider/source/connection model).

Content completeness: an RSS/Atom entry with real inline content
(`content`/`summary`, non-trivial length, not visibly truncated) is
`COMPLETE`; an entry with only a `title`/`link` and nothing else is
`POINTER_ONLY`; a `summary` that looks cut off (ends in an ellipsis) is
the honest `UNKNOWN` middle case -- NEVER silently resolved to
`COMPLETE` (hard rule 11).

Signal emission: a `source_observations` row NEVER becomes a `Signal`
unless `purpose == "signal_candidate"` (an EXPLICIT, operator-set field
on this adapter -- `is_signal_candidate_route`, default `False`) AND its
`completeness` is `COMPLETE` AND the reused `app.sources.
article_classifier` pipeline (the SAME classification-before-extraction
step `WebsiteSource` already uses -- not a second, parallel trade-
parsing grammar invented here) actually resolves a tradeable candidate.
A fresh RSS source defaults to `purpose="research"`, which can NEVER
produce a `Signal`, regardless of content -- "don't fabricate an alert
just to demonstrate the connection works" (this track's own brief).
Every `Signal` that IS produced still goes through `self.on_signal`
unchanged -- routing, the qualified-route gate
(`app.engine.SignalCopierEngine._check_route_qualified`), and every
other engine-level safety check are completely untouched by this
adapter, exactly like `WebsiteSource`'s own docstring states for itself.
"""
from __future__ import annotations

import hashlib
import logging
import time as _time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Optional

from app.notification_bridge import ContentCompleteness
from app.sources.adapter_contract import (
    MAX_RESPONSE_BYTES,
    Observation,
    ObservationNormalizationError,
    PollBounds,
    ProbeResult,
    SourceAdapterContract,
    retry_with_backoff,
)
from app.sources.article_classifier import (
    ArticleClassification,
    build_trade_candidate,
    classify_article,
)
from app.sources.base import SourceAdapter
from app.sources.url_safety import validate_public_fetch_url

logger = logging.getLogger(__name__)

#: This adapter's own exact normalization/parsing logic version -- same
#: "a fixed string identifying this specific implementation, bumped
#: whenever the normalization logic changes" convention as
#: `app.sources.text_parser.PARSER_VERSION`/`app.sources.
#: article_classifier.CLASSIFIER_VERSION`.
RSS_ADAPTER_VERSION = "rss-source-adapter-v1"

#: A `summary`/`content` that ends with one of these is treated as
#: visibly truncated -- a real, honest mid-content cut, not a complete
#: thought (see this module's own docstring on completeness).
_TRUNCATION_MARKERS = ("...", "…", "[...]", "[&#8230;]")

#: Below this length, a non-empty summary/content is too short to
#: confidently call COMPLETE but isn't nothing either -- the honest
#: UNKNOWN middle ground (hard rule 11: never guess).
_MIN_CONFIDENT_COMPLETE_LENGTH = 40


@dataclass(frozen=True)
class RssConnectionConfig:
    """This adapter's own `connection` shape (see
    `SourceAdapterContract`'s own docstring for why `connection` is
    typed `Any` at the contract level) -- a thin, adapter-local view
    over whatever the real `connections`/`sources` catalog rows hold,
    not a new persisted table of its own."""

    feed_url: str
    connection_id: Optional[str] = None
    provider_id: Optional[str] = None
    source_id: Optional[str] = None


def _entry_identity(entry: dict) -> Optional[str]:
    return entry.get("id") or entry.get("link")


def _entry_text(entry: dict) -> Optional[str]:
    content_list = entry.get("content")
    if content_list:
        value = content_list[0].get("value") if isinstance(content_list[0], dict) else None
        if value:
            return str(value)
    for key in ("summary", "description"):
        value = entry.get(key)
        if value:
            return str(value)
    return None


def _classify_completeness(entry: dict) -> str:
    text = _entry_text(entry)
    if not text or not text.strip():
        return ContentCompleteness.POINTER_ONLY.value
    stripped = text.strip()
    if any(stripped.endswith(marker) for marker in _TRUNCATION_MARKERS):
        return ContentCompleteness.UNKNOWN.value
    if len(stripped) >= _MIN_CONFIDENT_COMPLETE_LENGTH:
        return ContentCompleteness.COMPLETE.value
    return ContentCompleteness.UNKNOWN.value


def _entry_published_at(entry: dict) -> Optional[datetime]:
    for field_name in ("published_parsed", "updated_parsed"):
        struct_time = entry.get(field_name)
        if struct_time:
            return datetime.fromtimestamp(_time.mktime(struct_time), tz=timezone.utc)
    return None


def _content_hash(entry: dict) -> str:
    text = _entry_text(entry) or ""
    payload = "\x1f".join([entry.get("title") or "", entry.get("link") or "", text])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


class RssSourceAdapter(SourceAdapter, SourceAdapterContract):
    """One feed's worth of polling -- one instance per registered RSS
    `sources`/`connections` row pair, mirroring every other adapter's
    one-instance-per-registry-row convention (see `app.sources.website.
    WebsiteSource`'s own docstring)."""

    name = "rss_v2"

    def __init__(
        self,
        on_signal,
        *,
        source_id: str,
        feed_url: str,
        connection_id: Optional[str] = None,
        provider_id: Optional[str] = None,
        store: Optional[Any] = None,
        is_signal_candidate_route: bool = False,
        poll_interval_seconds: float = 300.0,
        max_items: int = 50,
        overlap_count: int = 3,
        on_source_event=None,
        # Injection points for tests -- default to the real network call.
        fetch_text_fn: Optional[Callable[[str], Awaitable[str]]] = None,
    ):
        super().__init__(on_signal, on_source_event=on_source_event)
        self.source_id = source_id
        self.feed_url = feed_url
        self.connection_id = connection_id
        self.provider_id = provider_id
        self.store = store
        #: Explicit, operator-set flag -- NEVER inferred. See this
        #: module's own docstring on signal emission.
        self.is_signal_candidate_route = is_signal_candidate_route
        self.poll_interval_seconds = poll_interval_seconds
        self.bounds = PollBounds(max_items=max_items, overlap_count=overlap_count)
        self._fetch_text_fn = fetch_text_fn or self._default_fetch_text
        self._poll_task: Any = None

    # -- app.sources.base.SourceAdapter ----------------------------------

    async def start(self) -> None:
        import asyncio

        self._poll_task = asyncio.create_task(self._poll_loop())

    async def stop(self) -> None:
        if self._poll_task is not None:
            self._poll_task.cancel()

    async def _poll_loop(self) -> None:
        import asyncio

        while True:
            try:
                await self.poll_once()
            except Exception:  # noqa: BLE001 - one bad cycle must not kill the loop
                logger.exception("rss source %s: poll cycle failed", self.source_id)
            await asyncio.sleep(self.poll_interval_seconds)

    # -- networking -------------------------------------------------------

    async def _default_fetch_text(self, url: str) -> str:
        import httpx

        async def _do_fetch() -> str:
            async with httpx.AsyncClient(follow_redirects=True, timeout=15.0) as client:
                async with client.stream(
                    "GET", url, headers={"User-Agent": "Mozilla/5.0 (compatible; SignalCopierRssAdapter/1.0)"}
                ) as response:
                    response.raise_for_status()
                    chunks: list[bytes] = []
                    total = 0
                    async for chunk in response.aiter_bytes():
                        total += len(chunk)
                        if total > MAX_RESPONSE_BYTES:
                            raise ValueError(f"response from {url!r} exceeded {MAX_RESPONSE_BYTES} byte cap")
                        chunks.append(chunk)
                    return b"".join(chunks).decode(response.encoding or "utf-8", errors="replace")

        return await retry_with_backoff(_do_fetch)

    # -- SourceAdapterContract --------------------------------------------

    async def probe(self, connection: Any) -> ProbeResult:
        try:
            import feedparser  # noqa: F401

            dependency_installed: Optional[bool] = True
            dependency_reason = None
        except ImportError as exc:  # pragma: no cover - feedparser is a hard dependency today
            dependency_installed = False
            dependency_reason = f"feedparser is not installed: {exc}"

        feed_url = getattr(connection, "feed_url", None) or self.feed_url
        required_fields_available = bool(feed_url)
        required_fields_reason = None if required_fields_available else "feed_url is not configured"

        target_readable: Optional[bool] = None
        target_reason = None
        if dependency_installed and required_fields_available:
            try:
                await self._fetch_text_fn(feed_url)
                target_readable = True
            except Exception as exc:  # noqa: BLE001 - a fetch failure is a real, honest probe outcome
                target_readable = False
                target_reason = str(exc)

        return ProbeResult(
            dependency_installed=dependency_installed,
            dependency_installed_reason=dependency_reason,
            # A public RSS feed has no credential concept at all -- honestly
            # "not checked" rather than a fabricated True.
            credentials_configured=None,
            credentials_configured_reason="RSS feeds configured via this adapter carry no credential",
            authentication_verified=None,
            authentication_verified_reason="no authentication concept for a public RSS feed",
            target_readable=target_readable,
            target_readable_reason=target_reason,
            required_fields_available=required_fields_available,
            required_fields_available_reason=required_fields_reason,
        )

    async def fetch(self, connection: Any, source_reference: Any) -> Observation:
        feed_url = getattr(connection, "feed_url", None) or self.feed_url
        import feedparser

        raw_text = await self._fetch_text_fn(feed_url)
        parsed = feedparser.parse(raw_text)
        for entry in parsed.get("entries", []):
            if _entry_identity(entry) == source_reference:
                return self.normalize({"entry": entry, "adapter_name": self.name})
        raise KeyError(f"no entry with id/link {source_reference!r} found in feed {feed_url!r}")

    async def poll(self, connection: Any, checkpoint: Any, bounds: PollBounds) -> tuple[list[Observation], Any]:
        feed_url = getattr(connection, "feed_url", None) or self.feed_url
        import feedparser

        raw_text = await self._fetch_text_fn(feed_url)
        parsed = feedparser.parse(raw_text)
        if parsed.get("bozo") and not parsed.get("entries") and not parsed.get("feed", {}).get("title"):
            raise ObservationNormalizationError(
                f"content at {feed_url!r} does not parse as a recognizable RSS/Atom feed: "
                f"{parsed.get('bozo_exception')!r}"
            )

        previous_seen: dict[str, str] = dict((checkpoint or {}).get("seen", {})) if checkpoint else {}
        previous_order: list[str] = list((checkpoint or {}).get("order", [])) if checkpoint else []

        observations: list[Observation] = []
        new_seen: dict[str, str] = {}
        new_order: list[str] = []
        overlap_ids = set(previous_order[: bounds.overlap_count])

        for entry in parsed.get("entries", []):
            item_id = _entry_identity(entry)
            if not item_id:
                continue  # an entry with neither id nor link can't be identified -- skipped, not guessed
            content_hash = _content_hash(entry)
            new_seen[item_id] = content_hash
            new_order.append(item_id)

            previously_seen_hash = previous_seen.get(item_id)
            if previously_seen_hash is None:
                kind = "retrieved"
            elif previously_seen_hash != content_hash and item_id in overlap_ids:
                kind = "edited"
            else:
                continue  # unchanged and already seen -- not re-emitted every poll

            observation = self.normalize({"entry": entry, "adapter_name": self.name, "observation_kind": kind})
            observations.append(observation)
            if len(observations) >= bounds.max_items:
                break

        # Bound checkpoint growth -- keep a window comfortably larger
        # than both max_items and overlap_count so a slow-moving feed's
        # whole visible window stays comparable across polls.
        window = max(bounds.max_items, bounds.overlap_count) * 4
        next_checkpoint = {
            "order": new_order[:window],
            "seen": {item_id: new_seen[item_id] for item_id in new_order[:window]},
        }
        return observations, next_checkpoint

    @staticmethod
    def normalize(raw_result: Any) -> Observation:
        if not isinstance(raw_result, dict) or "entry" not in raw_result:
            raise ObservationNormalizationError(
                f"expected a dict with an 'entry' key, got {type(raw_result).__name__}"
            )
        entry = raw_result["entry"]
        if not isinstance(entry, dict):
            raise ObservationNormalizationError(f"'entry' must be a dict, got {type(entry).__name__}")

        item_id = _entry_identity(entry)
        if not item_id:
            raise ObservationNormalizationError("feed entry has neither 'id' nor 'link' -- cannot identify it")

        now = datetime.now(timezone.utc)
        completeness = _classify_completeness(entry)
        return Observation(
            platform="rss",
            original_item_id=item_id,
            canonical_url=entry.get("link"),
            observation_kind=raw_result.get("observation_kind", "retrieved"),
            content_hash=_content_hash(entry),
            source_authored_at=_entry_published_at(entry),
            first_observed_at=now,
            retrieved_at=now,
            timestamp_origin="source_reported" if _entry_published_at(entry) else "retrieval_time",
            timestamp_uncertain=_entry_published_at(entry) is None,
            completeness=completeness,
            extracted_text=_entry_text(entry),
            adapter_name=raw_result.get("adapter_name", "rss_v2"),
            backend="feedparser",
            parser_version=RSS_ADAPTER_VERSION,
            retrieval_method="feed_poll",
        )

    async def fetch_linked_article_text(self, canonical_url: str) -> str:
        """Fetches the full text at a URL found INSIDE a feed entry (a
        linked article -- content the feed's own author controls, not
        the operator-configured `feed_url` itself). Goes through
        `validate_public_fetch_url` FIRST (see `app.sources.url_safety`'s
        own module docstring for exactly why this is a different trust
        level than the feed URL) -- raises `UnsafeFetchUrlError` for a
        URL that fails that check, same retry/timeout/size-cap
        discipline as every other network call this adapter makes."""
        safe_url = validate_public_fetch_url(canonical_url)
        return await self._fetch_text_fn(safe_url)

    # -- orchestration (persists observations + checkpoint, emits Signal) -

    async def poll_once(self) -> list[Observation]:
        """One full poll cycle against this adapter's own configured
        connection -- discover observations, persist each one, update
        this source's checkpoint, and emit a `Signal` ONLY for an
        eligible `signal_candidate` observation (see this module's own
        docstring). Returns the observations discovered this cycle
        (`[]`, never `None`, for a genuinely quiet poll -- distinguishable
        from a new-item poll by the caller inspecting the returned list,
        per this track's "never fabricate an alert just to demonstrate
        the connection works" rule)."""
        connection = RssConnectionConfig(
            feed_url=self.feed_url,
            connection_id=self.connection_id,
            provider_id=self.provider_id,
            source_id=self.source_id,
        )
        existing_checkpoint = None
        if self.store is not None:
            source_row = self.store.get_source(self.source_id)
            if source_row is not None:
                existing_checkpoint = source_row.get("acquisition_checkpoint")

        observations, next_checkpoint = await self.poll(connection, existing_checkpoint, self.bounds)

        for observation in observations:
            purpose, eligibility_state, rejection_reason = self._classify_disposition(observation)
            if self.store is not None:
                self.store.record_source_observation(
                    platform=observation.platform,
                    original_item_id=observation.original_item_id,
                    observation_kind=observation.observation_kind,
                    completeness=observation.completeness,
                    adapter_name=observation.adapter_name,
                    first_observed_at=observation.first_observed_at or datetime.now(timezone.utc),
                    retrieved_at=observation.retrieved_at or datetime.now(timezone.utc),
                    connection_id=self.connection_id,
                    provider_id=self.provider_id,
                    source_id=self.source_id,
                    canonical_url=observation.canonical_url,
                    content_hash=observation.content_hash,
                    source_authored_at=observation.source_authored_at,
                    timestamp_origin=observation.timestamp_origin,
                    timestamp_uncertain=observation.timestamp_uncertain,
                    extracted_text=observation.extracted_text,
                    backend=observation.backend,
                    parser_version=observation.parser_version,
                    retrieval_method=observation.retrieval_method,
                    purpose=purpose,
                    eligibility_state=eligibility_state,
                    rejection_reason=rejection_reason,
                )
            if eligibility_state == "eligible":
                await self._maybe_emit_signal(observation)

        if self.store is not None:
            self.store.update_source_checkpoint(self.source_id, next_checkpoint)

        return observations

    def _classify_disposition(self, observation: Observation) -> tuple[str, str, Optional[str]]:
        """Never a guess: `purpose` is this adapter's own EXPLICIT,
        operator-set `is_signal_candidate_route` flag -- `research`
        (never eligible) unless that flag is `True`. Even then, only a
        genuinely `COMPLETE` observation can be eligible -- a `signal_
        candidate` route observation that arrives `PARTIAL`/
        `POINTER_ONLY`/`TRUNCATED`/`UNKNOWN` is recorded, never silently
        promoted, never silently dropped."""
        if not self.is_signal_candidate_route:
            return "research", "not_eligible", None
        if observation.completeness != ContentCompleteness.COMPLETE.value:
            return "signal_candidate", "not_eligible", f"completeness is {observation.completeness!r}, not complete"
        return "signal_candidate", "eligible", None

    async def _maybe_emit_signal(self, observation: Observation) -> None:
        """Reuses the SAME classification-before-extraction pipeline
        `app.sources.website.WebsiteSource` already runs articles
        through -- never a second, parallel trade-parsing grammar."""
        text = observation.extracted_text or ""
        if not text.strip():
            return
        classification = classify_article(text)
        if classification.classification not in (
            ArticleClassification.ACTIONABLE_RECOMMENDATION,
            ArticleClassification.CONDITIONAL_SETUP,
        ):
            return
        candidate = build_trade_candidate(
            text,
            channel_id=self.source_id,
            message_id=observation.original_item_id,
            classification=classification,
            raw_source_event={"url": observation.canonical_url, "adapter": self.name},
        )
        if candidate is None or not candidate.resolved:
            return
        signal = candidate.to_signal(source=self.name)
        if signal is not None:
            # Track 29: this adapter genuinely knows its own Track 14
            # provider-catalog `sources.id` (`self.source_id`) -- the one
            # real case today where a Signal's catalog source is known
            # at emission time, not fabricated.
            signal.source_catalog_id = self.source_id
            await self.on_signal(signal)
