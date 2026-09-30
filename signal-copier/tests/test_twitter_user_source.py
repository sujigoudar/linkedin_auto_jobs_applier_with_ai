"""Tests for TwitterUserSource -- the OAuth 2.0 user-context, polling
collector (app/sources/twitter_user.py). Mirrors
tests/test_telegram_user_source.py's own pattern (lightweight fake tweet/
response objects, no real tweepy Client/network connection needed) for
tweet/edit handling, the polling cycle, and checkpoint/health/
qualification-evidence behavior."""
import pytest

from app.models import Side, SourceEventKind
from app.sources.twitter_user import TwitterUserSource
from app.collector_registry import CollectorHealth


class _FakeTweet:
    def __init__(self, id, text="", author_id=None, edit_history_tweet_ids=None):
        self.id = id
        self.text = text
        self.author_id = author_id
        self.edit_history_tweet_ids = edit_history_tweet_ids


class _FakeResponse:
    def __init__(self, data):
        self.data = data


class _FakeClient:
    def __init__(self, responses):
        # list of _FakeResponse, popped one per get_users_tweets call
        self._responses = list(responses)
        self.calls = []

    def get_users_tweets(self, **kwargs):
        self.calls.append(kwargs)
        return self._responses.pop(0) if self._responses else _FakeResponse([])


class _FakeRegistry:
    def __init__(self, checkpoint=None):
        self.checkpoint = checkpoint
        self.health_calls = []
        self.qualification_calls = []
        self.checkpoint_advances = []

    def get_pull_collector_checkpoint(self, collector_id):
        return self.checkpoint

    def advance_pull_collector_checkpoint(self, collector_id, checkpoint):
        self.checkpoint = checkpoint
        self.checkpoint_advances.append(checkpoint)

    def update_pull_collector_health(self, collector_id, health_state, *, detail=None):
        self.health_calls.append((health_state, detail))

    def record_pull_collector_qualification_evidence(self, collector_id, *, evidence):
        self.qualification_calls.append(evidence)


def _source(registry=None):
    received = []
    events = []

    async def on_signal(signal):
        received.append(signal)

    async def on_source_event(event):
        events.append(event)

    src = TwitterUserSource(
        on_signal,
        collector_id="somehandle",
        target_user_id="99999",
        on_source_event=on_source_event,
        registry=registry,
    )
    return src, received, events


@pytest.mark.asyncio
async def test_handle_tweet_with_text_dispatches_and_advances_checkpoint():
    registry = _FakeRegistry(checkpoint=None)
    src, received, events = _source(registry)
    tweet = _FakeTweet(id="123", text="BUY BTCUSDT", author_id="99999", edit_history_tweet_ids=["123"])

    await src.handle_tweet(tweet)

    assert len(received) == 1
    assert received[0].symbol == "BTCUSDT"
    assert received[0].side == Side.BUY
    assert received[0].channel_id == "99999"
    assert received[0].message_id == "123"
    assert received[0].parser_version == "twitter-user-text-parser-v1"
    assert registry.checkpoint_advances == ["123"]
    assert len(events) == 1
    assert events[0].kind == SourceEventKind.ORIGINAL


@pytest.mark.asyncio
async def test_tweet_with_no_text_is_classified_unsupported_not_silently_dropped():
    registry = _FakeRegistry()
    src, received, events = _source(registry)
    tweet = _FakeTweet(id="7", text="")

    await src.handle_tweet(tweet)

    assert received == []
    assert registry.health_calls == [
        (CollectorHealth.UNSUPPORTED_FORMAT_ENCOUNTERED.value, "tweet with no text content (tweet_id=7)")
    ]
    assert len(events) == 1
    assert events[0].kind == SourceEventKind.ORIGINAL
    assert "unsupported_format" in events[0].reason


@pytest.mark.asyncio
async def test_plain_non_signal_text_is_dropped_not_flagged_unsupported():
    registry = _FakeRegistry()
    src, received, events = _source(registry)
    tweet = _FakeTweet(id="8", text="gm everyone")

    await src.handle_tweet(tweet)

    assert received == []
    assert events == []
    assert registry.health_calls == []


@pytest.mark.asyncio
async def test_edited_tweet_emits_edit_kind_with_original_message_id():
    """`edit_history_tweet_ids` with more than one entry, where the
    current tweet id is the LAST entry, is a later revision of the
    chain -- see this module's own docstring for the X API v2 field this
    is reasoned from."""
    registry = _FakeRegistry()
    src, received, events = _source(registry)
    tweet = _FakeTweet(id="124", text="BUY BTCUSDT", edit_history_tweet_ids=["123", "124"])

    await src.handle_tweet(tweet)

    assert len(received) == 1
    assert received[0].message_id == "124"
    assert received[0].revision_id == "124"
    assert received[0].original_message_id == "123"
    assert events[0].kind == SourceEventKind.EDIT


@pytest.mark.asyncio
async def test_dict_shaped_fake_tweet_is_also_supported():
    """`_tweet_field` must work against a plain dict, not just an
    attribute-bearing object -- both are realistic test/fake shapes."""
    registry = _FakeRegistry()
    src, received, _ = _source(registry)
    tweet = {"id": "1", "text": "BUY ETHUSDT", "author_id": "55"}

    await src.handle_tweet(tweet)

    assert len(received) == 1
    assert received[0].symbol == "ETHUSDT"
    assert received[0].analyst == "55"


@pytest.mark.asyncio
async def test_poll_once_processes_oldest_first_and_advances_checkpoint_per_tweet():
    registry = _FakeRegistry(checkpoint=None)
    src, received, _ = _source(registry)
    # X returns newest-first.
    client = _FakeClient([_FakeResponse([
        _FakeTweet(id="3", text="SELL BTCUSDT"),
        _FakeTweet(id="2", text="BUY ETHUSDT"),
        _FakeTweet(id="1", text="BUY BTCUSDT"),
    ])])

    count = await src.poll_once(client)

    assert count == 3
    assert [s.message_id for s in received] == ["1", "2", "3"]
    assert registry.checkpoint_advances == ["1", "2", "3"]
    assert client.calls[0]["id"] == "99999"
    assert "since_id" not in client.calls[0]


@pytest.mark.asyncio
async def test_poll_once_passes_since_id_from_checkpoint():
    registry = _FakeRegistry(checkpoint="5")
    src, received, _ = _source(registry)
    client = _FakeClient([_FakeResponse([])])

    await src.poll_once(client)

    assert client.calls[0]["since_id"] == "5"


@pytest.mark.asyncio
async def test_poll_once_with_no_new_tweets_returns_zero_and_dispatches_nothing():
    registry = _FakeRegistry(checkpoint="5")
    src, received, _ = _source(registry)
    client = _FakeClient([_FakeResponse(None)])

    count = await src.poll_once(client)

    assert count == 0
    assert received == []


@pytest.mark.asyncio
async def test_no_registry_wired_is_a_safe_no_op():
    src, received, _ = _source(registry=None)
    tweet = _FakeTweet(id="1", text="BUY BTCUSDT")

    await src.handle_tweet(tweet)

    assert len(received) == 1


# -- Historical import -- read-only, never live-routed ----------------------


@pytest.mark.asyncio
async def test_import_history_never_calls_on_signal_and_tags_import_batch():
    saved = []
    received = []

    async def on_signal(signal):
        received.append(signal)

    src = TwitterUserSource(
        on_signal,
        collector_id="somehandle",
        target_user_id="99999",
        save_historical_signal=saved.append,
    )
    tweets = [
        _FakeTweet(id="1", text="BUY BTCUSDT"),
        _FakeTweet(id="2", text="just tweeting"),
        _FakeTweet(id="3", text=""),
    ]

    result = await src.import_history(tweets, batch_label="twitter-2024-history")

    assert received == []
    assert len(saved) == 1
    assert saved[0].import_batch == "twitter-2024-history"
    assert saved[0].symbol == "BTCUSDT"
    assert len(result["imported"]) == 1
    assert len(result["skipped"]) == 2


@pytest.mark.asyncio
async def test_import_history_never_advances_live_checkpoint():
    registry = _FakeRegistry(checkpoint=None)
    saved = []

    async def on_signal(signal):
        pass

    src = TwitterUserSource(
        on_signal,
        collector_id="somehandle",
        target_user_id="99999",
        registry=registry,
        save_historical_signal=saved.append,
    )
    await src.import_history([_FakeTweet(id="99", text="BUY BTCUSDT")], batch_label="backfill")

    assert registry.checkpoint_advances == []
    assert registry.get_pull_collector_checkpoint("somehandle") is None
