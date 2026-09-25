"""The piece that makes "the system is constantly monitoring an open
position and moving/replacing its protective stop" actually true in
production, not just a tested-but-dormant capability.

`PositionLifecycleManager.on_price_update()` (targets, trailing, stop
resizing — see app/lifecycle/manager.py) already existed and was fully
tested, but nothing called it outside tests: there was no live price
feed. `PriceMonitor` closes that gap the same way `OrderReconciler`
closes the PENDING-order gap — a background loop, polling on an
interval, calling into the existing engine rather than reimplementing it.

## What actually drives it

Each broker's optional `get_last_price()` (see app/brokers/base.py) is
the source of truth per position — no separate custom price-feed
infrastructure. The only real implementation shipped is `CCXTBroker`'s,
using ccxt's own unified `fetch_ticker` REST call
(https://github.com/ccxt/ccxt) — an existing, broadly-verified library
capability, not something built from scratch here. This is REST polling
on a fixed interval, not a websocket/tick stream: ccxt's own websocket
("pro") support, Alpaca's market-data websocket, an MT5 terminal's tick
feed, and IBKR's `reqMktData` are all real, better options for brokers
that have them, and are a documented next step — not implemented yet.
Until a broker declares `has_last_price_capability`, its managed-
lifecycle positions simply aren't polled (visible via `GET /brokers`),
which is honest: no feed is not the same as "nothing needs monitoring."
"""
from __future__ import annotations

import asyncio
import logging

from app.brokers.base import BrokerAdapter
from app.lifecycle.manager import PositionLifecycleManager
from app.models import DestinationAccount

logger = logging.getLogger(__name__)


class PriceMonitor:
    def __init__(
        self,
        lifecycle_manager: PositionLifecycleManager,
        brokers: dict[str, BrokerAdapter],
        interval_seconds: float = 15.0,
    ):
        self.lifecycle_manager = lifecycle_manager
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
                await self.poll_once()
            except Exception:  # noqa: BLE001 - one bad pass must not kill the loop
                logger.exception("error during price monitor pass")

    async def poll_once(self) -> int:
        """Poll every open managed-lifecycle position once, feeding whatever
        price is available into `on_price_update()`. Returns how many
        positions actually got a fresh price this pass — positions on a
        broker with no `get_last_price` implementation are silently
        skipped (not an error; see this module's docstring)."""
        updated = 0
        for lifecycle in self.lifecycle_manager.list_open_lifecycles():
            account_id, symbol = lifecycle.key
            broker_name = lifecycle.plan.broker
            broker = self.brokers.get(broker_name)
            if broker is None or not broker.has_last_price_capability:
                continue

            account = DestinationAccount(account_id=account_id, broker=broker_name)
            try:
                price = await broker.get_last_price(account, symbol)
            except Exception:  # noqa: BLE001 - one broker's failure must not block the rest
                logger.exception(
                    "get_last_price failed for account=%s symbol=%s broker=%s", account_id, symbol, broker_name
                )
                continue
            if price is None:
                continue

            await self.lifecycle_manager.on_price_update(account, symbol, price)
            updated += 1
        return updated
