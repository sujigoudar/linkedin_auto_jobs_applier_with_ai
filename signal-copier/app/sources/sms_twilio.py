"""SMS signal source via Twilio — STUB.

To implement:
    pip install twilio
    1. Buy/configure a Twilio phone number, set its "A message comes in"
       webhook to POST /sms/twilio on this service (needs a public URL —
       ngrok in dev, a real domain in prod).
    2. Add a FastAPI route that reads Twilio's form-encoded `Body` and
       `From` fields, validates the request signature with Twilio's
       `RequestValidator` (X-Twilio-Signature header) to stop spoofed
       requests, then calls `self.parse()` and `self.on_signal()`.
    3. `parse()` needs real logic for whatever fixed SMS format the signal
       provider sends (SMS signal formats are usually short and rigid,
       e.g. "BUY EURUSD 0.10 SL 1.0950 TP 1.1050" — a regex is usually enough).
"""
from __future__ import annotations

from app.models import Signal
from app.sources.base import SourceAdapter


class TwilioSMSSource(SourceAdapter):
    name = "sms_twilio"

    def __init__(self, on_signal, account_sid: str | None = None, auth_token: str | None = None):
        super().__init__(on_signal)
        self.account_sid = account_sid
        self.auth_token = auth_token

    async def start(self) -> None:
        # Push-based via the Twilio webhook route — nothing to start here once
        # the route + signature validation + parse() below are implemented.
        raise NotImplementedError(
            "TwilioSMSSource is a stub. See module docstring: wire the /sms/twilio route, "
            "validate X-Twilio-Signature, and implement parse()."
        )

    def parse(self, sms_body: str) -> Signal:
        raise NotImplementedError("Implement SMS body parsing for your signal provider's format.")
