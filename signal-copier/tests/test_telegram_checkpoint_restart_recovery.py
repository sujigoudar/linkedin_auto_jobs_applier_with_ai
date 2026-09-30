"""Track 5, point 7: checkpoint-based restart recovery, and historical
import staying structurally out of the live-routing path -- end to end
through TelegramUserSource + SignalStore's real persisted checkpoint (not
the lightweight in-memory fake registry used in
test_telegram_user_source.py)."""
from pathlib import Path

import pytest

from app.db import SignalStore
from app.sources.telegram_user import TelegramUserSource


@pytest.fixture
def store(tmp_path: Path) -> SignalStore:
    s = SignalStore(tmp_path / "test.db")
    s.register_telegram_collector(
        collector_id="buyalerts", connection_mode="user_account", identity_ref="+1",
        credential_env_var="TELEGRAM_USER_BUYALERTS_SESSION_PATH", chat_id="-100123", provider_name="buyalerts",
    )
    return s


class _FakeMessage:
    def __init__(self, id, message=""):
        self.id = id
        self.message = message
        self.media = None
        self.date = None
        self.edit_date = None


class _FakeEvent:
    def __init__(self, chat_id, message):
        self.chat_id = chat_id
        self.message = message


def _source(store):
    received = []

    async def on_signal(signal):
        received.append(signal)

    src = TelegramUserSource(
        on_signal,
        collector_id="buyalerts",
        chat_id=-100123,
        registry=store,
        save_historical_signal=store.save_signal,
    )
    return src, received


@pytest.mark.asyncio
async def test_process_restart_does_not_readmit_already_caught_up_messages_as_live(store):
    """Simulates: process handles message 10 live (checkpoint -> 10),
    then "restarts" (a brand new TelegramUserSource instance, same
    persisted registry) and MTProto redelivers messages up to and
    including 10 again on reconnect -- those must not be re-admitted,
    while a genuinely new message above the checkpoint still is."""
    src, received = _source(store)
    await src.handle_new_message_event(_FakeEvent(-100123, _FakeMessage(10, "BUY BTCUSDT")))
    assert len(received) == 1
    assert store.get_telegram_collector_checkpoint("buyalerts") == 10

    # "Restart": fresh adapter instance, same persisted store -- exactly
    # what app/main.py's own wiring reconstructs on every process boot.
    restarted_src, restarted_received = _source(store)
    await restarted_src.handle_new_message_event(_FakeEvent(-100123, _FakeMessage(10, "BUY BTCUSDT")))  # redelivered
    await restarted_src.handle_new_message_event(_FakeEvent(-100123, _FakeMessage(11, "SELL ETHUSDT")))  # genuinely new

    assert len(restarted_received) == 1  # only the genuinely new one
    assert restarted_received[0].symbol == "ETHUSDT"
    assert store.get_telegram_collector_checkpoint("buyalerts") == 11


@pytest.mark.asyncio
async def test_first_ever_live_message_on_a_freshly_authorized_collector_is_admitted(store):
    """A collector's very first live message (checkpoint is None) is
    genuinely new activity, not backlog -- it IS admitted, unlike an
    explicit historical import (see the tests below)."""
    assert store.get_telegram_collector_checkpoint("buyalerts") is None
    src, received = _source(store)

    await src.handle_new_message_event(_FakeEvent(-100123, _FakeMessage(1, "BUY BTCUSDT")))

    assert len(received) == 1
    assert store.get_telegram_collector_checkpoint("buyalerts") == 1


@pytest.mark.asyncio
async def test_historical_import_populates_source_ledger_but_never_advances_checkpoint_or_dispatches_live(store):
    """Point 7a: an explicit historical import (e.g. an operator
    backfilling a channel's past week for research/backtesting right
    after authorizing a fresh collector) must never (1) call on_signal,
    (2) advance the live checkpoint at all."""
    src, received = _source(store)
    historical_messages = [
        _FakeMessage(100, "BUY BTCUSDT"),
        _FakeMessage(101, "SELL ETHUSDT"),
    ]

    result = await src.import_history(historical_messages, batch_label="telegram-2024-history")

    assert received == []  # never live-routed
    assert store.get_telegram_collector_checkpoint("buyalerts") is None  # never advanced
    assert len(result["imported"]) == 2

    # These rows exist as ordinary saved signals with import_batch set --
    # same as the B9/E02 import-signals HTTP workflow (app/main.py's
    # import_signals) already guarantees, via the SAME save_signal call.
    imported_ids = {row["id"] for row in result["imported"]}
    with store._connect() as conn:
        persisted = {
            r[0]
            for r in conn.execute(
                "SELECT id FROM signals WHERE import_batch = ?", ("telegram-2024-history",)
            ).fetchall()
        }
    assert persisted == imported_ids


@pytest.mark.asyncio
async def test_historical_import_followed_by_live_message_does_not_treat_backlog_as_a_checkpoint(store):
    """A historical import happening AFTER live messages have already
    started arriving (e.g. an operator backfills later) must not corrupt
    the live checkpoint that live processing already established."""
    src, received = _source(store)
    await src.handle_new_message_event(_FakeEvent(-100123, _FakeMessage(5, "BUY BTCUSDT")))
    assert store.get_telegram_collector_checkpoint("buyalerts") == 5

    # Backfilling much OLDER history (ids below the live checkpoint) must
    # not move the checkpoint backward, and must not dispatch anything.
    await src.import_history([_FakeMessage(1, "SELL ETHUSDT")], batch_label="old-history")

    assert store.get_telegram_collector_checkpoint("buyalerts") == 5
    assert len(received) == 1  # only ever the one live message
