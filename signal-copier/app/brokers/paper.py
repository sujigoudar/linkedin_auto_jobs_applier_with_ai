"""In-memory paper/mock broker.

Fills every order instantly at the signal's price (or 0.0 if none given).
Use this for end-to-end testing of the routing/sizing pipeline without
touching a real exchange or broker.
"""
from __future__ import annotations

from app.models import DestinationAccount, OrderResult, OrderStatus, Signal
from app.brokers.base import BrokerAdapter


class PaperBroker(BrokerAdapter):
    name = "paper"

    def __init__(self) -> None:
        # account_id -> symbol -> net position
        self.positions: dict[str, dict[str, float]] = {}
        self.fills: list[OrderResult] = []

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
