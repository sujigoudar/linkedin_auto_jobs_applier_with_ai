"""NinjaTrader as an execution destination — STUB.

Same ATI vs. custom-NinjaScript tradeoff as app/sources/ninjatrader.py, but
in reverse (this service needs to push orders IN):
    - Simplest: a small NinjaScript AddOn that runs a lightweight HTTP
      listener (or polls a local file/queue this service writes to) and
      calls `Account.CreateOrder(...)` / `Order.Submit()` on receipt.
    - ATI also supports order submission via shared memory for supported
      configurations — more brittle to integrate than the AddOn approach.

`place_order()` here should send the order command to whichever bridge you
implement and await its fill confirmation.
"""
from __future__ import annotations

from app.models import DestinationAccount, OrderResult, Signal
from app.brokers.base import BrokerAdapter


class NinjaTraderBroker(BrokerAdapter):
    name = "ninjatrader"

    def __init__(self, bridge_endpoint: str | None = None):
        self.bridge_endpoint = bridge_endpoint

    async def place_order(
        self, signal: Signal, account: DestinationAccount, quantity: float, symbol: str
    ) -> OrderResult:
        raise NotImplementedError(
            "NinjaTraderBroker is a stub. See module docstring: build a NinjaScript AddOn "
            "bridge that accepts order commands and submits them via Account.CreateOrder."
        )
