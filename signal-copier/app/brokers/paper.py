"""In-memory paper/mock broker.

Fills every order instantly at the signal's price (or 0.0 if none given).
Use this for end-to-end testing of the routing/sizing pipeline without
touching a real exchange or broker.

Also the reference implementation of the managed-lifecycle capabilities
(app/brokers/base.py's `place_protective_stop`/`cancel_order`/
`replace_stop_quantity`/`get_broker_position`) — a real broker's stop
orders sit on its servers and fill against real market data; this one
tracks them in memory and fills them only when `simulate_price()` is
called, so app/lifecycle/manager.py's behavior (protect-first, resize
transitions, oversell prevention) can be tested deterministically without
a live broker or feed.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.models import DestinationAccount, OrderResult, OrderStatus, Side, Signal
from app.brokers.base import BrokerAdapter


@dataclass
class _StopOrder:
    order_id: str
    account_id: str
    symbol: str
    side: Side  # the side of the STOP order itself (opposite of the position)
    quantity: float
    stop_price: float


class PaperBroker(BrokerAdapter):
    name = "paper"

    def __init__(self) -> None:
        # account_id -> symbol -> net position
        self.positions: dict[str, dict[str, float]] = {}
        self.fills: list[OrderResult] = []
        self._stop_orders: dict[str, _StopOrder] = {}
        self._next_stop_id = 1

    async def place_order(
        self, signal: Signal, account: DestinationAccount, quantity: float, symbol: str
    ) -> OrderResult:
        book = self.positions.setdefault(account.account_id, {})
        current = book.get(symbol, 0.0)

        if signal.side.value == "buy":
            book[symbol] = current + quantity
        elif signal.side.value == "sell":
            book[symbol] = current - quantity
        else:  # close
            book[symbol] = 0.0

        result = OrderResult(
            account_id=account.account_id,
            status=OrderStatus.FILLED,
            signal_id=signal.id,
            broker_order_id=f"paper-{len(self.fills) + 1}",
            filled_quantity=quantity,
            filled_price=signal.price or 0.0,
            message="filled by paper broker",
        )
        self.fills.append(result)
        return result

    async def place_protective_stop(
        self, account: DestinationAccount, symbol: str, quantity: float, stop_price: float, exit_side: Side
    ) -> OrderResult | None:
        order_id = f"paper-stop-{self._next_stop_id}"
        self._next_stop_id += 1
        self._stop_orders[order_id] = _StopOrder(
            order_id=order_id,
            account_id=account.account_id,
            symbol=symbol,
            side=exit_side,
            quantity=quantity,
            stop_price=stop_price,
        )
        return OrderResult(
            account_id=account.account_id,
            status=OrderStatus.PENDING,
            signal_id="",
            broker_order_id=order_id,
            message=f"paper stop resting: {quantity} @ {stop_price}",
        )

    async def cancel_order(self, account: DestinationAccount, broker_order_id: str) -> bool:
        return self._stop_orders.pop(broker_order_id, None) is not None

    async def replace_stop_quantity(
        self,
        account: DestinationAccount,
        broker_order_id: str,
        new_quantity: float,
        new_price: float | None = None,
    ) -> OrderResult | None:
        stop = self._stop_orders.get(broker_order_id)
        if stop is None:
            return None
        stop.quantity = new_quantity
        if new_price is not None:
            stop.stop_price = new_price
        return OrderResult(
            account_id=account.account_id,
            status=OrderStatus.PENDING,
            signal_id="",
            broker_order_id=broker_order_id,
            message=f"paper stop resized: {new_quantity} @ {stop.stop_price}",
        )

    async def get_broker_position(self, account: DestinationAccount, symbol: str) -> float | None:
        return self.positions.setdefault(account.account_id, {}).get(symbol, 0.0)

    def simulate_price(self, symbol: str, price: float) -> list[OrderResult]:
        """Test/simulation hook: check every resting stop order on `symbol`
        against `price` and fill any that trigger. A SELL stop triggers when
        price <= stop_price (protecting a long); a BUY stop triggers when
        price >= stop_price (protecting a short). Returns the fills so the
        caller (normally PositionLifecycleManager) can react to them the same
        way it would react to a real broker's fill notification.
        """
        triggered_ids = []
        for order_id, stop in self._stop_orders.items():
            if stop.symbol != symbol:
                continue
            if stop.side == Side.SELL and price <= stop.stop_price:
                triggered_ids.append(order_id)
            elif stop.side == Side.BUY and price >= stop.stop_price:
                triggered_ids.append(order_id)

        results = []
        for order_id in triggered_ids:
            stop = self._stop_orders.pop(order_id)
            book = self.positions.setdefault(stop.account_id, {})
            current = book.get(stop.symbol, 0.0)
            book[stop.symbol] = current - stop.quantity if stop.side == Side.SELL else current + stop.quantity

            result = OrderResult(
                account_id=stop.account_id,
                status=OrderStatus.FILLED,
                signal_id="",
                broker_order_id=order_id,
                filled_quantity=stop.quantity,
                filled_price=price,
                message=f"paper stop filled at simulated price {price}",
            )
            self.fills.append(result)
            results.append(result)

        return results
