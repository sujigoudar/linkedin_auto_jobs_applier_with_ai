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

            if result is None or result.status == OrderStatus.PENDING:
                # Still open on the broker's side. A PENDING result here can
                # carry partial-fill progress (see AlpacaBroker/IBKRBroker's
                # get_order_status) -- for a lifecycle-tracked position that's
                # exactly what `_reconcile_pending_entries` below independently
                # polls and acts on. Applying it here too, or letting
                # `update_order_status` overwrite this row's `filled_quantity`
                # with the broker's raw in-progress number, would corrupt the
                # baseline `_correct_position` needs once this order actually
                # reaches a terminal status -- so a non-terminal PENDING is
                # left alone here no matter which account it belongs to.
                continue

            # A lifecycle-tracked (account, symbol) owns its own position/
            # protection truth exclusively through `_reconcile_pending_exits`/
            # `_reconcile_pending_entries` below -- applying `_correct_position`
            # here too would double-apply the same confirmed fill a second
            # time (once here, once there). Still update this row's display
            # status (FILLED/REJECTED) so GET /orders doesn't show it stuck
            # at "pending" forever.
            is_lifecycle_tracked = (
                self.lifecycle_manager is not None
                and self.lifecycle_manager.get_lifecycle(order["account_id"], order["symbol"]) is not None
            )
            if not is_lifecycle_tracked:
                self._correct_position(order, result.status, result.filled_quantity)
            self.store.update_order_status(order["id"], result)
            corrected += 1

        corrected += await self._reconcile_pending_exits()
        corrected += await self._reconcile_pending_entries()
        return corrected

    async def _reconcile_pending_entries(self) -> int:
        """Poll every managed-lifecycle position's unresolved entry (see
        app/lifecycle/manager.py's PendingEntry) and hand each new
        observation to `resolve_pending_entry` — the only thing allowed to
        place/resize protection for it, and (since this round) the one place
        that applies a confirmed increment to `SignalStore`'s tracked
        position too (immediately next to the persisted protection-state
        change, not here — see that method's docstring on why). A working
        partial fill whose remainder is still open gets acted on the same as
        a terminal one; only a timeout/lost response (still nothing new to
        report) is skipped. A no-op if no `PositionLifecycleManager` was
        wired in."""
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

            if result is None or result.status not in (OrderStatus.FILLED, OrderStatus.REJECTED, OrderStatus.PENDING):
                continue  # nothing new to report at all -- a timeout/lost response is not a rejection

            is_terminal = result.status in (OrderStatus.FILLED, OrderStatus.REJECTED)
            filled = result.filled_quantity if result.filled_quantity is not None else 0.0
            if not is_terminal and filled <= pending.confirmed_filled_quantity:
                continue  # a repeated observation of the same progress -- nothing new to act on

            await self.lifecycle_manager.resolve_pending_entry(account, symbol, filled, remainder_cancelled=is_terminal)
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
            # REJECTED also covers "canceled"/"expired" on adapters like Alpaca
            # (see its get_order_status), and a canceled order can still carry
            # a real partial fill from before the cancellation -- reverse only
            # the UNFILLED remainder of what was optimistically applied, not
            # the whole thing, or a genuinely-filled partial quantity gets
            # wiped to zero. `confirmed_quantity` is None (treated as 0) only
            # for an adapter that doesn't report a fill on rejection, meaning
            # "assume nothing filled," same as this branch's old behavior.
            actual_quantity = confirmed_quantity if confirmed_quantity is not None else 0.0
            delta = actual_quantity - optimistic_quantity
            if delta:
                signed_delta = delta if side == Side.BUY else -delta
                self.store.adjust_position(order["account_id"], order["symbol"], signed_delta)
        elif new_status == OrderStatus.FILLED:
            actual_quantity = confirmed_quantity if confirmed_quantity is not None else optimistic_quantity
            delta = actual_quantity - optimistic_quantity
            if delta:
                signed_delta = delta if side == Side.BUY else -delta
                self.store.adjust_position(order["account_id"], order["symbol"], signed_delta)
