"""The single serialization point every exit intent (a logical target firing,
trailing stop, protective stop fill, provider EXIT signal, time exit,
emergency exit) must go through, so the system can never have several
independent full-position sell orders competing with one another.

The key invariant:

    available_to_sell = confirmed_owned_quantity - reserved_quantity

`reserved_quantity` is the sum of exit quantities currently in flight
(submitted to the broker, outcome not yet known). Every exit reserves its
quantity here *before* talking to a broker, and settles the reservation
once the broker's outcome is known. `reserve()` refuses any request that
would push `reserved` past `owned`, and `_check_invariant` self-halts a
position if that ever happens anyway (a bug, a race, or a broker
reporting something unexpected) rather than let more orders through it.

All bookkeeping for one (account_id, symbol) is serialized through a
single `asyncio.Lock`, so this is also the mechanism that prevents the
race in the design's section 7 (a stop filling while a target's resize
transition is in flight): both paths acquire the same lock, so one runs
to completion before the other can even see the arbiter's state.
"""
from __future__ import annotations

import asyncio
from collections import defaultdict
from contextlib import asynccontextmanager
from typing import AsyncIterator

_EPSILON = 1e-9


class _Ledger:
    """Per-(account, symbol) quantities. Only ever mutated while the arbiter's
    lock for that key is held — see `_locked_ops` / `transition`."""

    __slots__ = ("owned", "reserved", "halted", "halt_reason")

    def __init__(self) -> None:
        self.owned = 0.0
        self.reserved = 0.0
        self.halted = False
        self.halt_reason = ""


class Halted(RuntimeError):
    """Raised by reservation attempts on a halted position."""


class CloseArbiter:
    def __init__(self) -> None:
        self._ledgers: dict[tuple[str, str], _Ledger] = defaultdict(_Ledger)
        self._locks: dict[tuple[str, str], asyncio.Lock] = defaultdict(asyncio.Lock)

    def available_to_sell(self, account_id: str, symbol: str) -> float:
        ledger = self._ledgers[(account_id, symbol)]
        return max(0.0, ledger.owned - ledger.reserved)

    def is_halted(self, account_id: str, symbol: str) -> bool:
        return self._ledgers[(account_id, symbol)].halted

    def halt_reason(self, account_id: str, symbol: str) -> str:
        return self._ledgers[(account_id, symbol)].halt_reason

    async def set_owned_quantity(self, account_id: str, symbol: str, quantity: float) -> None:
        """Set the confirmed owned quantity outright — used once, right after an
        entry fills (see PositionLifecycleManager.on_entry_fill)."""
        key = (account_id, symbol)
        async with self._locks[key]:
            self._ledgers[key].owned = quantity

    async def reserve(self, account_id: str, symbol: str, quantity: float) -> bool:
        """Reserve `quantity` for an in-flight sell. Returns False (reserving
        nothing) if that would oversell or the position is halted."""
        key = (account_id, symbol)
        async with self._locks[key]:
            return self._reserve_locked(key, quantity)

    async def release(self, account_id: str, symbol: str, quantity: float) -> None:
        """Release a reservation that didn't execute (order rejected outright)."""
        key = (account_id, symbol)
        async with self._locks[key]:
            self._release_locked(key, quantity)

    async def settle(self, account_id: str, symbol: str, reserved_quantity: float, filled_quantity: float) -> None:
        """A reserved sell finished: release the reservation and reduce owned by
        whatever actually filled (which may be less than reserved — see the
        design's section 6, "the application responds to broker fills, not the
        intended quantity")."""
        key = (account_id, symbol)
        async with self._locks[key]:
            ledger = self._ledgers[key]
            ledger.reserved = max(0.0, ledger.reserved - reserved_quantity)
            ledger.owned = max(0.0, ledger.owned - filled_quantity)

    async def halt(self, account_id: str, symbol: str, reason: str) -> None:
        key = (account_id, symbol)
        async with self._locks[key]:
            ledger = self._ledgers[key]
            ledger.halted = True
            ledger.halt_reason = reason

    @asynccontextmanager
    async def transition(self, account_id: str, symbol: str) -> AsyncIterator["_TransactionOps"]:
        """Hold this position's lock across a multi-step operation (the
        stop-resize sequence in app/lifecycle/manager.py) so nothing else can
        touch it — reservations, settlements, or another transition — until
        this one finishes. That's the "freeze competing close intents" step.
        """
        key = (account_id, symbol)
        async with self._locks[key]:
            yield _TransactionOps(self, key)

    def _reserve_locked(self, key: tuple[str, str], quantity: float) -> bool:
        ledger = self._ledgers[key]
        if quantity <= 0:
            return True
        if ledger.halted:
            return False
        available = ledger.owned - ledger.reserved
        if quantity > available + _EPSILON:
            return False
        ledger.reserved += quantity
        self._check_invariant(key)
        return True

    def _release_locked(self, key: tuple[str, str], quantity: float) -> None:
        ledger = self._ledgers[key]
        ledger.reserved = max(0.0, ledger.reserved - quantity)

    def _settle_locked(self, key: tuple[str, str], reserved_quantity: float, filled_quantity: float) -> None:
        ledger = self._ledgers[key]
        ledger.reserved = max(0.0, ledger.reserved - reserved_quantity)
        ledger.owned = max(0.0, ledger.owned - filled_quantity)

    def _check_invariant(self, key: tuple[str, str]) -> None:
        ledger = self._ledgers[key]
        if ledger.reserved > ledger.owned + _EPSILON:
            ledger.halted = True
            ledger.halt_reason = (
                f"invariant violated: reserved ({ledger.reserved}) exceeds owned ({ledger.owned})"
            )


class _TransactionOps:
    """Lock-free ledger access for use only inside `CloseArbiter.transition()` —
    the surrounding `async with` already holds this key's lock."""

    def __init__(self, arbiter: CloseArbiter, key: tuple[str, str]):
        self._arbiter = arbiter
        self._key = key

    @property
    def owned(self) -> float:
        return self._arbiter._ledgers[self._key].owned

    @property
    def reserved(self) -> float:
        return self._arbiter._ledgers[self._key].reserved

    @property
    def available(self) -> float:
        return max(0.0, self.owned - self.reserved)

    @property
    def is_halted(self) -> bool:
        return self._arbiter._ledgers[self._key].halted

    @property
    def halt_reason(self) -> str:
        return self._arbiter._ledgers[self._key].halt_reason

    def reserve(self, quantity: float) -> bool:
        return self._arbiter._reserve_locked(self._key, quantity)

    def release(self, quantity: float) -> None:
        self._arbiter._release_locked(self._key, quantity)

    def settle(self, reserved_quantity: float, filled_quantity: float) -> None:
        self._arbiter._settle_locked(self._key, reserved_quantity, filled_quantity)

    def set_owned(self, quantity: float) -> None:
        self._arbiter._ledgers[self._key].owned = quantity
