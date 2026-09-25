"""Slack signal source, via Socket Mode (no public URL required).

Setup:
    pip install slack-bolt
    1. Create a Slack app at api.slack.com/apps.
    2. Enable Socket Mode, generate an app-level token (starts `xapp-`) with
       the `connections:write` scope.
    3. Under OAuth & Permissions, add the `channels:history` (or
       `groups:history` for private channels) and `channels:read` bot
       scopes, install the app to the workspace, and grab the bot token
       (starts `xoxb-`).
    4. Invite the bot to the signal channel, find the channel's ID (right
       click the channel -> View channel details), and set
       SLACK_BOT_TOKEN / SLACK_APP_TOKEN.

Message parsing uses the shared free-text parser (app/sources/text_parser.py).
Override `parse()` if the channel's format doesn't fit that family.
"""
from __future__ import annotations

import asyncio
import logging

from app.errors import SignalValidationError
from app.models import AssetClass, Signal
from app.sources.base import SourceAdapter
from app.sources.text_parser import parse_text_signal

logger = logging.getLogger(__name__)


class SlackSource(SourceAdapter):
    name = "slack"

    def __init__(
        self,
        on_signal,
        bot_token: str,
        app_token: str,
        channel_id: str,
        asset_class: AssetClass = AssetClass.CRYPTO,
    ):
        super().__init__(on_signal)
        self.bot_token = bot_token
        self.app_token = app_token
        self.channel_id = channel_id
        self.asset_class = asset_class
        self._handler = None

    def parse(self, message_text: str) -> Signal:
        return parse_text_signal(message_text, source=self.name, asset_class=self.asset_class)

    async def start(self) -> None:
        try:
            from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler
            from slack_bolt.async_app import AsyncApp
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("slack-bolt is not installed; run `pip install slack-bolt`") from exc

        app = AsyncApp(token=self.bot_token)

        @app.event("message")
        async def handle_message(event: dict, **kwargs) -> None:
            if event.get("channel") != self.channel_id or event.get("subtype"):
                return
            text = event.get("text", "")
            try:
                signal = self.parse(text)
            except SignalValidationError:
                logger.debug("slack message did not parse as a signal: %r", text)
                return
            await self.on_signal(signal)

        self._handler = AsyncSocketModeHandler(app, self.app_token)
        asyncio.create_task(self._handler.start_async())

    async def stop(self) -> None:
        if self._handler is not None:
            await self._handler.close_async()
