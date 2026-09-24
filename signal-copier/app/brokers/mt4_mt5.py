"""MT4/MT5 as an execution destination — STUB.

Like the MT4/MT5 source, there's no native inbound API — execution needs a
bridge on the receiving terminal too:
    - MT5: `pip install MetaTrader5` works if this service runs on the same
      Windows host/VPS as the terminal (the official package talks to a
      running MT5 terminal via local IPC — it cannot connect to a remote
      terminal). This is the simplest path for MT5.
    - MT4: no official Python package; use an EA bridge (e.g. ZeroMQ REQ/REP,
      matching the pattern in app/sources/mt4_mt5.py) that receives order
      commands from this service and calls OrderSend().
    - Cross-platform/remote option: a hosted terminal bridge service if you
      don't want this process on the same machine as MetaTrader.

`place_order()` should translate the Signal into the chosen bridge's order
command and await its fill confirmation.
"""
from __future__ import annotations

from app.models import DestinationAccount, OrderResult, Signal
from app.brokers.base import BrokerAdapter


class MT4MT5Broker(BrokerAdapter):
    name = "mt4_mt5"

    def __init__(self, bridge_endpoint: str | None = None):
        self.bridge_endpoint = bridge_endpoint

    async def place_order(
        self, signal: Signal, account: DestinationAccount, quantity: float, symbol: str
    ) -> OrderResult:
        raise NotImplementedError(
            "MT4MT5Broker is a stub. See module docstring: use the MetaTrader5 package "
            "(same-host MT5) or an EA bridge (MT4/remote MT5)."
        )
