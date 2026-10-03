# Capital Allocation: the admission gate

Source of truth: `app/capital_allocator.py`. This document describes
the real admission-gate logic that governs whether a new entry is
allowed to submit at all. See ADR-0007 for why it is fail-closed by
design.

## Scope

`CapitalAllocator` is deliberately narrower than a complete
institutional risk engine. It enforces:

- **Per-account notional ceiling** — `DestinationAccount.
  max_notional_exposure`, opt-in, `None` (unenforced) by default.
- **Owner-wide notional ceiling** — `app.config.
  MAX_OWNER_NOTIONAL_EXPOSURE`, opt-in, `None` by default. Sums
  `confirmed_open_notional` plus this process's own pending
  reservations across every account this single-tenant deployment's
  `RoutingConfig` knows about.
- **Risk-basis sizing** — `DestinationAccount.risk_percent_of_equity`,
  opt-in, `None` by default. Rejects an entry whose risk-to-stop
  (`|entry_price - stop_loss| * quantity`) would exceed that
  percentage of the account's real, freshly-fetched equity
  (`BrokerAdapter.get_account_balance`). Fails closed whenever
  `stop_loss` is missing on the signal, or the broker cannot report a
  real `equity` figure — see `app/engine.py`'s `_check_risk_basis`.

It explicitly does **not** attempt multi-currency conversion,
per-analyst overlap accounting, stress-loss/scenario modeling, or
cross-account netting beyond plain summation — all named and disclosed
in the module's own docstring rather than silently absent.

## Confirmed exposure: `confirmed_open_notional`

"Current exposure" for an account is always the account's real,
confirmed open notional: `sum(|open_quantity| * average_cost)` per
*resolved* symbol, replayed from the same confirmed-fill journal
`app/economics.py` already trusts — never a separately maintained
running total that could drift from what actually executed.

`confirmed_open_notional` returns an `ExposureReport`:

```python
@dataclass
class ExposureReport:
    notional: float
    unresolved_symbols: list[str] = field(default_factory=list)
```

A symbol `app/economics.py`'s replay could not resolve an
`average_cost` for (a fill with a missing/invalid quantity or price,
or an unresolved side) is **never** folded into `notional` as if it
contributed `0.0` — it is listed in `unresolved_symbols` instead.
`unresolved_symbols` non-empty means `notional` is a **known
understatement** of the account's true exposure, and every gate in
this module treats that as automatic grounds to reject new admissions
for the account, rather than sizing against a figure known to be
incomplete.

`owner_wide_exposure` sums `confirmed_open_notional` plus
`allocator.pending_reservation(account_id)` across every account
passed in, with `unresolved_symbols` prefixed `account_id:symbol` so
two accounts' own lists can never be confused — gated the same way at
the owner-wide level.

## Admission: `admit()`

```python
async def admit(self, account_id, notional, *, confirmed_exposure, max_exposure, signal_id=None) -> bool:
    async with self._locks[account_id]:
        if confirmed_exposure + self._pending[account_id] + notional > max_exposure:
            return False
        if self.store is not None and notional:
            self.store.create_capital_reservation(uuid4(), account_id, notional, signal_id=signal_id)
        self._pending[account_id] += notional
        return True
```

Holding the per-account `asyncio.Lock` across "read confirmed
exposure, check, provisionally reserve" is what closes the race two
signals for the same account, arriving concurrently in this process,
would otherwise create: both reading the same confirmed exposure
before either order is placed, both admitted even though only one
fits under the ceiling. The second concurrent caller sees the first's
reservation before it decides.

`owner_lock` (a single `asyncio.Lock`) extends this across the
owner-wide gate, acquired *before* the per-account lock by any caller
that needs it, so two concurrent owner-wide checks can never both read
the same not-yet-reserved state and both admit past the owner-wide
ceiling. **Still not closed**: an admission enforcing only a
per-account gate (no owner-wide ceiling configured, so it never
touches `owner_lock`) can still run concurrently with an owner-wide
admission on a *different* account and mutate that account's own
`_pending` entry mid-computation — accepted because the owner-wide
gate is opt-in and off by default; fully closing this would mean
funneling every admission through one global lock regardless of
whether any account actually uses the owner-wide gate.

`reserve_locked` is the lock-free primitive for a caller (this
module's own multi-gate orchestration in `app/engine.py`'s
`_try_reserve_capital`) that already holds `account_lock(account_id)`
and is enforcing more than one gate atomically in one locked section.

## Zero-price signals fail closed

Whenever *any* gate is configured for an account (notional ceiling,
owner-wide ceiling, or risk-basis sizing) and the admitting signal
carries no price this module has no other way to resolve, admission is
now **rejected**, never silently skipped — skipping was economically
identical to admitting an unbounded, unsized order. See ADR-0007.

## Reservation release timing

- **REJECTED / FILLED**: the reservation is released
  immediately at the call site — the broker definitely rejected it
  (REJECTED), or the fill is now immediately part of confirmed exposure the next
  admission call will see (FILLED).
- **PENDING with a real `broker_order_id`**: the reservation is
  **held**. Releasing it immediately, the same as the terminal cases,
  would briefly count it toward neither the reservation ledger nor
  confirmed exposure (confirmed exposure only counts a symbol once its
  `average_cost` is resolvable from an actual recorded fill) — a
  second signal admitted in that window could push real total exposure
  past the configured ceiling. `app/reconciliation.py`'s polling loop
  is guaranteed to eventually observe this exact order reach a
  terminal status and is the one place allowed to release the
  reservation then.
- **Ambiguous submission** (the broker call raised or timed out, returned
  ERROR, or returned PENDING with no `broker_order_id` to poll) —
  **held** (ALLOC-05, superseding the earlier "releases immediately"
  rule). The order may have been accepted at the venue, so it is an
  unresolved obligation: the account and strategy reservations stay
  until `SignalCopierEngine.resolve_unknown_submission(key,
  outcome="not_placed", evidence=...)` records independent evidence that
  nothing was placed. A submission that did reach the venue is not
  released on an operator's word; its fill must be recorded through
  reconciliation so confirmed exposure replaces the reservation.
  Only entry commands are covered; the unresolved ledger row shows in
  `list_unresolved_command_ledger_entries`.

## Strategy ceiling and joint admission (ALLOC-03)

`strategy_budgets` holds an opt-in global notional ceiling per strategy
(keyed by signal `source`). It is counted **once across every account**
the strategy can use, so adding accounts never multiplies it. After the
account gates pass, `CapitalAllocator.reserve_with_strategy_ceiling`
runs one `BEGIN IMMEDIATE` transaction that reads the strategy's
confirmed notional (`confirmed_strategy_notional`, the same fill replay
filtered to the strategy's own signals) plus its unresolved reservations
(`capital_reservations.strategy_key`, all accounts, all processes) and
inserts the reservation only if it fits. A missing price fails closed;
unresolved symbols block the strategy. Releases match the reservation by
signal id first so another strategy's identically sized reservation is
never resolved by mistake.

The account-level gate still serializes in-process with `asyncio` locks;
only the strategy gate is atomic across processes. Cross-process safety
for accounts rests on the single-writer lease (ADR-0002).

## Durability across a restart (P0-4)

`admit()`/`reserve_locked()` write a durable row to
`capital_reservations` the instant admission succeeds — **before**
the broker call it is gating even starts, unlike `orders.
reserved_notional`, which is only ever set after `save_order_result`
runs (i.e. after the broker call has already returned). This closes a
real gap: a remote broker can accept an order and this process can
still die before `save_order_result` ever commits, leaving
`orders.reserved_notional`'s own release accounting nothing to work
with.

`CapitalAllocator.__init__(store=...)` sums every still-unresolved row
in `capital_reservations`, per account, into `_pending` on
construction — a restart does not start from "no in-flight admissions
to lose." An order the broker already accepted before the crash stays
reserved (uncertain) until reconciliation independently confirms its
outcome. `CapitalAllocator()` with no `store` (e.g.
`app/backtest/replay.py`'s synthetic allocator) behaves exactly as
before this change — in-memory only, no restore — since a backtest has
no real broker and nothing to survive a restart for.

`capital_reservations` is not yet integrated with `command_ledger`'s
own command/intent id space — see ADR-0004's Consequences section for
the named follow-up.
