# Known issues

Genuine, disclosed gaps — pulled from this codebase's own docstrings,
`ops/commercial_state.json`, and `INTEGRATION_ACCEPTANCE_STATUS.md`
(repo root), not a generic disclaimer list. Each item says exactly
what's real versus missing.

## Publisher/broker integrations are request-shape-only — nothing transmits

- **Collective2 (`app/services/collective2_publisher.py`)** builds a
  correct API4 Order envelope from a `PublicationIntent` but never
  sends it — no C2 sandbox or credentials exist in this environment.
  The `Tif` enum deliberately omits the disputed value `2` (see
  `docs/PENDING_DECISIONS.md`) and `build_order` raises rather than
  guessing. No StrategyId/symbol resolution, no OCA/stop-target group
  reconciliation, no external-acknowledgment readback wired to
  `publication.transition()`.
- **eToro (`app/services/etoro_adapter.py`)** builds a trade request
  shape only — `build_trade_request` structurally refuses any
  `account_mode` other than `"demo"`; there is no code path that could
  accidentally select a real endpoint. No real eToro
  application/credentials exist.
- **CopyFactory (`app/services/copyfactory_close_only.py`)** only
  classifies close-only mode strings a caller already read back from
  the API — it never calls CopyFactory itself.

## No real execution-activation pipeline exists for `CopyMandate`/`ManagedProgram`

`app/services/trading_authority.py`'s qualification gate
(`GET /system/readiness`, ADR-0010) checks every real platform-side
gate this build has -- publication, release-review approval, order-
routing-specific rights, open incidents -- and, for an order-routing
product, structurally cannot report `qualified=True` even when all of
those pass: `CopyMandateState` (`app/models/copy_mandate.py`) is only
`draft`/`cancelled` and `ManagedProgramState`
(`app/models/managed_program.py`) is only `DRAFT`/
`SUBMITTED_FOR_REVIEW` -- neither has a real ACTIVE/enrolled-live state,
because "activation needs a real scoped publisher/execution pipeline
this build does not have" (both models' own docstrings). The gate
reports this honestly as `missing_input:execution_activation_pipeline`
rather than fabricating a pass. This was previously an unrecorded gap
(the question was simply never asked); it is now a named, computed, and
tested one.

## PAMM/MAM accounting is simulation-only

`app/services/mam_allocation.py` (largest-remainder integer-unit
allocation, deterministic tie-break) and
`app/services/pamm_accounting.py` (simple no-cashflow high-water-mark
fee, which rejects any interval with a deposit/withdrawal rather than
computing a wrong fee) never talk to a real broker. Per the spec's own
"Fair MAM allocation" section: "production uses the actual contractual
broker rule instead." No `AllocationProgram` persistence layer exists
yet either.

## Stripe billing has no real processor behind it

`app/services/stripe_webhook.py` reimplements Stripe's own public
signature scheme (HMAC-SHA256 over `{timestamp}.{raw_body}`) and is
tested against synthetic, Stripe-shaped payloads — there is no Stripe
SDK call, no real Stripe account, and no webhook endpoint exposed to
the real internet yet. No `Product`/`ProductVersion` persistence layer
beyond the `ProductTier` test-mode price fixtures; no proration, tax,
or coupon handling.

## Email delivery is not wired

The local auth system's verification/password-reset link is shown
directly on the page rather than emailed — no SMTP/SendGrid account
exists in this environment. Anyone who can view the page after
triggering the flow can complete it; this is fine for development but
is not a real delivery channel.

## Portfolio research is bounded to the deterministic half

`app/services/portfolio_research.py` covers candidate-subset
enumeration and the single equal-weight recipe. Explicitly NOT built:
correlation/complementarity statistics, the other three recipes
(inverse-volatility, hierarchical risk parity, constrained minimum-
CVaR), walk-forward/holdout/forward-shadow evaluation, and capacity-
stress computation — all genuinely need real authorized historical
sleeve data this environment doesn't have.

## No real LLM/model provider is called

`app/services/model_gateway.py` implements the permission boundary
(allowed purposes, a hard deny-list, mandatory human review, read-
only-research-only API key roles) but calls no real model provider
anywhere — per the spec's own "omit this dependency when no model
feature is selected" rule.

## No real deployment infrastructure exists

AD-22's "commercial deployments and recovery" screen reports real
environment status but honestly discloses there is no actual
deployment infrastructure behind it yet — see `docs/process/RELEASE.md`.

## A real regression this codebase already found and fixed (for context)

Not currently open, but worth knowing this class of bug is real here:
`64d596d`'s own commit message documents a drawdown peak-tracking
regression left mid-verification by a prior session, which the
existing load-bearing test did not catch (its own true peak happened
to sit immediately before its own true trough). Fixed, and a stronger
regression test added. See `docs/agents/VERIFICATION.md` for why this
matters to every future change, not just this one.

## `POST /internal/relay/ingest-batch` reports "applied" for a genuinely PARKED event

**Found by Track 40's own fault-injection/fuzzing pass
(tests/test_c40_relay_ingest_adversarial_payloads.py::
test_out_of_order_sequence_in_one_batch_parks_then_resolves_without_500),
flagged rather than fixed — see that test's own docstring for the full
reasoning.**

`app/api/relay_routes.py`'s `ingest_batch` per-event loop hardcodes
`results.append({"status": "applied", "event_id": applied_event_id})`
for every event that reaches that line without raising one of the
three explicitly-named exceptions (`UnregisteredStreamError`/
`EventIntegrityError`/`SequenceSlotAlreadyConsumedError`, plus Track
40's own new `ValidationError`/`DataError` handling for malformed
input — see below). It never actually checks `InboxEvent.applied_at`.

A genuinely **PARKED** event — received and durably stored, but with
NO ledger projection applied (a sequence gap waiting on a missing
predecessor, an unsupported `schema_version`, an unimplemented
`event_type`, a `FEE`/`ROUTING_ADMISSION_OUTCOME` whose correlation
target hasn't arrived yet, a generation rollback/bootstrap-required
case — every `PARKED_REASON_*` constant in `app/services/
integration_inbox.py`) is reported back to the caller as `"applied"`
exactly the same as a real, economically-applied event. This is a
fabricated status for this codebase's own stated discipline (see
CLAUDE.md rule 11 and this module's own docstring: "never coerced,
never blanket-accepted").

**Real, non-cosmetic consequence:** signal-copier's own
`app/relay_worker.py` consumes this exact field (`if status ==
"applied": delivered.append(envelope.event_id)`) and calls
`store.mark_export_events_delivered(delivered)` on it — permanently
removing the event from its own outbox, never to be redelivered. For
most park reasons this is harmless (the event is already durably
received on the commercial side, and redelivery alone couldn't unstick
a *structural* park like an unsupported schema version anyway) — but
the status label itself is still dishonest, and a consumer with no way
to see `applied_at` on the other side of the wire has no way to tell
"this was really booked to the ledger" from "this was merely received
and will sit here forever."

**Why this is flagged, not fixed:** the "obvious" fix (check
`inbox_event.applied_at` and report a distinct `"parked"` status with
`parked_reason`) is a cross-service WIRE CONTRACT change — signal-
copier's own `relay_worker.py` has no branch for a `"parked"` status
today (it would simply fall through unrecognized, which happens to be
harmless now, but that's an accident of the current `if/elif` chain,
not a decision anyone made). Deciding what the relay worker should
actually DO with a distinct "parked" signal (retry forever? only for
some park reasons? surface an operator alert for the structural-park
cases that will never resolve on their own?) is real, reviewed design
work spanning both repos, not a same-file bugfix — exactly the kind of
high-stakes ambiguous finding this track's own instructions say to
flag rather than guess at.

**What WAS fixed in the same pass** (see
`app/api/relay_routes.py`'s new `except ValidationError`/`except
DataError` blocks, CHANGELOG.md, and the tests named above): a single
malformed event anywhere in a batch — an envelope failing its own
`EventEnvelope.model_validate_json` (e.g. JSON nested deep enough to
trip pydantic-core's own internal recursion guard), a per-event-type
payload with an extra/wrong-typed field, or a string field carrying a
byte Postgres text columns reject outright (an embedded NUL byte) —
used to raise an UNHANDLED exception straight out of the route as a
real 500, aborting the entire batch request including every other,
already-committed, perfectly valid event processed earlier in the same
loop. That crash is now caught, rolled back cleanly (no partial ledger
entry), and reported as a new, honest `"malformed_envelope"` per-event
status instead — this one WAS small, obvious, and matched this exact
file's own pre-existing per-event try/except convention, so it was
fixed directly rather than flagged.

## SOURCE_EVENT projection: every kind now has a real, honest disposition

`app/services/integration_inbox.py`'s `_apply_projection` has a real
`EventType.SOURCE_EVENT` branch (previously: every `SOURCE_EVENT` fell
into the generic "unimplemented event type" case and parked forever at
sequence 0, permanently blocking every later event on the same stream
-- including the stream's own `SOURCE_RECEIPT` and any
`ROUTING_ADMISSION_OUTCOME` -- since `_next_expected_sequence` is keyed
off `applied_at`). Track 30 closed this for `ORIGINAL`; Track 35 closed
it for every other `SourceEventKind`:

- **`ORIGINAL`/`ADD`/`REPLY`** apply as a no-op and advance the stream.
  Verified against signal-copier's own live adapters
  (`app/sources/*.py`, `app/export_events.py`): each of these calls
  `on_signal(signal)` on its own fresh `Signal.id` (ADD/REPLY) or is
  itself that fresh signal (ORIGINAL) *before* exporting this
  `SOURCE_EVENT` row, so any real instruction it carries is
  independently booked by its own, separate `SOURCE_RECEIPT` on the
  same stream -- this row is pure, already-redundant provenance, never
  a second ledger entry for the same economic fact. A `REPLY` with no
  correction at all (`signal is None`) simply has nothing economic to
  book in the first place.
- **`DELETE`/`CANCEL`/`CLOSE`** apply as a no-op and advance the
  stream. By the taxonomy's own definition
  (`signal_platform_contracts.payloads.SourceEventKind`'s own
  docstring) none of these ever carries bookable instrument/side/
  quantity content, so there is no economic fact for this row to book;
  applying it unconditionally is honest, not a guess.
- **`EDIT`** correlates by native provider identity: a new, indexed
  `InboxEvent.source_event_native_key` column
  (`f"{tenant_id}|{source_provider_id}|{source_channel_id}|
  {source_event_id}"`, Alembic revision `a7c3f29d1e56`) is set on
  every `SOURCE_EVENT` row. An `EDIT` whose own `source.
  original_source_event_id` resolves, via that key, to a real,
  already-applied `SOURCE_EVENT` row for the SAME tenant applies as a
  no-op (its own revised content, if any, is independently booked by
  the edit's own `SOURCE_RECEIPT`, same reasoning as ORIGINAL/ADD/
  REPLY above). An `EDIT` with no `original_source_event_id` at all, or
  one that doesn't resolve to any known row, parks honestly as
  `edit_without_resolvable_target:<missing_original_source_event_id |
  unresolved-key>` -- never silently treated as safe provenance just to
  unblock the stream.
- **`TARGET_UPDATE`/`STOP_UPDATE`** always park, as
  `source_event_kind_not_ledger_representable:<kind>` -- a KIND this
  build genuinely understands, but `app/models/ledger.py::LedgerEntry`
  has no stop-loss/target column at all (see
  `docs/design/LEDGER_MODEL.md`'s own column table), so there is
  nowhere honest to write a stop/target revision even if it correlated
  perfectly to an existing position. No adapter in this codebase emits
  either kind today (grep `SourceEventKind\.\(TARGET_UPDATE\|
  STOP_UPDATE\)` across signal-copier's own `app/`), so this is a
  disclosed theoretical gap, not a live one the way EDIT/DELETE were
  before this track.

**Deliberately NOT built here** (flagged, out of this track's own
inbox-projection boundary, per its own task scope):

- A `DELETE`/`CANCEL`/`CLOSE` does not retroactively mark the earlier
  `Book.SOURCE` entry it retracts/closes as void/superseded -- the
  original entry stays in `Book.SOURCE` unmarked, exactly as before.
  `LedgerEntry` has no "void"/correction concept for a `SOURCE`-book
  row today (`append_correction` is only ever called against an
  `EXECUTION_APPLIED` row, for `FEE`); adding one is a real ledger-
  model design decision for a future track, not a guess to make here.
- Nothing in this repo stops signal-copier's own engine from
  continuing to act on a position whose originating signal was later
  edited/cancelled/deleted/closed -- that's signal-copier's own
  trading logic, strictly outside this commercial platform's read-only
  inbox-projection boundary, and is not touched by this change.
- `TARGET_UPDATE`/`STOP_UPDATE` correlation (the same native-key
  mechanism as EDIT) was deliberately not built, since even a perfect
  match has nowhere to write its data today -- building the
  correlation plumbing for a projection that still couldn't apply
  would just move the "no real work happens" point without closing the
  actual gap, which is the ledger model itself.
- A SEPARATE, pre-existing concern this track did NOT fix (it lives in
  `SOURCE_RECEIPT`'s own branch, unchanged here, not in `SOURCE_EVENT`):
  an edited signal gets its own, independent `SOURCE_RECEIPT` (a new
  `Signal.id`, since signal-copier's own `_handle_signal` only
  dedupes by exact `(channel_id, message_id, revision_id)`), so a
  message edited once ends up with TWO `Book.SOURCE` rows -- the
  original's and the edit's -- rather than one row superseding the
  other. For an analyst who frequently edits price/quantity, this
  could overstate `compute_book_performance`/`compute_analyst_
  attribution`'s recommended size for that instrument. Fixing it would
  mean teaching `SOURCE_RECEIPT`'s own projection to correlate and
  correct against a prior revision's ledger entry -- a real,
  non-trivial design decision (what does "correcting" a `SOURCE`-book
  row even mean, since `Book.SOURCE` quantity feeds attribution
  differently than `Book.PLATFORM` does) squarely outside this track's
  SOURCE_EVENT-kind scope. Flagged for a future track, not guessed at
  here.

This has live impact: signal-copier's Telegram (`app/sources/
telegram.py`, `telegram_user.py`: `EDIT`/`DELETE`), Slack
(`app/sources/slack_user.py`: `EDIT`/`DELETE`), Twitter
(`app/sources/twitter_user.py`: `EDIT`), and email
(`app/sources/email_source.py`: `REPLY`) adapters already emit these
kinds live today -- before this track, any of them parked its own
`SOURCE_EVENT` row and blocked everything after it on that stream
forever. That permanent-block class of bug, for every live kind this
build will ever receive from a currently-shipped adapter, is now
closed.

## Where to look for the authoritative, requirement-by-requirement account

- `INTEGRATION_ACCEPTANCE_STATUS.md` (repo root) — every one of the
  integration pack's 40 acceptance cases, marked PASS/PARTIAL/BLOCKED/
  NOT_RUN with cited evidence.
- `docs/12_validation_report.md` / `docs/12_addendum_post_phase12_work.md`
  — the original 13-phase build's own line-by-line audit against all
  114 requirements.
