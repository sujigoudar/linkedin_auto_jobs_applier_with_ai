"""Interactive Brokers execution destination, via ib_async.

C08 (component adoption plan): this used to depend on ib_insync, whose
upstream repository is archived (erdewit/ib_insync, last real release
2023) -- ib_async (ib-api-reloaded/ib_async) is the maintained community
successor, a fork that kept the same public API (IB, Stock, MarketOrder,
LimitOrder, StopOrder, Trade, client.getReqId -- every name this module
uses), so this migration is a rename, not a rewrite. It is a community
fork, not IBKR-official software, and doesn't grant or verify account
permissions/entitlements on its own -- those remain whatever your actual
Gateway/TWS login and account are configured for.

Setup:
    pip install ib_async
    1. Run IB Gateway or Trader Workstation with the API enabled
       (Configuration -> API -> Settings -> Enable ActiveX and Socket
       Clients), on a host/port this service can reach.
    2. Paper trading: IB Gateway's default paper port is 4002 (7497 for
       TWS paper). Live: 4001 (Gateway) / 7496 (TWS) — double-check your
       own config before pointing this at a live account.
    3. One IBKRBroker instance holds one connection; if you have several
       IBKR accounts each needs its own IB Gateway/TWS instance (IBKR's API
       is one login per gateway process) and its own IBKRBroker(host, port,
       client_id) registered under a different `name` if you need more than
       one simultaneously — see app/main.py for how brokers are registered.

Only equities are wired up (a plain market order on a STK contract);
extend `_contract_for` if you need forex/futures/options routed through
IBKR specifically.

A Signal carrying `stop_loss` and/or `take_profit` is sent as a bracket:
a market parent order (`transmit=False`) plus one or two child exit orders
(`parentId` pointing at the parent, only the last one `transmit=True` so
the whole group submits together) — the same parent/child/transmit
pattern `IB.bracketOrder()` uses (verified against ib_async's source),
just with a market rather than limit parent since this service only
places market entries.

`get_last_price` — `IB.reqTickersAsync()`, a one-shot market-data
snapshot (verified against ib_async's installed source: it requests,
awaits the first update, then cancels the subscription itself), reading
`last` (falling back to `close`) — what app/pricing.py's `PriceMonitor`
polls to drive managed-lifecycle targets/trailing/stop resizing for IBKR
positions. `last` needs a live/delayed market-data subscription
entitlement your account may or may not have; `close` doesn't.
"""
from __future__ import annotations

import math

from app.brokers.base import BrokerAdapter
from app.models import AssetClass, DestinationAccount, OrderResult, OrderStatus, Signal


class IBKRBroker(BrokerAdapter):
    name = "ibkr"
    # IB's own native parent/child/transmit bracket mechanism (not a synthetic
    # app-side workaround) — see module docstring and _build_bracket below.
    supports_native_bracket = True
    # This module's own docstring: "Only equities are wired up (a plain
    # market order on a STK contract)" — ib_async/IBKR itself supports far
    # more, but _contract_for here doesn't. See BrokerAdapter.supported_asset_classes.
    supported_asset_classes = frozenset({AssetClass.EQUITY})

    def __init__(self, host: str = "127.0.0.1", port: int = 7497, client_id: int = 1):
        try:
            import ib_async
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("ib_async is not installed; run `pip install ib_async`") from exc

        self._ib_async = ib_async
        self.host = host
        self.port = port
        self.client_id = client_id
        self._ib = None
        self._trades: dict[str, object] = {}  # order id -> ib_async Trade, for get_order_status

    async def _connected_ib(self):
        if self._ib is None:
            ib = self._ib_async.IB()
            await ib.connectAsync(self.host, self.port, clientId=self.client_id)
            self._ib = ib
        return self._ib

    async def place_order(
        self, signal: Signal, account: DestinationAccount, quantity: float, symbol: str
    ) -> OrderResult:
        if signal.side.value == "close":
            return OrderResult(
                account_id=account.account_id,
                status=OrderStatus.REJECTED,
                signal_id=signal.id,
                message="'close' side reached the broker directly without engine-level resolution (see SignalCopierEngine._resolve_close); this broker only accepts buy/sell",
            )

        try:
            ib = await self._connected_ib()
        except Exception as exc:  # noqa: BLE001 - surface any connection error as a failed order
            return OrderResult(
                account_id=account.account_id,
                status=OrderStatus.ERROR,
                signal_id=signal.id,
                message=f"could not connect to IB Gateway/TWS: {exc}",
            )

        contract = self._ib_async.Stock(symbol, "SMART", "USD")
        action = signal.side.value.upper()

        try:
            if signal.stop_loss or signal.take_profit:
                orders = self._build_bracket(action, quantity, signal.stop_loss, signal.take_profit, ib)
                for order in orders:
                    # ADP-05: IB routes a blank `account` to whatever account
                    # is "current" on this gateway login -- fine for a
                    # single-account gateway, but a multi-account/FA gateway
                    # would submit against an unintended default rather than
                    # the specific account this call names.
                    order.account = account.account_id
                trades = [ib.placeOrder(contract, o) for o in orders]
                parent_trade = trades[0]
            else:
                order = self._ib_async.MarketOrder(action, quantity)
                order.account = account.account_id
                parent_trade = ib.placeOrder(contract, order)
        except Exception as exc:  # noqa: BLE001
            return OrderResult(
                account_id=account.account_id,
                status=OrderStatus.ERROR,
                signal_id=signal.id,
                message=str(exc),
            )

        # IBKR order status (ack/fill/reject) arrives asynchronously via ib_async's
        # own event loop integration; this reports PENDING immediately rather than
        # trying to block on a fill here. The Trade object keeps updating itself in
        # the background (ib_async wires it to IB's event stream) — get_order_status
        # below reads its current state rather than making a fresh network call.
        self._trades[str(parent_trade.order.orderId)] = parent_trade

        return OrderResult(
            account_id=account.account_id,
            status=OrderStatus.PENDING,
            signal_id=signal.id,
            broker_order_id=str(parent_trade.order.orderId),
            message=f"submitted to IBKR (status: {parent_trade.orderStatus.status})",
        )

    def _build_bracket(self, action: str, quantity: float, stop_loss, take_profit, ib) -> list:
        """A market parent plus whichever exit legs are present, linked via
        parentId with only the last order transmit=True — see module docstring."""
        reverse_action = "SELL" if action == "BUY" else "BUY"
        parent = self._ib_async.MarketOrder(
            action, quantity, orderId=ib.client.getReqId(), transmit=False
        )

        children = []
        if take_profit:
            children.append(
                self._ib_async.LimitOrder(
                    reverse_action, quantity, take_profit,
                    orderId=ib.client.getReqId(), parentId=parent.orderId, transmit=False,
                )
            )
        if stop_loss:
            children.append(
                self._ib_async.StopOrder(
                    reverse_action, quantity, stop_loss,
                    orderId=ib.client.getReqId(), parentId=parent.orderId, transmit=False,
                )
            )
        children[-1].transmit = True  # only the last order in the group submits it

        return [parent, *children]

    async def get_order_status(
        self, account: DestinationAccount, broker_order_id: str
    ) -> OrderResult | None:
        trade = self._trades.get(broker_order_id)
        if trade is None:
            return None  # order placed before this process started; nothing cached to read

        status = trade.orderStatus.status
        if status == "Filled":
            new_status = OrderStatus.FILLED
        elif status in ("Cancelled", "ApiCancelled", "Inactive"):
            new_status = OrderStatus.REJECTED
        elif trade.orderStatus.filled:
            # Still open (Submitted, etc.) but with real fill progress worth
            # reporting -- e.g. so a managed-lifecycle entry can be protected
            # for what's actually confirmed owned so far, not just once the
            # whole order is done (see
            # PositionLifecycleManager.resolve_pending_entry). Stays PENDING
            # since more may yet fill or the remainder may still be canceled.
            new_status = OrderStatus.PENDING
        else:  # PendingSubmit / PreSubmitted / Submitted / etc — still open, nothing filled yet
            return None

        return OrderResult(
            account_id=account.account_id,
            status=new_status,
            signal_id="",  # filled in by the reconciler from its own stored order row
            broker_order_id=broker_order_id,
            # RISK-FC-01: `x or None` silently turns a genuine, reported
            # zero (e.g. status=="Filled" with orderStatus.filled==0.0, a
            # real broker-glitch edge case) into "unknown," which
            # app/engine.py's `_submit_order`/`handle_signal` and
            # app/lifecycle/manager.py's `resolve_pending_entry` treat as
            # "fall back to the full requested quantity" -- exactly the
            # "explicit zero fill becomes a fictitious full fill" bug
            # app/brokers/ccxt_broker.py's own place_order already
            # documents and avoids (`order.get("filled")`, no `or`).
            # Passed straight through (no `or None`) so a real 0.0 stays
            # 0.0 rather than becoming "unknown."
            filled_quantity=trade.orderStatus.filled,
            filled_price=trade.orderStatus.avgFillPrice,
            message=f"IBKR order status: {status}",
        )

    async def get_last_price(self, account: DestinationAccount, symbol: str) -> float | None:
        # `reqTickersAsync` is ib_async's one-shot snapshot request (verified
        # against its installed source: it requests a snapshot, awaits the
        # first update, then cancels the subscription itself) -- unlike
        # `reqMktData`'s persistent streaming subscription, this fits
        # app/pricing.py's PriceMonitor polling-on-an-interval model without
        # this adapter having to separately manage subscribe/cancel
        # lifecycles per symbol.
        try:
            ib = await self._connected_ib()
        except Exception:  # noqa: BLE001 - genuinely unknown right now, not "unchanged"
            return None

        contract = self._ib_async.Stock(symbol, "SMART", "USD")
        try:
            [ticker] = await ib.reqTickersAsync(contract)
        except Exception:  # noqa: BLE001 - e.g. no market data subscription entitlement for this symbol
            return None

        # ib_async represents "no value reported" as NaN, not None, for
        # numeric ticker fields (verified against its Ticker dataclass) --
        # `last` needs a live/delayed trade tick permission that not every
        # account has; `close` (yesterday's settle) is reported far more
        # reliably and is the same kind of "last known price" fallback
        # CCXTBroker's get_last_price already uses.
        price = ticker.last
        if price is None or math.isnan(price):
            price = ticker.close
        if price is None or math.isnan(price):
            return None
        return float(price)

    async def close(self) -> None:
        if self._ib is not None:
            self._ib.disconnect()
