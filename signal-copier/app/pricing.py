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
from datetime import datetime, timezone

from app.brokers.base import BrokerAdapter
from app.lifecycle.manager import PositionLifecycleManager
from app.models import DestinationAccount

logger = logging.getLogger(__name__)

#: How many `get_last_price` calls run concurrently in one pass. Bounded so a
#: growing number of tracked positions can't turn into an unbounded burst of
#: simultaneous requests against a broker/exchange's rate limits -- each
#: position's own call is still independent (one slow/failing symbol doesn't
#: block another), but at most this many are ever in flight at once.
_MAX_CONCURRENT_PRICE_LOOKUPS = 10


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
        #: Set at the end of every pass that completes without the whole loop
        #: itself dying -- a pass where every single position's price lookup
        #: failed still updates this (see app/main.py's /health: "did a cycle
        #: complete" is a different question from "did every lookup succeed").
        self.last_success_at: datetime | None = None

    async def start(self) -> None:
        # OPS-02: idempotent -- a second start() while one loop is already
        # running is a no-op, same fix/reasoning as OrderReconciler.start()
        # in app/reconciliation.py.
        if self._task is not None and not self._task.done():
            return
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
                succeeded = await self.poll_once()
                if succeeded > 0:
                    # OPS-01: a pass that completed without crashing used to
                    # update this unconditionally, even one where every
                    # single position's price lookup failed (0 usable
                    # reads). That let a fully-broken feed still report
                    # "worker ok" via /health as long as the loop itself
                    # kept iterating. Only a pass with at least one actually
                    # usable read counts as a success now.
                    self.last_success_at = datetime.now(timezone.utc)
            except asyncio.CancelledError:
                # Structured concurrency: a cancellation is `stop()` asking this
                # task to end, not a failed pass -- swallowing it here (like the
                # broad except below does for ordinary errors) would leave the
                # task silently un-cancellable. Let it propagate.
                raise
            except Exception:  # noqa: BLE001 - one bad pass must not kill the loop
                logger.exception("error during price monitor pass")

    async def poll_once(self) -> int:
        """Poll every open managed-lifecycle position once, feeding whatever
        price is available into `on_price_update()`. Returns how many
        positions actually got a fresh price this pass — positions on a
        broker with no `get_last_price` implementation are silently
        skipped (not an error; see this module's docstring).

        Lookups run with bounded concurrency (see `_MAX_CONCURRENT_PRICE_LOOKUPS`)
        rather than one at a time -- with N open positions, a fully sequential
        pass costs N broker round-trips end to end, which means the *effective*
        polling interval for the Nth position grows with the position count;
        `on_price_update()` itself is still called one result at a time, in
        whatever order the lookups complete, since it does its own per-
        (account, symbol) locking (see app/lifecycle/manager.py)."""
        semaphore = asyncio.Semaphore(_MAX_CONCURRENT_PRICE_LOOKUPS)

        async def _poll_one(lifecycle) -> bool:
            account_id, symbol = lifecycle.key
            broker_name = lifecycle.plan.broker
            broker = self.brokers.get(broker_name)
            if broker is None or not broker.has_last_price_capability:
                return False

            account = DestinationAccount(account_id=account_id, broker=broker_name)
            async with semaphore:
                try:
                    price = await broker.get_last_price(account, symbol)
                except asyncio.CancelledError:
                    raise
                except Exception:  # noqa: BLE001 - one broker's failure must not block the rest
                    logger.exception(
                        "get_last_price failed for account=%s symbol=%s broker=%s", account_id, symbol, broker_name
                    )
                    return False
            if price is None:
                return False

            await self.lifecycle_manager.on_price_update(account, symbol, price)
            return True

        lifecycles = self.lifecycle_manager.list_open_lifecycles()
        results = await asyncio.gather(*(_poll_one(lifecycle) for lifecycle in lifecycles))
        return sum(results)
