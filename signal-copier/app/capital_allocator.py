"""E03 (bounded): a per-account notional-exposure admission gate, closing
one specific race the adoption plan's own E03 acceptance obligations
call out -- "competing signals cannot independently spend the same
capacity."

Scope, deliberately narrower than the full plan (a complete risk basis,
released-fallback-protection, deadline, price-bounds, fee/stress
allowance and legal-units checklist before every admission):

- Only a NOTIONAL exposure ceiling is enforced (`DestinationAccount.
  max_notional_exposure`, opt-in and None by default). No basis-currency
  conversion, per-analyst overlap accounting, or owner-wide ceiling across
  accounts -- each account's own ceiling is independent.
- The check only runs when the signal carries a `price` (a market order
  with no price gives no reliable notional at admission time -- silently
  guessing one would be worse than not checking at all, so this is
  skipped and disclosed rather than invented).
- "Current exposure" is the account's REAL, confirmed open notional --
  `abs(open_quantity) * average_cost` per symbol, replayed from the same
  confirmed-fill journal app/economics.py already trusts (never a
  separately-maintained running total that could drift from what
  actually executed).
- What this closes: two signals for the same account, arriving
  concurrently in this process, both reading that same confirmed
  exposure before either one's order has been placed, and both being
  admitted even though only one fits under the ceiling. `admit()` holds
  a per-account asyncio.Lock across "read confirmed exposure, check,
  provisionally reserve" so the second concurrent caller sees the
  first's reservation before deciding. The provisional reservation is
  released as soon as that order call returns for REJECTED/ERROR
  (nothing happened, nothing to keep reserved) and for FILLED (the fill
  is immediately part of the confirmed exposure the next admission call
  will see).
- **PENDING reservation timing (previously a known gap, now closed for
  the pollable case):** a PENDING order is NOT yet part of confirmed
  exposure (confirmed exposure only counts a symbol once its
  `average_cost` is resolvable from an actual recorded fill -- see
  `confirmed_open_notional` below), so releasing its reservation
  immediately, the same as REJECTED/ERROR/FILLED, would briefly count it
  toward neither the reservation ledger nor confirmed exposure -- a
  second signal admitted in that window could push real total exposure
  past the configured ceiling. When the PENDING result carries a real
  `broker_order_id`, app/reconciliation.py's polling loop is guaranteed
  to eventually observe this exact order reach a terminal status
  (FILLED/REJECTED) and is the one place allowed to release the
  reservation then -- see app/engine.py's `_try_reserve_capital`
  docstring and both its callers, `app/db.py`'s
  `save_order_result(reserved_notional=...)` for a plain account, and
  `app/lifecycle/manager.py`'s `PendingEntry.reserved_notional` /
  `resolve_pending_entry` for a managed_lifecycle account.
  **Still not closed:** a PENDING result with no `broker_order_id` at
  all (nothing to poll -- the same ambiguous case as EXE-01/EXE-01b's
  raised-or-returned ERROR) still releases immediately, same as before
  this fix. Deferring release for an order nothing is guaranteed to ever
  revisit would risk a reservation that's never released, which would be
  worse (silently blocking all future admissions for that account) than
  the timing gap it would close -- this is a narrower, deliberately
  bounded slice of the original gap, not the whole thing.
- **P0-4: this provisional ledger is now durable across a restart.**
  Previously this docstring claimed "a restart has no in-flight
  admissions to lose (nothing survives a request that never returned)"
  -- an external release audit correctly called that out as unsound: a
  remote broker can accept an order (see e.g. `_try_reserve_capital`'s
  own PENDING-with-broker_order_id case) and this process can still die
  before anything else observes that acceptance. `admit()` now writes a
  durable row to the `capital_reservations` table (see app/db.py's own
  SCHEMA comment on it) the INSTANT admission succeeds -- before the
  broker call it's gating even starts, not after the fact the way
  `orders.reserved_notional` already was. `CapitalAllocator.__init__`
  reloads every still-unresolved row, summed per account, into `_pending`
  when constructed with a `store` -- so a fresh instance built against
  the SAME database a crashed process was using resumes with exactly the
  reservations that process couldn't finish resolving, not a clean
  slate. Confirmed exposure is still always freshly recomputed from the
  store, never from this ledger -- only the PROVISIONAL half changed.
  `CapitalAllocator()` with no `store` (e.g. app/backtest/replay.py's
  synthetic allocator) behaves exactly as before this change -- no table
  writes, no restore, pure in-memory -- since a backtest has no real
  broker and nothing to survive a restart for.

  Not yet integrated with a `command_ledger`/command-id concept: at the
  time this was written, no sibling branch work introducing one had
  landed here yet (see `capital_reservations`' own SCHEMA comment for
  the specific follow-up this leaves for whoever lands it).
"""
from __future__ import annotations

import asyncio
import uuid
from collections import defaultdict

from app.db import SignalStore
from app.economics import compute_account_economics


def confirmed_open_notional(store: SignalStore, account_id: str) -> float:
    """Sum of |open_quantity| * average_cost across every symbol this
    account currently holds a position in, per the confirmed-fill replay
    app/economics.py already performs. Symbols compute_account_economics
    couldn't resolve (see its own incomplete_symbols) contribute nothing
    -- an unresolved position is never silently treated as zero exposure
    *or* invented exposure; it simply can't be sized here, same as this
    module's own "no price, skip the check" rule.
    """
    economics = compute_account_economics(store, account_id)
    total = 0.0
    for symbol_economics in economics.per_symbol.values():
        if symbol_economics.average_cost is None:
            continue
        total += abs(symbol_economics.open_quantity) * symbol_economics.average_cost
    return total


class CapitalAllocator:
    """One instance shared by the engine for its whole lifetime (like
    PositionLifecycleManager) -- per-account locks and provisional
    reservations only mean anything shared across every call.

    `store` (P0-4, optional): when given, every reservation `admit()`
    grants is also durably recorded (see app/db.py's own
    `capital_reservations` SCHEMA comment), and `__init__` reloads any
    still-unresolved reservation left behind by a previous process
    against this SAME database -- see this module's own docstring.
    `None` (the default) keeps this exactly the in-memory-only ledger it
    always was, for a caller with no real database to survive a restart
    against (app/backtest/replay.py's synthetic allocator)."""

    def __init__(self, store: SignalStore | None = None) -> None:
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._pending: dict[str, float] = defaultdict(float)
        self.store = store
        if store is not None:
            # P0-4: rebuild in-memory state from whatever this database
            # still shows as unresolved -- NOT a clean slate. Any
            # reservation a previous process made but never resolved
            # (no confirming fill, no confirming rejection observed
            # before it died) stays reserved here until reconciliation
            # independently confirms its outcome, same as it would have
            # if this process had never restarted at all.
            for account_id, notional in store.sum_unresolved_capital_reservations().items():
                self._pending[account_id] = notional

    async def admit(
        self,
        account_id: str,
        notional: float,
        *,
        confirmed_exposure: float,
        max_exposure: float,
        signal_id: str | None = None,
    ) -> bool:
        async with self._locks[account_id]:
            if confirmed_exposure + self._pending[account_id] + notional > max_exposure:
                return False
            if self.store is not None and notional:
                # P0-4: durably reserved BEFORE this coroutine returns to
                # its caller -- i.e. before the broker call `admit()` is
                # gating ever starts. See this class's own docstring for
                # why that ordering (not "record it after the broker
                # call returns", which is all `orders.reserved_notional`
                # already did) is the actual point.
                self.store.create_capital_reservation(
                    str(uuid.uuid4()), account_id, notional, signal_id=signal_id
                )
            self._pending[account_id] += notional
            return True

    def release(self, account_id: str, notional: float) -> None:
        self._pending[account_id] = max(0.0, self._pending[account_id] - notional)
        if self.store is not None:
            self.store.resolve_one_capital_reservation(account_id, None, notional)

    def pending_reservation(self, account_id: str) -> float:
        """Phase B7: this account's real, current in-memory provisional
        reservation (notional admitted via `admit()` and not yet released) --
        the one figure this module tracks that `confirmed_open_notional`
        above does NOT already cover (that function only ever replays
        confirmed fills). Read-only: never mutates `_pending`, unlike
        `admit`/`release`. Exposed as a plain accessor (rather than reading
        `_pending` directly from outside this module) so a read-only GET
        endpoint (app/main.py's `/capital-allocation`) has a stable, narrow
        surface onto this otherwise process-internal ledger instead of
        reaching into a "private" attribute."""
        return self._pending[account_id]
