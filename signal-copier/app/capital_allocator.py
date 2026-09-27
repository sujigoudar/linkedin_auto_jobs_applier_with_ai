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
  released as soon as that order call returns, for every outcome
  (REJECTED/ERROR/FILLED/PENDING alike) -- for REJECTED/ERROR that's
  correct (nothing happened, nothing to keep reserved), and for FILLED
  the fill is immediately part of the confirmed exposure the next
  admission call will see.
- **Known gap, not yet closed: a PENDING result's notional is released
  the same way, but a PENDING order is NOT yet part of confirmed
  exposure** (confirmed exposure only counts a symbol once its
  `average_cost` is resolvable from an actual recorded fill -- see
  `confirmed_open_notional` below). Between "broker accepted, reported
  PENDING" and "reconciliation later confirms the fill," this specific
  notional briefly counts toward neither the reservation ledger nor
  confirmed exposure -- a second signal admitted in that window could
  push real total exposure past the configured ceiling. Correctly
  closing this needs the reservation to survive until reconciliation
  resolves the order to a terminal state (dropped on a confirmed
  rejection/cancellation, or handed off to confirmed exposure exactly
  once on a confirmed fill) rather than being released unconditionally
  right after the synchronous call returns -- deliberately not attempted
  in this pass: doing it wrong risks a reservation that's never
  released, which would be worse (silently blocking all future
  admissions for that account) than the gap it would close.
- This provisional ledger is in-memory and process-lifetime only, same
  as e.g. app/pricing.py's PriceMonitor cache -- a restart has no
  in-flight admissions to lose (nothing survives a request that never
  returned), and confirmed exposure is always freshly recomputed from
  the store, never from this ledger.
"""
from __future__ import annotations

import asyncio
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
    reservations only mean anything shared across every call."""

    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._pending: dict[str, float] = defaultdict(float)

    async def admit(self, account_id: str, notional: float, *, confirmed_exposure: float, max_exposure: float) -> bool:
        async with self._locks[account_id]:
            if confirmed_exposure + self._pending[account_id] + notional > max_exposure:
                return False
            self._pending[account_id] += notional
            return True

    def release(self, account_id: str, notional: float) -> None:
        self._pending[account_id] = max(0.0, self._pending[account_id] - notional)
