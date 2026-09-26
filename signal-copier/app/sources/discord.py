"""Discord signal source.

Setup:
    pip install discord.py
    1. Create a Discord application + bot at discord.com/developers, enable
       the "Message Content" privileged intent in the bot's settings, and
       invite it to the signal server (needs "Read Messages"/"View Channel"
       and "Read Message History" permissions).
    2. Find the target channel's ID (enable Developer Mode in Discord,
       right-click the channel -> Copy Channel ID).
    3. Set DISCORD_BOT_TOKEN and pass channel_id to DiscordSource.

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


class DiscordSource(SourceAdapter):
    name = "discord"

    def __init__(
        self,
        on_signal,
        bot_token: str,
        channel_id: int,
        asset_class: AssetClass = AssetClass.CRYPTO,
    ):
        super().__init__(on_signal)
        self.bot_token = bot_token
        self.channel_id = channel_id
        self.asset_class = asset_class
        self._client = None
        self._task: asyncio.Task | None = None

    def parse(self, message_text: str, analyst: str | None = None) -> Signal:
        return parse_text_signal(message_text, source=self.name, asset_class=self.asset_class, analyst=analyst)

    async def start(self) -> None:
        try:
            import discord
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("discord.py is not installed; run `pip install discord.py`") from exc

        intents = discord.Intents.default()
        intents.message_content = True
        client = discord.Client(intents=intents)

        @client.event
        async def on_message(message: "discord.Message") -> None:
            if message.author == client.user or message.channel.id != self.channel_id:
                return
            try:
                signal = self.parse(message.content, analyst=str(message.author))
            except SignalValidationError:
                logger.debug("discord message did not parse as a signal: %r", message.content)
                return
            await self.on_signal(signal)

        self._client = client
        self._task = asyncio.create_task(client.start(self.bot_token))

    async def stop(self) -> None:
        if self._client is not None:
            await self._client.close()
        if self._task is not None:
            self._task.cancel()
