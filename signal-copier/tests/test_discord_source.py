"""Tests for DiscordSource.handle_message -- the self-message guard,
channel authorization check, analyst derivation, and
SignalValidationError handling that used to live only inside the
`@client.event`-registered `on_message` closure nested in `start()`
(unreachable without a real discord.py `Client` connection). Extracted to
a real method (`handle_message`) specifically so it can be exercised
directly with lightweight fake message objects, as done here."""
import pytest

from app.models import Side
from app.sources.discord import DiscordSource


class _FakeChannel:
    def __init__(self, channel_id):
        self.id = channel_id


class _FakeAuthor:
    def __init__(self, name):
        self._name = name

    def __str__(self):
        return self._name

    def __eq__(self, other):
        return self is other


class _FakeMessage:
    def __init__(self, content, channel_id, author):
        self.content = content
        self.channel = _FakeChannel(channel_id)
        self.author = author


def _source():
    received = []

    async def on_signal(signal):
        received.append(signal)

    src = DiscordSource(on_signal, bot_token="x", channel_id=555)
    return src, received


@pytest.mark.asyncio
async def test_authorized_channel_with_valid_signal_is_dispatched():
    src, received = _source()
    author = _FakeAuthor("Trader#1234")
    message = _FakeMessage("BUY BTCUSDT", channel_id=555, author=author)

    await src.handle_message(message, bot_user=_FakeAuthor("Bot#0001"))

    assert len(received) == 1
    assert received[0].symbol == "BTCUSDT"
    assert received[0].side == Side.BUY
    assert received[0].analyst == "Trader#1234"


@pytest.mark.asyncio
async def test_unauthorized_channel_is_rejected():
    src, received = _source()
    message = _FakeMessage("BUY BTCUSDT", channel_id=999, author=_FakeAuthor("Trader#1234"))

    await src.handle_message(message, bot_user=_FakeAuthor("Bot#0001"))

    assert received == []


@pytest.mark.asyncio
async def test_bot_own_message_is_ignored_to_prevent_self_loop():
    """If the bot's own message (e.g. an echo/relay) lands in the channel,
    it must never be re-parsed as an incoming signal."""
    src, received = _source()
    bot_user = _FakeAuthor("Bot#0001")
    message = _FakeMessage("BUY BTCUSDT", channel_id=555, author=bot_user)

    await src.handle_message(message, bot_user=bot_user)

    assert received == []


@pytest.mark.asyncio
async def test_malformed_content_in_authorized_channel_is_dropped_not_raised():
    src, received = _source()
    message = _FakeMessage(
        "just chatting, nothing to trade here", channel_id=555, author=_FakeAuthor("Trader#1234")
    )

    await src.handle_message(message, bot_user=_FakeAuthor("Bot#0001"))

    assert received == []


@pytest.mark.asyncio
async def test_no_bot_user_yet_still_authorizes_by_channel():
    """Before the client has finished logging in, `client.user` may be
    `None`; that must not itself cause a message to be rejected -- only a
    channel mismatch or a genuine self-message should."""
    src, received = _source()
    message = _FakeMessage("BUY BTCUSDT", channel_id=555, author=_FakeAuthor("Trader#1234"))

    await src.handle_message(message, bot_user=None)

    assert len(received) == 1
