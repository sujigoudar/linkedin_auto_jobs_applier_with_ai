"""Slack signal source — STUB.

To implement:
    pip install slack-bolt
    1. Create a Slack app at api.slack.com/apps, enable Socket Mode or
       Events API, subscribe to `message.channels`, install to workspace.
    2. Since Slack push events look almost exactly like the generic webhook
       case, the simplest path is: point the Slack Events API HTTP endpoint
       at a route that extracts `event.text` and reuses
       `WebhookSource.parse()` after transforming the Slack message text
       into the same JSON shape (or write a dedicated parser here for the
       channel's actual signal format).
    3. Wire it into app/main.py as its own POST route (Slack requires a URL
       verification handshake on first setup — handle the `challenge` field).
"""
from __future__ import annotations

from app.models import Signal
from app.sources.base import SourceAdapter


class SlackSource(SourceAdapter):
    name = "slack"

    def __init__(self, on_signal, bot_token: str | None = None, channel_id: str | None = None):
        super().__init__(on_signal)
        self.bot_token = bot_token
        self.channel_id = channel_id

    async def start(self) -> None:
        raise NotImplementedError(
            "SlackSource is a stub. See module docstring: set up a Slack app with Events API "
            "or Socket Mode, and implement parse() for the channel's message format."
        )

    def parse(self, message_text: str) -> Signal:
        raise NotImplementedError("Implement message parsing for your specific Slack channel's format.")
