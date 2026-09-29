# FIFO-Lot Analyst Attribution Design

Source of truth: `app/services/analyst_attribution.py`. See also ADR-0005.

## Problem

INT-026 ("Analyst allocation survives shared symbol"): two distinct analysts can
trade the same instrument, in the same `Book.PLATFORM` account, under separate
allocations. Per-analyst ownership of realized P&L must be retained, and the sum
across analysts must reconcile exactly to the account's real aggregate. Joining a
closing fill to "the" analyst by account/symbol alone, or splitting a close
arbitrarily between whichever analysts are involved, are both explicitly prohibited
outcomes.

`app/services/platform_performance.py` cannot answer this: it keeps one blended
volume-weighted average cost per `(tenant, book, instrument)`, which is correct for
current holdings but erases, once a second analyst's fill touches the same
instrument, which analyst's entry actually contributed to that average.

## Algorithm

Ported from signal-copier's own `app/provider_value.py::compute_provider_value`
(same algorithm; reimplemented rather than imported, for the same `Decimal`-not-
`float` and no-`asset_class`-dimension reasons `platform_performance.py` already
gives for reimplementing signal-copier's `app/economics.py`).

Per `(tenant, book, instrument)`:

1. **State**: a signed `open_quantity` (positive = net long, negative = net short —
   same convention as `platform_performance.py`) plus an ordered list of open
   **lots**. Each lot: `quantity` (always positive, remaining), `entry_price`,
   `analyst` (the entry fill's `originating_analyst_id`, `None` is its own real
   bucket), `opened_at`, and a running `episode_pnl`.

2. **Replay** every real fill (excluding correction rows — a fee correction is never
   itself replayed as a second trade) in `(event_time, created_at)` order:

   - **Same-side fill** (opening, or adding to the position on the same side): push
     a brand-new lot, tagged with *this fill's own* analyst. `open_quantity`
     updates by the signed quantity.
   - **Opposite-side fill** (reducing, or flipping through flat): consume existing
     lots **FIFO** — oldest lot first. For each lot consumed:
     `realized = (entry.price - lot.entry_price) * direction * consumed *
     entry.multiplier`, where `direction = +1` if the position being closed is
     long, `-1` if short (the same single-formula trick `platform_performance.py`
     uses for its own directional sign, correct for either side without a branch).
     Realized P&L is credited to **that lot's own analyst**, never to the analyst on
     the closing fill. When a lot's `quantity` reaches zero, it is popped and
     recorded as one `CompletedEpisode`. If the closing fill's quantity exceeds
     every existing lot combined (a flip through flat), the remainder opens a fresh
     lot in the new direction, attributed to *this* fill's own analyst.

3. **Buckets**: `AnalystInstrumentPerformance`, keyed `(instrument, analyst)` —
   `analyst=None` is a real, distinct bucket, never merged into another analyst's
   numbers. Tracks `realized_pnl`, `closing_fills`, `entries_opened`.

## Completed episodes / win rate

A "completed episode" is exactly one lot, from the moment it opens (or is
re-opened by a flip through flat) to the moment it is fully consumed by FIFO
closes — the same lot-tracking the algorithm already does for per-analyst P&L,
never a second, separately-invented open/close concept. `CompletedEpisode.pnl` is
the sum of every real closing fill's realized P&L against that lot (a lot closed
across several partial fills is still exactly one episode). `is_win` is `pnl > 0`.
`win_rate = winning_episode_count / completed_episode_count`, `None` (never a
fabricated 0%) when no episode has completed yet. Ported the same way
signal-copier's own `SymbolEconomics` derives a win rate from its own completed
round trips.

## Reconciliation invariant

`AnalystAttributionReport.account_total_realized_pnl` — the sum of every
`(instrument, analyst)` bucket's `realized_pnl` — is, by construction of the
replay, always equal to
`platform_performance.compute_book_performance(...).realized_pnl` for the same
tenant/book: every unit of realized P&L is credited to exactly one consumed lot's
own analyst bucket, never duplicated, never dropped. This is the property tests
assert to catch a future change to either module that would break the
reconciliation.

## Disclosed scope limits

- Gross-only: no per-analyst open-position/average-cost reporting, matching
  signal-copier's own disclosed scope for the same algorithm.
- `follower_connection_ids` filtering exists (same contract as
  `platform_performance.py::load_ordered_root_entries`) but is only meaningful, and
  only ever passed, for `book == FOLLOWER` — to scope a replay to one customer's own
  connections rather than a tenant's whole FOLLOWER book across every customer.
