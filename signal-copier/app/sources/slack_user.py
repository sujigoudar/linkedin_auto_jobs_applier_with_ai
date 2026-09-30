"""Slack USER-TOKEN signal source (Track 6).

Parallel to `app.sources.slack.SlackSource` (a Slack app's BOT token,
`xoxb-...`, which requires the workspace owner to separately invite the
bot into the channel) -- this adapter is for a workspace the account
owner cannot invite a bot into at all (e.g. a workspace they don't
administer), using a Slack OAuth **user token** (`xoxp-...`) instead. A
user token is an OFFICIALLY-SANCTIONED, documented Slack mechanism for an
app to act AS the authenticated person -- not a bot the workspace owner
must separately add -- obtained via a real OAuth 2.0 install flow with
user scopes (`channels:history`, `groups:history`, `channels:read`)
granted to that person specifically. See
`docs/security/SLACK_USER_TOKEN.md` for the full, owner-run authorization
procedure; this module never performs any part of that OAuth flow itself
and never accepts a token as anything but an already-issued string read
from an environment variable (see `__init__`'s own docstring).

Transport: Slack's real-time delivery mechanism is Socket Mode
(`slack_bolt`/`slack_sdk`, already this codebase's Slack dependency --
see `app.sources.slack`'s own docstring), which needs an app-level token
(`xapp-...`, `connections:write` scope) exactly like the bot-based
adapter already uses. What differs is which token identifies the
*caller* of the Slack Web API calls this app makes: a bot token
(`SlackSource`) vs. a user token (this adapter). Both can share the same
Socket Mode app-level token/connection; Slack delivers `message` events
(including `message_changed`/`message_deleted` subtypes) to any
Socket-Mode-connected app subscribed to them, for any channel the
AUTHENTICATED IDENTITY (bot or user, whichever token installed the
relevant scope) can see -- this is what lets this adapter read channels
the user is a member of without a bot ever being invited.

Message-lifecycle event shapes -- verified against Slack's own published
Events API reference (never against a real live workspace connection --
none is available in this environment; see this module's own tests for
what's actually exercised against fake event dicts):

  - A plain new message: `{"type": "message", "channel": ..., "user": ...,
    "text": ..., "ts": "1699999999.000100"}` -- `ts` is Slack's own
    message identifier (a stringified microsecond Unix timestamp, unique
    per channel), used here as `Signal.message_id` exactly the way
    `SlackSource` already treats it as the event's identity.
  - An edit: Slack delivers a distinct `subtype: "message_changed"` event
    (https://api.slack.com/events/message/message_changed, current as of
    this codebase's own knowledge) carrying `message` (the edited
    message, itself with `edited: {"user": ..., "ts": "..."}`) and
    `previous_message`. `message.ts` is the ORIGINAL message's stable id
    (unchanged by an edit); `message.edited.ts` is this specific
    revision's own id -- used here for `Signal.revision_id`, the same
    original/revision split `TelegramUserSource` uses for a Telegram
    edit.
  - A delete: Slack delivers a distinct `subtype: "message_deleted"`
    event (https://api.slack.com/events/message/message_deleted)
    carrying `deleted_ts` (the id of the message that was deleted) and
    `previous_message`. This IS a real, documented Slack Events API
    event for any channel the connected app can see -- unlike bot-only
    Telegram (which the Track 5 module's own docstring notes has NO
    delete notification at all), Slack exposes deletion regardless of
    whether the observer is a bot or a user-token identity. This is
    reasoned from Slack's own published API reference, not empirically
    re-confirmed against a live workspace in this build -- if real-world
    testing ever contradicts it (e.g. a workspace-level setting that
    suppresses `message_deleted` delivery), that is exactly the kind of
    discovery `CollectorHealth`/health_detail exists to surface honestly,
    not silently assume away.

Historical import vs. live admission: mirrors
`TelegramUserSource.import_history` exactly -- a separate, read-only
`import_history` method that calls `save_historical_signal`, never
`on_signal`, and never advances the live checkpoint. Only the live
event handlers below ever call `on_signal` or advance the checkpoint.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Callable, Optional, Protocol

from app.errors import SignalValidationError
from app.models import AssetClass, Signal, SourceEvent, SourceEventKind
from app.sources.base import SourceAdapter, SourceEventHandler
from app.sources.text_parser import parse_text_signal
from app.collector_registry import CollectorHealth

logger = logging.getLogger(__name__)

#: This adapter's own exact interpretation implementation -- see
#: `Signal.parser_version`'s own docstring. Distinct from
#: `app.sources.slack.SlackSource`'s implicit (unset) parser identity --
#: this is a different transport/adapter reading a different event shape
#: (Socket Mode + user-token identity, with edit/delete handling
#: `SlackSource` doesn't do at all), even though both call the same
#: underlying `parse_text_signal` grammar.
PARSER_VERSION = "slack-user-text-parser-v1"


class CollectorRegistryPort(Protocol):
    """The narrow slice of `app.db.SignalStore` this adapter actually
    needs -- named here (rather than importing `SignalStore` directly)
    so a test can hand this adapter a lightweight fake with no real
    database. `app.db.SignalStore` satisfies this protocol as-is
    (structural, not nominal)."""

    def get_pull_collector_checkpoint(self, collector_id: str) -> Optional[str]: ...

    def advance_pull_collector_checkpoint(self, collector_id: str, checkpoint: str) -> None: ...

    def update_pull_collector_health(
        self, collector_id: str, health_state: str, *, detail: str | None = None
    ) -> None: ...

    def record_pull_collector_qualification_evidence(
        self, collector_id: str, *, evidence: dict
    ) -> None: ...


def _checkpoint_key(ts: str) -> float:
    """Slack `ts` values (`"1699999999.000100"`) sort correctly as
    floats (they're not snowflake-style opaque ids) -- used only to
    decide `_admits_live`, never persisted as anything but the original
    string (see `app.collector_registry`'s own docstring on why the
    registry itself stores an opaque TEXT checkpoint)."""
    try:
        return float(ts)
    except (TypeError, ValueError):
        return 0.0


class SlackUserSource(SourceAdapter):
    name = "slack_user"

    def __init__(
        self,
        on_signal,
        *,
        collector_id: str,
        channel_id: str,
        user_token: str | None = None,
        app_token: str | None = None,
        asset_class: AssetClass = AssetClass.CRYPTO,
        on_source_event: SourceEventHandler | None = None,
        registry: CollectorRegistryPort | None = None,
        save_historical_signal: Callable[[Signal], None] | None = None,
    ):
        super().__init__(on_signal, on_source_event=on_source_event)
        self.collector_id = collector_id
        self.channel_id = channel_id
        #: The OAuth USER token (`xoxp-...`) -- read from an env var by
        #: the caller (see `docs/security/SLACK_USER_TOKEN.md`), never
        #: accepted as a literal/prompted value from this class itself.
        self.user_token = user_token
        self.app_token = app_token
        self.asset_class = asset_class
        self.registry = registry
        self.save_historical_signal = save_historical_signal
        self._handler = None
        if registry is None:
            logger.warning(
                "SlackUserSource collector_id=%s constructed with no registry -- checkpoint/health/"
                "qualification-evidence will NOT be persisted; this is only appropriate for a test",
                collector_id,
            )

    def parse(self, message_text: str, analyst: str | None = None) -> Signal:
        return parse_text_signal(message_text, source=self.name, asset_class=self.asset_class, analyst=analyst)

    # -- Checkpoint / health helpers (safe no-ops with no registry) -------

    def _get_checkpoint(self) -> str | None:
        if self.registry is None:
            return None
        return self.registry.get_pull_collector_checkpoint(self.collector_id)

    def _advance_checkpoint(self, ts: str) -> None:
        if self.registry is None:
            return
        self.registry.advance_pull_collector_checkpoint(self.collector_id, ts)

    def _set_health(self, state: CollectorHealth, detail: str | None = None) -> None:
        if self.registry is None:
            return
        self.registry.update_pull_collector_health(self.collector_id, state.value, detail=detail)

    def _record_qualified(self, *, ts: str | None, method: str) -> None:
        if self.registry is None:
            return
        self.registry.record_pull_collector_qualification_evidence(
            self.collector_id, evidence={"observed_ts": ts, "method": method}
        )

    def _admits_live(self, ts: str) -> bool:
        """Same point-7 pattern as `TelegramUserSource._admits_live`: only
        a message strictly newer than this collector's persisted
        checkpoint is treated as live -- a reconnect re-observing old
        history is never re-admitted."""
        checkpoint = self._get_checkpoint()
        if checkpoint is None:
            return True
        return _checkpoint_key(ts) > _checkpoint_key(checkpoint)

    # -- Live message/edit/delete handling --------------------------------

    async def handle_event(self, event: dict) -> None:
        """The real dispatch entrypoint for one raw Slack `message` event
        dict (as delivered by Socket Mode) -- routes to the new/edit/
        delete handler by `subtype`, exactly the same subtype-routing
        `app.sources.slack.SlackSource.handle_event` already does for
        filtering, but this adapter (unlike the bot-based one) actually
        HANDLES `message_changed`/`message_deleted` instead of skipping
        every subtype."""
        if event.get("channel") != self.channel_id:
            return
        subtype = event.get("subtype")
        if subtype == "message_changed":
            await self.handle_message_changed_event(event)
        elif subtype == "message_deleted":
            await self.handle_message_deleted_event(event)
        elif subtype:
            # bot_message, channel_join, thread_broadcast, etc. -- same
            # "not a real trading instruction" skip SlackSource already
            # applies to every other subtype.
            return
        else:
            await self.handle_new_message_event(event)

    async def handle_new_message_event(self, event: dict, analyst: str | None = None) -> None:
        ts = event.get("ts")
        text = event.get("text", "")
        analyst = analyst if analyst is not None else event.get("user")

        if not text:
            if event.get("files"):
                self._set_health(
                    CollectorHealth.UNSUPPORTED_FORMAT_ENCOUNTERED,
                    detail=f"file-only message with no text (ts={ts})",
                )
                await self._emit_source_event(
                    SourceEvent(
                        source=self.name,
                        kind=SourceEventKind.ORIGINAL,
                        channel_id=self.channel_id,
                        message_id=ts,
                        reason="unsupported_format: file-only message, no text",
                        raw_source_event=event,
                    )
                )
            return

        try:
            signal = self.parse(text, analyst=analyst)
        except SignalValidationError:
            logger.debug("slack_user message did not parse as a signal: %r", text)
            return

        signal.channel_id = self.channel_id
        signal.message_id = ts
        signal.parser_version = PARSER_VERSION

        if ts is not None and not self._admits_live(ts):
            logger.info(
                "slack_user collector_id=%s ts=%s at/below checkpoint -- not re-admitted live",
                self.collector_id,
                ts,
            )
            return

        await self.on_signal(signal)
        if ts is not None:
            self._advance_checkpoint(ts)
        self._record_qualified(ts=ts, method="live_message_event")
        await self._emit_source_event(
            SourceEvent(
                source=self.name,
                kind=SourceEventKind.ORIGINAL,
                channel_id=self.channel_id,
                message_id=ts,
                local_receipt_timestamp=signal.received_at,
                signal=signal,
                raw_source_event=event,
            )
        )

    async def handle_message_changed_event(self, event: dict, analyst: str | None = None) -> None:
        """`event`: a Slack `message_changed`-subtype event dict --
        `event["message"]` is the edited message (with its own
        `edited: {"ts": ...}` marker), `event["message"]["ts"]` is the
        ORIGINAL message's stable id. Deliberately NOT checkpoint-gated
        (same reasoning as `TelegramUserSource.handle_edited_message_
        event`): an edit is real new activity, not a re-admission of an
        already-seen original."""
        message = event.get("message") or {}
        original_ts = message.get("ts")
        text = message.get("text", "")
        edited = message.get("edited") or {}
        revision_ts = edited.get("ts") or original_ts
        analyst = analyst if analyst is not None else message.get("user")

        if not text:
            self._set_health(
                CollectorHealth.UNSUPPORTED_FORMAT_ENCOUNTERED,
                detail=f"edited file-only message with no text (ts={original_ts})",
            )
            return

        try:
            signal = self.parse(text, analyst=analyst)
        except SignalValidationError:
            logger.debug("slack_user edited message did not parse as a signal: %r", text)
            return

        signal.channel_id = self.channel_id
        signal.message_id = original_ts
        signal.revision_id = revision_ts
        signal.original_message_id = original_ts
        signal.parser_version = PARSER_VERSION

        await self.on_signal(signal)
        self._record_qualified(ts=original_ts, method="live_message_changed_event")
        await self._emit_source_event(
            SourceEvent(
                source=self.name,
                kind=SourceEventKind.EDIT,
                channel_id=self.channel_id,
                message_id=original_ts,
                revision_id=revision_ts,
                original_message_id=original_ts,
                local_receipt_timestamp=signal.received_at,
                signal=signal,
                raw_source_event=event,
            )
        )

    async def handle_message_deleted_event(self, event: dict) -> None:
        """`event`: a Slack `message_deleted`-subtype event dict --
        `event["deleted_ts"]` is the id of the message that was deleted.
        See this module's own docstring for the citation this is based
        on (Slack's published Events API reference, not empirically
        re-verified against a live workspace here)."""
        deleted_ts = event.get("deleted_ts")
        if deleted_ts is None:
            logger.warning(
                "slack_user collector_id=%s received a message_deleted event with no deleted_ts -- skipping",
                self.collector_id,
            )
            return
        await self._emit_source_event(
            SourceEvent(
                source=self.name,
                kind=SourceEventKind.DELETE,
                channel_id=self.channel_id,
                message_id=deleted_ts,
                original_message_id=deleted_ts,
                raw_source_event=event,
            )
        )

    # -- Historical import -- read-only, NEVER live-routed -----------------

    async def import_history(
        self,
        messages: list[dict],
        *,
        batch_label: str,
        analyst: str | None = None,
    ) -> dict:
        """Mirrors `TelegramUserSource.import_history` exactly: every
        imported `Signal` is tagged `import_batch=batch_label` and handed
        to `save_historical_signal` -- NEVER `self.on_signal`. Never
        advances the live checkpoint. `messages` is whatever the caller's
        real wiring already fetched via `conversations.history` (kept out
        of this method so it's directly testable with plain dicts)."""
        imported: list[dict] = []
        skipped: list[dict] = []
        for message in messages:
            ts = message.get("ts")
            text = message.get("text", "")
            if not text:
                skipped.append({"ts": ts, "outcome": "unsupported_format", "detail": "no text"})
                continue
            try:
                signal = self.parse(text, analyst=analyst or message.get("user"))
            except SignalValidationError as exc:
                skipped.append({"ts": ts, "outcome": "no_match", "detail": str(exc)})
                continue
            signal.channel_id = self.channel_id
            signal.message_id = ts
            signal.parser_version = PARSER_VERSION
            signal.import_batch = batch_label
            if self.save_historical_signal is not None:
                self.save_historical_signal(signal)
            imported.append({"id": signal.id, "ts": ts, "symbol": signal.symbol})
        return {"batch_label": batch_label, "imported": imported, "skipped": skipped}

    # -- Lifecycle ----------------------------------------------------------

    async def start(self) -> None:
        try:
            from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler
            from slack_bolt.async_app import AsyncApp
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("slack-bolt is not installed; run `pip install slack-bolt`") from exc

        if not self.user_token or not self.app_token:
            self._set_health(CollectorHealth.MISSING_CREDENTIALS, detail="user_token/app_token not fully configured")
            raise RuntimeError(
                "SlackUserSource requires user_token and app_token -- see docs/security/SLACK_USER_TOKEN.md"
            )

        # The AsyncApp's own WebClient is constructed with the USER token
        # (xoxp-...), not a bot token -- every Web API call this app makes
        # (including the conversations.history call `import_history`'s
        # real caller uses) is made AS the authenticated user, not a bot.
        app = AsyncApp(token=self.user_token)

        try:
            auth_test = await app.client.auth_test()
            identity = auth_test.get("user_id") or auth_test.get("user")
        except Exception as exc:  # pragma: no cover - real network/API errors, shape varies
            self._set_health(CollectorHealth.NO_CHANNEL_ACCESS, detail=str(exc))
            raise

        try:
            await app.client.conversations_info(channel=self.channel_id)
        except Exception as exc:  # pragma: no cover - real network/API errors, shape varies
            self._set_health(CollectorHealth.NO_CHANNEL_ACCESS, detail=str(exc))
            raise

        logger.info("slack_user collector_id=%s authenticated as user_id=%s", self.collector_id, identity)

        @app.event("message")
        async def handle_message(event: dict, **kwargs) -> None:
            await self.handle_event(event)

        self._handler = AsyncSocketModeHandler(app, self.app_token)
        asyncio.create_task(self._handler.start_async())

    async def stop(self) -> None:
        if self._handler is not None:
            await self._handler.close_async()
