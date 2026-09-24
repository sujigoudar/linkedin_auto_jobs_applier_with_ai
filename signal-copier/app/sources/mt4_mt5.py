"""MT4/MT5 as a signal SOURCE (reading trades opened on a master MT4/MT5
account so they can be copied elsewhere) — STUB.

MetaTrader has no native outbound webhook/API, so this always needs a
bridge:
    - An MQL4/MQL5 Expert Advisor on the master account that detects new
      trades (OnTradeTransaction) and forwards them out — most commonly via
      a local ZeroMQ socket (see the popular open-source project
      "DarwinexLabs/metatrader-bridge" or "EA31337" style bridges) or by
      writing to a file this service tails.
    - This adapter then either binds a ZeroMQ SUB socket (pip install pyzmq)
      to receive trade events from the EA, or polls the file the EA writes
      to.

`start()` should launch that listener as a background asyncio task, and
`parse()` should convert the bridge's trade-event payload into a Signal.
See app/brokers/mt4_mt5.py for the matching execution-side bridge notes.
"""
from __future__ import annotations

from app.models import Signal
from app.sources.base import SourceAdapter


class MT4MT5Source(SourceAdapter):
    name = "mt4_mt5"

    def __init__(self, on_signal, bridge_endpoint: str | None = None):
        super().__init__(on_signal)
        self.bridge_endpoint = bridge_endpoint  # e.g. "tcp://127.0.0.1:5556" for a ZeroMQ bridge

    async def start(self) -> None:
        raise NotImplementedError(
            "MT4MT5Source is a stub. See module docstring: needs an MQL4/MQL5 EA bridge "
            "(ZeroMQ or file-based) on the master terminal; implement the listener and parse()."
        )

    def parse(self, bridge_payload: dict) -> Signal:
        raise NotImplementedError("Implement parsing for your specific EA bridge's trade-event payload.")
