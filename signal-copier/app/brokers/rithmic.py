"""Rithmic as an execution destination — STUB.

Same licensing requirement as app/sources/rithmic.py: needs R | API access
issued by Rithmic/your broker before this can be built. Once available,
`place_order()` should submit a new order via the API's order-entry
messages and await the fill/reject confirmation.
"""
from __future__ import annotations

from app.models import DestinationAccount, OrderResult, Signal
from app.brokers.base import BrokerAdapter


class RithmicBroker(BrokerAdapter):
    name = "rithmic"

    def __init__(self, credentials: dict | None = None):
        self.credentials = credentials or {}

    async def place_order(
        self, signal: Signal, account: DestinationAccount, quantity: float, symbol: str
    ) -> OrderResult:
        raise NotImplementedError(
            "RithmicBroker is a stub. Requires licensed R | API access before this can be "
            "implemented. See module docstring."
        )
