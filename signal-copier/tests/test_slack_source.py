"""Tests for SlackSource.handle_event -- the channel authorization check,
subtype-event filtering, analyst derivation, and SignalValidationError
handling that used to live only inside the `@app.event("message")`-
registered closure nested in `start()` (unreachable without a real
slack-bolt `AsyncApp`/Socket Mode connection). Extracted to a real method
(`handle_event`) specifically so it can be exercised directly with plain
dicts, as done here."""
import pytest

from app.models import Side
from app.sources.slack import SlackSource


def _source():
    received = []

    async def on_signal(signal):
        received.append(signal)

    src = SlackSource(on_signal, bot_token="x", app_token="y", channel_id="C123")
    return src, received


@pytest.mark.asyncio
async def test_authorized_channel_with_valid_signal_is_dispatched():
    src, received = _source()
    event = {"channel": "C123", "text": "BUY BTCUSDT", "user": "U123ABC"}

    await src.handle_event(event)

    assert len(received) == 1
    assert received[0].symbol == "BTCUSDT"
    assert received[0].side == Side.BUY
    assert received[0].analyst == "U123ABC"


@pytest.mark.asyncio
async def test_unauthorized_channel_is_rejected():
    src, received = _source()
    event = {"channel": "C999", "text": "BUY BTCUSDT", "user": "U123ABC"}

    await src.handle_event(event)

    assert received == []


@pytest.mark.asyncio
async def test_events_with_a_subtype_are_ignored():
    """Message edits, deletions, channel_join, bot_message, etc. all carry
    a `subtype` and must never be parsed as a fresh incoming signal, even
    when their (stale/echoed) text would otherwise parse cleanly."""
    src, received = _source()
    event = {"channel": "C123", "text": "BUY BTCUSDT", "user": "U123ABC", "subtype": "message_changed"}

    await src.handle_event(event)

    assert received == []


@pytest.mark.asyncio
async def test_malformed_text_in_authorized_channel_is_dropped_not_raised():
    src, received = _source()
    event = {"channel": "C123", "text": "just chatting, nothing to trade here", "user": "U123ABC"}

    await src.handle_event(event)

    assert received == []


@pytest.mark.asyncio
async def test_missing_text_field_defaults_to_empty_and_is_dropped():
    src, received = _source()
    event = {"channel": "C123", "user": "U123ABC"}

    await src.handle_event(event)

    assert received == []


@pytest.mark.asyncio
async def test_missing_user_field_leaves_analyst_none():
    src, received = _source()
    event = {"channel": "C123", "text": "BUY BTCUSDT"}

    await src.handle_event(event)

    assert len(received) == 1
    assert received[0].analyst is None
