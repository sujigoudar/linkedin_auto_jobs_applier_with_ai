"""Tests for TelegramUserSource -- the Telethon-based user-account
collector (app/sources/telegram_user.py). Mirrors
tests/test_telegram_source.py's own pattern (lightweight fake event
objects, no real Telethon client/network connection needed) for the
new/edited/deleted event handlers, plus the checkpoint/health/
qualification-evidence behavior this adapter adds on top."""
from datetime import datetime, timezone

import pytest

from app.models import Side, SourceEventKind
from app.sources.telegram_user import TelegramUserSource, extract_noforwards_flag
from app.telegram_collectors import CollectorHealth


class _FakeMessage:
    def __init__(self, id=None, message="", media=None, date=None, edit_date=None):
        self.id = id
        self.message = message
        self.media = media
        self.date = date
        self.edit_date = edit_date


class _FakeNewMessageEvent:
    def __init__(self, chat_id, message: _FakeMessage):
        self.chat_id = chat_id
        self.message = message


class _FakeDeletedEvent:
    def __init__(self, chat_id, deleted_ids):
        self.chat_id = chat_id
        self.deleted_ids = deleted_ids


class _FakeRegistry:
    """A minimal fake satisfying CollectorRegistryPort -- no real
    database, so these tests never depend on app.db.SignalStore."""

    def __init__(self, checkpoint=None):
        self.checkpoint = checkpoint
        self.health_calls = []
        self.qualification_calls = []
        self.checkpoint_advances = []

    def get_telegram_collector_checkpoint(self, collector_id):
        return self.checkpoint

    def advance_telegram_collector_checkpoint(self, collector_id, message_id):
        self.checkpoint = message_id
        self.checkpoint_advances.append(message_id)

    def update_telegram_collector_health(self, collector_id, health_state, *, detail=None):
        self.health_calls.append((health_state, detail))

    def record_telegram_collector_qualification_evidence(self, collector_id, *, evidence, noforwards=None, qualified_at=None):
        self.qualification_calls.append((evidence, noforwards))


def _source(registry=None):
    received = []
    events = []

    async def on_signal(signal):
        received.append(signal)

    async def on_source_event(event):
        events.append(event)

    src = TelegramUserSource(
        on_signal,
        collector_id="buyalerts",
        chat_id=-100123,
        on_source_event=on_source_event,
        registry=registry,
    )
    return src, received, events


@pytest.mark.asyncio
async def test_new_message_with_text_dispatches_and_advances_checkpoint():
    registry = _FakeRegistry(checkpoint=None)
    src, received, events = _source(registry)
    event = _FakeNewMessageEvent(-100123, _FakeMessage(id=42, message="BUY BTCUSDT"))

    await src.handle_new_message_event(event, analyst="alice")

    assert len(received) == 1
    assert received[0].symbol == "BTCUSDT"
    assert received[0].side == Side.BUY
    assert received[0].channel_id == "-100123"
    assert received[0].message_id == "42"
    assert received[0].parser_version == "telegram-user-text-parser-v1"
    assert registry.checkpoint_advances == [42]
    assert len(events) == 1
    assert events[0].kind == SourceEventKind.ORIGINAL


@pytest.mark.asyncio
async def test_new_message_with_caption_on_photo_is_parsed_same_as_plain_text():
    """Telethon's own data model uses the SAME `.message` field for a
    photo/document's caption -- so a captioned media message is already
    text-parseable exactly like a plain text message."""
    registry = _FakeRegistry()
    src, received, _ = _source(registry)
    event = _FakeNewMessageEvent(-100123, _FakeMessage(id=1, message="BUY ETHUSDT", media=object()))

    await src.handle_new_message_event(event)

    assert len(received) == 1
    assert received[0].symbol == "ETHUSDT"


@pytest.mark.asyncio
async def test_media_only_message_with_no_text_is_classified_unsupported_not_silently_dropped():
    registry = _FakeRegistry()
    src, received, events = _source(registry)
    event = _FakeNewMessageEvent(-100123, _FakeMessage(id=7, message="", media=object()))

    await src.handle_new_message_event(event)

    assert received == []
    assert registry.health_calls == [
        (CollectorHealth.UNSUPPORTED_FORMAT_ENCOUNTERED.value, "media-only message with no text/caption (message_id=7)")
    ]
    # A visible source-ledger row, not a silent drop.
    assert len(events) == 1
    assert events[0].kind == SourceEventKind.ORIGINAL
    assert "unsupported_format" in events[0].reason


@pytest.mark.asyncio
async def test_plain_non_signal_text_with_no_media_is_dropped_not_flagged_unsupported():
    """Ordinary chatter ("gm everyone") is NOT the same outcome as a
    media-only post -- only the latter is UNSUPPORTED_FORMAT."""
    registry = _FakeRegistry()
    src, received, events = _source(registry)
    event = _FakeNewMessageEvent(-100123, _FakeMessage(id=8, message="gm everyone"))

    await src.handle_new_message_event(event)

    assert received == []
    assert events == []
    assert registry.health_calls == []


@pytest.mark.asyncio
async def test_message_at_or_below_checkpoint_is_not_readmitted_live():
    registry = _FakeRegistry(checkpoint=50)
    src, received, events = _source(registry)
    event = _FakeNewMessageEvent(-100123, _FakeMessage(id=50, message="BUY BTCUSDT"))

    await src.handle_new_message_event(event)

    assert received == []
    assert events == []
    assert registry.checkpoint_advances == []


@pytest.mark.asyncio
async def test_message_above_checkpoint_is_admitted_live():
    registry = _FakeRegistry(checkpoint=50)
    src, received, _ = _source(registry)
    event = _FakeNewMessageEvent(-100123, _FakeMessage(id=51, message="BUY BTCUSDT"))

    await src.handle_new_message_event(event)

    assert len(received) == 1
    assert registry.checkpoint_advances == [51]


@pytest.mark.asyncio
async def test_edited_message_emits_edit_kind_and_is_never_checkpoint_gated():
    """An edit to a message below the checkpoint must still surface --
    the checkpoint only prevents an ORIGINAL from being re-admitted, per
    this module's own docstring."""
    registry = _FakeRegistry(checkpoint=100)
    src, received, events = _source(registry)
    edit_time = datetime.now(timezone.utc)
    event = _FakeNewMessageEvent(-100123, _FakeMessage(id=5, message="BUY BTCUSDT", edit_date=edit_time))

    await src.handle_edited_message_event(event, analyst="alice")

    assert len(received) == 1
    assert received[0].revision_id == f"5:{edit_time.isoformat()}"
    assert received[0].original_message_id == "5"
    assert len(events) == 1
    assert events[0].kind == SourceEventKind.EDIT
    assert events[0].provider_timestamp == edit_time


@pytest.mark.asyncio
async def test_edited_media_only_message_is_flagged_unsupported():
    registry = _FakeRegistry()
    src, received, _ = _source(registry)
    event = _FakeNewMessageEvent(-100123, _FakeMessage(id=5, message="", media=object()))

    await src.handle_edited_message_event(event)

    assert received == []
    assert registry.health_calls == [
        (CollectorHealth.UNSUPPORTED_FORMAT_ENCOUNTERED.value, "edited media-only message with no text/caption (message_id=5)")
    ]


@pytest.mark.asyncio
async def test_deleted_event_in_a_channel_emits_delete_source_events():
    """A group/channel/supergroup delete DOES carry chat_id -- see this
    module's own docstring for the private-chat limitation this is
    distinguishing from."""
    src, received, events = _source()
    event = _FakeDeletedEvent(chat_id=-100123, deleted_ids=[10, 11])

    await src.handle_deleted_event(event)

    assert len(events) == 2
    assert {e.message_id for e in events} == {"10", "11"}
    assert all(e.kind == SourceEventKind.DELETE for e in events)
    assert all(e.channel_id == "-100123" for e in events)


@pytest.mark.asyncio
async def test_deleted_event_with_no_chat_id_is_skipped_not_guessed():
    """The documented MTProto limitation: a private one-to-one chat's
    delete update carries no peer/chat id at all. This adapter must skip
    it honestly rather than attributing it to some guessed channel."""
    src, received, events = _source()
    event = _FakeDeletedEvent(chat_id=None, deleted_ids=[1])

    await src.handle_deleted_event(event)

    assert events == []


def test_extract_noforwards_flag_reads_real_attribute_and_is_none_when_absent():
    class _Chan:
        noforwards = True

    class _User:
        pass

    assert extract_noforwards_flag(_Chan()) is True
    assert extract_noforwards_flag(_User()) is None


@pytest.mark.asyncio
async def test_record_chat_protection_flag_writes_qualification_evidence():
    registry = _FakeRegistry()
    src, _, _ = _source(registry)

    class _Chan:
        noforwards = True

    await src.record_chat_protection_flag(_Chan())

    assert registry.qualification_calls == [({"chat_noforwards_observed": True}, True)]


@pytest.mark.asyncio
async def test_no_registry_wired_is_a_safe_no_op_for_checkpoint_and_health():
    """An adapter constructed with no registry (e.g. a bare unit test)
    must still dispatch signals -- checkpoint/health simply aren't
    persisted, never a crash."""
    src, received, _ = _source(registry=None)
    event = _FakeNewMessageEvent(-100123, _FakeMessage(id=1, message="BUY BTCUSDT"))

    await src.handle_new_message_event(event)

    assert len(received) == 1


# -- Historical import (point 7a) -- read-only, never live-routed ----------


@pytest.mark.asyncio
async def test_import_history_never_calls_on_signal_and_tags_import_batch():
    saved = []
    received = []

    async def on_signal(signal):
        received.append(signal)

    src = TelegramUserSource(
        on_signal,
        collector_id="buyalerts",
        chat_id=-100123,
        save_historical_signal=saved.append,
    )
    messages = [
        _FakeMessage(id=1, message="BUY BTCUSDT"),
        _FakeMessage(id=2, message="just chatting"),
        _FakeMessage(id=3, media=object(), message=""),
    ]

    result = await src.import_history(messages, batch_label="telegram-2024-history")

    assert received == []  # NEVER live-routed
    assert len(saved) == 1
    assert saved[0].import_batch == "telegram-2024-history"
    assert saved[0].symbol == "BTCUSDT"
    assert len(result["imported"]) == 1
    assert len(result["skipped"]) == 2


@pytest.mark.asyncio
async def test_import_history_never_advances_live_checkpoint():
    registry = _FakeRegistry(checkpoint=None)
    saved = []

    async def on_signal(signal):
        pass

    src = TelegramUserSource(
        on_signal,
        collector_id="buyalerts",
        chat_id=-100123,
        registry=registry,
        save_historical_signal=saved.append,
    )
    messages = [_FakeMessage(id=99, message="BUY BTCUSDT")]

    await src.import_history(messages, batch_label="backfill")

    assert registry.checkpoint_advances == []
    assert registry.get_telegram_collector_checkpoint("buyalerts") is None
