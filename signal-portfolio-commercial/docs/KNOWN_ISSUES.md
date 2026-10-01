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

## SOURCE_EVENT projection covers ORIGINAL only -- other kinds still park

`app/services/integration_inbox.py`'s `_apply_projection` now has a
real `EventType.SOURCE_EVENT` branch (previously: every `SOURCE_EVENT`
fell into the generic "unimplemented event type" case and parked
forever at sequence 0, permanently blocking every later event on the
same stream -- including the stream's own `SOURCE_RECEIPT` and any
`ROUTING_ADMISSION_OUTCOME` -- since `_next_expected_sequence` is keyed
off `applied_at`). An `ORIGINAL` `SourceEventKind` (the raw inbound
event, informational/provenance only -- the same economic fact its
stream's own `SOURCE_RECEIPT` already books into `Book.SOURCE`) is now
applied as a no-op: it advances the sequence, and stays durably,
verbatim stored in its own `envelope_json`, but produces no second
ledger entry.

Genuinely remaining gap: `EDIT`/`DELETE`/`REPLY`/`CANCEL`/`CLOSE`/
`ADD`/`TARGET_UPDATE`/`STOP_UPDATE` -- every OTHER `SourceEventKind` --
still parks (`unimplemented_source_event_kind:<kind>`), honestly, since
this build has no real ledger projection for an edited/cancelled/
retargeted source instruction yet (e.g. revising a stop or cancelling a
pending entry has no effect on `Book.SOURCE` today). As today, a
producer that emits any of these on a real stream will park that event
and everything after it on the same stream until a future build adds a
real projection for it -- the same honest "keep separate received/
applied cursors" consequence as any other unimplemented shape, not a
silent drop (the row is always received and durably stored first,
`SourceEventPayload.raw_source_event` carries the immutable raw
provider event when the producer sends it). This has live impact
today, not just future risk: signal-copier's own webhook ingress path
(`app/sources/webhook.py`) only ever emits `ORIGINAL`, but several
other real adapters already emit non-`ORIGINAL` kinds -- Telegram
(`app/sources/telegram.py`, `telegram_user.py`: `EDIT`/`DELETE`),
Slack (`app/sources/slack_user.py`: `EDIT`/`DELETE`), and Twitter
(`app/sources/twitter_user.py`: `EDIT`). A source message edited or
deleted through any of those adapters still parks its own `SOURCE_EVENT`
row (and blocks everything after it on that stream) until a future
build adds a real projection for that kind -- the webhook path's own
permanent-park bug from this track is fixed, but these adapters' own
edit/delete events are a separate, pre-existing instance of the same
"unimplemented event type parks forever" shape, not newly introduced by
this fix and not yet closed by it.

## Where to look for the authoritative, requirement-by-requirement account

- `INTEGRATION_ACCEPTANCE_STATUS.md` (repo root) — every one of the
  integration pack's 40 acceptance cases, marked PASS/PARTIAL/BLOCKED/
  NOT_RUN with cited evidence.
- `docs/12_validation_report.md` / `docs/12_addendum_post_phase12_work.md`
  — the original 13-phase build's own line-by-line audit against all
  114 requirements.
