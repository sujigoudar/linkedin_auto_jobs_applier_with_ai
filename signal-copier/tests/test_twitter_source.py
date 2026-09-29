"""Tests for TwitterSource.parse_tweet -- the analyst-derivation (from
`tweet.author_id`) and SignalValidationError handling that used to live
only inside `_Stream.on_tweet`, a method of a tweepy-`StreamingClient`
subclass created dynamically inside `start()` (unreachable without a real
tweepy connection). Extracted to a real method (`parse_tweet`)
specifically so it can be exercised directly with a lightweight fake
tweet object, as done here."""
import pytest

from app.models import Side
from app.sources.twitter import TwitterSource


class _FakeTweet:
    def __init__(self, text, author_id=None):
        self.text = text
        self.author_id = author_id


def _source():
    received = []

    async def on_signal(signal):
        received.append(signal)

    src = TwitterSource(on_signal, bearer_token="x")
    return src, received


def test_valid_tweet_with_author_id_returns_signal_with_analyst():
    src, _ = _source()
    tweet = _FakeTweet("BUY BTCUSDT", author_id=9876543210)

    signal = src.parse_tweet(tweet)

    assert signal is not None
    assert signal.symbol == "BTCUSDT"
    assert signal.side == Side.BUY
    # author_id is a numeric account ID (not a @handle), stringified.
    assert signal.analyst == "9876543210"


def test_tweet_without_author_id_leaves_analyst_none():
    src, _ = _source()
    tweet = _FakeTweet("BUY BTCUSDT", author_id=None)

    signal = src.parse_tweet(tweet)

    assert signal is not None
    assert signal.analyst is None


def test_unparseable_tweet_text_returns_none_not_raises():
    """SignalValidationError must be caught and turned into a `None`
    return (logged, not raised) -- mirrors the original inline
    `on_tweet` handler, which never let a parse failure propagate and
    crash the tweepy stream thread."""
    src, _ = _source()
    tweet = _FakeTweet("just tweeting about my day", author_id=123)

    signal = src.parse_tweet(tweet)

    assert signal is None


@pytest.mark.asyncio
async def test_start_wires_stream_dispatch_to_parse_tweet(monkeypatch):
    """Confirms the SDK registration site (`_Stream.on_tweet` built inside
    `start()`) still delegates to `parse_tweet` and dispatches through
    `on_signal` exactly as before -- i.e. the refactor didn't change
    externally observable wiring, only where the logic lives."""
    tweepy = pytest.importorskip("tweepy")

    received = []

    async def on_signal(signal):
        received.append(signal)

    src = TwitterSource(on_signal, bearer_token="x")

    captured = {}

    class _FakeStreamingClient:
        def __init__(self, bearer_token):
            captured["instance"] = self

        def filter(self, **kwargs):
            pass

        def disconnect(self):
            pass

    monkeypatch.setattr(tweepy, "StreamingClient", _FakeStreamingClient)

    await src.start()

    stream = captured["instance"]
    # The dynamically-created _Stream subclass's on_tweet is a plain sync
    # method; call it directly with a fake tweet and let it schedule the
    # coroutine onto the running loop, then let the loop run it.
    stream.on_tweet(_FakeTweet("BUY ETHUSDT", author_id=42))
    import asyncio
    await asyncio.sleep(0.05)

    assert len(received) == 1
    assert received[0].symbol == "ETHUSDT"
    assert received[0].analyst == "42"

    await src.stop()
