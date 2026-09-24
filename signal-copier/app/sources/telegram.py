"""Telegram signal source — STUB.

To implement:
    pip install python-telegram-bot
    1. Create a bot via @BotFather, get TELEGRAM_BOT_TOKEN.
    2. Add the bot to the signal channel/group (or use a user session via
       Telethon/Pyrogram if you need to read a channel the bot can't join).
    3. In `start()`, register a message handler that calls `self.parse()` on
       each incoming message and, on a successful parse, awaits
       `self.on_signal(signal)`.
    4. `parse()` needs real logic for whatever signal format the channel
       actually posts (e.g. "BUY BTCUSDT @ 65000 SL 63000 TP 70000") — there
       is no universal Telegram signal format, so this has to be tailored to
       the specific channel(s) you're copying from. Consider a small regex
       or LLM-based parser depending on how consistent the formatting is.
"""
from __future__ import annotations

from typing import Any

from app.models import Signal
from app.sources.base import SourceAdapter


class TelegramSource(SourceAdapter):
    name = "telegram"

    def __init__(self, on_signal, bot_token: str | None = None, chat_id: str | None = None):
        super().__init__(on_signal)
        self.bot_token = bot_token
        self.chat_id = chat_id

    async def start(self) -> None:
        raise NotImplementedError(
            "TelegramSource is a stub. See module docstring for the integration steps: "
            "install python-telegram-bot, register a message handler, and implement parse()."
        )

    def parse(self, message_text: str) -> Signal:
        raise NotImplementedError("Implement message parsing for your specific Telegram channel's format.")
