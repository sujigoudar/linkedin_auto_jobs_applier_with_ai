# Phase 00: discovery, reconciled against the actual current app

The spec package's own `docs/00_current_state_and_scope.md` was written from a
single targeted source review ("PR metadata, current dashboard source,
economics source and JEV README... not a new end-to-end audit"). This
document reconciles that against the actual, current state of
`signal-copier/` as of this build's start, since a great deal of relevant
work has landed since the package's own review commit
(`cdbcd1eca999adfaabea76fffda856852f3ecd08`).

## Actual current state (verified directly, not inherited from the package)

- Current HEAD at the start of this build: see `git log -1` in the repo;
  the package's own inspected commit is several commits behind it.
- `app/economics.py`'s `completed_trade_win_rate` genuinely does count
  profitable REDUCING FILLS, not independently completed trade episodes,
  exactly as the package's doc 00/04 describe. This is real and still
  needs the `closing_fill_win_rate` rename with a deprecated alias --
  tracked under Phase 03, not fixed by this discovery doc alone.
- Since the package's review commit, this build added (all in
  `signal-copier/`, all already tested and documented in its own
  README.md -- not re-described here in full):
  - **E03 capital-allocator reservation-timing fix**: a PENDING order
    with a real broker_order_id now keeps its notional reservation until
    reconciliation confirms a terminal status, instead of releasing it
    immediately. Relevant to Phase 04's "replay combined decisions,
    never sum independent sleeves that each assumed the whole account
    was available" requirement -- this existing allocator is a
    per-account admission gate, not the shared-capital portfolio replay
    Phase 04 needs, but its lifecycle-tracking pattern (reservation
    surviving until reconciliation) is a candidate model for how the
    portfolio engine should track provisional capital commitments too.
  - **Balance/margin tracking** (`get_account_balance`, real for
    Alpaca, deliberately not implemented for ccxt) and **price-feed
    extension to Alpaca + IBKR** (beyond ccxt-only).
  - **Signal-provider value analysis + subscription cost tracking**
    (`app/provider_value.py`, `app/provider_scout.py`) -- a FIFO-lot P&L
    attribution per (source, analyst, asset_class) and a cost/subscription
    tracker with a "still worth paying for" verdict, plus a scheduled
    free-provider promotion scan. **This is the single-account,
    single-owner precursor to this package's whole Phase 03/04 scope,
    and the two must not be confused or merged carelessly**: the existing
    module explicitly disclaims managed-lifecycle stop/target/trailing
    exits (they never reach the `orders` table it replays), uses a
    volume-weighted-average/FIFO-lot method scoped to ONE owner's own
    accounts, and has no concept of a customer, a subscription entitlement,
    or a published product. The new commercial economic journal (Phase 03)
    is a SEPARATE, more complete accounting system (four independent
    books, Decimal-safe money, append-only correction events, verified
    metric registry) -- it may reuse `provider_value.py`'s FIFO-lot
    reasoning as a reference for HOW to attribute shared-position P&L to
    an originating identity, but it must not simply wrap or re-export the
    existing module as if it were commercial-grade.
  - **NinjaTrader signal source**: closed from a disclosed stub to a
    real, tested implementation (`app/sources/ninjatrader.py`,
    `POST /ninjatrader/webhook`, `ninjascript/SignalCopierAutoJournal.cs`
    -- the C# side is disclosed-unverified pending a real NinjaTrader
    install). Not directly relevant to this commercial package's own
    scope, but changes the "what sources exist" inventory Phase 01's
    rights registry and Phase 04's sleeve definitions should draw from.

## Boundary this package's own docs already establish correctly

- One repository; a separate `commercial_api`/`portfolio_worker`/
  `publication_worker` process; the owner's private execution
  application (`signal-copier/`) is retained UNCHANGED and UNTOUCHED by
  this build except where a narrow, reviewed outbox/projection bridge is
  later added (Phase 03) -- never a live query into its broker
  credentials or account/flatten routes.
- New customer identity/subscription/product records target PostgreSQL +
  Supabase Auth + RLS. This build (`signal-portfolio-commercial/`) uses a
  **local disposable PostgreSQL cluster** for all dev/test work (verified
  available in this environment: `postgresql-16` server binaries +
  `psycopg` driver, both installable/usable directly -- see
  `tests/conftest.py`). No Supabase project, Stripe merchant account, or
  any other hosted external account is created by this build; those are
  the owner's own external deployment actions (CARD-4 and the Supabase
  project itself).
- Every screenshot claim (the "$68-to-$750K", "48-hour", JEV) in the
  originating request is explicitly out of scope as evidence, per the
  package's own doc 00 -- not used anywhere in this build's tests,
  copy, or acceptance criteria.

## What this discovery step changes about the plan

Nothing structural -- the package's own phase order (01 rights -> 02
architecture -> 03 accounting -> 04 research -> 05 publication -> 06/07
platforms -> 08 billing -> 09 website -> 10 PAMM/MAM -> 11 optional AI ->
12 validation) is sound and is followed as specified. This document exists
so a later session resuming this build (via `ops/COMMERCIAL_RESUME.md`)
starts from an accurate picture of the current app, not the package's own
now-stale single-commit snapshot.
