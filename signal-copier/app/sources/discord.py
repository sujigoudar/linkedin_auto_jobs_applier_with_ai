"""Discord signal source — STUB.

To implement:
    pip install discord.py
    1. Create a Discord application + bot at discord.com/developers, enable
       the "Message Content" privileged intent, invite it to the signal
       server/channel.
    2. In `start()`, create a discord.Client with message_content intent,
       register an on_message handler filtered to the configured channel
       id, parse the message body, and await `self.on_signal(signal)` on a
       successful parse. Run the client with `asyncio.create_task(client.start(token))`
       so `start()` doesn't block the rest of the app.
    3. `parse()` needs real logic tailored to the specific server's signal
       format, same caveat as TelegramSource.
"""
from __future__ import annotations

from app.models import Signal
from app.sources.base import SourceAdapter


class DiscordSource(SourceAdapter):
    name = "discord"

    def __init__(self, on_signal, bot_token: str | None = None, channel_id: str | None = None):
        super().__init__(on_signal)
        self.bot_token = bot_token
        self.channel_id = channel_id

    async def start(self) -> None:
        raise NotImplementedError(
            "DiscordSource is a stub. See module docstring: install discord.py, enable the "
            "Message Content intent, register on_message, and implement parse()."
        )

    def parse(self, message_text: str) -> Signal:
        raise NotImplementedError("Implement message parsing for your specific Discord channel's format.")
