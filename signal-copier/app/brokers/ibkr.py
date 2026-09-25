"""Interactive Brokers execution destination, via ib_insync.

Setup:
    pip install ib_insync
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
pattern `IB.bracketOrder()` uses (verified against ib_insync's source),
just with a market rather than limit parent since this service only
places market entries.
"""
from __future__ import annotations

from app.brokers.base import BrokerAdapter
from app.models import DestinationAccount, OrderResult, OrderStatus, Signal


class IBKRBroker(BrokerAdapter):
    name = "ibkr"
    # IB's own native parent/child/transmit bracket mechanism (not a synthetic
    # app-side workaround) — see module docstring and _build_bracket below.
    supports_native_bracket = True

    def __init__(self, host: str = "127.0.0.1", port: int = 7497, client_id: int = 1):
        try:
            import ib_insync
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("ib_insync is not installed; run `pip install ib_insync`") from exc

        self._ib_insync = ib_insync
        self.host = host
        self.port = port
        self.client_id = client_id
        self._ib = None
        self._trades: dict[str, object] = {}  # order id -> ib_insync Trade, for get_order_status

    async def _connected_ib(self):
        if self._ib is None:
            ib = self._ib_insync.IB()
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

        contract = self._ib_insync.Stock(symbol, "SMART", "USD")
        action = signal.side.value.upper()

        try:
            if signal.stop_loss or signal.take_profit:
                orders = self._build_bracket(action, quantity, signal.stop_loss, signal.take_profit, ib)
                trades = [ib.placeOrder(contract, o) for o in orders]
                parent_trade = trades[0]
            else:
                order = self._ib_insync.MarketOrder(action, quantity)
                parent_trade = ib.placeOrder(contract, order)
        except Exception as exc:  # noqa: BLE001
            return OrderResult(
                account_id=account.account_id,
                status=OrderStatus.ERROR,
                signal_id=signal.id,
                message=str(exc),
            )

        # IBKR order status (ack/fill/reject) arrives asynchronously via ib_insync's
        # own event loop integration; this reports PENDING immediately rather than
        # trying to block on a fill here. The Trade object keeps updating itself in
        # the background (ib_insync wires it to IB's event stream) — get_order_status
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
        parent = self._ib_insync.MarketOrder(
            action, quantity, orderId=ib.client.getReqId(), transmit=False
        )

        children = []
        if take_profit:
            children.append(
                self._ib_insync.LimitOrder(
                    reverse_action, quantity, take_profit,
                    orderId=ib.client.getReqId(), parentId=parent.orderId, transmit=False,
                )
            )
        if stop_loss:
            children.append(
                self._ib_insync.StopOrder(
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
        else:  # PendingSubmit / PreSubmitted / Submitted / etc — still open
            return None

        return OrderResult(
            account_id=account.account_id,
            status=new_status,
            signal_id="",  # filled in by the reconciler from its own stored order row
            broker_order_id=broker_order_id,
            filled_quantity=trade.orderStatus.filled or None,
            filled_price=trade.orderStatus.avgFillPrice or None,
            message=f"IBKR order status: {status}",
        )

    async def close(self) -> None:
        if self._ib is not None:
            self._ib.disconnect()
