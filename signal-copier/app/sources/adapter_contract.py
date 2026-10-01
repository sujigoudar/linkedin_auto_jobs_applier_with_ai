"""Track 24: the generic external-source-ingestion adapter boundary.

Context (read before touching this file): an "Agent Reach"-adjacent
integration brief asked for a generic external-source-ingestion
capability. After actually inspecting the upstream Agent-Reach project
and this codebase's own existing architecture, the conclusion was that
Agent Reach itself adds nothing for RSS -- it is a router that tells an
agent to call `feedparser` directly, it does not wrap reading a feed.
This module is signal-copier's OWN adapter boundary, wired to exactly
ONE real, testable example (`app/sources/rss_source.py`'s
`RssSourceAdapter`), with no dependency on Agent Reach or any external
account/credential. X/Twitter (Agent Reach's only genuine value-add,
since it needs real browser cookies this environment does not have) is
explicitly OUT OF SCOPE here -- nothing in this module is stubbed as if
it works for X.

This is a NEW, parallel contract alongside the pre-existing
`app.sources.base.SourceAdapter` (`start`/`stop`/`on_signal`), not a
replacement for it: `SourceAdapter` is the push/poll-loop lifecycle every
adapter this codebase already has implements; `SourceAdapterContract`
below is the NEW, explicitly multi-step shape ("probe this connection's
readiness", "fetch one named item", "poll a bounded batch",
"normalize a raw backend result") this track's brief specifies, designed
so a future adapter can be built against the SAME contract regardless of
backend. `RssSourceAdapter` (app/sources/rss_source.py) implements BOTH:
`SourceAdapter` for the existing adapter lifecycle this codebase's
startup wiring understands, and `SourceAdapterContract` for the new,
richer four-call shape this track adds.

Four calls, each doing exactly one thing (never conflated):

- `probe(connection)` -> `ProbeResult`: readiness along MULTIPLE
  independent dimensions, never one boolean. This is explicitly NOT the
  same shape as `app.connections.compute_connection_health`'s existing
  single `ConnectionHealthState` -- a probe can say "dependency
  installed: yes, credentials configured: n/a (this connection type
  needs none), target reachable: no, reason: DNS resolution failed" all
  at once, which a single health enum cannot represent.
- `fetch(connection, source_reference)` -> `Observation`: retrieve ONE
  explicitly identified item (e.g. a single feed entry's own id/link) --
  never a scan/guess of "whatever's new".
  `poll(connection, checkpoint, bounds)` -> `(list[Observation],
  next_checkpoint)`: a BOUNDED batch (never unbounded -- see
  `PollBounds.max_items`), proposing the next checkpoint without
  committing it (the caller, e.g. `RssSourceAdapter.poll_once`, persists
  it only after successfully processing the batch -- same "caller
  commits, contract proposes" split as `app.website_collectors`'s own
  checkpoint handling). `PollBounds.overlap_count` makes BOUNDED OVERLAP
  (re-checking the last N already-seen items on every poll, to catch a
  feed's delayed publication or a silent edit) an explicit, configurable
  parameter -- never a hardcoded magic number.
- `normalize(raw_result)` -> `Observation`: backend-specific raw output
  (e.g. one `feedparser` entry dict) -> this module's own `Observation`
  dataclass. Raises `ObservationNormalizationError` for anything
  malformed/unexpected -- a REAL, testable rejection path, never a silent
  best-effort guess (hard rule 11: never fabricate data).

`Observation` mirrors `app/db.py`'s `source_observations` table column
for column (see that table's own CREATE TABLE comment) -- this dataclass
IS the in-process shape `SignalStore.record_source_observation` persists
field-for-field, so the two can never silently drift apart. It reuses
`app.notification_bridge.ContentCompleteness`'s exact five states for
`completeness` (COMPLETE/PARTIAL/POINTER_ONLY/TRUNCATED/UNKNOWN) --
never a new vocabulary invented for this track, per the brief's own
instruction.

Network discipline (every network-call-capable function in this module
and in `rss_source.py` follows this): an explicit `httpx` timeout, a
response-size cap (`MAX_RESPONSE_BYTES`), and bounded retry with backoff
(`retry_with_backoff` below). This codebase was grepped first for an
existing retry/backoff utility (`backoff`/`retry` across `app/`) and has
none -- every adapter and broker today either does a single `httpx` call
with no retry (`app/sources/website.py`, every broker in `app/brokers/`)
or layers its own ad hoc rate limiting (`app/context/*`'s `aiolimiter`
usage, a different concern: outbound QUOTA, not retry-on-failure). This
is therefore a genuinely new, small, local utility, not a duplicate of
something that already existed.
"""
from __future__ import annotations

import abc
import asyncio
import random
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Awaitable, Callable, Optional, TypeVar

#: A conservative cap on any single fetched response body -- a feed or
#: linked-article fetch is never allowed to pull an unbounded amount of
#: data into memory. 5 MiB comfortably covers any real RSS/Atom feed or
#: article page; anything larger is almost certainly not a feed/article
#: at all.
MAX_RESPONSE_BYTES = 5 * 1024 * 1024

#: Default bounded-retry policy for a network call -- 3 attempts total
#: (1 initial + 2 retries), exponential backoff with jitter, capped so a
#: single poll cycle can never hang indefinitely.
DEFAULT_RETRY_ATTEMPTS = 3
DEFAULT_RETRY_BASE_DELAY_SECONDS = 0.5
DEFAULT_RETRY_MAX_DELAY_SECONDS = 8.0

T = TypeVar("T")


class ObservationNormalizationError(ValueError):
    """Raised by `normalize()` (and any backend-specific normalizer built
    against this contract) when the raw input isn't a shape it can
    honestly turn into an `Observation` -- e.g. missing the one field
    every observation must have (`original_item_id`), or a value of the
    wrong type entirely. Never caught-and-guessed by this module itself;
    a caller decides whether a rejected item is skipped, logged, or
    surfaced as a health state (see `app.sources.rss_source`)."""


async def retry_with_backoff(
    fn: Callable[[], Awaitable[T]],
    *,
    attempts: int = DEFAULT_RETRY_ATTEMPTS,
    base_delay: float = DEFAULT_RETRY_BASE_DELAY_SECONDS,
    max_delay: float = DEFAULT_RETRY_MAX_DELAY_SECONDS,
    retry_on: tuple[type[BaseException], ...] = (Exception,),
) -> T:
    """Calls `fn()` (a zero-arg async callable -- wrap a real call in a
    lambda/partial) up to `attempts` times, with exponential backoff plus
    jitter between attempts. Re-raises the LAST exception once `attempts`
    is exhausted -- never silently swallows a persistent failure.
    `attempts <= 1` means no retry at all (a single attempt, exception
    propagates immediately), the honest behavior for a caller that wants
    this utility's shape without its retrying."""
    if attempts < 1:
        raise ValueError("attempts must be >= 1")
    last_exc: BaseException | None = None
    for attempt_index in range(attempts):
        try:
            return await fn()
        except retry_on as exc:  # noqa: PERF203 - bounded, small `attempts`
            last_exc = exc
            if attempt_index == attempts - 1:
                break
            delay = min(max_delay, base_delay * (2**attempt_index))
            delay += random.uniform(0, base_delay)  # noqa: S311 - jitter only, not security-sensitive
            await asyncio.sleep(delay)
    assert last_exc is not None  # loop always either returns or sets this before breaking
    raise last_exc


@dataclass(frozen=True)
class ProbeResult:
    """Multiple independent readiness dimensions for one connection --
    deliberately NOT a single boolean/health-enum (see this module's own
    docstring). Each dimension is `True`/`False`/`None` -- `None` means
    "not checked" (e.g. `authentication_verified` is `None` for a
    connection type, like a public RSS feed, that has no credential
    concept at all -- never fabricated to `True` just because there was
    nothing to fail), paired with a human-readable `reason` for that
    SAME dimension when it's `False` or `None`, never for `True` (a pass
    needs no explanation)."""

    dependency_installed: Optional[bool]
    dependency_installed_reason: Optional[str] = None
    credentials_configured: Optional[bool] = None
    credentials_configured_reason: Optional[str] = None
    authentication_verified: Optional[bool] = None
    authentication_verified_reason: Optional[str] = None
    target_readable: Optional[bool] = None
    target_readable_reason: Optional[str] = None
    required_fields_available: Optional[bool] = None
    required_fields_available_reason: Optional[str] = None

    @property
    def fully_ready(self) -> bool:
        """`True` only when every dimension that was actually checked
        (not `None`) came back `True` AND at least one dimension was
        checked at all -- an all-`None` `ProbeResult` (nothing checked)
        is honestly NOT ready, never vacuously `True`."""
        checked = [
            self.dependency_installed,
            self.credentials_configured,
            self.authentication_verified,
            self.target_readable,
            self.required_fields_available,
        ]
        if all(v is None for v in checked):
            return False
        return all(v is not False for v in checked)


@dataclass(frozen=True)
class PollBounds:
    """Bounds on one `poll()` call -- always explicit, never an
    unbounded "fetch everything new" call."""

    #: Hard cap on how many NEW items a single poll may return.
    max_items: int = 50
    #: How many of the most-recently-seen items to RE-fetch/re-check on
    #: every poll (bounded overlap), to catch a source's delayed
    #: publication or a silent edit to something already seen -- see
    #: this module's own docstring. `0` disables overlap entirely
    #: (an explicit operator choice, not this contract's default).
    overlap_count: int = 3


@dataclass(frozen=True)
class Observation:
    """Mirrors `app/db.py`'s `source_observations` table column-for-
    column -- see that table's own CREATE TABLE comment for what each
    field means. This is the one shape every `probe`/`fetch`/`poll`/
    `normalize` implementation built against this contract produces,
    regardless of backend."""

    # -- Identity --
    platform: str
    original_item_id: str
    connection_id: Optional[str] = None
    provider_id: Optional[str] = None
    source_id: Optional[str] = None
    source_namespace: Optional[str] = None
    canonical_url: Optional[str] = None

    # -- Revision --
    revision_identifier: Optional[str] = None
    observation_kind: str = "retrieved"  # created | edited | deleted | retrieved
    content_hash: Optional[str] = None
    revision_seq: int = 1

    # -- Timing --
    source_authored_at: Optional[datetime] = None
    source_updated_at: Optional[datetime] = None
    first_observed_at: Optional[datetime] = None
    retrieved_at: Optional[datetime] = None
    timestamp_origin: Optional[str] = None  # "source_reported" | "retrieval_time"
    timestamp_uncertain: bool = False

    # -- Content --
    completeness: str = "unknown"  # app.notification_bridge.ContentCompleteness value
    extracted_text: Optional[str] = None
    attachment_refs: list[dict] = field(default_factory=list)

    # -- Provenance --
    adapter_name: str = ""
    backend: Optional[str] = None
    parser_version: Optional[str] = None
    retrieval_method: Optional[str] = None
    correlation_id: Optional[str] = None
    acquisition_run_id: Optional[str] = None

    # -- Disposition --
    purpose: str = "research"  # research | backfill | signal_candidate
    eligibility_state: str = "not_eligible"
    rejection_reason: Optional[str] = None


class SourceAdapterContract(abc.ABC):
    """The four-call adapter boundary (see this module's own docstring).
    `connection`/`source_reference`/`checkpoint`/`raw_result` are typed
    `Any` deliberately -- each concrete adapter defines what its own
    `connection` shape (e.g. a `dict` row from `app/db.py`'s
    `connections` table, or a bare config object for a test) and
    `checkpoint` shape (an opaque, adapter-owned JSON-serializable value)
    actually are; this contract fixes the CALL SHAPE, not every
    backend's internal representation."""

    @abc.abstractmethod
    async def probe(self, connection: Any) -> ProbeResult:
        """Readiness check -- must never raise for an ordinary not-ready
        condition (a missing dependency, an unreachable target); those
        are real, honest `ProbeResult` dimensions, not exceptions. An
        exception here means something genuinely unexpected happened
        while probing itself."""

    @abc.abstractmethod
    async def fetch(self, connection: Any, source_reference: Any) -> Observation:
        """Retrieve and normalize exactly ONE explicitly identified
        item."""

    @abc.abstractmethod
    async def poll(
        self, connection: Any, checkpoint: Any, bounds: PollBounds
    ) -> tuple[list[Observation], Any]:
        """One bounded batch. Returns `(observations, next_checkpoint)`
        -- `observations` is `[]` (never `None`) when genuinely nothing
        new was found this cycle; `next_checkpoint` is the PROPOSED next
        value (the caller persists it, see this module's own
        docstring)."""

    @staticmethod
    @abc.abstractmethod
    def normalize(raw_result: Any) -> Observation:
        """Backend-specific raw output -> `Observation`. Raises
        `ObservationNormalizationError` for malformed/unexpected input
        -- never guesses."""
