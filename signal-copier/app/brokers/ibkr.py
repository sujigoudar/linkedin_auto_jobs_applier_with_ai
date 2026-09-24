"""Interactive Brokers execution destination — STUB.

To implement:
    pip install ib_insync
    1. Run IB Gateway or Trader Workstation with the API enabled, on the
       same host/network this service can reach.
    2. In `place_order()` (or a connection set up once in __init__/start),
       connect via `ib_insync.IB().connect(host, port, clientId)`, build a
       `Contract` for the mapped symbol/asset class, submit a `MarketOrder`,
       and map the fill back into an OrderResult.
    3. IBKR's API is synchronous-callback based under the hood; ib_insync
       wraps it in an asyncio-friendly interface, which is why it's the
       recommended client here over the raw TWS API.
"""
from __future__ import annotations

from app.models import DestinationAccount, OrderResult, Signal
from app.brokers.base import BrokerAdapter


class IBKRBroker(BrokerAdapter):
    name = "ibkr"

    def __init__(self, host: str = "127.0.0.1", port: int = 7497, client_id: int = 1):
        self.host = host
        self.port = port
        self.client_id = client_id

    async def place_order(
        self, signal: Signal, account: DestinationAccount, quantity: float, symbol: str
    ) -> OrderResult:
        raise NotImplementedError(
            "IBKRBroker is a stub. See module docstring: install ib_insync, connect to a "
            "running IB Gateway/TWS, and submit orders via its Contract/MarketOrder API."
        )
