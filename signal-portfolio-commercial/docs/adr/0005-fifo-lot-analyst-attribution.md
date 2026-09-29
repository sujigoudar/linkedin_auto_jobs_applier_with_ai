# ADR-0005: FIFO-lot analyst attribution for provider-value computation

## Status

Accepted. Implemented in `app/services/analyst_attribution.py`
(`compute_analyst_attribution`).

## Context

INTEGRATION_ACCEPTANCE_CASES.json INT-026 ("Analyst allocation survives shared
symbol"): when two distinct analysts trade the same instrument under separate
allocations within the same `Book.PLATFORM` account, "per-analyst ownership
[must be] retained; account totals reconcile to actual aggregate." The prohibited
outcomes are explicit: "Join only by account/symbol" and "Exit allocated
arbitrarily."

`app/services/platform_performance.py` already computes book-level performance, but
it keeps a single running volume-weighted average cost per `(tenant, book,
instrument)` — correct for "what does this account currently hold and at what
basis," but a blended average erases which analyst's entry contributed what once a
second analyst's fill touches the same instrument. There is no way to recover, after
the fact, which analyst a blended average cost came from, and naively joining a
closing fill to "the" analyst by account/symbol would arbitrarily credit whichever
analyst happens to be attached to the reducing fill — which may be neither analyst
who actually opened the position being closed.

## Decision

Reimplement, per `(tenant, book, instrument)`, the same FIFO-lot algorithm
signal-copier's own `app/provider_value.py::compute_provider_value` already uses
(ported, not imported — same `Decimal`-not-`float` and no-`asset_class`-dimension
reasoning `platform_performance.py` already documents for its own reimplementation
of signal-copier's `app/economics.py`):

- Replay every real fill (excluding correction rows) in `(event_time, created_at)`
  order.
- Keep a signed `open_quantity` and an ordered list of open **lots** — one per
  same-side entry fill — each tagged with its own `originating_analyst_id`
  (`None` is its own real bucket, never folded into another analyst's numbers).
- A same-side fill opens a new lot, attributed to that fill's own analyst.
- An opposite-side fill **consumes existing lots FIFO** — oldest lot first —
  realizing P&L against each consumed lot's own entry price, credited to **that
  lot's own analyst**, regardless of which analyst's signal (if any) triggered the
  close.
- A remainder left over after every existing lot is consumed (a flip through flat)
  opens a fresh lot attributed to the closing fill's own analyst.
- A fully-consumed lot becomes one `CompletedEpisode` (CU-06's own "episode/
  trade-completion concept"), the natural unit for win-rate reporting — never a
  second, separately-invented open/close concept.

`account_total_realized_pnl` (the sum of every per-analyst bucket) is, by
construction of the replay, always equal to
`platform_performance.compute_book_performance(...).realized_pnl` for the same
tenant/book — every unit of realized P&L is credited to exactly one consumed lot's
analyst, never duplicated or dropped.

## Consequences

- Two analysts trading the same instrument under the same account reconcile
  correctly: each analyst's own realized P&L reflects exactly the lots FIFO-matched
  to their own entries, and the two together sum to the real account total.
- The report is deliberately gross-only and open-quantity-unaware for per-analyst
  reporting (same disclosed scope as signal-copier's own module) — no per-analyst
  open-position/average-cost view exists.
- A future change to `platform_performance.py`'s own average-cost algorithm must not
  be assumed to also fix per-analyst attribution — the two modules intentionally use
  different internal representations (blended average vs. FIFO lots) for different
  questions, and must be kept reconciled by this ADR's own invariant, not merged.
