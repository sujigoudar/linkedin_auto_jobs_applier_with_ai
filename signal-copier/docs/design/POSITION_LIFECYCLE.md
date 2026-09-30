# Position Lifecycle: the managed-lifecycle state machine

Source of truth: `app/lifecycle/manager.py` (`PositionLifecycleManager`,
~2000 lines), `app/lifecycle/models.py` (the data model), and
`app/lifecycle/close_arbiter.py` (`CloseArbiter`). This document
describes the design already implemented there — it does not introduce
new behavior.

## Why this subsystem exists

`PositionLifecycleManager` is the fallback for accounts/brokers that
cannot submit entry + stop + take-profit as a single atomic
bracket/OCO order (`BrokerAdapter.supports_native_bracket = False`).
When a broker *can* do that atomically, its `place_order` sends
`stop_loss`/`take_profit` directly and this subsystem is not involved.

Firing a protective stop order and a full-size profit-target order
independently, against a broker that treats them as unrelated orders,
risks exactly the failure this design is built around: two independent
sell orders for the same shares, both able to fill, for more than what
is actually owned. The subsystem's answer is to make every exit funnel
through one authority:

```
ENTRY -> actual fill observed -> protect the filled quantity FIRST
      -> manage targets/trailing as logical (app-side) instructions
      -> coordinate every exit through one CloseArbiter
```

A logical target firing, a trailing-stop ratchet, the protective stop
itself filling, a provider EXIT signal, a time exit, and an emergency
exit are all, without exception, funneled through `CloseArbiter`,
which is the only thing allowed to authorize reducing a tracked
position (see `app/lifecycle/close_arbiter.py`).

## Core data model (`app/lifecycle/models.py`)

- **`PositionPlan`** — computed once, before the entry is submitted:
  account/symbol/side, `planned_quantity`, `initial_stop`, `targets`
  (list of `Target`), an optional `TrailingPolicy`, `time_exit`,
  `max_risk`, and `entry_signal_id` (the originating `Signal.id`,
  carried for the position's whole life so a later CLOSE with no
  natural signal of its own can still be recorded under the same
  `orders.family_id` as its entry). Sizing here is a plan, not a
  guarantee — what actually filled is tracked separately.
- **`Target`** — a logical, app-managed profit action, not necessarily
  a standing broker order. `action` is `SELL`, `TIGHTEN_STOP`, or
  `ACTIVATE_TRAIL`. `reduce_fraction` (required for `SELL`) is a
  fraction of the *originally planned* quantity, not whatever happens
  to be owned when it fires.
- **`TrailingPolicy`** — `activate_at_price`, `trail_distance`, an
  `active` flag, and the current `floor_price` once active.
- **`StopRecord`** — `desired_price` / `submitted_price` /
  `broker_confirmed_price`, `broker_order_id`, `protected_quantity`,
  and `status` (`ProtectionStatus`: `UNPROTECTED` → `STOP_PENDING` →
  `STOP_CONFIRMED`). `confirmed_at` is the exact moment `status` last
  became `STOP_CONFIRMED`, reset to `None` whenever coverage is lost
  again (rejected, errored, or ambiguous submission) so a stale
  confirmation timestamp never survives a real loss of coverage.
- **`PendingEntry`** — an entry order whose broker response reported
  PENDING rather than a synchronous, final fill. `on_entry_fill` must
  **not** be called while this is open; `resolve_pending_entry`
  (driven by `app/reconciliation.py` polling `broker_order_id`) is the
  only thing allowed to settle it — a zero-fill, confirmed-cancelled
  outcome unregisters the plan; any positive confirmed fill calls
  `on_entry_fill` with exactly that amount, even if less than
  `requested_quantity`. `unresolved_remainder` is the genuinely
  uncertain part still surfaced to `get_outstanding_possible_fill`.
  `reserved_notional` carries the capital-allocator reservation for
  this entry forward from admission time (ADR-0007), released only
  once reconciliation resolves it.
- **`PendingExit`** — an exit (target fill, trailing ratchet, provider
  close) whose broker order reported PENDING. Deliberately **not**
  folded into `StopRecord`: it tracks a distinct fact — up to
  `requested_quantity - confirmed_filled_quantity` shares have no
  covering stop, because the old stop was already cancelled/reduced to
  free them, and how many will actually sell is still unknown. The
  stop is not resized/restored while `phase != COMPLETE`
  (`remainder_resolved` is `False`) — restoring against
  `confirmed_owned_quantity - confirmed_filled_quantity` while some of
  `requested_quantity` might still fill would recreate exactly the
  multi-order oversell this subsystem exists to prevent. `phase`
  (`TransferPhase`) tracks where the exit stands:
  `STOP_REDUCED` → `EXIT_SUBMITTED` → `AWAITING_REMAINDER_RESOLUTION`
  → `RESTORING` → `COMPLETE`. `stop_amended` distinguishes "the same
  resting stop order was shrunk in place" from "the old stop was
  cancelled outright," which changes how the terminal restore step
  must act on it.
- **`PositionLifecycle`** — the aggregate: `plan`,
  `confirmed_owned_quantity`, `stop`, `closed`, `pending_exit`,
  `pending_entry`, plus real MAE/MFE tracking
  (`entry_price`, `highest_price_since_entry`,
  `lowest_price_since_entry`, `mae`, `mfe`, `has_price_data`) and a
  last-observed-price snapshot (`last_observed_price`, for
  `app/equity_history.py`'s unrealized-P&L calculation — a distinct
  concept from the MAE/MFE extremes, which only ever ratchet outward).
  `has_unresolved_entry` (True while `pending_entry` is open and
  unresolved) exists specifically so `closed` never becomes `True`
  while a still-working entry's remaining fill is unknown — being flat
  *right now* is not the same as having no remaining obligation.
  Halt state lives on `CloseArbiter`, never duplicated here, so the
  lifecycle and the arbiter's ledger can never disagree about it.

## The stop-resize transition (`request_exit`)

A profit target selling part of a protected position cannot simply
submit a sell order alongside an unchanged stop — if both later fill,
that is an oversell (e.g. a 62-share position with a 62-share stop
plus a 15-share target sell settling at 77 shares sold against 62
owned). `request_exit()` performs a specific sequence, holding the
position's `CloseArbiter.transition()` lock for the whole sequence so
nothing else can touch this position concurrently:

1. Cancel/reduce the existing protective stop — and do **not** proceed
   if that cannot be *confirmed*: a cancel that might have raced a
   real fill is treated as "the stop may have already filled," never
   as success.
2. Submit the exit order.
3. Observe what actually filled (not what was requested — a partial
   fill changes the arithmetic for step 4).
4. Recompute the true remaining quantity from the confirmed owned
   total, never from the plan.
5. Submit a replacement stop sized to that true remainder.
6. Release the lock.

`_compute_reduction_plan` is the pure (no I/O, no mutation) planning
step this sequence uses, extracted specifically so a read-only preview
(`preview_reduction`) computes the exact same numbers `request_exit`
itself will act on, rather than a second, independently maintained
copy that could drift. `_compute_trailing_candidate` is the equivalent
pure extraction for the trailing-stop ratchet logic, used by both
`_update_trailing` and `preview_stop_change`.

## Writer-lease fencing on every mutating path

`PositionLifecycleManager.on_entry_fill`, `resolve_pending_entry`,
`on_price_update` (guards the time-exit/target-firing/tighten-stop/
trailing paths — never the honest price/MAE/MFE observation itself),
`request_exit`, `resolve_pending_exit`, and
`retry_unprotected_positions` are each reached both from
`app/engine.py`'s signal-driven paths and directly from background
loops (`app/reconciliation.py`, `app/pricing.py`'s `PriceMonitor`) that
never go through `app/engine.py` at all — so each calls
`self.lease_guard.require_active()` independently before touching the
broker (see ADR-0002).

## Durable command ledger on every broker call

Every real broker-write call site in this manager — `_submit_exit_order`
for close/flatten, and the stop placement/replace/cancel call sites for
`stop_change`/`replace`/`cancel` — opens a `command_ledger` entry
before calling the broker and resolves it afterward, per the
three-step contract in `app/command_ledger.py` (see ADR-0004).

## Stop/target event history (`stop_target_events`)

`PositionLifecycleManager`'s call sites append an append-only event
(`StopTargetEventType`: `STOP_PLACED`, `STOP_TIGHTENED`,
`PROTECTION_FAILED`, `TARGET_HIT`) at the exact moment each real state
change happens — never backfilled or reconstructed after the fact.
See `docs/database/SCHEMA.md`'s `stop_target_events` section for the
full column semantics.

## Documented gap

Startup reconciliation against the broker's own live position/order
state is **not implemented**: this manager trusts its own in-memory +
`CloseArbiter` bookkeeping, seeded by `on_entry_fill`. A process
restart loses in-memory lifecycle state for any position it was still
managing unless `lifecycle_state` (the crash-resumable persistence
table — see `docs/database/SCHEMA.md`) restores it via
`PositionLifecycleManager.restore_from_store`. Wiring
`BrokerAdapter.get_broker_position` into a genuine startup
reconciliation pass is a named next step, not done as of this document.
