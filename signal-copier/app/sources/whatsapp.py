"""WhatsApp signal source, via Meta's official WhatsApp Business Cloud API.

Setup:
    1. Create a Meta developer app (developers.facebook.com) and add the
       "WhatsApp" product to it.
    2. Under WhatsApp > API Setup, get a phone number (Meta gives a free
       test number for development; production needs your own verified
       WhatsApp Business number) and the App Secret (App Settings > Basic).
    3. Under WhatsApp > Configuration, set the Callback URL to
       https://<your-public-host>/whatsapp/webhook and a Verify Token of
       your choosing -- this triggers a one-time GET handshake Meta sends
       to confirm you control the endpoint (see app/main.py's
       `verify_whatsapp_webhook`).
    4. Subscribe the webhook to the "messages" field.
    5. Set WHATSAPP_APP_SECRET (validates the `X-Hub-Signature-256` header
       on every inbound POST), WHATSAPP_VERIFY_TOKEN (the GET handshake),
       and WHATSAPP_ALLOWED_FROM_NUMBERS (comma-separated E.164 numbers,
       no leading '+' -- WhatsApp's own `wa_id` format -- authorized to
       submit trading instructions; unset means no sender is authorized,
       same fail-closed pattern as every other optional ingress here).

This is the OFFICIAL Cloud API
(developers.facebook.com/docs/whatsapp/cloud-api), not an unofficial
WhatsApp Web automation library (whatsapp-web.js, Baileys, OpenWA, etc.).
Those work by scripting a personal WhatsApp Web session, which is a
direct Terms of Service violation Meta actively detects -- the number
used gets banned with no appeal, not a slap on the wrist. The Cloud API
is free to RECEIVE (Meta only charges for messages this app SENDS in an
outbound conversation window, which this read-only source never does)
and needs a registered/verified WhatsApp Business number, not a personal
one paired by QR code.

No extra pip package needed: the inbound webhook this source reads is
signed with a plain HMAC-SHA256 (verified in app/main.py, the same way
Twilio's signature is for /sms/twilio), and this source never calls
WhatsApp's own Graph API to send anything.

Message parsing uses the shared free-text parser (app/sources/text_parser.py) --
WhatsApp signal formats are usually short and rigid, similar to SMS,
which is exactly what that parser handles. Override `parse()` if a
specific channel's format doesn't fit.
"""
from __future__ import annotations

from app.models import AssetClass, Signal
from app.sources.base import SourceAdapter
from app.sources.text_parser import parse_text_signal


class WhatsAppSource(SourceAdapter):
    name = "whatsapp"

    def __init__(self, on_signal, asset_class: AssetClass = AssetClass.CRYPTO):
        super().__init__(on_signal)
        self.asset_class = asset_class

    async def start(self) -> None:
        # Push-based: signals arrive via the /whatsapp/webhook FastAPI
        # route in app/main.py, which validates the request and calls
        # ingest() per message in the (possibly batched) webhook payload.
        return None

    def parse(self, message_text: str, analyst: str | None = None) -> Signal:
        return parse_text_signal(message_text, source=self.name, asset_class=self.asset_class, analyst=analyst)

    async def ingest(self, message_text: str, analyst: str | None = None) -> Signal:
        signal = self.parse(message_text, analyst=analyst)
        await self.on_signal(signal)
        return signal
