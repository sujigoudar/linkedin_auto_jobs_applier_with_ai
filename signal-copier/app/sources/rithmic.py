"""Rithmic as a signal SOURCE — STUB.

Rithmic requires their licensed R | API+ (protocol buffers over their own
SSL socket) or the newer R | ProtocolAPI; access requires a signed
agreement with Rithmic/Omne and per-environment credentials issued by your
broker — this cannot be wired up without that account in place first.

To implement once you have R | API access:
    pip install async_rithmic   (community async wrapper) or use the
    official C++/·NET SDK via a small sidecar process this service talks to
    over a local socket/queue.
    1. Authenticate and subscribe to order/fill updates for the account(s)
       you want to copy from.
    2. On each fill event, build a Signal and await `self.on_signal()`.
"""
from __future__ import annotations

from app.models import Signal
from app.sources.base import SourceAdapter


class RithmicSource(SourceAdapter):
    name = "rithmic"

    def __init__(self, on_signal, credentials: dict | None = None):
        super().__init__(on_signal)
        self.credentials = credentials or {}

    async def start(self) -> None:
        raise NotImplementedError(
            "RithmicSource is a stub. Requires licensed R | API access from Rithmic/your broker "
            "before this can be implemented. See module docstring."
        )

    def parse(self, fill_event: dict) -> Signal:
        raise NotImplementedError("Implement parsing for R | API's fill-event payload.")
