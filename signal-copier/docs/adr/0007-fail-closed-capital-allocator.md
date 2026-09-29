# ADR-0007: Fail-closed capital allocator — unresolved exposure and unpriced signals block admission

Status: Accepted
Date: 2026-09-29

## Context

`app/capital_allocator.py`'s admission gate is the mechanism that
enforces per-account notional ceilings, owner-wide notional ceilings,
and risk-basis (percent-of-equity) sizing before a new entry is
allowed to submit. An external release audit identified two fail-open
gaps in the original, narrower slice of this gate:

1. Admission used to be silently **skipped** whenever the admitting
   signal had no `price` at all, even when a real ceiling/risk gate
   was configured for the account. Skipping is a pass by omission —
   economically identical to admitting an unbounded, unsized order.
2. A position this replay could not resolve an `average_cost` for
   (`AccountEconomics.incomplete_symbols` in `app/economics.py`, e.g. a
   fill with a missing/invalid quantity or price, or an unresolved
   side) used to contribute exactly `0.0` to
   `confirmed_open_notional`'s total — treating genuinely **unknown**
   exposure as economically equivalent to **no** exposure. For a hard
   risk gate, that is never safe: real notional could be sitting
   there, invisible to the ceiling.

## Decision

Both gaps were closed by making the allocator fail closed instead of
fail open:

1. Whenever **any** gate is configured for an account (a notional
   ceiling, the owner-wide ceiling, or risk-basis sizing) and the
   admitting signal carries no price this module has no other way to
   resolve, admission is **rejected**, not skipped.
2. `confirmed_open_notional` returns an `ExposureReport` carrying both
   the resolved `notional` total *and* `unresolved_symbols` — the
   positions this replay could not price. `unresolved_symbols`
   non-empty means `notional` is a **known understatement**, never a
   reliable "the rest is zero" figure, and every admission gate in
   this module treats a non-empty list as an automatic reject for that
   account: new admissions are blocked until the position resolves.

This was a deliberate choice over inventing a "last-known-good mark"
estimate: the replay has no reliable historical mark to fall back to
beyond the fill rows `app/economics.py` itself already declined to
trust for that symbol (see that module's own "incomplete stays
incomplete" rule) — a fabricated number would be worse than refusing
to size against it at all.

Risk-basis sizing (`DestinationAccount.risk_percent_of_equity`) is
governed by the same principle: an entry is rejected whenever the
admitting signal has no `stop_loss`, or the account's broker cannot
report a real, freshly-fetched `equity` figure right now — never a
guessed or cached value.

The owner-wide ceiling
(`app.config.MAX_OWNER_NOTIONAL_EXPOSURE`) sums `confirmed_open_notional`
plus in-flight reservations across every account this single-tenant
deployment's `RoutingConfig` knows about — for a single-owner service,
"every configured account" already is "owner-wide." `unresolved_symbols`
at the owner-wide level is the union (prefixed `account_id:symbol`) of
every contributing account's own unresolved list, gated the same way.

Provisional reservations are held in-memory per account (an
`asyncio.Lock`-guarded `_pending` ledger) and, since P0-4, durably
recorded in `capital_reservations` the instant admission succeeds —
before the broker call it's gating even starts — so a crash between
admission and order placement does not silently forget a real,
broker-accepted reservation on restart (`CapitalAllocator.__init__`
reloads every still-unresolved row, summed per account).

## Consequences

- A misconfigured or temporarily unpriceable signal now blocks trading
  for the affected account rather than trading unsized — the explicit,
  accepted cost of eliminating a fail-open economic gap.
- A single position this replay cannot price can halt all new
  admissions for that account until reconciliation resolves it — a
  real operational consequence, not a theoretical one, and the
  intended one: the alternative (silently under-counting exposure) was
  judged strictly worse.
- Several scope boundaries remain explicitly, honestly unimplemented
  rather than fabricated: no multi-currency/basis-currency conversion,
  no per-analyst overlap accounting, no stress-loss/scenario modeling,
  and no cross-account netting for the same owner beyond plain
  summation — see `app/capital_allocator.py`'s module docstring for
  the full, disclosed list.
- A PENDING order with no `broker_order_id` at all (nothing to poll)
  still releases its reservation immediately — the same ambiguous case
  as a raised exception or an ERROR result — because deferring release
  for an order nothing is guaranteed to ever revisit would risk a
  reservation that never releases, judged worse than the narrower
  timing gap this leaves open.
