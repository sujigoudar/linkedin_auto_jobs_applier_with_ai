# Changelog

All notable changes to signal-copier, reconstructed from the real commit
history on `claude/signal-copier-redesign`. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/en/1.0.0/); this project
does not yet cut versioned releases (see `docs/process/RELEASE.md`), so
entries are grouped by theme and rough chronological wave instead of by
version number. Newest wave first.

## [Unreleased] — Track 39: mutation-testing pass

Ran `mutmut` against the existing test suite for the financially
load-bearing modules named in the track brief (`app/risk.py`,
`app/capital_allocator.py`, `app/quantity.py`, the AUD-01 quantity
fields in `app/db.py`, `app/routing.py`), scoped per-module to the
dedicated test file(s) that exercise each one, to measure whether those
tests actually CATCH a real injected bug rather than merely not
failing against current code.

### Added
- `tests/test_risk_sizing.py`: `app/risk.py`'s `size_for_account`/
  `symbol_for_account` had no direct unit test before this (only
  indirect, end-to-end coverage) — closes the two real survivors this
  found: the `else 1.0` default-quantity fallback, and
  `symbol_for_account`'s symbol-translation lookup.
- `tests/test_routing_evaluate.py`: `app/routing.py`'s `RoutingConfig
  .evaluate`/`destinations_for` and the static YAML/DB config loaders
  (`load_routing_config`/`load_routing_config_from_store`) had no
  dedicated direct unit test file before this (only incidental coverage
  through dozens of other tests' own account/routing setup). Closes
  several real survivors, most seriously two `continue` → `break`
  mutations in `evaluate`'s per-rule loop that would silently stop
  evaluating every rule after the first one whose source/symbol_filter
  doesn't match the current signal — undetected by every existing
  config in this suite because none of them happens to put a
  non-matching rule before a matching one. Also closes governance-field
  (`managed_lifecycle`/`management_recipe`/`qualification_level`/
  `exclusive_writer_qualified`) parsing gaps in both config loaders.
- `tests/test_capital_allocator.py`: added coverage for
  `confirmed_open_notional`'s per-symbol loop (a flat/closed position
  ordered before an open one), `owner_wide_exposure` (previously
  untested at all — wrong sign on pending-reservation addition,
  wrong/dropped account-id argument, dropped `unresolved_symbols`), and
  `admit`/`reserve_locked`'s `signal_id` persistence and `+=`
  accumulation (a plain `=` bug there would only show up on a THIRD
  sequential admission to the same account, which no existing test
  exercised).
- `tests/test_trkq1_quantity_breakdown.py` / `tests/test_aud01_distinct_quantity_model.py`:
  added coverage for `quantity_still_executable_for`'s `None`-confirmed-
  fill and exactly-zero-remainder boundaries, `quantity_breakdown_for_lifecycle`'s
  `requested_quantity` field (previously never asserted), and
  `SignalStore.get_outstanding_possible_fill`'s SELL-side sign and
  zero-remainder exclusion (every existing scenario in that file only
  ever used BUY).

See `docs/state/PROGRESS.md` for the full per-module mutation scores,
root-cause analysis of every survivor, and which ones were triaged as
equivalent mutations rather than fixed.

## [Unreleased] — P0 audit-response foundation wave

Fixes responding to an external release-readiness audit of the trading
engine's data integrity and operational-safety guarantees.

### Fixed
- Track 36: `docs/state/PROGRESS.md`'s long-standing "`signal_platform_
  contracts` is stale relative to Track 14 and Track 22/23" note was
  re-investigated and found itself stale — it had been carried forward
  unverified across Tracks 24–35, past the point (Track 29) where it
  stopped being accurate. Track 14's one contract-boundary-relevant
  field (`SourceIdentity.source_catalog_id`) was already added and wired
  end-to-end; the rest of Track 14's vocabulary is operator-UI state
  signal-portfolio-commercial never consumes. Track 22/23's AUD-01
  quantity fields are internal `orders`-table bookkeeping, not fields of
  the cross-service `EXECUTION_APPLIED` event, whose one real
  cross-boundary fact (`filled_quantity`/`filled_price`) was already a
  required, typed field, built and consumed through typed models (never
  a loose dict) on both sides. No contract or application code changed;
  see `docs/state/PROGRESS.md` for the full investigation.

### Added
- Track 33: `GET /export-events` and `GET /export-events/{event_id}` --
  the first live, read-only HTTP surface over the `export_events` outbox
  (`SignalStore.list_export_events`/`get_export_event`, app/db.py).
  Previously inspecting the outbox's contents required a direct DB
  connection; `export_outbox_backlog`'s aggregate count was the only
  thing exposed over the running API. Same `require_owner_read`/bounded-
  `limit`/newest-first convention as `GET /signals`/`GET /orders`, with
  optional `source_stream`/`delivered` filters. Returns the real typed
  `signal_platform_contracts` payload (subject/quantities/prices) as-is
  -- it carries no credentials or secrets (those live only as
  `credential_reference` env-var names) and is the same content the
  owner already sees via `GET /signals`/`GET /orders`.
- Track 33: `register_source` (app/db.py) now detects a new source's
  `url_or_reference` (e.g. an RSS `feed_url`) already matching another
  enabled `sources` row's and surfaces a non-blocking `duplicate_url_
  warning` on the returned dict. Deliberately NOT a hard rejection or a
  DB-level unique constraint -- two sources legitimately sharing one
  feed_url (e.g. a `research`-purpose route and a `signal_candidate`-
  purpose route, or a PRIMARY/RECONCILIATION pair -- see `SourceRole`'s
  own docstring in app/provider_catalog.py) is a real, intentional use
  case a hard constraint would break. Closes the gap where two sources
  silently polling the identical feed could each independently emit a
  `Signal` for the same real-world article with no detection at all.
- Track 29: `SourceIdentity.source_catalog_id` (`signal_platform_contracts`
  v1.1.0) -- a new, additive, optional field making Track 14's
  Provider/Source/Connection catalog's `sources.id` expressible in the
  shared cross-service event envelope, distinct from the pre-existing
  `source_provider_id`/`source_channel_id`. `app/models.py`'s `Signal`
  gained the matching `source_catalog_id` field (also optional,
  default `None`); `app/export_events.py` carries it through to
  `SOURCE_RECEIPT`/`SOURCE_EVENT` envelopes whenever it's set.
  `app/sources/rss_source.py` (Track 24) is, today, the only adapter
  that actually populates it, from its own known Track 14 `sources.id`
  -- every other adapter leaves it honestly `None`, not fabricated.
- `command_ledger` table: a durable, pre-effect record of every broker
  command, with idempotency-key dedup and `unknown_ambiguous`/fingerprint-
  mismatch handling (P0-2, `ae6a016`).
- Cross-process writer-lease fencing (`writer_lease` table,
  `WriterLeaseGuard`) and a manual, three-flag-confirmed `promote_cli`
  (P0-6, `55976eb`); see `docs/FAILOVER.md`.
- `GET /system/readiness`: 6 independent readiness dimensions plus a
  rollup derived only from them, replacing the previous folded green/red
  signal (P0-8, `9d22b32`).
- Per-exact-route live qualification ladder, separate from capability
  inference (`a5d5aec`).
- Plain-account CLOSE reconciliation against broker truth; explicit
  management-recipe declaration (`fe6dd76`).
- Escalation-only active phone-control retrieval (`app/phone_escalation.py`,
  Track 13): a per-provider `CapabilityState` lifecycle
  (`disabled` -> `shadow` -> `enabled`, owner-gated promotion), a
  hardcoded read-only `PhoneControlAdapter` action surface with no
  send/type/submit-capable method, a broker/banking `open_app` deny-list
  enforced structurally (raises, never just logs), and an
  `EscalationAttempt` audit ledger (`phone_escalation_configs`/
  `phone_escalation_attempts` tables, migration `0026`). No real device
  backend is wired yet — see ADR-0009 and `AdbPhoneControlAdapter`'s own
  docstring for the documented, not-yet-implemented ADB-based design.
- Track 24: a generic, transport-agnostic adapter boundary
  (`app/sources/adapter_contract.py` — `probe`/`fetch`/`poll`/`normalize`,
  `Observation`/`ProbeResult`/`PollBounds` dataclasses, a bounded
  retry-with-backoff helper) and the new `source_observations` table
  (migration `0034`, `SignalStore.record_source_observation`/
  `get_source_observations_for_source`), generalizing
  `notification_bridge_events`'s `content_completeness`/`content_hash`/
  `revision_seq` shape across transports. Wired to ONE real, testable
  example: `app/sources/rss_source.py`'s `RssSourceAdapter`, a direct
  `feedparser`-based RSS/Atom adapter with no dependency on Agent Reach
  or any external account/credential (X/Twitter is explicitly out of
  scope — it needs real cookies this environment doesn't have). Defaults
  every new RSS source to `purpose="research"` (never emits a `Signal`)
  unless explicitly marked a `signal_candidate` route, reusing the
  pre-existing `app.sources.article_classifier` pipeline (never a second,
  parallel trade-parsing grammar) only for an eligible, `COMPLETE`
  observation. Adds `sources.acquisition_checkpoint` for this adapter's
  own poll checkpoint (deliberately not a new `collectors` row — see that
  column's own comment in `app/db.py`). New `app/sources/url_safety.py`
  SSRF guard (`validate_public_fetch_url`, mirroring the approach the
  upstream Agent-Reach project's own `agent_reach/utils/url.py` takes,
  written independently) applied to any linked-article URL found inside
  a feed entry — never to the operator-configured feed URL itself. The
  pre-existing `"rss"` connection-catalog entry was confirmed to already
  be backed by a real adapter (`app.sources.website.WebsiteSource`'s FEED
  mode, not a placeholder) — no catalog change was needed; a new test
  exercises the TR-17 wizard's full three-call sequence against it.
- `TR-18`: Mobile devices (`#/trade/mobile-devices`) — the dashboard
  screen Track 20's `/mobile-devices*` REST surface never had. Lists
  every paired device's real health/battery/permission/last-heartbeat
  state (honestly `null` until that device reports it), drills into a
  device's per-app configuration (`GET /mobile-devices/{id}/apps`), edits
  `device_name`/`allowed_apps`/`blocked_apps` via `PATCH
  /mobile-devices/{id}`, and runs "Test App" via the existing
  `POST .../test` route, which this build always answers with an honest
  `status="unavailable"` (no physical Android/ADB backend exists). No
  unpair/revoke action is offered — the backend has none.
- `TR-19`: Provider catalog (`#/trade/provider-catalog`) — the dashboard
  screen Track 14/21's newer provider/source/connection catalog data
  model never had, so `TR-17`'s onboarding wizard previously had nowhere
  to send an operator to see what it just created (`TR-09` reads a
  different, older `ProviderConfig` model and never will). Lists
  providers from `GET /provider-catalog/providers`, with drill-down into
  a provider's sources (`GET /provider-catalog/providers/{id}/sources`)
  and each source's linked connection, showing `status`/
  `execution_eligibility`/`certification_state`/`health_state` exactly as
  the API returns them. `TR-17`'s post-creation success and
  partial-failure messages now link here instead of to the `TR-09`
  dead end.

### Changed
- Capital allocator: fail-closed sizing on a missing price, an
  unresolved-exposure block (instead of treating unknown exposure as
  zero), owner-wide + risk-basis admission gates (P0-3, `c6e4e7b`).
- Replaced optimistic PENDING-order position accounting with a
  distinct-field quantity model (`requested_quantity`,
  `confirmed_cumulative_fill`, `applied_execution_delta`,
  `outstanding_possible_fill`, `actual_remaining_ownership`) (AUD-01,
  `c88bb66`).

### Fixed
- Track 37: `app/main.py`'s `_evaluate_phone_escalation_for_event`
  (Track 13's phone-escalation gate) passed `covered_by_direct_source=
  False` to `evaluate_escalation` unconditionally, a documented stopgap
  from before Track 12's cross-transport correlation layer landed. It
  now computes this from Track 12's real correlation query
  (`SignalStore.find_correlation_candidates` + `app/signal_correlation
  .classify_candidate`, via the new `_resolve_direct_source_coverage`
  helper) whenever the escalation-eligible notification event has a
  parsed `Signal` to fingerprint from — `True` only for a real
  CORROBORATING candidate from a different transport, `False` when
  checked and none exists. For the genuinely structural case (no
  parseable content at all, e.g. a bare pointer notification), this is
  honestly reported as `None`/`"not_computable"` rather than guessed as
  `False` — see docs/KNOWN_ISSUES.md for exactly what remains
  uncomputable and why.
- Managed-lifecycle fills never built or exported a real `EXECUTION_APPLIED`
  envelope (`signal_platform_contracts.EventEnvelope`/
  `ExecutionAppliedPayload`) to the private export outbox — only
  TRK-22's own-`orders`-table quantity fields were populated. Since
  managed-lifecycle fills are "the majority of real trading activity"
  (see TRK-22's own entry below), this left signal-portfolio-commercial's
  `Book.PLATFORM` ledger and customer performance reports silently
  missing most fills, with no error and no failed test (a HIGH-severity
  cross-repo audit finding). Every real confirmed-fill point now builds
  and persists one, using the exact same `build_execution_applied_envelope`/
  `_build_export_envelope` machinery the plain-account path already used,
  never a fabricated quantity/price/fee: a synchronous managed entry or
  provider-CLOSE fill (`_handle_signal`'s managed branch, app/engine.py),
  a manual "Exit now"/"Flatten" fill (`close_position`), an
  asynchronously-confirmed managed entry or CLOSE
  (`OrderReconciler.reconcile_once`'s own lifecycle-owned-pending-order
  branch, which never reached the plain-account `_correct_position`/
  `_export_reconciled_fill` path at all), and a protective stop filling
  on its own with no engine/reconciler call site of its own
  (`PositionLifecycleManager.on_stop_filled`, via
  `_apply_exit_fill`/`_persist_self_initiated_exit`, which also gained a
  real `broker_order_id` threaded through from `request_exit`/
  `resolve_pending_exit`/the stop's own record — required by
  `build_execution_applied_envelope` and previously never set for a
  self-initiated exit's `OrderResult` at all). A managed CLOSE's `side`
  is resolved from the lifecycle's own `exit_side`/`plan.side`, never the
  literal `Side.CLOSE` some of these call sites already store in
  `orders.side` — `build_execution_applied_envelope` refuses that outright
  rather than silently mis-exporting it (TRK-23).
- Managed-lifecycle orders (`_handle_managed_entry`/`_handle_managed_close`)
  never populated `orders.applied_execution_delta`/`confirmed_cumulative_
  fill`/`outstanding_possible_fill`/`acknowledged_quantity` — leaving
  Track 16's `/positions/{symbol}/provider-allocations` and Track 18's
  `get_provider_position_ownership` blind to the majority of real trading
  activity (managed-lifecycle fills). Both methods now return a
  `_ManagedOrderOutcome` carrying AUD-01's distinct-field quantity model
  through to `save_order_result`, computed from the exact same
  FILLED/PENDING classification `_submit_order` already uses for the
  plain-account path, without a second, duplicate `record_fill` call
  (TRK-22).
- Broker/source fill data: genuine `0.0` fills silently collapsing to
  `None` (and, for Rithmic, silently bypassing a notional-exposure
  ceiling check) — `app/brokers/ibkr.py`, `app/sources/rithmic.py` (P0-9,
  `d363e79`).
- CI: guarded ccxt-dependent qualification tests and avoided importing
  ccxt in an HTTP-only test (`00665ce`); ignored `CVE-2026-49265`
  (oauthlib, no fix available for tweepy's pin) in `pip-audit` (`9d9e5a6`).
- Renumbered the writer_lease Alembic migration from `0014` to `0015`
  after `command_ledger` independently claimed `0014` first on the
  shared branch (`cdfee8b`) — see `docs/process/GIT.md`.
- TRK-27: managed-lifecycle exit idempotency only ever protected against
  a *concurrent* duplicate exit (`CloseArbiter.pending_exit`) — a
  genuinely duplicate EXIT signal for the same real-world event, with a
  different `channel_id`/`message_id`, arriving AFTER the first exit had
  already fully resolved, was caught by neither that guard nor SIG-01's
  signal-id replay guard, and was processed as a brand-new request
  against whatever remained of the position (safely refused when
  already flat, but with no durable record distinguishing that refusal
  from an ordinary error). `PositionLifecycleManager` now records each
  exit episode's identity the instant it closes and recognizes a
  same-position, same-window repeat as a duplicate (logged, REJECTED,
  no new broker order — never a fabricated FILLED replay, which would
  double-count the execution) — see ADR-0010 for the exact mechanism
  and its explicitly bounded scope (in-memory only; does not, and is
  not meant to, suppress a real exit after a real re-entry).
- TRK-27 (Finding 2): the qualification ladder's route key
  (`adapter_type`, `route_key`, `asset_class`) can't distinguish two
  distinct products (e.g. spot vs. perpetual) sharing one
  `account_id`/`route_key` — nothing previously stopped an operator from
  recording qualification history for both under the same route_key,
  which the live-routing gate (always checking one fixed `product_type`)
  could never correctly honor. `SignalStore.record_route_qualification`
  now refuses to record a second, different `product_type` for a
  route_key that already has qualification history under another one.

## Redesign waves — trading console, screens, and integration hardening

A large sequence of work rebuilding the operational dashboard and
closing integration gaps. Representative highlights (not exhaustive —
see `git log` for the full sequence):

### Added
- Shared design-system foundation: 3-level tokens, shared components,
  grouped nav (`3d8e43b`), replacing scattered "not tracked/unsupported"
  prose with capability-state badges (`d774653`).
- Redesigned `TR-01`..`TR-16` operational-readiness screens: KPI band and
  attention-required queue, per-position result-attribution waterfall,
  strategy/sleeve portfolio risk panel (correlation, co-drawdown,
  contribution, marginal risk), signal funnel and routing graph,
  granular capability/venue/reconciliation status, a tabbed policy
  editor, saved filter views, persisted backtest runs with a real
  research report, and real Chart.js visualizations throughout.
- Real order `purpose`/`family_id`, `PaperBroker` cash/fee tracking, and
  small derived aggregates (`25582fc`).
- Real append-only stop/target lifecycle event log; rolling stats +
  pairwise correlation (`app/statistics.py`).
- Real multi-stage execution-latency timestamp capture, and MAE/MFE
  excursion tracking on managed-lifecycle positions.
- Real max-drawdown/win-rate stats from real FIFO-lot equity/episodes
  (CU-06, `64d596d`).
- Real on-demand reconciliation trigger; real reduction/stop-change
  previews (TR-06/TR-03, `5e8d9e4`).
- Real cross-signal capital-sharing in the backtest replay (B7,
  `a26f917`).
- Revocable JWT sessions with a real jti-keyed denylist, fail-closed
  verification (`72efeae`).
- CURRENT+PREVIOUS dual-secret verification for secret rotation
  (`9ddc681`).
- Owner-gated historical message import/review workflow (TR-10,
  `0417411`).
- AD-18 evidence manifest export (`f752904`).
- Encoded the real routing/admission/fill outcome per `SOURCE_RECEIPT`
  (INT-027, `edbe3a8`).
- Real storage-ceiling/alerting policy for the export outbox (INT-040,
  `bb61055`).
- Six new broker/exchange-coverage adapters: Tradovate, OANDA,
  TradeStation, Tastytrade, Schwab (no sandbox), Robinhood (no sandbox,
  ToS-risk gated); multi-exchange ccxt support.
- NinjaTrader as a signal source (Python side real/tested; NinjaScript
  side reviewed but unverified — see `docs/KNOWN_ISSUES.md`).
- Real local sign-in/sign-up/verify/recovery system (ID-01/02/03,
  `e303af9`).
- INT-001 real single-command installation (compose + bootstrap,
  `777dee1`); INT-033 real-account single-writer enforcement (`3b8cf40`);
  INT-008/009 bootstrap snapshot/manifest mechanism (`b462a77`).
- Per-analyst P&L attribution, FIFO-lot method (INT-026, `d846626`).

### Fixed
- Order-dependent test flake (`asyncio.get_event_loop()` vs. the repo's
  `asyncio.run()` convention) found during full-suite verification
  (`9d22b32`).
- Legacy dashboard content bleeding through under `TR-0X` routes
  (`f8f4650`); gated the legacy dashboard behind
  `LEGACY_DASHBOARD_ENABLED` (default off, `1f74471`).
- `orders.submitted_at`/`protection_confirmed_at` missing from
  `_COLUMN_MIGRATIONS` (`e48ec15`).
- CI: real mypy union-attr narrowing (`c59cd97`); pinned the disposable
  Postgres cluster's superuser role (`2de2d7e`); declared `httpx` as a
  real dependency (`a06baac`); pinned a ruff ruleset and fixed 3 real
  findings in `signal-portfolio-commercial` (`78f87ab`); scoped the root
  test job around `signal-portfolio-commercial` too (`f999e50`); guarded
  several ccxt/IBKR/Rithmic-dependent tests with `importorskip`; fixed a
  subprocess import-boundary test that relied on an implicit cwd-based
  `sys.path` (`146935e`); fixed a smoke test reading a masked owner token
  from logs instead of a file (`43e1ac0`).
- Two more real deployment gaps found by actually running containers
  (`2c1079a`); `docker-compose`'s load of signal-copier's real `.env`,
  `SESSION_SECRET` wiring (`d6613bb`).
- Made `IBKR` host/port/client_id and ccxt exchange/sandbox real env
  vars (B1/B3, `396674f`).
- `EXECUTION_APPLIED` export for reconciler-confirmed fills (B5,
  `e53bc07`).
- Added `C38` (ruff lint + mypy type checking; Dependabot config,
  `2dfaa8e`); added `C07` outbound quota limiter (`aiolimiter`) for
  `app/context/*` (`5436f8d`).

### Security
- `DEP-01`: dropped container capabilities to `ALL`, `no-new-privileges`,
  read-only root filesystem, non-root image user (Dockerfile,
  `docker-compose.yml`).
- Loopback-only port binding by default, documenting the expectation of
  a reverse proxy in front for real deployment.

## Earlier integration work

Producer-generation binding (reject rollback and reused sequence slots,
INT-010, `b330d3d`); control-plane audit trail on every command endpoint
(S12 step 7, `89a32b0`); real FOLLOWER observation ingestion, idempotent
(S12 step 6, `7c29651`); Portfolio Lab source feed export/`SOURCE_RECEIPT`
projection (S12 step 5, `e89464f`); re-verification of the rights
registry and command authority against new integration boundaries
(INT-021/031, `77a6fef`).
