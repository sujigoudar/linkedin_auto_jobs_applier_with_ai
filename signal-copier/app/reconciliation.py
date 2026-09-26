"""Corrects tracked positions once a broker confirms what a PENDING order
actually did.

Several brokers (SignalStack, Alpaca, IBKR, NinjaTrader, Rithmic) report
PENDING from `place_order` rather than a confirmed fill, so the engine
records an optimistic position update at request time (see
app/engine.py's docstring). This background loop periodically re-checks
those PENDING orders via each broker's optional `get_order_status()` (see
app/brokers/base.py) and corrects the tracked position — reversing it if
the order was actually rejected, or truing it up if the confirmed filled
quantity differs from the optimistic guess.

Only brokers that implement `get_order_status()` are checked (currently
Alpaca and IBKR — see their modules for how). Brokers without it are
silently skipped on every pass; their PENDING orders just stay PENDING and
optimistic in the position tracker, same as before this module existed.

## Managed-lifecycle pending exits

When a `PositionLifecycleManager` is wired in (`lifecycle_manager=`), this
loop also polls every unresolved `PendingExit` it's tracking (see
app/lifecycle/manager.py's `request_exit`/`resolve_pending_exit`) the same
way — via `get_order_status()` — and, once that exit order reaches a
terminal state, hands the result to `resolve_pending_exit`. That's the only
thing that settles the reservation and restores the protective stop after
a target/trailing exit that didn't fill synchronously; until this fires,
the position stays with those shares deliberately uncovered rather than
guessing at what the broker will still do with them.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from app.brokers.base import BrokerAdapter
from app.db import SignalStore
from app.lifecycle.manager import PositionLifecycleManager
from app.models import DestinationAccount, OrderStatus, Side

logger = logging.getLogger(__name__)


class OrderReconciler:
    def __init__(
        self,
        store: SignalStore,
        brokers: dict[str, BrokerAdapter],
        interval_seconds: float = 30.0,
        lifecycle_manager: PositionLifecycleManager | None = None,
    ):
        self.store = store
        self.brokers = brokers
        self.interval_seconds = interval_seconds
        self.lifecycle_manager = lifecycle_manager
        self._task: asyncio.Task | None = None
        #: See PriceMonitor.last_success_at (app/pricing.py) -- same contract,
        #: surfaced by app/main.py's /health.
        self.last_success_at: datetime | None = None

    async def start(self) -> None:
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(self.interval_seconds)
            try:
                await self.reconcile_once()
                self.last_success_at = datetime.now(timezone.utc)
            except asyncio.CancelledError:
                raise  # see PriceMonitor._loop's identical comment
            except Exception:  # noqa: BLE001 - one bad pass must not kill the loop
                logger.exception("error during order reconciliation pass")

    async def reconcile_once(self) -> int:
        """Re-check every PENDING order once. Returns how many were corrected."""
        corrected = 0
        for order in self.store.list_pending_orders():
            broker = self.brokers.get(order["broker"])
            if broker is None:
                continue

            account = DestinationAccount(account_id=order["account_id"], broker=order["broker"])
            try:
                result = await broker.get_order_status(account, order["broker_order_id"])
            except Exception:  # noqa: BLE001 - one broker's failure must not block the rest
                logger.exception(
                    "get_order_status failed for account=%s order=%s", order["account_id"], order["id"]
                )
                continue

            if result is None:
                continue  # still pending on the broker's side, nothing to correct yet

            self._correct_position(order, result.status, result.filled_quantity)
            self.store.update_order_status(order["id"], result)
            corrected += 1

        corrected += await self._reconcile_pending_exits()
        corrected += await self._reconcile_pending_entries()
        return corrected

    async def _reconcile_pending_entries(self) -> int:
        """Poll every managed-lifecycle position's unresolved entry (see
        app/lifecycle/manager.py's PendingEntry) and, once the broker gives a
        final word, hand it to `resolve_pending_entry` — the only thing
        allowed to call `on_entry_fill` (and so place the protective stop)
        for it. Until this resolves, the position has no protection at all,
        so this is at least as important as pending-exit reconciliation.
        A no-op if no `PositionLifecycleManager` was wired in."""
        if self.lifecycle_manager is None:
            return 0

        resolved = 0
        for account_id, symbol, broker_name, pending in self.lifecycle_manager.list_pending_entries():
            broker = self.brokers.get(broker_name)
            if broker is None or pending.broker_order_id is None:
                continue

            account = DestinationAccount(account_id=account_id, broker=broker_name)
            try:
                result = await broker.get_order_status(account, pending.broker_order_id)
            except Exception:  # noqa: BLE001 - one broker's failure must not block the rest
                logger.exception(
                    "get_order_status failed for pending entry account=%s symbol=%s order=%s",
                    account_id,
                    symbol,
                    pending.broker_order_id,
                )
                continue

            if result is None or result.status not in (OrderStatus.FILLED, OrderStatus.REJECTED):
                continue  # still open on the broker's side -- a timeout/lost response is not a rejection

            filled = result.filled_quantity if result.filled_quantity is not None else 0.0
            lifecycle = self.lifecycle_manager.get_lifecycle(account_id, symbol)
            entry_side = lifecycle.plan.side if lifecycle is not None else None
            await self.lifecycle_manager.resolve_pending_entry(account, symbol, filled, remainder_cancelled=True)
            if filled > 0 and entry_side is not None:
                # Nothing optimistically touched SignalStore's tracked position
                # for this entry (see engine.py's PENDING branch) -- this is the
                # one point that applies the confirmed fill, exactly once.
                self.store.record_fill(account_id, symbol, entry_side, filled)
            resolved += 1

        return resolved

    async def _reconcile_pending_exits(self) -> int:
        """Poll every managed-lifecycle position's unresolved exit (see
        app/lifecycle/manager.py's PendingExit) and, once the broker gives a
        final word, hand it to `resolve_pending_exit` — the only thing
        allowed to settle that reservation and restore the protective stop.
        A no-op if no `PositionLifecycleManager` was wired in."""
        if self.lifecycle_manager is None:
            return 0

        resolved = 0
        for account_id, symbol, broker_name, pending in self.lifecycle_manager.list_pending_exits():
            broker = self.brokers.get(broker_name)
            if broker is None or pending.broker_order_id is None:
                continue

            account = DestinationAccount(account_id=account_id, broker=broker_name)
            try:
                result = await broker.get_order_status(account, pending.broker_order_id)
            except Exception:  # noqa: BLE001 - one broker's failure must not block the rest
                logger.exception(
                    "get_order_status failed for pending exit account=%s symbol=%s order=%s",
                    account_id,
                    symbol,
                    pending.broker_order_id,
                )
                continue

            if result is None or result.status not in (OrderStatus.FILLED, OrderStatus.REJECTED):
                continue  # still open on the broker's side — more of it may yet fill

            # FILLED or REJECTED are both terminal for this order: either everything
            # requested filled, or nothing more of it can (rejected/canceled/expired).
            # Either way the remainder is resolved, so this order's own filled_qty
            # (which brokers report even on a canceled-after-partial-fill order — see
            # e.g. AlpacaBroker.get_order_status) is the true, final fill.
            filled = result.filled_quantity if result.filled_quantity is not None else 0.0
            await self.lifecycle_manager.resolve_pending_exit(account, symbol, filled, remainder_cancelled=True)
            resolved += 1

        return resolved

    def _correct_position(self, order: dict, new_status: OrderStatus, confirmed_quantity: float | None) -> None:
        if not order["symbol"] or not order["side"]:
            return  # nothing was optimistically recorded for this order to correct

        side = Side(order["side"])
        optimistic_quantity = order["filled_quantity"] or 0.0

        if new_status == OrderStatus.REJECTED:
            # Reverse the optimistic fill entirely — it never actually happened.
            reversal = -optimistic_quantity if side == Side.BUY else optimistic_quantity
            if reversal:
                self.store.adjust_position(order["account_id"], order["symbol"], reversal)
        elif new_status == OrderStatus.FILLED:
            actual_quantity = confirmed_quantity if confirmed_quantity is not None else optimistic_quantity
            delta = actual_quantity - optimistic_quantity
            if delta:
                signed_delta = delta if side == Side.BUY else -delta
                self.store.adjust_position(order["account_id"], order["symbol"], signed_delta)
