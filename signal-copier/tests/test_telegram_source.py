"""Tests for TelegramSource.handle_update -- the chat_id authorization
check, analyst-name derivation, and SignalValidationError handling that
used to live only inside a closure nested in `start()` (unreachable
without a real python-telegram-bot `Application`). Extracted to a real
method (`handle_update`) specifically so it can be exercised directly with
lightweight fake `update` objects, as done here."""
import pytest

from app.models import Side
from app.sources.telegram import TelegramSource


class _FakeUser:
    def __init__(self, username=None, full_name=None):
        self.username = username
        self.full_name = full_name


class _FakeChat:
    def __init__(self, chat_id):
        self.id = chat_id


class _FakeMessage:
    def __init__(self, text):
        self.text = text


class _FakeUpdate:
    def __init__(self, chat_id, text, user=None):
        self.effective_chat = _FakeChat(chat_id)
        self.effective_message = _FakeMessage(text)
        self.effective_user = user


def _source():
    received = []

    async def on_signal(signal):
        received.append(signal)

    src = TelegramSource(on_signal, bot_token="x", chat_id=12345)
    return src, received


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
