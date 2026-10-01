# Changelog

All notable changes to signal-copier, reconstructed from the real commit
history on `claude/signal-copier-redesign`. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/en/1.0.0/); this project
does not yet cut versioned releases (see `docs/process/RELEASE.md`), so
entries are grouped by theme and rough chronological wave instead of by
version number. Newest wave first.

## [Unreleased] — Track 44: mutation-testing scope widened to the position-lifecycle state machine

Per the same explicit "mutation covering needs to cover every module"
instruction Track 43 acted on, widened `pyproject.toml`'s `[tool.mutmut]`
scope further to `app/lifecycle/manager.py` (the broker-agnostic managed-
position lifecycle state machine) and `app/lifecycle/close_arbiter.py`
(the single serialization/oversell-prevention authority every exit
funnels through) -- the next highest-financial-risk slice after
`app/auth.py` and Track 43's `app/engine.py`. `only_mutate` now also
lists both files; `pytest_add_cli_args_test_selection` adds
`tests/test_lifecycle_manager.py`, `tests/test_close_arbiter.py`,
`tests/test_pro06_stop_amend_on_partial_exit.py`, and
`tests/test_pro02_activation_and_time_exit.py` (the two modules' own
direct unit-test files plus the targeted regression suites for the
specific financially-dangerous behaviors mutation testing flagged).

### Added
- `tests/test_close_arbiter.py`: `close_arbiter.py`'s `snapshot()`/
  `restore()` round trip had NO test at all before this -- a dict-key-
  rename regression in `snapshot()` would silently break every real
  `PositionLifecycleManager` save/restore round trip, with no other
  safety net given this module's own documented "no startup
  reconciliation" gap. Also added: `halt()` preserving the real halt
  reason (not discarding it), `reserve(0)`'s deliberate always-succeeds
  no-op semantics, the `_EPSILON` float-tolerance boundary on both
  `reserve()`'s oversell guard and `_check_invariant`'s reserved-exceeds-
  owned check, and -- the most financially meaningful survivor --
  `_check_invariant`'s SECOND halt branch (`reserved > owned`, independent
  of the `owned < 0` branch) actually halting rather than silently
  no-op'ing when a confirmed-owned observation drops below an outstanding
  reservation without going negative (e.g. a corrected/decreased entry
  fill landing while an exit's reservation is still open). 10/10 new
  tests pass against current code; no production code changed.
- `tests/test_lifecycle_manager.py`: three new tests closing real
  mutation-testing survivors in `request_exit` -- (1) the guard refusing
  a second exit while a prior one's remainder is unresolved (`lifecycle
  .pending_exit is not None and not ...remainder_resolved`) had no test
  driving a genuine second `request_exit` call while the first was still
  PENDING; a dropped `not` here would let two broker writes race for the
  same shares, exactly the oversell this module exists to prevent. (2)
  `request_exit` with `quantity=0` had no test; the `requested <= 0`
  boundary must reject outright rather than fall through to reserving/
  submitting a real 0-share broker order. (3) The final stop-restore
  gate (`had_stop and lifecycle.stop.desired_price is not None`) had no
  test distinguishing it from the `or` mutation, which would attempt a
  fresh stop placement on every qualifying exit even for a position
  whose initial protection already failed (that recovery is deliberately
  `retry_unprotected_positions`'s job, not every exit's). 3/3 new tests
  pass against current code; no production code changed.

### Mutation-testing results and scope limitation (environment)
`close_arbiter.py` (120 mutants): baseline 88 killed / 32 non-killed;
after the new tests above, 110 killed / 10 non-killed (confirmed via
isolated per-mutant reruns after the earlier aggregate run's cache was
repeatedly lost to the same shared-container contention Track 43
documented -- see below). The 10 remaining are documented, not chased:
2 cosmetic default-field mutations (`halt_reason`'s unread default value
when never halted), 1 unreachable-by-construction argument swap inside
`_reserve_locked`'s success path (which can never itself violate the
invariant), 1 float-precision-fragile `<`/`<=` boundary mutation on the
owned-negative check (practically untestable deterministically with
floats, and no more permissive than the original), 2 cosmetic string-
wording mutations the existing assertions correctly don't pin down, and
4 "no tests" mutants in `_TransactionOps.release` -- genuinely unused
by any current production call site (`manager.py` has no `tx.release(`
call), not a production gap.

`app/lifecycle/manager.py` (2432 mutants, far larger): a full clean
aggregate `mutmut run` was attempted twice and did not survive to
completion either time -- the shared container's `.mutmut-cache`/
`mutants/` state was lost mid-run both times (a `FileNotFoundError` on
`mutants/app/lifecycle/manager.py.meta` the second time), consistent
with the same heavy concurrent multi-session load Track 43 already
documented for `app/engine.py` on this box. One full run DID complete
before the first corruption and gave a real baseline -- 837 killed /
1021 survived / 574 no-tests -- which this track used to prioritize a
manual, sampled review of the highest financial-risk functions
(`request_exit`, `resolve_pending_exit`, `resolve_pending_entry`,
`on_stop_filled`, `check_duplicate_exit`, `_apply_exit_fill`,
`_restore_stop_coverage`: 347 survivors across these 7 alone) rather
than attempting every one of the ~1000 survivors. The 3 genuine bugs
found there are fixed above (each individually confirmed killed via an
isolated `mutmut run <mutant-id>` rerun, since the aggregate score
couldn't be re-confirmed). Two other real findings were investigated and
found to be already covered elsewhere rather than true gaps: `resolve_
pending_entry`'s `has_unresolved_exit` guard (dropped `not`) is exercised
end-to-end by `tests/test_5e91e78_lifecycle_composition.py::test_entry_
growth_does_not_rearm_over_an_unresolved_exit` (F02), just not inside
this track's narrower, faster test selection; `check_duplicate_exit`'s
`age_seconds < 0`/`> WINDOW` boundary mutations (`<=`/`>=`) are real but
impractical to pin to an exact float instant deterministically and have
negligible real-world impact at that precision. The vast majority of the
~1021 baseline survivors in `manager.py` are `OrderResult` field-level
mutations (`account_id=None`, `signal_id="XXXX"`, message wording) on
error/rejection branches whose exact field values this suite correctly
doesn't assert on (only `.status`) -- not chased here, consistent with
Track 39/43's own precedent of triaging rather than chasing every
survivor to zero. A full `manager.py` aggregate score remains unverified
by this track; a future run on an unloaded box (or in CI) can produce it
using the scope already committed.

`ruff check .` and the CI-scoped `mypy` command both clean on this
branch. Full `pytest -q` re-verified after these additions.

## [Unreleased] — Track 43: mutation-testing scope widened to app/engine.py

Per the explicit instruction that mutation coverage must eventually cover
every module, widened `pyproject.toml`'s `[tool.mutmut]` scope (Track 39's
own comment already named this as the obvious next step) to `app/engine.py`
-- the core signal-processing/order-routing engine, financially the
highest-risk module in the service after owner auth. `only_mutate` now
also lists `app/engine.py`; `pytest_add_cli_args_test_selection` now also
lists every test file that imports `SignalCopierEngine` directly (44
files, 372 tests total), identified via `grep -rl "from app.engine"
tests/`.

### Added
- `tests/test_p0_5_close_reconciliation.py`: direct unit tests for
  `_positions_reconcile` -- the pure tolerance-comparison function
  `_reconcile_before_plain_close` uses to decide whether a fresh broker
  position readback "matches" this service's locally tracked quantity
  before a plain-account close is allowed to proceed. Previously only
  exercised indirectly, through whichever specific values the
  reconciliation-flow tests happened to use -- no test asserted the
  boundary itself. New tests cover: exact match, the pure-absolute-
  tolerance boundary at zero, the tolerance correctly SCALING with
  magnitude (a difference that fails at small positions must pass at
  large ones, and vice versa), sign-symmetry (a broker position below
  local by some delta must be rejected identically to one above by the
  same delta -- catches a mutation that compares the signed difference
  instead of its absolute value), and that the boundary is inclusive
  (`<=`, not `<`). All 6 pass against current code.

### Known limitation (environment, not scope)
A full `mutmut run` against this widened scope did not complete within
this session: the stats-collection pass (one coverage-instrumented run of
all 372 selected tests, needed before any individual mutant can be
checked) did not finish after 12+ minutes of real wall-clock time, with
the shared container's load average measured at 11-13 on 4 cores (several
other agent sessions running their own test suites/mutmut concurrently on
the same machine) -- confirmed via repeated process/CPU-time sampling
that the run was genuinely progressing, just starved for CPU, not hung.
Rather than report a fabricated or guessed mutation score, the run was
stopped and this track instead did a complete manual read of
`app/engine.py` (all ~3100 lines) prioritized by financial risk (close-
signal resolution, the P0-5 reconciliation tolerance math above, Track 18
provider-ownership gating, E03 capital/risk admission gates, the AUD-01
distinct-field quantity model, command-ledger idempotency) against the
already-extensive existing test files for each. The widened
`[tool.mutmut]` scope is left in place (not reverted) so a future run --
on an unloaded box, or in CI -- can pick up from here and produce the
real mutation score; see `docs/state/PROGRESS.md` for the full note.

## [Unreleased] — Track 45: widen mutation-testing scope to writer_lease, command_ledger, reconciliation (2026-10-01)

Per explicit instruction ("mutation covering needs to cover every
module"), widened pyproject.toml's `[tool.mutmut]` scope (previously
app/auth.py only, see its own C31 comment) one deliberate step further
to the next three highest financial-risk modules: `app/writer_lease.py`
(cross-process/cross-host single-writer fencing), `app/command_ledger.py`
(the idempotent, pre-effect financial-command ledger), and
`app/reconciliation.py` (fill-confirmation reconciliation against
PENDING orders and managed-lifecycle pending exits) — see pyproject.toml's
own comment for the full per-file mutation-score breakdown and what was
fixed vs. disclosed as a residual.

### Added
- `tests/test_p0_2_command_ledger.py`: 11 new direct unit tests for
  `app/command_ledger.py`'s classification/fingerprint helpers
  (`classify_order_result`, `compute_fingerprint`,
  `ambiguous_evidence_for_exception`, `classify_optional_order_result`),
  which had NO direct tests at all before this — every branch was only
  ever exercised indirectly through `PaperBroker`, which always returns
  FILLED. Closes the audit's own named exposure case (a PENDING result
  with no broker_order_id must land UNKNOWN_AMBIGUOUS with an EMPTY
  `remote_identifiers`, not a stray `{"broker_order_id": None}`) and the
  idempotency-key fingerprint's order-independence guarantee
  (`sort_keys=True`).
- `tests/test_writer_lease_fencing.py`: 6 new tests. The most significant:
  `WriterLeaseGuard.renew()`'s happy path had never been exercised by any
  existing test (only the already-fenced-raises case was) — an `is None`
  -> `is not None` mutation there makes `renew()` always raise
  `FencedOutError` once a token is held, which would self-fence a
  genuinely live, current writer's own heartbeat renewal. Also added:
  `is_expired`'s exact-expiry-boundary case, and `holder_id`'s
  site-id-prefix format.
- `tests/test_reconciliation.py`: a FILLED **SELL**-side order's sign in
  `_correct_position` had never been tested (only REJECTED-SELL and
  FILLED-BUY were) — a `+delta`/`-delta` mix-up there would move a SELL
  position's correction in the WRONG direction instead of truing it up.
- `tests/test_pending_fill_reconciliation_integration.py`: a pass with
  TWO pending managed entries, where the first hits an early
  `continue` (its broker isn't registered with the reconciler) and the
  second genuinely confirms FILLED — proves the first's early exit
  doesn't silently stop the loop before reaching the second (a
  `continue` -> `break` mutation in `_reconcile_pending_entries` would
  do exactly that, leaving every account after the first one's own
  unresolvable entry unreconciled for the rest of that pass).
- `tests/test_5e91e78_lifecycle_composition.py`: a managed close that's
  flatly REJECTED by the broker with zero new fill (same, already-known
  zero progress) must still call `resolve_pending_exit` to restore the
  stop and release the reservation — `_reconcile_pending_exits`'s guard
  requires BOTH not-terminal AND no-new-progress together; an `and` ->
  `or` mutation there would leave a managed position stuck with no
  protective stop after its close attempt is rejected.
- `tests/test_exe01_exit_response_lost.py`: the zero-drop counterpart of
  the existing lost-exit-response test — an exit whose response was lost
  AND that genuinely never reached the venue at all (broker's own book
  unchanged) must resolve to exactly 0.0 filled, not a floor of 1.0 (a
  `max(0.0, ...)` -> `max(1.0, ...)` mutation in
  `_reconcile_broker_positions` would fabricate a phantom 1-unit fill).
- `tests/test_b2_b6_reconciliation_and_lifecycle_previews.py`:
  `run_now()`'s `pending_exits_examined`/`pending_entries_examined`
  breakdown fields had never been exercised against a real
  `lifecycle_manager` with actual pending work queued (only an empty
  store was tested) — closes mutations that hard-code either field to
  `[]` regardless of real state, or flip `+`/`-` in the `orders_examined`
  sum.

Every new test above was individually, hand-verified to fail against its
exact target mutant (by hand-applying that mutant's diff and re-running
just that test) and pass against real code — a genuine kill, not merely
"passes against current code." No existing test was weakened or deleted.

### Known residual (disclosed, not chased to zero)
`app/reconciliation.py`'s ~130 remaining mutmut survivors are
predominantly cosmetic (`logger.info`/`logger.exception` argument and
string-literal mutations inside exception handlers, which the suite
correctly never asserts on) plus a smaller set of real but lower-severity
AUD-01 field-plumbing gaps (`confirmed_cumulative_fill`/
`applied_execution_delta`/`outstanding_possible_fill` passed as `None` or
a wrong literal to `correct_position_and_update_order_status`) and a
handful of analogous `continue`->`break` survivors elsewhere in
`_reconcile_broker_positions` not yet individually triaged. See
pyproject.toml's own comment for the full baseline numbers and
docs/state/PROGRESS.md for this track's verification status.

## [Unreleased] — Track 42: honest handling of a relay `"parked"` status

signal-portfolio-commercial's `POST /internal/relay/ingest-batch` used
to report `"applied"` for every event it didn't raise a named error
for, including a genuinely parked one — `app/relay_worker.py`
inherited that dishonesty by treating any such status identically.
Track 42 closed it on both sides; see the commercial repo's own
CHANGELOG.md for its half.

### Added
- `app/relay_worker.py`: `classify_parked_reason` — the real
  transient-vs-structural split over every named `parked_reason` the
  commercial relay can report (plus the unnamed sequence-gap case,
  `"sequence_gap_awaiting_predecessor"`). TRANSIENT (`fee_target_not_
  found`, `routing_outcome_target_not_found`, the sequence-gap case,
  and an `edit_without_resolvable_target` whose suffix is a real
  native-identity key): resolves itself, with no code change, once a
  correlated event arrives and the commercial side's own cascade
  applies it — left undelivered, polled again next cycle. STRUCTURAL
  (`unsupported_schema_version`, `unimplemented_event_type`,
  `source_event_kind_not_ledger_representable`,
  `manifest_generation_mismatch`/`manifest_metadata_mismatch`,
  `generation_rollback_detected`/`new_generation_requires_bootstrap`,
  an `edit_without_resolvable_target:missing_original_source_event_id`,
  and any reason this worker doesn't recognize): can never resolve by
  waiting or redelivery, only by a code change — marked terminally
  parked (new `export_events.terminal_park_reason`/
  `terminal_parked_at` columns) and excluded from every future poll,
  but **never** marked `delivered` (that column means "the commercial
  side genuinely applied this" and a structurally parked event never
  was).
- `app/db.py`: `SignalStore.mark_export_events_terminally_parked`,
  `terminally_parked_export_event_count`; `list_undelivered_export_
  events` now also excludes terminally-parked rows.
- `GET /health`: new informational-only `terminally_parked_export_
  event_count` field, same INT-040 "never gates `status`" treatment as
  `outbox_backlog_ok`.
- `RelayIngestResult`: new `transiently_parked_event_ids`/
  `terminally_parked_event_ids` fields.
- `tests/test_relay_worker.py`: the full `classify_parked_reason`
  taxonomy, and `run_once`'s real handling of both a transient and a
  structural `"parked"` response. `tests/test_export_outbox_ceiling.py`:
  the new health field.
- `alembic/versions/0035_add_export_events_terminal_park_columns.py` —
  new Alembic head `0035`. Caught by
  `tests/test_e01_alembic_migration_stamping.py`'s own CLI-upgrade-vs-
  bootstrap parity check: the new `export_events` columns had only
  been added via `app/db.py`'s `_COLUMN_MIGRATIONS` bootstrap path,
  with no matching numbered revision — now fixed, both paths produce
  the identical schema again.

### Fixed
- `app/relay_worker.py`'s `run_once` no longer treats a `"parked"`
  status (once the commercial side started reporting it honestly) the
  same as `"applied"` — it never marks a parked event delivered.

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

### Added
- Track 38: `tests/test_trk38_quantity_conservation_and_idempotency.py`,
  a Hypothesis stateful test extending C29's quantity-conservation
  machine (`tests/test_c29_hypothesis_quantity_conservation.py`) with
  full closes via the real engine `Signal(side=CLOSE)` entrypoint,
  re-entries after a close, and genuinely duplicate CLOSE signals (both
  the literal same signal.id, caught by SIG-01, and a different
  channel_id/message_id within the TRK-27 duplicate-exit window). Proves
  across 75 generated event-orderings (25 steps each) that
  `confirmed_owned_quantity`/broker book/SignalStore position always
  agree, owned quantity never goes negative or exceeds cumulative
  entered-minus-exited, and a REJECTED duplicate close never changes
  quantity anywhere. Supplements, never replaces, C29 and
  `tests/test_trk27_managed_exit_duplicate_episode.py`'s two hand-picked
  scenarios. No bug found; all invariants held across every generated
  case.

### Fixed
- Track 40: `GET /connections/{connection_id}/cost-summary`'s `since`
  query-param and `POST /connections/{connection_id}/cost-events`'s
  `occurred_at` body field were both parsed with
  `datetime.fromisoformat(...)` with no effective guard against a
  malformed value -- the GET route had no try/except around it at all,
  and the POST route parsed it BEFORE entering its own
  `try/except ValueError: raise HTTPException(422, ...)` block, so that
  existing except clause could never actually catch it. Both were
  unhandled 500s on adversarial input, found by extending
  tests/test_c30_schemathesis_api_fuzzing.py's coverage to these newer
  routes (Schemathesis generated the value `"Subject"` for `since` and
  reproduced it immediately). Fixed to match this file's own extensive,
  pre-existing "malformed input must 4xx, not 500" convention: the GET
  route now has its own `try/except ValueError -> 400`, and the POST
  route's parse moved inside its existing try block. Regression tests:
  `test_cost_summary_route_rejects_a_malformed_since_param_not_500` and
  `test_record_cost_event_route_rejects_a_malformed_occurred_at_not_500`,
  both in `tests/test_connection_health_catalog_cost.py`.
- Track 40: `app/brokers/alpaca.py` passed `order.get("id")` (Alpaca's
  own JSON response field) straight into `OrderResult.broker_order_id`
  at every one of its four call sites with no type coercion at all --
  `OrderResult.broker_order_id` is declared `Optional[str]`
  (app/models.py) but, being a plain `@dataclass`, never enforces that
  at construction. A malformed/unexpected response shape (this
  adapter's own API contract drifting, or a corrupted response
  surviving `response.raise_for_status()`/`response.json()` without
  raising) whose `"id"` is some other JSON type used to reach
  `app/db.py`'s `save_order_result` completely unchanged and crash
  there with an opaque `sqlite3.ProgrammingError: type 'dict' is not
  supported` -- a real, reproduced unhandled exception NOT wrapped by
  `app/engine.py`'s own per-account `except Exception` (that one only
  wraps the `broker.place_order` call itself, not everything the
  engine does with its result afterward), so it could crash the entire
  `handle_signal` call. Fixed with a new `_coerce_broker_order_id`
  helper (`str(x) if x is not None else None`), the exact same
  discipline this codebase already uses for `app/sources/webhook.py`'s
  own externally-sourced `message_id` field. Found by this track's own
  fault-injection test, tests/test_c36_broker_submission_fault_
  injection.py.

### Added
- Track 40 (fuzzing/fault-injection extension): tests/test_c30_
  schemathesis_api_fuzzing.py's `SAFE_PATHS` now also covers `GET
  /system/readiness`, `/export-events`, `/export-events/{event_id}`,
  `/mobile-devices` + its sub-paths, and `/connections/catalog`/
  `/connections/health-summary`/per-connection health/checkpoint/cost
  routes -- every read-only GET route added by recent tracks that
  C30's own schema-wide pass had not yet picked up. A new, separate
  tests/test_c37_webhook_schemathesis_fuzzing.py adds the one
  deliberately-excluded-from-C30 route it is actually most worth
  fuzzing: `POST /webhook/{source_name}`, the single most adversarial-
  input-exposed endpoint in this service (untrusted external payloads,
  no owner session). A new tests/test_c36_broker_submission_fault_
  injection.py extends C32's own real-httpx-transport-fault approach
  from `AlpacaBroker.get_order_status` to `AlpacaBroker.place_order`
  itself (the real order-submission call) -- connect/read/connect
  timeouts, and a genuinely malformed-but-200 success response -- and
  is what found the `broker_order_id` type-coercion bug fixed above.
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
