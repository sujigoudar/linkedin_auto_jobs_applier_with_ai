"""Telegram USER-ACCOUNT signal source (Track 5).

Parallel to `app.sources.telegram.TelegramSource` (a BotFather bot, a
member of the chat) -- this adapter is for a channel the owner can only
read via their OWN Telegram account (the provider doesn't allow adding a
bot, or the channel has protected content / forwarding disabled). It uses
Telethon (https://docs.telethon.dev/), the current stable MTProto client
library, authenticated as a real Telegram user.

SECURITY, hard requirement -- read before touching this file or
`scripts/telegram_user_login.py`:

    This adapter NEVER performs Telegram's interactive login flow
    (phone number -> code -> optional 2FA password) itself, and never
    accepts a session string as a plain Python argument. It only ever
    CONNECTS to an already-authorized session -- a `.session` file (or a
    session-string file) produced OFFLINE by
    `scripts/telegram_user_login.py`, which the account owner runs
    THEMSELVES, outside of any AI-visible terminal. See that script's own
    docstring and `docs/security/TELEGRAM_USER_LOGIN.md` for the full
    procedure and why. If the configured session is missing or not
    authorized, this adapter fails closed (`CollectorHealth.
    MISSING_CREDENTIALS`) -- it never falls back to prompting for a code
    or password, which would defeat the entire point of the standalone
    script.

Ingestion vs. forwarding/redistribution -- read before relying on either
claim below, and see this module's own tests for what's actually
exercised (never against a real Telegram account -- there is no way to
verify either claim empirically in this environment; both are reasoned
from Telethon's/Telegram's own published documentation only):

  - Telegram delivers a chat's messages, edits, and (where at all
    observable -- see `handle_deleted_event`'s own docstring) deletions
    to any client legitimately in that chat, REGARDLESS of the chat's
    own "restrict saving content" (`noforwards`) UI flag -- that flag
    governs whether a client's own UI offers forward/save/download
    actions, not whether the update stream itself is withheld from a
    member. This is the real, documented distinction point 5 of the
    Track 5 brief asks to verify rather than assume: raw ingestion
    (what this adapter does) is not the same operation `noforwards`
    restricts. If real-world testing against an actual `noforwards`
    channel ever contradicts this, `record_chat_protection_flag`'s own
    qualification-evidence write is exactly the place that discovery
    must be recorded honestly (see `CollectorHealth.
    PROTECTED_CONTENT_RESTRICTED`), not silently assumed away.
  - This adapter reads and records `noforwards` (see
    `extract_noforwards_flag`) purely as evidence for DOWNSTREAM
    forwarding/redistribution policy (point 10's `allowed_uses`
    isolation) -- it never attempts to bypass, strip, or ignore
    whatever restriction that flag implies for anything this service
    might do with the content LATER (e.g. republish it commercially).

Deletion observability: unlike `TelegramSource` (bots have NO Telegram
Bot API delete-notification update at all -- a real, disclosed,
permanent gap for that transport), a user-account MTProto session's
`events.MessageDeleted` IS a real, documented Telethon event. It is
implemented here (`handle_deleted_event`) -- but with one honest
disclosed limitation of MTProto itself, not this adapter's own
implementation choice: **for a private one-to-one chat**, Telegram's own
delete-message update does not carry the peer/chat id at all (only the
message id), so `event.chat_id` comes back `None` and this handler
cannot honestly attribute the deletion to a specific configured
collector's channel -- it logs and skips rather than guessing. For a
group/channel/supergroup (every configured collector's actual use case
per the Track 5 brief -- BuyAlerts/TradeAlgo-style broadcast channels),
`event.chat_id` IS populated and DELETE is fully handled. This
distinction is asserted directly in this module's own tests with fake
event objects, never empirically confirmed against a real Telegram
account (see this module's own docstring intro).

Historical import vs. live admission (point 7): `import_history` (read-
only, uses Telethon's `client.iter_messages`) is a COMPLETELY SEPARATE
code path from the live `events.NewMessage`/`MessageEdited`/
`MessageDeleted` handlers below -- it never calls `on_signal`
(app.sources.base.SourceAdapter's live-routing hook), only
`on_source_event` and the caller-supplied `save_historical_signal`
(reusing this codebase's own `import_batch`-tagged, owner-gated B9/E02
workflow -- see `app/main.py`'s `POST /sources/{source}/import-signals`
for the precedent this mirrors), and it never advances a collector's
live checkpoint. Only the live handlers below ever call `on_signal` or
advance the checkpoint, and only for a message strictly newer than it
(see `_admits_live`).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Optional, Protocol

from app.errors import SignalValidationError
from app.models import AssetClass, Signal, SourceEvent, SourceEventKind
from app.sources.base import SourceAdapter, SourceEventHandler
from app.sources.text_parser import parse_text_signal
from app.telegram_collectors import CollectorHealth

logger = logging.getLogger(__name__)

#: This adapter's own exact interpretation implementation -- see
#: `Signal.parser_version`'s own docstring. Deliberately distinct from
#: `app.sources.telegram.PARSER_VERSION` ("telegram-text-parser-v1"):
#: this is a different adapter reading a different transport's event
#: shape, even though both call the SAME underlying `parse_text_signal`
#: grammar -- a consumer replaying a raw message needs to know WHICH
#: adapter's field-extraction (message id, edit/delete semantics,
#: caption handling) produced it, not just which text grammar.
PARSER_VERSION = "telegram-user-text-parser-v1"


class CollectorRegistryPort(Protocol):
    """The narrow slice of `app.db.SignalStore` this adapter actually
    needs for checkpoint/health/qualification-evidence persistence --
    named here (rather than importing `SignalStore` directly) so a test
    can hand this adapter a lightweight fake with no real database, and
    so this adapter never depends on `app.db`'s much larger surface.
    `app.db.SignalStore` satisfies this protocol as-is (structural, not
    nominal -- no inheritance needed)."""

    def get_telegram_collector_checkpoint(self, collector_id: str) -> Optional[int]: ...

    def advance_telegram_collector_checkpoint(self, collector_id: str, message_id: int) -> None: ...

    def update_telegram_collector_health(
        self, collector_id: str, health_state: str, *, detail: str | None = None
    ) -> None: ...

    def record_telegram_collector_qualification_evidence(
        self, collector_id: str, *, evidence: dict, noforwards: bool | None = None, qualified_at: datetime | None = None
    ) -> None: ...


@dataclass
class UnsupportedFormatEvent:
    """What `handle_new_message_event`/`handle_edited_message_event`
    return (in addition to emitting the `UNSUPPORTED_FORMAT_ENCOUNTERED`
    health state) for a real message with neither text nor a caption --
    e.g. a photo/document/sticker/voice-note posted with no accompanying
    words. Point 5: this is a distinct, VISIBLE outcome, never a silent
    drop indistinguishable from "not a signal" plain-text chatter."""

    channel_id: str
    message_id: str
    media_type: Optional[str] = None


def extract_noforwards_flag(chat: Any) -> Optional[bool]:
    """Telethon's own `Chat`/`Channel.noforwards` attribute (the
    "restrict saving content" flag) -- `None` when the given entity
    doesn't expose one at all (e.g. a private one-to-one `User` entity,
    which has no such concept), never guessed as `False`. See this
    module's own docstring for what this flag does and doesn't restrict."""
    return getattr(chat, "noforwards", None)


def _message_text(message: Any) -> str:
    """The text OR caption of a Telethon `Message` -- Telethon's own data
    model uses the SAME `.message` field for a plain text message's body
    and a photo/document message's caption (there is no separate
    "caption" attribute to miss) -- see this module's own docstring for
    why captions are therefore already handled by reading this one
    field, same as `TelegramSource.handle_update` reads `message.text`
    for the Bot API's (structurally different, but analogous) shape."""
    text = getattr(message, "message", None)
    if text is None:
        text = getattr(message, "text", None)
    return text or ""


def _has_media(message: Any) -> bool:
    return getattr(message, "media", None) is not None


class TelegramUserSource(SourceAdapter):
    name = "telegram_user"

    def __init__(
        self,
        on_signal,
        *,
        collector_id: str,
        chat_id: int | str,
        api_id: int | None = None,
        api_hash: str | None = None,
        session_path: str | None = None,
        topic_id: int | str | None = None,
        asset_class: AssetClass = AssetClass.CRYPTO,
        on_source_event: SourceEventHandler | None = None,
        registry: CollectorRegistryPort | None = None,
        save_historical_signal: Callable[[Signal], None] | None = None,
    ):
        super().__init__(on_signal, on_source_event=on_source_event)
        self.collector_id = collector_id
        self.chat_id = chat_id
        self.topic_id = topic_id
        self.api_id = api_id
        self.api_hash = api_hash
        self.session_path = session_path
        self.asset_class = asset_class
        #: `None` is a supported, honest configuration (e.g. in a unit
        #: test) -- checkpoint/health simply aren't persisted anywhere
        #: and this adapter logs that loudly rather than pretending to
        #: track them. A real deployment (see `app/main.py`'s wiring)
        #: always passes the real `SignalStore`.
        self.registry = registry
        self.save_historical_signal = save_historical_signal
        self._client: Any = None
        if registry is None:
            logger.warning(
                "TelegramUserSource collector_id=%s constructed with no registry -- checkpoint/health/"
                "qualification-evidence will NOT be persisted; this is only appropriate for a test",
                collector_id,
            )

    def parse(self, message_text: str, analyst: str | None = None) -> Signal:
        return parse_text_signal(message_text, source=self.name, asset_class=self.asset_class, analyst=analyst)

    # -- Checkpoint / health helpers (safe no-ops with no registry) ------

    def _get_checkpoint(self) -> int | None:
        if self.registry is None:
            return None
        return self.registry.get_telegram_collector_checkpoint(self.collector_id)

    def _advance_checkpoint(self, message_id: int) -> None:
        if self.registry is None:
            return
        self.registry.advance_telegram_collector_checkpoint(self.collector_id, message_id)

    def _set_health(self, state: CollectorHealth, detail: str | None = None) -> None:
        if self.registry is None:
            return
        self.registry.update_telegram_collector_health(self.collector_id, state.value, detail=detail)

    def _record_qualified(self, *, message_id: str | None, method: str, noforwards: bool | None) -> None:
        if self.registry is None:
            return
        self.registry.record_telegram_collector_qualification_evidence(
            self.collector_id,
            evidence={"observed_message_id": message_id, "method": method},
            noforwards=noforwards,
        )

    def _admits_live(self, message_id: int) -> bool:
        """Point 7b: on every reconnect, only a message strictly NEWER
        than this collector's persisted checkpoint is treated as live and
        passed to `on_signal` -- a message at or below the checkpoint is
        one this collector (or MTProto's own gap-recovery on reconnect)
        is merely re-observing, never a fresh trading instruction. `None`
        checkpoint (never live-processed before) admits everything --
        the FIRST live message this collector ever sees is genuinely
        new, not backlog (backlog is only ever ingested through the
        separate, explicit `import_history`, point 7a)."""
        checkpoint = self._get_checkpoint()
        return checkpoint is None or message_id > checkpoint

    # -- Live message/edit/delete handling --------------------------------

    async def handle_new_message_event(self, event: Any, analyst: str | None = None) -> None:
        """`event`: a Telethon `events.NewMessage.Event` (or, in a test, a
        lightweight fake exposing the same attributes read here:
        `.chat_id`, `.message.id`, `.message.message` (text or caption),
        `.message.media`, `.message.date`). `analyst` is resolved by the
        real `start()` wiring via `await event.get_sender()` BEFORE this
        is called (kept out of this method so its own logic stays
        synchronous and directly testable, same separation
        `TelegramSource.handle_update` uses for its own already-resolved
        `update.effective_user`)."""
        channel_id = str(event.chat_id)
        message = event.message
        message_id = getattr(message, "id", None)
        message_id_str = str(message_id) if message_id is not None else None
        text = _message_text(message)

        if not text:
            if _has_media(message):
                self._set_health(
                    CollectorHealth.UNSUPPORTED_FORMAT_ENCOUNTERED,
                    detail=f"media-only message with no text/caption (message_id={message_id_str})",
                )
                await self._emit_source_event(
                    SourceEvent(
                        source=self.name,
                        kind=SourceEventKind.ORIGINAL,
                        channel_id=channel_id,
                        message_id=message_id_str,
                        provider_timestamp=getattr(message, "date", None),
                        reason="unsupported_format: media-only message, no text or caption",
                        raw_source_event={"message_id": message_id_str, "has_media": True, "text": ""},
                    )
                )
            return

        try:
            signal = self.parse(text, analyst=analyst)
        except SignalValidationError:
            logger.debug("telegram_user message did not parse as a signal: %r", text)
            return

        signal.channel_id = channel_id
        signal.message_id = message_id_str
        signal.parser_version = PARSER_VERSION

        if message_id is not None and not self._admits_live(message_id):
            logger.info(
                "telegram_user collector_id=%s message_id=%s at/below checkpoint -- not re-admitted live",
                self.collector_id,
                message_id_str,
            )
            return

        await self.on_signal(signal)
        if message_id is not None:
            self._advance_checkpoint(message_id)
        self._record_qualified(message_id=message_id_str, method="live_new_message_event", noforwards=None)
        await self._emit_source_event(
            SourceEvent(
                source=self.name,
                kind=SourceEventKind.ORIGINAL,
                channel_id=channel_id,
                message_id=message_id_str,
                provider_timestamp=getattr(message, "date", None),
                local_receipt_timestamp=signal.received_at,
                signal=signal,
                raw_source_event={"text": text, "message_id": message_id_str},
            )
        )

    async def handle_edited_message_event(self, event: Any, analyst: str | None = None) -> None:
        """`event`: a Telethon `events.MessageEdited.Event` -- structurally
        the same shape as `NewMessage.Event` but for an edit; Telethon
        fires this as a genuinely distinct event type (not a flag on
        `NewMessage`), unlike the Bot API's `update.edited_message`. An
        edit is deliberately NOT checkpoint-gated (see `_admits_live`'s
        own docstring: the checkpoint only prevents re-admitting an
        already-seen ORIGINAL as new -- an edit is real, new activity on
        a message this collector may have originally seen before its own
        checkpoint even existed, and must still be surfaced)."""
        channel_id = str(event.chat_id)
        message = event.message
        message_id = getattr(message, "id", None)
        message_id_str = str(message_id) if message_id is not None else None
        text = _message_text(message)
        edit_date = getattr(message, "edit_date", None)
        revision_id = f"{message_id_str}:{edit_date.isoformat()}" if edit_date and message_id_str else message_id_str

        if not text:
            if _has_media(message):
                self._set_health(
                    CollectorHealth.UNSUPPORTED_FORMAT_ENCOUNTERED,
                    detail=f"edited media-only message with no text/caption (message_id={message_id_str})",
                )
            return

        try:
            signal = self.parse(text, analyst=analyst)
        except SignalValidationError:
            logger.debug("telegram_user edited message did not parse as a signal: %r", text)
            return

        signal.channel_id = channel_id
        signal.message_id = message_id_str
        signal.revision_id = revision_id
        signal.original_message_id = message_id_str
        signal.parser_version = PARSER_VERSION

        await self.on_signal(signal)
        self._record_qualified(message_id=message_id_str, method="live_edited_message_event", noforwards=None)
        await self._emit_source_event(
            SourceEvent(
                source=self.name,
                kind=SourceEventKind.EDIT,
                channel_id=channel_id,
                message_id=message_id_str,
                revision_id=revision_id,
                original_message_id=message_id_str,
                provider_timestamp=edit_date,
                local_receipt_timestamp=signal.received_at,
                signal=signal,
                raw_source_event={"text": text, "message_id": message_id_str, "is_edit": True},
            )
        )

    async def handle_deleted_event(self, event: Any) -> None:
        """`event`: a Telethon `events.MessageDeleted.Event` -- exposes
        `.deleted_ids` (a list of message ids) and `.chat_id` (see this
        module's own docstring for the one real, disclosed limitation:
        `chat_id` is `None` for a private one-to-one chat's delete
        update, per MTProto's own update shape, not this adapter's
        choice). A `None` chat_id is skipped (logged, never guessed) --
        this adapter has no configured chat to honestly attribute it to."""
        chat_id = getattr(event, "chat_id", None)
        if chat_id is None:
            logger.warning(
                "telegram_user collector_id=%s received a MessageDeleted event with no chat_id "
                "(a documented MTProto limitation for private one-to-one chats) -- cannot attribute "
                "this deletion to a specific channel, skipping rather than guessing",
                self.collector_id,
            )
            return
        channel_id = str(chat_id)
        for message_id in getattr(event, "deleted_ids", []) or []:
            message_id_str = str(message_id)
            await self._emit_source_event(
                SourceEvent(
                    source=self.name,
                    kind=SourceEventKind.DELETE,
                    channel_id=channel_id,
                    message_id=message_id_str,
                    original_message_id=message_id_str,
                    raw_source_event={"message_id": message_id_str, "deleted": True},
                )
            )

    # -- Protected-content (noforwards) evidence -------------------------

    async def record_chat_protection_flag(self, chat: Any) -> None:
        """Call once after resolving this collector's chat entity (real
        `start()` wiring does this right after `client.get_entity`).
        Records `noforwards` as qualification evidence -- see this
        module's own docstring and `SignalStore.
        record_telegram_collector_qualification_evidence`'s docstring for
        why this ALSO sets `health_state` to `protected_content_
        restricted` (never silently `healthy_qualified`) when the flag is
        set."""
        noforwards = extract_noforwards_flag(chat)
        if self.registry is not None:
            self.registry.record_telegram_collector_qualification_evidence(
                self.collector_id,
                evidence={"chat_noforwards_observed": noforwards},
                noforwards=noforwards,
            )

    # -- Historical import (point 7a) -- read-only, NEVER live-routed ----

    async def import_history(
        self,
        messages: list[Any],
        *,
        batch_label: str,
        analyst: str | None = None,
    ) -> dict:
        """Point 7a: an explicit, owner-triggered, READ-ONLY historical
        import -- `messages` is whatever the caller's real `start()`
        wiring already fetched via Telethon's own `client.iter_messages`
        (kept out of this method so it's directly testable with plain
        fake message objects, no real Telethon client needed, same
        separation as every handler above).

        Mirrors `app/main.py`'s existing `POST /sources/{source}/
        import-signals` (E02/B9) workflow exactly: every imported
        `Signal` is tagged with `import_batch=batch_label` and handed to
        `save_historical_signal` (the caller wires this to `SignalStore.
        save_signal` directly) -- NEVER to `self.on_signal`
        (`SignalCopierEngine.handle_signal`, the live-routing entrypoint).
        This is the one, load-bearing distinction point 7 requires: a
        history import can never itself produce a live order, no matter
        how it's invoked, because it structurally never calls the
        function that could. It also never advances this collector's
        live checkpoint (`advance_telegram_collector_checkpoint`) -- a
        history import catching this collector up on the past must not
        be mistaken for "already live-caught-up to here."

        Returns the same `{"imported": [...], "skipped": [...]}` shape
        `import_signals`'s endpoint returns, for a caller to relay
        onward."""
        imported: list[dict] = []
        skipped: list[dict] = []
        for message in messages:
            message_id = getattr(message, "id", None)
            message_id_str = str(message_id) if message_id is not None else None
            text = _message_text(message)
            if not text:
                skipped.append({"message_id": message_id_str, "outcome": "unsupported_format", "detail": "no text or caption"})
                continue
            try:
                signal = self.parse(text, analyst=analyst)
            except SignalValidationError as exc:
                skipped.append({"message_id": message_id_str, "outcome": "no_match", "detail": str(exc)})
                continue
            signal.channel_id = str(self.chat_id)
            signal.message_id = message_id_str
            signal.parser_version = PARSER_VERSION
            signal.import_batch = batch_label
            if self.save_historical_signal is not None:
                self.save_historical_signal(signal)
            imported.append({"id": signal.id, "message_id": message_id_str, "symbol": signal.symbol})
        return {"batch_label": batch_label, "imported": imported, "skipped": skipped}

    # -- Lifecycle --------------------------------------------------------

    async def start(self) -> None:
        try:
            from telethon import TelegramClient, events
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "telethon is not installed; run `pip install telethon` -- see "
                "docs/security/TELEGRAM_USER_LOGIN.md for the full setup procedure"
            ) from exc

        if not self.session_path or not self.api_id or not self.api_hash:
            self._set_health(CollectorHealth.MISSING_CREDENTIALS, detail="session_path/api_id/api_hash not fully configured")
            raise RuntimeError(
                "TelegramUserSource requires session_path, api_id, and api_hash -- see "
                "docs/security/TELEGRAM_USER_LOGIN.md; this adapter never performs interactive login itself"
            )

        # SECURITY: connect() only -- NEVER client.start() (that method's
        # own interactive prompts for phone/code/2FA password are exactly
        # what this codebase must never trigger from inside a live
        # service process; see this module's own docstring).
        self._client = TelegramClient(self.session_path, self.api_id, self.api_hash)
        await self._client.connect()
        if not await self._client.is_user_authorized():
            self._set_health(
                CollectorHealth.MISSING_CREDENTIALS,
                detail="session is not authorized -- run scripts/telegram_user_login.py to (re)authorize it",
            )
            raise RuntimeError(
                "Telegram user session is not authorized. Never perform interactive login from here -- "
                "run `python scripts/telegram_user_login.py` yourself, locally, to (re)authorize this session."
            )

        try:
            chat = await self._client.get_entity(self.chat_id)
        except Exception as exc:  # pragma: no cover - real network/API errors, shape varies
            self._set_health(CollectorHealth.NO_CHANNEL_ACCESS, detail=str(exc))
            raise
        await self.record_chat_protection_flag(chat)

        async def _on_new(event):
            sender = await event.get_sender()
            analyst = (getattr(sender, "username", None) or getattr(sender, "first_name", None)) if sender else None
            await self.handle_new_message_event(event, analyst=analyst)

        async def _on_edit(event):
            sender = await event.get_sender()
            analyst = (getattr(sender, "username", None) or getattr(sender, "first_name", None)) if sender else None
            await self.handle_edited_message_event(event, analyst=analyst)

        async def _on_delete(event):
            await self.handle_deleted_event(event)

        self._client.add_event_handler(_on_new, events.NewMessage(chats=chat))
        self._client.add_event_handler(_on_edit, events.MessageEdited(chats=chat))
        self._client.add_event_handler(_on_delete, events.MessageDeleted(chats=chat))

    async def stop(self) -> None:
        if self._client is None:
            return
        await self._client.disconnect()
