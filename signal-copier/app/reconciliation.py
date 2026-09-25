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
"""
from __future__ import annotations

import asyncio
import logging

from app.brokers.base import BrokerAdapter
from app.db import SignalStore
from app.models import DestinationAccount, OrderStatus, Side

logger = logging.getLogger(__name__)


class OrderReconciler:
    def __init__(self, store: SignalStore, brokers: dict[str, BrokerAdapter], interval_seconds: float = 30.0):
        self.store = store
        self.brokers = brokers
        self.interval_seconds = interval_seconds
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(self.interval_seconds)
            try:
                await self.reconcile_once()
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

        return corrected

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
