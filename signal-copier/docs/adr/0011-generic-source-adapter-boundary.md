# ADR-0011: A generic probe/fetch/poll/normalize adapter boundary, with an SSRF guard for untrusted embedded URLs (Track 24)

Status: Accepted
Date: 2026-10-01

## Context

An "Agent Reach"-adjacent integration brief asked for a generic
external-source-ingestion capability, so a new source adapter (starting
with RSS/public-web, and intended as the pattern future adapters follow)
would not have to be built as a one-off against each source's own
transport. Before building anything, the actual upstream
`Panniantong/Agent-Reach` project was inspected directly. The conclusion:
it adds nothing for RSS/public-web sources — it is a router that tells an
agent to call `feedparser`/a web-reader directly, not a content-reading
wrapper — and its only genuine value (avoiding Twitter/X's paid API) needs
real browser credentials unavailable in this environment. X/Twitter was
therefore left explicitly out of scope, and nothing in this codebase is
stubbed as if it works for X via this path.

This codebase already had `app.sources.base.SourceAdapter`
(`start`/`stop`/`on_signal`) — the push/poll-loop lifecycle every existing
source adapter implements, and startup wiring understands. That contract
has no shape for "probe this connection's readiness along multiple
dimensions," "fetch one explicitly identified item," or "poll a bounded
batch, proposing (not committing) the next checkpoint" — all of which a
generic, backend-agnostic adapter boundary needs in order to let a future
adapter (not just RSS) be built against the same four calls regardless of
what's behind them.

A second, related gap: any adapter fetching content from a source the
*operator* configured (a feed URL, a connection's own target) can trust
that URL the same way every other adapter's operator-configured target
already is (see `app/connection_catalog.py`'s own "never guesses or scans
arbitrary URLs on the operator's behalf" convention). But content fetched
*from* that trusted source can itself contain URLs the feed's *author*
controls — e.g. a linked-article URL inside an RSS entry's own body — and
fetching those blindly is a real SSRF vector (internal services, cloud
metadata endpoints, loopback) with no existing guard anywhere in this
codebase.

## Decision

Two new, paired pieces, with no dependency on Agent Reach or any external
account/credential:

- **`app/sources/adapter_contract.py`**: `SourceAdapterContract`, a NEW,
  parallel contract alongside (never a replacement for)
  `app.sources.base.SourceAdapter`. Four calls, each doing exactly one
  thing:
  - `probe(connection) -> ProbeResult` — readiness along multiple
    independent dimensions (dependency installed, credentials configured,
    authentication verified, target readable, required fields available),
    each `True`/`False`/`None` ("not checked," never fabricated to
    `True`), deliberately NOT the same shape as
    `app.connections.compute_connection_health`'s single
    `ConnectionHealthState` enum — a probe can say multiple independent
    things at once that one enum cannot represent.
  - `fetch(connection, source_reference) -> Observation` — one explicitly
    identified item, never a scan/guess of "whatever's new."
  - `poll(connection, checkpoint, bounds: PollBounds) -> (list[Observation], next_checkpoint)`
    — a bounded batch (`PollBounds.max_items`), with an explicit,
    configurable `overlap_count` (re-checking the last N already-seen
    items on every poll, to catch a source's delayed publication or a
    silent edit) rather than a hardcoded magic number. The caller commits
    the proposed checkpoint only after successfully processing the batch
    — same "caller commits, contract proposes" split
    `app.website_collectors` already uses for its own checkpoints.
  - `normalize(raw_result) -> Observation` — backend-specific raw output
    (e.g. one `feedparser` entry dict) into this module's own
    `Observation` dataclass, raising `ObservationNormalizationError` for
    anything malformed rather than silently guessing (hard rule 11: never
    fabricate data).

  `Observation` mirrors the new `source_observations` table
  (`app/db.py`) column for column, so the in-process dataclass and the
  persisted row can never silently drift apart. It reuses
  `app.notification_bridge.ContentCompleteness`'s exact five states
  (COMPLETE/PARTIAL/POINTER_ONLY/TRUNCATED/UNKNOWN) for `completeness`
  rather than inventing a new vocabulary, and adds its own disposition
  gate — `purpose` (research/backfill/signal_candidate) and
  `eligibility_state` — so an observation becomes eligible to ever become
  a real `Signal` only when an adapter explicitly classifies it
  `signal_candidate` and its source was explicitly operator-configured
  for that.

  Every network-call-capable function in this module (and in
  `app/sources/rss_source.py`, its one real implementation,
  `RssSourceAdapter`, which implements both this contract and the
  pre-existing `SourceAdapter`) follows the same network discipline: an
  explicit `httpx` timeout, a response-size cap (`MAX_RESPONSE_BYTES`,
  5 MiB), and `retry_with_backoff` — bounded retries (3 attempts) with
  exponential backoff and jitter, re-raising the last exception once
  exhausted rather than swallowing a persistent failure. This is a
  genuinely new, small, local utility: the codebase was grepped first for
  an existing retry/backoff helper and had none (every existing adapter
  and broker does either a single unretried `httpx` call, or layers its
  own unrelated outbound-quota rate limiting via `aiolimiter`).

- **`app/sources/url_safety.py`**: `validate_public_fetch_url`, an SSRF
  guard applied ONLY to a URL this codebase did not itself choose — content
  embedded inside a fetched item, never a connection's own
  operator-configured `feed_url`/`article_list_url` (those remain trusted
  exactly as before). It mirrors the approach Agent-Reach's own
  `agent_reach/utils/url.py` takes — reject non-`http(s)` schemes, reject
  embedded userinfo, resolve the hostname and reject any address that
  isn't globally routable (via `ipaddress`'s own `is_global`, checked
  against EVERY resolved address — fail closed on DNS rebinding risk, not
  just the first one), reject known metadata/internal hostnames
  (`169.254.169.254`, `metadata.google.internal`, `localhost`,
  `.local`/`.internal`/`.lan` suffixes) — written independently for this
  codebase (different license, no code copied) rather than importing or
  vendoring that project's module, consistent with taking Agent Reach as
  inspiration, never as a dependency.

## Consequences

- Future source adapters (beyond RSS) have an established, tested pattern
  to build against: implement `SourceAdapterContract`'s four calls,
  persist via `source_observations`, and run any URL embedded in fetched
  content (not the operator-configured target itself) through
  `validate_public_fetch_url` before fetching it.
- `SourceAdapterContract` and `SourceAdapter` now coexist deliberately.
  `RssSourceAdapter` implements both because it needs to: the existing
  codebase's startup wiring only understands `SourceAdapter`'s lifecycle,
  while the richer four-call shape is what this contract's own callers
  (and any future adapter built the same way) use. A future adapter that
  has no need for the old lifecycle hook could implement
  `SourceAdapterContract` alone — this ADR does not require every adapter
  to implement both.
- X/Twitter ingestion remains unbuilt and is explicitly not a gap this
  contract claims to close — it was evaluated and intentionally deferred
  for lack of real credentials, documented in
  `app/sources/adapter_contract.py`'s own module docstring, not silently
  dropped.
- `validate_public_fetch_url` is scoped narrowly on purpose: it guards
  content-embedded URLs only. A future adapter that introduces a NEW kind
  of untrusted, non-operator-configured URL (not just "a link found inside
  fetched content") should extend this same guard rather than add a
  parallel one, to avoid two independently-maintained SSRF checks drifting
  apart.
