"""SMS signal source via Twilio.

Setup:
    pip install twilio
    1. Buy/configure a Twilio phone number.
    2. Set its "A message comes in" webhook (Messaging config) to
       POST https://<your-public-host>/sms/twilio (needs a public URL —
       ngrok in dev, a real domain in prod).
    3. Set TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN; the FastAPI route in
       app/main.py uses the auth token to validate the X-Twilio-Signature
       header, so a request not actually from Twilio is rejected.

Message parsing uses the shared free-text parser (app/sources/text_parser.py) —
SMS signal formats are usually short and rigid ("BUY EURUSD 0.10 SL 1.0950
TP 1.1050"), which is exactly what that parser handles. Override `parse()`
if your provider's format doesn't fit.
"""
from __future__ import annotations

from app.models import AssetClass, Signal
from app.sources.base import SourceAdapter
from app.sources.text_parser import parse_text_signal


class TwilioSMSSource(SourceAdapter):
    name = "sms_twilio"

    def __init__(self, on_signal, asset_class: AssetClass = AssetClass.CRYPTO):
        super().__init__(on_signal)
        self.asset_class = asset_class

    async def start(self) -> None:
        # Push-based: signals arrive via the /sms/twilio FastAPI route in
        # app/main.py, which validates the request and calls ingest().
        return None

    def parse(self, sms_body: str, analyst: str | None = None) -> Signal:
        return parse_text_signal(sms_body, source=self.name, asset_class=self.asset_class, analyst=analyst)

    async def ingest(self, sms_body: str, analyst: str | None = None) -> Signal:
        signal = self.parse(sms_body, analyst=analyst)
        await self.on_signal(signal)
        return signal
