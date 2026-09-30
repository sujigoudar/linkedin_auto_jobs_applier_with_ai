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

Provider message identity / source ledger: every dispatched `Signal` now
carries this chat's own `channel_id`, the update's native `message_id`,
and `parser_version`; an edited message (python-telegram-bot's own
`update.edited_message`) is recognized as a real EDIT (same `message_id`,
new `revision_id`) rather than silently re-processed as a brand-new
message. Pass `on_source_event` to also receive a `SourceEvent` for each
ORIGINAL/EDIT. Known, disclosed gap: Telegram's Bot API has no
delete-notification update at all, so DELETE is not (and cannot honestly
be) implemented here -- see `handle_update`'s own docstring.
"""
from __future__ import annotations

import logging

from app.errors import SignalValidationError
from app.models import AssetClass, Signal, SourceEvent, SourceEventKind
from app.sources.base import SourceAdapter, SourceEventHandler
from app.sources.text_parser import parse_text_signal

logger = logging.getLogger(__name__)

#: This adapter's own exact interpretation implementation -- see
#: `Signal.parser_version`'s own docstring. Bump this whenever `parse()`'s
#: reading of message text changes in a way that would matter to a
#: consumer replaying a past raw message.
PARSER_VERSION = "telegram-text-parser-v1"


class TelegramSource(SourceAdapter):
    name = "telegram"

    def __init__(
        self,
        on_signal,
        bot_token: str,
        chat_id: int | str,
        asset_class: AssetClass = AssetClass.CRYPTO,
        on_source_event: SourceEventHandler | None = None,
    ):
        super().__init__(on_signal, on_source_event=on_source_event)
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.asset_class = asset_class
        self._app = None

    def parse(self, message_text: str, analyst: str | None = None) -> Signal:
        return parse_text_signal(message_text, source=self.name, asset_class=self.asset_class, analyst=analyst)

    async def handle_update(self, update) -> None:
        """The real filtering/parsing/dispatch logic for one incoming
        Telegram update: chat_id authorization check, analyst-name
        derivation from the sender, and SignalValidationError handling for
        unparseable text. Extracted out of `start()`'s registered handler
        so it is directly callable/testable with a lightweight fake
        `update` object, without needing a real python-telegram-bot
        `Application` (that library is only needed to actually receive
        updates over the network, not to exercise this logic).

        Provider message identity / edit awareness: `update.edited_message`
        is how python-telegram-bot's own `Update` distinguishes an edited
        message from a brand-new one (an edit callback carries the SAME
        `message_id` with a new `edit_date`) -- `getattr` throughout so a
        lightweight fake `update` in a test (or any real update shape
        missing these attributes) degrades to `None`/"not an edit" rather
        than raising. Telegram's Bot API has no delete-notification update
        at all for a bot to receive -- DELETE is a real, disclosed gap
        this adapter cannot close (see this module's own docstring),
        never fabricated by inferring one from silence."""
        if str(update.effective_chat.id) != str(self.chat_id):
            return
        message = update.effective_message
        text = message.text or ""
        user = update.effective_user
        analyst = (user.username or user.full_name) if user else None

        edited_message = getattr(update, "edited_message", None)
        is_edit = edited_message is not None
        message_id = getattr(message, "message_id", None)
        message_id_str = str(message_id) if message_id is not None else None
        edit_date = getattr(message, "edit_date", None)
        provider_timestamp = edit_date if is_edit else getattr(message, "date", None)
        revision_id = f"{message_id_str}:{edit_date.isoformat()}" if is_edit and edit_date and message_id_str else None

        try:
            signal = self.parse(text, analyst=analyst)
        except SignalValidationError:
            logger.debug("telegram message did not parse as a signal: %r", text)
            return

        signal.channel_id = str(self.chat_id)
        signal.message_id = message_id_str
        signal.parser_version = PARSER_VERSION
        if is_edit:
            signal.revision_id = revision_id
            signal.original_message_id = message_id_str

        await self.on_signal(signal)
        await self._emit_source_event(
            SourceEvent(
                source=self.name,
                kind=SourceEventKind.EDIT if is_edit else SourceEventKind.ORIGINAL,
                channel_id=signal.channel_id,
                message_id=message_id_str,
                revision_id=revision_id,
                original_message_id=message_id_str if is_edit else None,
                provider_timestamp=provider_timestamp,
                local_receipt_timestamp=signal.received_at,
                signal=signal,
                raw_source_event={"text": text, "message_id": message_id_str, "is_edit": is_edit},
            )
        )

    async def start(self) -> None:
        try:
            from telegram import Update
            from telegram.ext import Application, ContextTypes, MessageHandler, filters
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "python-telegram-bot is not installed; run `pip install python-telegram-bot`"
            ) from exc

        async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            await self.handle_update(update)

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
