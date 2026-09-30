"""Tests for SlackUserSource -- the OAuth user-token collector
(app/sources/slack_user.py). Mirrors tests/test_telegram_user_source.py's
own pattern (lightweight fake event dicts, no real Slack Socket Mode
connection needed) for the new/edited/deleted event handlers, plus the
checkpoint/health/qualification-evidence behavior this adapter adds."""
import pytest

from app.models import Side, SourceEventKind
from app.sources.slack_user import SlackUserSource
from app.collector_registry import CollectorHealth


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

    src = SlackUserSource(
        on_signal,
        collector_id="buyalerts",
        channel_id="C0123ABC",
        on_source_event=on_source_event,
        registry=registry,
    )
    return src, received, events


@pytest.mark.asyncio
async def test_new_message_with_text_dispatches_and_advances_checkpoint():
    registry = _FakeRegistry(checkpoint=None)
    src, received, events = _source(registry)
    event = {"channel": "C0123ABC", "user": "U1", "text": "BUY BTCUSDT", "ts": "1699999999.000100"}

    await src.handle_event(event)

    assert len(received) == 1
    assert received[0].symbol == "BTCUSDT"
    assert received[0].side == Side.BUY
    assert received[0].channel_id == "C0123ABC"
    assert received[0].message_id == "1699999999.000100"
    assert received[0].parser_version == "slack-user-text-parser-v1"
    assert registry.checkpoint_advances == ["1699999999.000100"]
    assert len(events) == 1
    assert events[0].kind == SourceEventKind.ORIGINAL


@pytest.mark.asyncio
async def test_event_for_a_different_channel_is_ignored():
    src, received, events = _source()
    event = {"channel": "C_OTHER", "user": "U1", "text": "BUY BTCUSDT", "ts": "1"}

    await src.handle_event(event)

    assert received == []
    assert events == []


@pytest.mark.asyncio
async def test_file_only_message_with_no_text_is_classified_unsupported_not_silently_dropped():
    registry = _FakeRegistry()
    src, received, events = _source(registry)
    event = {"channel": "C0123ABC", "user": "U1", "text": "", "ts": "7", "files": [{"id": "F1"}]}

    await src.handle_event(event)

    assert received == []
    assert registry.health_calls == [
        (CollectorHealth.UNSUPPORTED_FORMAT_ENCOUNTERED.value, "file-only message with no text (ts=7)")
    ]
    assert len(events) == 1
    assert events[0].kind == SourceEventKind.ORIGINAL
    assert "unsupported_format" in events[0].reason


@pytest.mark.asyncio
async def test_plain_non_signal_text_is_dropped_not_flagged_unsupported():
    registry = _FakeRegistry()
    src, received, events = _source(registry)
    event = {"channel": "C0123ABC", "user": "U1", "text": "gm everyone", "ts": "8"}

    await src.handle_event(event)

    assert received == []
    assert events == []
    assert registry.health_calls == []


@pytest.mark.asyncio
async def test_bot_message_subtype_is_skipped():
    src, received, events = _source()
    event = {"channel": "C0123ABC", "text": "BUY BTCUSDT", "ts": "9", "subtype": "bot_message"}

    await src.handle_event(event)

    assert received == []
    assert events == []


@pytest.mark.asyncio
async def test_message_at_or_below_checkpoint_is_not_readmitted_live():
    registry = _FakeRegistry(checkpoint="50.0")
    src, received, events = _source(registry)
    event = {"channel": "C0123ABC", "text": "BUY BTCUSDT", "ts": "50.0"}

    await src.handle_event(event)

    assert received == []
    assert events == []
    assert registry.checkpoint_advances == []


@pytest.mark.asyncio
async def test_message_above_checkpoint_is_admitted_live():
    registry = _FakeRegistry(checkpoint="50.0")
    src, received, _ = _source(registry)
    event = {"channel": "C0123ABC", "text": "BUY BTCUSDT", "ts": "51.0"}

    await src.handle_event(event)

    assert len(received) == 1
    assert registry.checkpoint_advances == ["51.0"]


@pytest.mark.asyncio
async def test_message_changed_emits_edit_kind_and_is_never_checkpoint_gated():
    registry = _FakeRegistry(checkpoint="100.0")
    src, received, events = _source(registry)
    event = {
        "channel": "C0123ABC",
        "subtype": "message_changed",
        "message": {
            "ts": "5.0",
            "user": "U1",
            "text": "BUY BTCUSDT",
            "edited": {"user": "U1", "ts": "5.5"},
        },
    }

    await src.handle_event(event)

    assert len(received) == 1
    assert received[0].message_id == "5.0"
    assert received[0].revision_id == "5.5"
    assert received[0].original_message_id == "5.0"
    assert len(events) == 1
    assert events[0].kind == SourceEventKind.EDIT
    # An edit below the checkpoint must still surface.
    assert registry.checkpoint_advances == []


@pytest.mark.asyncio
async def test_message_changed_with_no_text_is_flagged_unsupported():
    registry = _FakeRegistry()
    src, received, _ = _source(registry)
    event = {
        "channel": "C0123ABC",
        "subtype": "message_changed",
        "message": {"ts": "5.0", "text": "", "edited": {"ts": "5.5"}},
    }

    await src.handle_event(event)

    assert received == []
    assert registry.health_calls == [
        (CollectorHealth.UNSUPPORTED_FORMAT_ENCOUNTERED.value, "edited file-only message with no text (ts=5.0)")
    ]


@pytest.mark.asyncio
async def test_message_deleted_emits_delete_source_event():
    src, received, events = _source()
    event = {"channel": "C0123ABC", "subtype": "message_deleted", "deleted_ts": "10.0"}

    await src.handle_event(event)

    assert received == []
    assert len(events) == 1
    assert events[0].kind == SourceEventKind.DELETE
    assert events[0].message_id == "10.0"
    assert events[0].channel_id == "C0123ABC"


@pytest.mark.asyncio
async def test_message_deleted_with_no_deleted_ts_is_skipped_not_guessed():
    src, received, events = _source()
    event = {"channel": "C0123ABC", "subtype": "message_deleted"}

    await src.handle_event(event)

    assert events == []


@pytest.mark.asyncio
async def test_no_registry_wired_is_a_safe_no_op():
    src, received, _ = _source(registry=None)
    event = {"channel": "C0123ABC", "text": "BUY BTCUSDT", "ts": "1"}

    await src.handle_event(event)

    assert len(received) == 1


# -- Historical import -- read-only, never live-routed ----------------------


@pytest.mark.asyncio
async def test_import_history_never_calls_on_signal_and_tags_import_batch():
    saved = []
    received = []

    async def on_signal(signal):
        received.append(signal)

    src = SlackUserSource(
        on_signal,
        collector_id="buyalerts",
        channel_id="C0123ABC",
        save_historical_signal=saved.append,
    )
    messages = [
        {"ts": "1", "text": "BUY BTCUSDT", "user": "U1"},
        {"ts": "2", "text": "just chatting"},
        {"ts": "3", "text": "", "files": [{"id": "F1"}]},
    ]

    result = await src.import_history(messages, batch_label="slack-2024-history")

    assert received == []
    assert len(saved) == 1
    assert saved[0].import_batch == "slack-2024-history"
    assert saved[0].symbol == "BTCUSDT"
    assert len(result["imported"]) == 1
    assert len(result["skipped"]) == 2


@pytest.mark.asyncio
async def test_import_history_never_advances_live_checkpoint():
    registry = _FakeRegistry(checkpoint=None)
    saved = []

    async def on_signal(signal):
        pass

    src = SlackUserSource(
        on_signal,
        collector_id="buyalerts",
        channel_id="C0123ABC",
        registry=registry,
        save_historical_signal=saved.append,
    )
    await src.import_history([{"ts": "99", "text": "BUY BTCUSDT"}], batch_label="backfill")

    assert registry.checkpoint_advances == []
    assert registry.get_pull_collector_checkpoint("buyalerts") is None
