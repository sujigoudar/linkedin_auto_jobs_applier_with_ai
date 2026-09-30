# ADR-0005: Distinct requested/confirmed/applied/outstanding quantity fields, not one `quantity` column

Status: Accepted
Date: 2026-09-29

## Context

`orders` previously carried `requested_quantity` and `filled_quantity`
alongside an older behavior (referenced by `app/db.py`'s `AUD-01`
comment) of "optimistically apply the requested quantity to positions
while an async broker's order is still PENDING." For a broker that
reports PENDING rather than a synchronous confirmed fill (SignalStack,
Alpaca, IBKR, NinjaTrader, Rithmic), that behavior meant
`positions.net_quantity` could reflect a quantity the broker had not
actually confirmed yet — a real, if well-intentioned, discrepancy
between "what this service believes it owns" and "what the broker has
actually confirmed," with nowhere to record the difference.

An external audit (`AUD-01`) required an honest model: what was asked
for, what the broker has actually confirmed, what this exact save
applied to the tracked position, and how much is still genuinely
uncertain — as four distinguishable facts, not one number conflating
them.

## Decision

`orders` carries four related-but-distinct nullable REAL columns
alongside the pre-existing `requested_quantity`:

- `confirmed_cumulative_fill` — the broker's own reported cumulative
  filled quantity for this order, exactly as given
  (`result.filled_quantity`), never guessed and never defaulted to the
  requested quantity. `NULL` means the broker has not confirmed
  anything yet for this specific PENDING order.
- `applied_execution_delta` — the actual signed-by-side quantity this
  exact save applied to `positions.net_quantity` (via `record_fill`),
  if anything. `0.0` (not `NULL`) is a genuine, known fact — "this
  save confirmed nothing new and touched the position not at all" —
  distinct from `NULL` ("this row predates the field / never
  applicable").
- `outstanding_possible_fill` — `requested_quantity -
  confirmed_cumulative_fill` at the moment this row was written: the
  quantity that could still be confirmed by the broker and must be
  treated as uncertain exposure, not zero, while the order remains
  PENDING. `0.0` once the order reaches a terminal status (nothing
  more can possibly fill).

Together with `positions.net_quantity` — which, by contract from this
pass on, **is** `actual_remaining_ownership`, updated **only** from a
broker-confirmed fill, never from a PENDING order's merely-requested
quantity — these four fields fully replace the old optimistic-apply
behavior. All three new columns are nullable specifically to
distinguish "this row predates this migration" or "this call site
hasn't been updated to populate it" from a fabricated `0.0` standing
in for genuinely unknown.

`SignalStore.get_outstanding_possible_fill` is the live, queryable
aggregate other code (the capital allocator, the position-detail UI)
should call instead of reading `orders.outstanding_possible_fill` off
one row directly.

## Consequences

- `positions.net_quantity` no longer moves ahead of what the broker
  has actually confirmed — a broker that reports PENDING leaves it
  unchanged until `app/reconciliation.py` (or a synchronous
  partial-fill report alongside PENDING) confirms a real quantity.
- Uncertain, in-flight exposure — quantity that *could* still fill but
  hasn't been confirmed either way — is now a first-class, queryable
  fact (`outstanding_possible_fill`) rather than invisible until it
  resolves.
- Every `save_order_result` call site had to be updated to pass these
  fields explicitly and correctly (see that method's own extensive
  docstring in `app/db.py`) — a caller that hasn't been updated yet
  correctly leaves them `NULL` rather than guessing, so an
  unmigrated call site is honestly incomplete, not silently wrong.
- `capital_allocator.py`'s own docstring documents the corresponding
  reservation-release timing this quantity model interacts with: a
  PENDING order's exposure isn't released until either a terminal fill
  confirms it or, for a broker_order_id-less PENDING, immediately (see
  ADR-0007) — the two models are deliberately consistent about what
  counts as "confirmed" versus "still uncertain."
