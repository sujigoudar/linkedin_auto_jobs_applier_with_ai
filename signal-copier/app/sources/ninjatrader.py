"""NinjaTrader as a signal SOURCE — STUB.

Two realistic integration paths:
    1. ATI (Automated Trading Interface): NinjaTrader can write executed-order
       state to shared memory / a local file it exposes for ATI consumers,
       or you write a small NinjaScript AddOn that posts trade events to an
       HTTP endpoint on this service (reuse the generic webhook parser in
       app/sources/webhook.py if you shape the AddOn's payload to match it).
    2. A custom NinjaScript strategy/indicator that calls out via
       HttpClient on OnExecutionUpdate to POST this service's webhook route.

Option 2 (custom NinjaScript -> webhook) is the simplest to build and
maintain — prefer it over parsing ATI's shared-memory format unless you
already have NinjaScript tooling in place.
"""
from __future__ import annotations

from app.models import Signal
from app.sources.base import SourceAdapter


class NinjaTraderSource(SourceAdapter):
    name = "ninjatrader"

    def __init__(self, on_signal):
        super().__init__(on_signal)

    async def start(self) -> None:
        raise NotImplementedError(
            "NinjaTraderSource is a stub. See module docstring: prefer a custom NinjaScript "
            "AddOn/strategy that POSTs trade events to the generic webhook route."
        )

    def parse(self, payload: dict) -> Signal:
        raise NotImplementedError("Implement parsing for your NinjaScript AddOn's event payload.")
