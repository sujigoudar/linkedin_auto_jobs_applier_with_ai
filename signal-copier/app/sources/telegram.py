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

## Revision commands ("close half", "move sl to breakeven", "cancel")

A message that doesn't parse as a fresh entry via `parse()` is tried
against `app/signal_commands.py`'s `classify_command_text` instead --
real-world follow-up phrasing an analyst uses to revise a trade already in
flight, not a new one. When that recognizes something AND this source was
constructed with `on_command` AND `symbol` (the open position/episode this
revision is about -- see `handle_message`'s docstring on where that comes
from), the resulting `CanonicalCommand` is handed to `on_command` (normally
`app/engine.py`'s `SignalCopierEngine.apply_canonical_command`) instead of
being silently dropped as "not a signal." A message that matches neither
grammar is genuinely not parsed here -- see `classify_revision_event` in
`app/signal_episode.py` for the richer commentary/no-action/ambiguous
classification a full episode-correlation pipeline would use instead of
this source's own bare pass/fail.

`parse()`'s shared grammar (app/sources/text_parser.py) accepts "close
<anything>" as a full close of an instrument literally named <anything> --
it has no notion of "half"/"all"/a percent being a FRACTION rather than a
symbol. So a message that parses with `side == Side.CLOSE` is deliberately
checked against `parse_command` FIRST: "close half"/"close all" are
recognized there as CLOSE_PERCENT/CLOSE_REMAINDER commands and dispatched
as such, never mistaken for a literal close of a symbol called "HALF"/"ALL".
Only when no command grammar recognizes it does a `side == Side.CLOSE`
parse fall through to a real close Signal (e.g. "close AAPL", a genuine
symbol) -- see `handle_message`.
"""
from __future__ import annotations

import logging

from app.errors import SignalValidationError
from app.models import AssetClass, Side, Signal
from app.signal_commands import CanonicalCommand, classify_command_text
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
        on_command=None,
        symbol_for_command=None,
        episode_correlator=None,
    ):
        super().__init__(on_signal)
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.asset_class = asset_class
        self._app = None
        #: Optional `app/signal_episode.py` `EpisodeCorrelator` -- when set,
        #: every message that DOES parse as a `Signal` (via `parse()`) is
        #: also fed through `EpisodeCorrelator.ingest` so this analyst's
        #: open-episode history (what `symbol_for_command` above resolves a
        #: bare revision command's symbol from) stays current. `None` (the
        #: default) disables this -- the source behaves exactly as before
        #: episode correlation existed.
        self.episode_correlator = episode_correlator
        #: Optional: called with a classified `CanonicalCommand` (see this
        #: module's docstring) -- `None` (the default) means this source
        #: only ever emits fresh `Signal`s, same as before this existed.
        self.on_command = on_command
        #: Optional: `(analyst) -> symbol | None` -- which open
        #: position/episode a bare revision message like "close half"
        #: (no symbol of its own) is about, for THIS analyst. Without one,
        #: revision commands that don't name their own symbol are never
        #: classified (there is nothing to attach them to) -- see
        #: `parse_command`.
        self.symbol_for_command = symbol_for_command

    def parse(self, message_text: str, analyst: str | None = None) -> Signal:
        return parse_text_signal(message_text, source=self.name, asset_class=self.asset_class, analyst=analyst)

    def parse_command(self, message_text: str, analyst: str | None = None) -> CanonicalCommand | None:
        """Best-effort: only produces a command when a symbol can be
        resolved for it (either from `self.symbol_for_command`, or from the
        message text itself for a command grammar that names one) -- see
        `app/signal_commands.py`'s `classify_command_text` docstring on why
        a symbol is required up front rather than guessed."""
        symbol = self.symbol_for_command(analyst) if self.symbol_for_command is not None else None
        if symbol is None:
            return None
        return classify_command_text(message_text, source=self.name, symbol=symbol, analyst=analyst)

    async def route_message(self, text: str, analyst: str | None) -> None:
        """The one place that decides what one inbound message text becomes:
        a fresh `Signal` (via `on_signal`), a `CanonicalCommand` (via
        `on_command`), or nothing recognizable. Used by `start()`'s real
        bot-polling handler AND directly testable without the network stack
        -- see this module's docstring for the `side == Side.CLOSE` /
        command-grammar ordering and why it matters."""
        try:
            signal = self.parse(text, analyst=analyst)
        except SignalValidationError:
            signal = None

        if signal is not None and signal.side is not Side.CLOSE:
            if self.episode_correlator is not None:
                self.episode_correlator.ingest(signal, text=text)
            await self.on_signal(signal)
            return

        command = self.parse_command(text, analyst=analyst)
        if command is not None and self.on_command is not None:
            await self.on_command(command)
            return

        if signal is not None:  # a genuine "close <real symbol>" -- no command grammar claimed it
            if self.episode_correlator is not None:
                self.episode_correlator.ingest(signal, text=text)
            await self.on_signal(signal)
            return

        logger.debug("telegram message did not parse as a signal or a command: %r", text)

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
            user = update.effective_user
            analyst = (user.username or user.full_name) if user else None
            await self.route_message(text, analyst)

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
