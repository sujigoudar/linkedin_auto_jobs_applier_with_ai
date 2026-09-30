"""Tests for TelegramSource.handle_update -- the chat_id authorization
check, analyst-name derivation, and SignalValidationError handling that
used to live only inside a closure nested in `start()` (unreachable
without a real python-telegram-bot `Application`). Extracted to a real
method (`handle_update`) specifically so it can be exercised directly with
lightweight fake `update` objects, as done here."""
from datetime import datetime, timezone

import pytest

from app.models import SourceEventKind, Side
from app.sources.telegram import TelegramSource


class _FakeUser:
    def __init__(self, username=None, full_name=None):
        self.username = username
        self.full_name = full_name


class _FakeChat:
    def __init__(self, chat_id):
        self.id = chat_id


class _FakeMessage:
    def __init__(self, text, message_id=None, date=None, edit_date=None):
        self.text = text
        self.message_id = message_id
        self.date = date
        self.edit_date = edit_date


class _FakeUpdate:
    def __init__(self, chat_id, text, user=None, message_id=None, date=None, is_edit=False, edit_date=None):
        self.effective_chat = _FakeChat(chat_id)
        message = _FakeMessage(text, message_id=message_id, date=date, edit_date=edit_date)
        self.effective_message = message
        self.effective_user = user
        self.edited_message = message if is_edit else None


def _source():
    received = []

    async def on_signal(signal):
        received.append(signal)

    src = TelegramSource(on_signal, bot_token="x", chat_id=12345)
    return src, received


def _source_with_events():
    received = []
    events = []

    async def on_signal(signal):
        received.append(signal)

    async def on_source_event(event):
        events.append(event)

    src = TelegramSource(on_signal, bot_token="x", chat_id=12345, on_source_event=on_source_event)
    return src, received, events


@pytest.mark.asyncio
async def test_authorized_chat_with_valid_signal_is_dispatched():
    src, received = _source()
    update = _FakeUpdate(12345, "BUY BTCUSDT", user=_FakeUser(username="alice"))

    await src.handle_update(update)

    assert len(received) == 1
    assert received[0].symbol == "BTCUSDT"
    assert received[0].side == Side.BUY
    assert received[0].analyst == "alice"


@pytest.mark.asyncio
async def test_unauthorized_chat_is_rejected():
    """A message from a chat other than the configured `chat_id` must never
    reach `on_signal`, no matter how well-formed its text is."""
    src, received = _source()
    update = _FakeUpdate(99999, "BUY BTCUSDT", user=_FakeUser(username="alice"))

    await src.handle_update(update)

    assert received == []


@pytest.mark.asyncio
async def test_unauthorized_chat_id_as_string_vs_int_still_matches():
    """chat_id comparison is done via str(), so an int-configured chat_id
    still matches an update whose chat.id happens to be the same value."""
    src, received = _source()
    update = _FakeUpdate(12345, "BUY BTCUSDT")

    await src.handle_update(update)

    assert len(received) == 1


@pytest.mark.asyncio
async def test_malformed_text_in_authorized_chat_is_dropped_not_raised():
    src, received = _source()
    update = _FakeUpdate(12345, "just chatting, nothing to trade here", user=_FakeUser(username="alice"))

    # Must not raise -- SignalValidationError is caught and logged, matching
    # the original inline handler's behavior.
    await src.handle_update(update)

    assert received == []


@pytest.mark.asyncio
async def test_analyst_falls_back_to_full_name_when_no_username():
    src, received = _source()
    update = _FakeUpdate(12345, "BUY BTCUSDT", user=_FakeUser(username=None, full_name="Alice Trader"))

    await src.handle_update(update)

    assert len(received) == 1
    assert received[0].analyst == "Alice Trader"


@pytest.mark.asyncio
async def test_no_effective_user_leaves_analyst_none():
    src, received = _source()
    update = _FakeUpdate(12345, "BUY BTCUSDT", user=None)

    await src.handle_update(update)

    assert len(received) == 1
    assert received[0].analyst is None


@pytest.mark.asyncio
async def test_empty_message_text_is_dropped_not_raised():
    src, received = _source()
    update = _FakeUpdate(12345, None, user=_FakeUser(username="alice"))

    await src.handle_update(update)

    assert received == []


# -- Provider message identity / source ledger (edit vs original) ----------


@pytest.mark.asyncio
async def test_dispatched_signal_carries_channel_and_message_identity():
    src, received = _source()
    update = _FakeUpdate(12345, "BUY BTCUSDT", user=_FakeUser(username="alice"), message_id=42)

    await src.handle_update(update)

    assert received[0].channel_id == "12345"
    assert received[0].message_id == "42"
    assert received[0].parser_version == "telegram-text-parser-v1"
    assert received[0].revision_id is None
    assert received[0].original_message_id is None


@pytest.mark.asyncio
async def test_original_message_emits_source_event_kind_original():
    src, received, events = _source_with_events()
    now = datetime.now(timezone.utc)
    update = _FakeUpdate(12345, "BUY BTCUSDT", user=_FakeUser(username="alice"), message_id=42, date=now)

    await src.handle_update(update)

    assert len(events) == 1
    assert events[0].kind == SourceEventKind.ORIGINAL
    assert events[0].message_id == "42"
    assert events[0].provider_timestamp == now
    assert events[0].signal is received[0]


@pytest.mark.asyncio
async def test_edited_message_emits_source_event_kind_edit_with_revision_linkage():
    src, received, events = _source_with_events()
    edit_time = datetime.now(timezone.utc)
    update = _FakeUpdate(
        12345, "BUY BTCUSDT", user=_FakeUser(username="alice"), message_id=42, is_edit=True, edit_date=edit_time,
    )

    await src.handle_update(update)

    # An edit still dispatches its (revised) content to on_signal -- this
    # adapter does not silently drop a legitimate edited trade instruction.
    assert len(received) == 1
    assert received[0].message_id == "42"
    assert received[0].revision_id == f"42:{edit_time.isoformat()}"
    assert received[0].original_message_id == "42"

    assert len(events) == 1
    assert events[0].kind == SourceEventKind.EDIT
    assert events[0].original_message_id == "42"
    assert events[0].provider_timestamp == edit_time


@pytest.mark.asyncio
async def test_no_source_event_handler_wired_is_a_safe_no_op():
    """An adapter constructed without `on_source_event` (every existing
    caller before this task) must keep working exactly as before --
    `on_signal` still fires, nothing raises for the missing handler."""
    src, received = _source()
    update = _FakeUpdate(12345, "BUY BTCUSDT", user=_FakeUser(username="alice"), message_id=42)

    await src.handle_update(update)

    assert len(received) == 1
