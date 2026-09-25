"""Telegram signal source.

Setup:
    pip install python-telegram-bot
    1. Create a bot via @BotFather, get a bot token.
    2. Add the bot to the signal channel/group (as admin if it's a channel
       broadcasting messages the bot needs to read).
    3. Find the channel/group's chat_id (e.g. forward a message from it to
       @userinfobot, or check the `chat.id` on any update your bot receives).
    4. Set TELEGRAM_BOT_TOKEN and pass chat_id to TelegramSource.

Message parsing uses the shared free-text parser (app/sources/text_parser.py),
which handles the common "BUY BTCUSDT @ 65000 SL 63000 TP 70000" family of
formats. If your channel's format doesn't fit that, override `parse()`.
"""
from __future__ import annotations

import logging

from app.errors import SignalValidationError
from app.models import AssetClass, Signal
from app.sources.base import SourceAdapter
from app.sources.text_parser import parse_text_signal

logger = logging.getLogger(__name__)


class TelegramSource(SourceAdapter):
    name = "telegram"

    def __init__(
        self,
        on_signal,
        bot_token: str,
        chat_id: int | str,
        asset_class: AssetClass = AssetClass.CRYPTO,
    ):
        super().__init__(on_signal)
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.asset_class = asset_class
        self._app = None

    def parse(self, message_text: str) -> Signal:
        return parse_text_signal(message_text, source=self.name, asset_class=self.asset_class)

    async def start(self) -> None:
        try:
            from telegram import Update
            from telegram.ext import Application, ContextTypes, MessageHandler, filters
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "python-telegram-bot is not installed; run `pip install python-telegram-bot`"
            ) from exc

        async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            if str(update.effective_chat.id) != str(self.chat_id):
                return
            text = update.effective_message.text or ""
            try:
                signal = self.parse(text)
            except SignalValidationError:
                logger.debug("telegram message did not parse as a signal: %r", text)
                return
            await self.on_signal(signal)

        self._app = Application.builder().token(self.bot_token).build()
        self._app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

        await self._app.initialize()
        await self._app.start()
        await self._app.updater.start_polling()

    async def stop(self) -> None:
        if self._app is None:
            return
        await self._app.updater.stop()
        await self._app.stop()
        await self._app.shutdown()
