# Current progress snapshot

As of `HEAD` = `4a24d07` on `claude/signal-copier-readiness-sm44tr`
(2026-10-01). This is a snapshot, not a roadmap — update it when the state
it describes actually changes. This file was previously stale for an
extended period (it referenced an old branch, `claude/signal-copier-
redesign`, and alembic head `0015`, long after both had moved on), and was
stale again after that (alembic head `0033`/2057 passed, and three items
below listed as not-yet-landed that had in fact landed as Track 25/27) — if
you find it stale again, fix it rather than working around it.

## What wave this is

The branch completed a long sequence of numbered "Tracks" (1 through 23)
building out the Provider/Source/Connection data model, a 10-source signal
ingestion architecture (webhook, Telegram, Slack, Twitter/X, email, website,
Android notification bridge, Whop, SMS, WhatsApp), an onboarding wizard, and
the AUD-01 distinct-field quantity model across both plain-account and
managed-lifecycle order paths. That work, plus a full end-to-end audit wave
(financial/tenant-isolation/UX review agents + two live Playwright runs
across both signal-copier and signal-portfolio-commercial, see the git log
for the "Merge Track NN" commits and the audit-findings document linked from
that session), is complete and verified as of this HEAD.

An "Agent Reach" integration brief was evaluated and dispatched as
Tracks 24–25 — after inspecting the actual upstream
`Panniantong/Agent-Reach` project, the conclusion was that it adds nothing
for RSS/public-web sources (it's a router that tells an agent to call
`feedparser`/a web-reader directly, not a content-reading wrapper) and its
only genuine value (avoiding Twitter's paid API) needs real credentials
unavailable in this environment. The resulting plan — build signal-
copier's own deterministic adapter boundary (probe/fetch/poll/normalize)
and a real RSS source adapter with no dependency on Agent Reach (Track 24),
plus the two UI gaps the audit found: a Mobile Devices dashboard screen for
Track 20's existing backend, and a screen listing Track 14's provider-
catalog rows (Track 25) — has since landed; see "What's genuinely landed
and working" below. Tracks 26–29 (managed-lifecycle exit-idempotency
guard/ADR-0010, and SourceIdentity extensions) have also since landed —
check `git log --oneline --all | grep -i "track3[0-9]"` for whether
anything past Track 29 has landed since this snapshot was written.

## What's genuinely landed and working, as of HEAD

- Alembic head is `0034`. Full `pytest -q` suite: **2129 passed, 0 failed**
  (re-verified against this exact HEAD, after Track 38's new Hypothesis
  stateful test landed on top of the Track 24–30/33 merges below).
- `ruff check .` and the CI-scoped `mypy` command (file list in
  `.github/workflows/signal-copier-ci.yml`, 39 files) both clean against
  this HEAD.
- Track 38: `tests/test_trk38_quantity_conservation_and_idempotency.py`
  extends C29's Hypothesis stateful quantity-conservation machine with
  generated full-close/re-entry/duplicate-close event orderings (75
  examples x 25 steps), proving quantity conservation, no negative/
  over-filled ownership, and the TRK-27 duplicate-exit guard's boundary
  all hold across far more interleavings than the existing hand-written
  suite covers alone. No real bug found.
- Managed-lifecycle fills now populate the AUD-01 quantity fields
  internally (Track 22) **and** export a real `EXECUTION_APPLIED` contract
  event (Track 23) — previously, per Track 22's own changelog entry, this
  was "the majority of real trading activity" silently invisible to
  signal-portfolio-commercial's platform ledger.
- The Track 21 onboarding wizard (`app/static/views/tr17.js`) is reachable
  and working end-to-end — a routing bug that made it completely
  unreachable (`#/providers/add` never matched the router's `/trade/...`
  prefix gate) was found and fixed this same session, along with a
  double-submit guard and partial-failure disclosure.
- **Mobile Devices dashboard screen** for Track 20's `/mobile-devices*`
  REST backend now exists (Track 25, `app/static/views/tr18.js`) — closes
  the gap this file previously listed below as not-yet-landed.
- **Provider-catalog screen** listing Track 14's provider-catalog rows now
  exists (Track 25, `app/static/views/tr19.js`) — closes the other gap
  this file previously listed below as not-yet-landed.
- **Managed-lifecycle exit-idempotency** gap closed by a bounded, in-
  memory duplicate-exit guard (Track 27, ADR-0010 —
  `docs/adr/0010-managed-exit-duplicate-episode-guard.md`), with a
  concrete two-signal reproduction test
  (`tests/test_trk27_managed_exit_duplicate_episode.py`) covering both the
  duplicate-recognized and genuine-re-entry-not-suppressed cases. See the
  ADR for the explicitly-scoped remainder this does NOT cover (in-memory
  only, no cross-restart persistence).
- An exhaustive, no-sampling E2E validation wave (both repos' full
  pytest suites, every dashboard screen/template driven live via
  Playwright, 5 live trading scenarios, a live cross-repo contract proof)
  surfaced one significant gap and several smaller ones, now all closed:
  **signal-portfolio-commercial's `_apply_projection` had no branch for
  `EventType.SOURCE_EVENT`** (Track 30) — a plain webhook `SOURCE_EVENT`
  emitted before its own `SOURCE_RECEIPT` on the same per-source stream
  parked permanently and blocked every later event on that stream, so in
  practice no `SOURCE_RECEIPT`/`ROUTING_ADMISSION_OUTCOME` delivered via
  the real webhook path ever reached the ledger. Fixed: `ORIGINAL`-kind
  events now advance the stream as a no-op (provenance already captured
  verbatim in `InboxEvent.envelope_json`); other kinds still park
  honestly as `unimplemented_source_event_kind`. Track 30 also fixed an
  owner-login bug (owners were redirected to the customer `/app` instead
  of `/ops`). Track 33 added an RSS-source duplicate-feed-URL warning
  (soft, not a rejection), a real-RLS proof that the rollback-recovery
  `require_tenant_scope` pattern is genuinely tenant-isolated (no bug
  found — reported as a verified-safe finding), and an owner-gated
  `GET /export-events` read endpoint for the export outbox. Tracks 31/32
  closed the remaining doc-staleness and accessibility-scaffolding
  findings from the same wave.

## What's genuinely NOT yet landed / still open

See the audit-findings document referenced in this session's history for
the full list; highlights carried forward here since they're not yet
closed:

- ~~`signal_platform_contracts` is stale relative to Track 14 and Track
  22/23~~ — **re-investigated (Track 36) and found NOT true; this line was
  itself stale.** Concretely checked both:
  - **Track 14** (Provider/Source/Connection model): the one piece of
    Track 14 vocabulary that actually crosses the contract boundary —
    which specific transport/`sources.id` a signal arrived through — was
    already added to `SourceIdentity.source_catalog_id` by Track 29, and
    is genuinely wired end-to-end on the producer side
    (`app/export_events.py`'s `build_source_receipt_envelope`/
    `build_source_event_envelope` both set it from
    `signal.source_catalog_id`; see `tests/test_export_events.py`'s own
    "Track 29: source_catalog_id" section). The REST of Track 14's
    vocabulary — `ProviderStatus`/`SourceHealth`/`CertificationState`/
    `ExecutionEligibility`/shadow-mode state (Track 17) — is operator-UI
    state local to this service (`app/provider_catalog.py`,
    `app/certification.py`, `app/shadow_mode.py`) that
    signal-portfolio-commercial never reads or needs (confirmed by
    grepping that repo for every one of those names — zero references
    outside its own unrelated uses of the word "certification").
    Formalizing those as shared contract types would be exactly the
    "invent fields nobody produces/consumes" this package's own
    discipline forbids.
  - **Track 22/23** (AUD-01 distinct-quantity model + EXECUTION_APPLIED
    export): the five AUD-01 fields (`requested_quantity`/
    `confirmed_cumulative_fill`/`applied_execution_delta`/
    `outstanding_possible_fill`/`actual_remaining_ownership`, see
    `tests/test_aud01_distinct_quantity_model.py`) are internal
    `orders`-table bookkeeping for this service's own position-ownership
    computation (Track 16/18) — not fields of the cross-service event.
    The ONE fact that genuinely crosses the boundary (one broker-
    confirmed fill's quantity/price) was already a required, typed
    `Money` field on `ExecutionAppliedPayload.filled_quantity`/
    `filled_price` before AUD-01 and remains so after — `app/export_events.
    py`'s `build_execution_applied_envelope` never constructs the envelope
    payload as a loose dict (every one of its four `EventEnvelope(...)`
    call sites in that module builds from a typed, `extra="forbid"`
    payload model first), and signal-portfolio-commercial's
    `app/services/integration_inbox.py` consumes it via
    `ExecutionAppliedPayload.model_validate(envelope.payload)` and typed
    attribute access (`payload.filled_quantity`, `payload.filled_price`,
    ...), never a raw dict lookup. Both sides' existing tests
    (`tests/test_export_events.py` here,
    `signal-portfolio-commercial/tests/test_integration_inbox.py` there)
    already round-trip these exact fields.

  No contract or application code changed as a result of this
  re-investigation — only this note, which had been carried forward
  unverified since it was written, long past the point (Track 29) where
  it stopped being accurate. If a REAL Track 14/22/23-shaped contract gap
  is found later, it should cite the specific missing field and the
  specific producer/consumer code path, not just a track number.
- `trading_authority`/`release_status` in `/system/readiness` remain
  honest, pre-existing placeholders (P0-6/P0-7 qualification/release-
  taxonomy work, never built) — out of scope for the Provider/Source/
  Connection and ingestion-architecture work this branch has otherwise
  been doing; not a regression, just still genuinely unbuilt.

## Track 40: fuzzing/fault-injection coverage extension

Extended both C30 (Schemathesis) and C32 (fault injection) past their
original scope: C30's `SAFE_PATHS` now also covers `/system/readiness`,
`/export-events`, `/mobile-devices`, and `/connections/*`'s read-only
GETs added by recent tracks; a new, separately-scoped
`tests/test_c37_webhook_schemathesis_fuzzing.py` fuzzes the webhook
ingress route itself (deliberately excluded from C30's generic pass --
see that file's own module docstring for why). A new
`tests/test_c36_broker_submission_fault_injection.py` extends C32's
real-httpx-transport-fault approach from `AlpacaBroker.get_order_status`
to `place_order` itself.

This found and fixed three real, previously-unproven unhandled-5xx bugs
on adversarial input, all now closed with regression tests (see
CHANGELOG.md's own Track 40 entries for the full detail on each):
`AlpacaBroker`'s four `broker_order_id=order.get("id")` call sites with
no type coercion, `GET /connections/{id}/cost-summary`'s unparsed
`since`, and `POST /connections/{id}/cost-events`'s `occurred_at` being
parsed outside its own except block. Nothing found that needed flagging
rather than fixing -- every gap this pass found had a small, obvious fix
matching an existing convention already established elsewhere in the
same file.

## Track 42: honest handling of a relay `"parked"` status

signal-portfolio-commercial's `POST /internal/relay/ingest-batch`
reported `"applied"` for every event it didn't raise a named error
for, including a genuinely parked one -- `app/relay_worker.py`
inherited that dishonesty by never distinguishing a `"parked"` status
from `"applied"` (it had no branch for it at all before this track).
Both sides fixed together (see the commercial repo's own
CHANGELOG.md/docs/KNOWN_ISSUES.md for its half):

- `app/relay_worker.py`'s new `classify_parked_reason` splits every
  named `parked_reason` (plus the unnamed sequence-gap case) into
  TRANSIENT (resolves on its own via the commercial side's own
  cascade once a correlated event arrives -- left undelivered,
  retried next poll) and STRUCTURAL (never resolves without a code
  change -- `unsupported_schema_version`, `unimplemented_event_type`,
  `source_event_kind_not_ledger_representable`, a manifest/generation
  mismatch, or an `edit_without_resolvable_target` missing its revision
  chain entirely). An unrecognized future reason defaults to
  STRUCTURAL -- the safe failure mode, never a silent infinite retry.
- A structurally-parked event is marked terminally parked (new
  `export_events.terminal_park_reason`/`terminal_parked_at` columns,
  `SignalStore.mark_export_events_terminally_parked`) and excluded
  from every future `list_undelivered_export_events` poll, but
  **never** marked `delivered` -- that would repeat the exact
  dishonesty this track closed on the commercial side, one hop later.
- `GET /health` gained `terminally_parked_export_event_count`, same
  informational-only treatment (never gates `status`) as INT-040's own
  `outbox_backlog_*` fields.
- New `tests/test_relay_worker.py` coverage (the full taxonomy plus
  `run_once`'s real transient/structural handling) and a new
  `GET /health` test in `tests/test_export_outbox_ceiling.py`.
- New Alembic revision `0035_add_export_events_terminal_park_columns.py`
  (`export_events.terminal_park_reason`/`terminal_parked_at`) -- Alembic
  head is now `0035`. `tests/test_e01_alembic_migration_stamping.py`'s
  own CLI-upgrade-vs-bootstrap parity checks (its real load-bearing
  property) updated to expect `0035`; this is what originally caught
  the gap (the new columns had been added only via `app/db.py`'s
  `_COLUMN_MIGRATIONS` bootstrap path, with no matching Alembic
  revision).

## Verification status

Last independently re-verified state: this exact HEAD (`4a24d07`), full
`pytest -q` (2119 passed), single alembic head `0034` confirmed via
`alembic heads`. Any commit after this HEAD should be treated as
unverified by this snapshot until its own merge commit documents a fresh
run of the same checks.

### Track 39: mutation-testing pass (2026-10-01)

Ran `mutmut` (scoped per-module, via a temporary `[tool.mutmut]`
override never committed -- the repo's one checked-in config stays
scoped to `app/auth.py`, see pyproject.toml's own comment) against
`app/risk.py`, `app/capital_allocator.py`, `app/quantity.py`,
`app/routing.py`, plus targeted manual mutation checks against the
AUD-01 fields in `app/db.py`'s `get_outstanding_possible_fill`. Added
`tests/test_risk_sizing.py` and `tests/test_routing_evaluate.py` (both
previously had no dedicated direct unit test file at all), plus
targeted additions to `tests/test_capital_allocator.py`,
`tests/test_trkq1_quantity_breakdown.py`, and
`tests/test_aud01_distinct_quantity_model.py` — see CHANGELOG.md for
the per-file summary. Final mutation scores: risk.py 11/11,
capital_allocator.py 86/87 (1 confirmed equivalent — a reservation row's
synthetic id, never actually used for matching), quantity.py 62/64 (2
confirmed equivalent — a dropped kwarg whose default equals what was
always passed anyway), routing.py 260/262 (2 confirmed equivalent — an
`or {}` fallback makes the `.get()` default irrelevant). No production
code changed; every real survivor was closed with a new test, no
genuine production logic bug was found. Full `pytest -q` re-verified
after these additions: 2170 passed, 0 failed.

### Track 43: mutation-testing scope widened to app/engine.py (2026-10-01)

Widened the repo's one checked-in `[tool.mutmut]` config (pyproject.toml)
to also cover `app/engine.py` -- per the explicit instruction that
mutation coverage must eventually cover every module, starting with the
highest financial-risk code; `app/engine.py` (the core signal-processing/
order-routing engine) is next after `app/auth.py` by that ordering.
`only_mutate` now includes it; `pytest_add_cli_args_test_selection` now
also lists all 44 test files that import `SignalCopierEngine` directly
(372 tests total).

Mutant generation succeeded (~140 mutable sites in `app/engine.py`
alone), but the stats-collection pass that must run once before any
individual mutant can be checked did not complete within this session:
after 12+ minutes of real wall-clock time with confirmed, steady CPU
progress (not a hang -- sampled repeatedly via `ps`), it was stopped
rather than let run indefinitely. The container's `uptime` showed a load
average of 11-13 on 4 cores throughout, from several other agent sessions
running their own test suites/mutmut concurrently on the same shared
machine (tracks 44-48 and others observed in `ps aux` during this run) --
an environment condition, not a defect in the engine.py mutation setup
itself. No mutation score for `app/engine.py` is reported here because
none was actually produced; a future run on a less-contended box (or in
CI, which runs single-tenant) should be able to complete it using the
scope already committed.

In its place, did a complete manual read of `app/engine.py` (~3100
lines, every method) against the already-extensive existing test files
for each area, prioritized by the same financial-risk ordering mutation
testing would prioritize: close-signal resolution (`_resolve_close`,
`_resolve_and_submit_plain_close`), the P0-5 broker-position
reconciliation tolerance math (`_positions_reconcile`/
`_reconcile_before_plain_close`), Track 18 per-provider position-
ownership gating, E03 capital/risk admission gates, the AUD-01 distinct-
field quantity model threaded through `_submit_order`/
`_handle_managed_entry`/`_handle_managed_close`, and command-ledger
idempotency. Found one real, previously-undetected gap this way:
`_positions_reconcile` (a small, pure tolerance-boundary function) had no
direct unit test at all -- only incidental indirect coverage through
whichever specific values the reconciliation-flow tests in
`tests/test_p0_5_close_reconciliation.py` happened to use, meaning a
mutation flipping its comparison operator, dropping the relative-
tolerance term, or comparing the signed difference instead of the
absolute one could plausibly have survived every existing test. Added 6
direct tests there (exact match, the absolute-tolerance boundary at zero,
tolerance scaling with magnitude, sign-symmetry, and boundary
inclusivity) -- all pass against current code; no production code changed.

`ruff check .` and the CI-scoped `mypy` command both clean on this
branch. Full `pytest -q` re-verified after this addition (isolated
`TMPDIR` to avoid colliding with the other concurrent sessions' own test
databases/ports on this shared box): see this file's own verification
line below once re-run; treat any gap here as this snapshot not yet
having been updated after that run completed, not as the run having been
skipped.

### Track 44: mutation-testing scope widened to the position-lifecycle state machine (2026-10-01)

Widened `[tool.mutmut]` further, same ordering principle as Track 43:
`app/lifecycle/manager.py` (the broker-agnostic managed-position
lifecycle state machine, ~2350 lines) and `app/lifecycle/close_arbiter.py`
(the single oversell-prevention authority every exit funnels through,
~220 lines) -- both flagged in CLAUDE.md's hard rule 1 as having the
most precise spec available. `only_mutate` now also lists both;
`pytest_add_cli_args_test_selection` adds the two modules' own direct
unit-test files plus `tests/test_pro06_stop_amend_on_partial_exit.py`
and `tests/test_pro02_activation_and_time_exit.py`.

**close_arbiter.py — full mutation score obtained and acted on.**
120 mutants. Baseline 88 killed / 32 non-killed (23 survived, 9 no
tests). Added 10 tests to `tests/test_close_arbiter.py`: a `snapshot()`/
`restore()` round-trip (previously completely untested -- a dict-key
rename here would silently break every real save/restore round trip,
with no other safety net given this module's documented "no startup
reconciliation" gap), `halt()` preserving its real reason, `reserve(0)`'s
no-op contract, the `_EPSILON` tolerance boundaries on both the reserve
oversell guard and the reserved-exceeds-owned invariant check, and --
the most financially meaningful one -- confirming `_check_invariant`'s
SECOND halt branch (`reserved > owned`, independent of `owned < 0`)
actually halts rather than silently no-op'ing when a confirmed-owned
observation drops below an outstanding reservation without going
negative. Result: 110 killed / 10 non-killed. The remaining 10 are
documented in CHANGELOG.md as equivalent/unreachable/impractical-float-
boundary/genuinely-unused-code, not chased further.

**app/lifecycle/manager.py — no full aggregate score; targeted,
risk-prioritized review instead.** 2432 mutants (far larger than
close_arbiter.py or even Track 43's app/engine.py). A full clean `mutmut
run` was attempted twice; both times the shared container's
`.mutmut-cache`/generated `mutants/` state was lost mid-run before
completion (once silently reverting all results to "not checked", once
with an explicit `FileNotFoundError` on a `.meta` file) -- the same class
of heavy-concurrent-session environment instability Track 43 already
hit on `app/engine.py` on this box. One run DID complete before the
first corruption, giving a real baseline: 837 killed / 1021 survived /
574 no-tests. That baseline was used to prioritize a manual, sampled
diff-by-diff review of the 7 functions implementing the financially
dangerous behaviors this track was asked to prioritize -- state-
transition guards, idempotency keys, double-apply/skip risk on a
close/reduce: `request_exit`, `resolve_pending_exit`,
`resolve_pending_entry`, `on_stop_filled`, `check_duplicate_exit`,
`_apply_exit_fill`, `_restore_stop_coverage` (347 survivors across these
7 alone -- the full ~1021 was not reviewed one by one). Found and fixed
3 genuine bugs (added to `tests/test_lifecycle_manager.py`, each
individually confirmed killed via an isolated `mutmut run <mutant-id>`
rerun since the aggregate couldn't be re-confirmed):
1. `request_exit`'s "refuse a second exit while a prior one's remainder
   is unresolved" guard had a reachable dropped-`not` mutant with no
   test driving a genuine concurrent-PENDING second `request_exit` call.
2. `request_exit(quantity=0)` had no test on the `requested <= 0`
   boundary that must reject outright rather than reserve/submit a real
   0-share order.
3. The final stop-restore gate (`had_stop and desired_price is not
   None`) had no test distinguishing it from an `or` mutation, which
   would attempt a fresh stop placement on every qualifying exit even
   for a position whose initial protection already failed (that
   recovery is `retry_unprotected_positions`'s job, not every exit's).

Two further findings were investigated and confirmed to be already
covered elsewhere (false negatives of this track's narrower/faster test
selection, not real gaps): `resolve_pending_entry`'s `has_unresolved_exit`
dropped-`not` mutant is exercised end-to-end by
`tests/test_5e91e78_lifecycle_composition.py`'s F02 test, just outside
this track's selection; `check_duplicate_exit`'s `age_seconds`
boundary operator mutations (`<`/`<=`, `>`/`>=` against 0 and the
duplicate-exit window) are real but practically untestable at an exact
floating-point instant, with negligible real-world impact at that
precision. The remaining bulk of the ~1021 baseline survivors are
`OrderResult` field-level mutations on error/rejection branches
(`account_id=None`, `signal_id="XXXX"`, message wording) that this
suite correctly only asserts `.status` on, not the full field set --
not chased here. **No full `app/lifecycle/manager.py` mutation score is
reported**, for the same honest reason Track 43 reported none for
`app/engine.py`: none was actually produced end-to-end on this box this
session. The scope is left committed for a future unloaded-box or CI run
to complete.

`ruff check .` and the CI-scoped `mypy` command both clean. Full
`pytest -q` re-verified after these additions.
