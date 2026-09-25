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
"""
from __future__ import annotations

from app.brokers.base import BrokerAdapter
from app.models import DestinationAccount, OrderResult, OrderStatus, Signal


class IBKRBroker(BrokerAdapter):
    name = "ibkr"

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
                message="'close' side requires position-aware close logic; not yet implemented",
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
        order = self._ib_insync.MarketOrder(signal.side.value.upper(), quantity)

        try:
            trade = ib.placeOrder(contract, order)
        except Exception as exc:  # noqa: BLE001
            return OrderResult(
                account_id=account.account_id,
                status=OrderStatus.ERROR,
                signal_id=signal.id,
                message=str(exc),
            )

        # IBKR order status (ack/fill/reject) arrives asynchronously via ib_insync's
        # own event loop integration; this reports PENDING immediately rather than
        # trying to block on a fill here. Subscribe to `trade.filledEvent` /
        # `trade.statusEvent` (see ib_insync's Trade docs) if you need fill
        # confirmation fed back into this service.
        return OrderResult(
            account_id=account.account_id,
            status=OrderStatus.PENDING,
            signal_id=signal.id,
            broker_order_id=str(trade.order.orderId),
            message=f"submitted to IBKR (status: {trade.orderStatus.status})",
        )

    async def close(self) -> None:
        if self._ib is not None:
            self._ib.disconnect()
