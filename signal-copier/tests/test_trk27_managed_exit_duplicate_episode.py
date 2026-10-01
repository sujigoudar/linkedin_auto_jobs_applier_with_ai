"""TRK-27: managed-lifecycle exit idempotency has a real gap -- the command-
ledger idempotency_key built in `PositionLifecycleManager._submit_exit_order`
uses a freshly-generated `exit_signal.id` every call, and `CloseArbiter`'s own
`pending_exit` guard only protects against a CONCURRENT duplicate exit while
one is already in flight. A genuinely duplicate EXIT signal for the same
real-world event, with a DIFFERENT channel_id/message_id (e.g. two different
collectors, or a provider re-sending the same real-world exit through two
notification paths), arriving AFTER the first exit has already fully
resolved, is caught by neither mechanism nor by `_handle_signal`'s SIG-01
replay guard (keyed on `signal.id`, which genuinely differs for a new signal
row).

This reproduces the gap's safe resolution: a genuine duplicate exit request
for a position that's already fully flat, arriving soon after the first
exit's own resolution, is now recognized and logged as a duplicate (no new
broker order) rather than silently re-processed -- see
`PositionLifecycleManager.check_duplicate_exit`/`_ClosedExitRecord`'s own
docstrings in app/lifecycle/manager.py for the exact mechanism and its
explicitly documented scope.

Just as important: the negative case. A real re-entry after the first exit,
followed by a REAL second exit for that new position, must NOT be suppressed
by this guard -- see `test_a_real_second_exit_after_a_real_re_entry_is_not_suppressed`.
"""
from datetime import datetime, timedelta, timezone

import pytest

from app import config as app_config
from app.brokers.paper import PaperBroker
from app.db import SignalStore
from app.engine import SignalCopierEngine
from app.lifecycle.manager import PositionLifecycleManager
from app.lifecycle.models import PositionPlan
from app.models import DestinationAccount, OrderStatus, Side, Signal
from app.routing import RoutingConfig, RoutingRule


@pytest.fixture
def store(tmp_path):
    return SignalStore(tmp_path / "test.db")


def _engine(store, paper, account):
    routing = RoutingConfig(
        rules=[RoutingRule(source="tradingview", destinations=[account.account_id])],
        accounts={account.account_id: account},
    )
    lifecycle_manager = PositionLifecycleManager(brokers={"paper": paper}, store=store)
    engine = SignalCopierEngine(
        routing=routing, brokers={"paper": paper}, store=store, lifecycle_manager=lifecycle_manager
    )
    return engine, lifecycle_manager


@pytest.mark.asyncio
async def test_duplicate_exit_with_a_different_channel_and_message_id_is_not_resubmitted(store):
    """The concrete reproduction: two EXIT signals for the same managed
    position and the same real-world event, with DIFFERENT channel_id/
    message_id (so SIG-01's same-signal.id dedup in `_handle_signal` does
    NOT catch them), the first allowed to fully resolve before the second
    is submitted."""
    paper = PaperBroker()
    account = DestinationAccount(account_id="acct1", broker="paper", managed_lifecycle=True)
    engine, lifecycle_manager = _engine(store, paper, account)

    entry = Signal(
        source="tradingview",
        symbol="AAPL",
        side=Side.BUY,
        quantity=10.0,
        stop_loss=48.50,
        channel_id="chan-aapl",
        message_id="msg-entry",
    )
    entry_result = await engine.handle_signal(entry)
    assert entry_result[0].status == OrderStatus.FILLED

    close_one = Signal(
        source="tradingview",
        symbol="AAPL",
        side=Side.CLOSE,
        channel_id="chan-aapl",
        message_id="msg-exit-collector-a",
    )
    first_close = await engine.handle_signal(close_one)
    assert first_close[0].status == OrderStatus.FILLED
    assert first_close[0].filled_quantity == 10.0

    lifecycle = lifecycle_manager.get_lifecycle("acct1", "AAPL")
    assert lifecycle is not None
    assert lifecycle.closed is True
    assert store.get_position("acct1", "AAPL") == 0.0

    place_order_calls = []
    original_place_order = paper.place_order

    async def tracking_place_order(*args, **kwargs):
        place_order_calls.append(args)
        return await original_place_order(*args, **kwargs)

    paper.place_order = tracking_place_order

    # A genuinely duplicate real-world exit, delivered through a DIFFERENT
    # collector/transport -- different channel_id AND message_id, so this
    # is NOT the same `signals` row as `close_one` and SIG-01's replay
    # guard in `_handle_signal` cannot dedupe it by signal id.
    close_two = Signal(
        source="tradingview",
        symbol="AAPL",
        side=Side.CLOSE,
        channel_id="chan-aapl-collector-b",
        message_id="msg-exit-collector-b",
    )
    second_close = await engine.handle_signal(close_two)

    assert place_order_calls == []  # never resubmitted to the broker
    assert second_close[0].status == OrderStatus.REJECTED
    assert "duplicate exit recognized" in second_close[0].message
    # The position is not oversold/re-closed -- still flat, exactly as the
    # first exit left it.
    assert store.get_position("acct1", "AAPL") == 0.0
    assert paper.positions.get("acct1", {}).get("AAPL", 0.0) == 0.0


@pytest.mark.asyncio
async def test_a_real_second_exit_after_a_real_re_entry_is_not_suppressed(store):
    """The important negative case: once a NEW position has been opened
    (a real re-entry) for the same account/symbol, its own real exit --
    even one delivered through yet another different channel_id/message_id
    -- must be processed normally, not swallowed by the duplicate guard."""
    paper = PaperBroker()
    account = DestinationAccount(account_id="acct1", broker="paper", managed_lifecycle=True)
    engine, lifecycle_manager = _engine(store, paper, account)

    entry_one = Signal(
        source="tradingview",
        symbol="AAPL",
        side=Side.BUY,
        quantity=10.0,
        stop_loss=48.50,
        channel_id="chan-aapl",
        message_id="msg-entry-1",
    )
    await engine.handle_signal(entry_one)

    close_one = Signal(
        source="tradingview",
        symbol="AAPL",
        side=Side.CLOSE,
        channel_id="chan-aapl",
        message_id="msg-exit-1",
    )
    first_close = await engine.handle_signal(close_one)
    assert first_close[0].status == OrderStatus.FILLED
    assert lifecycle_manager.get_lifecycle("acct1", "AAPL").closed is True

    # A genuine re-entry -- a brand new episode for the same account/symbol.
    entry_two = Signal(
        source="tradingview",
        symbol="AAPL",
        side=Side.BUY,
        quantity=7.0,
        stop_loss=50.0,
        channel_id="chan-aapl",
        message_id="msg-entry-2",
    )
    second_entry_result = await engine.handle_signal(entry_two)
    assert second_entry_result[0].status == OrderStatus.FILLED
    lifecycle_after_reentry = lifecycle_manager.get_lifecycle("acct1", "AAPL")
    assert lifecycle_after_reentry.closed is False
    assert lifecycle_after_reentry.confirmed_owned_quantity == 7.0

    # The real exit for THIS new episode -- a different channel_id/message_id
    # again, same as the duplicate-exit test above, but this one must go
    # through: it is a genuinely new real-world event, not a replay.
    close_two = Signal(
        source="tradingview",
        symbol="AAPL",
        side=Side.CLOSE,
        channel_id="chan-aapl-collector-b",
        message_id="msg-exit-2",
    )
    second_close = await engine.handle_signal(close_two)

    assert second_close[0].status == OrderStatus.FILLED
    assert second_close[0].filled_quantity == 7.0
    assert "duplicate exit recognized" not in second_close[0].message
    lifecycle_after_second_close = lifecycle_manager.get_lifecycle("acct1", "AAPL")
    assert lifecycle_after_second_close.closed is True
    assert store.get_position("acct1", "AAPL") == 0.0


@pytest.mark.asyncio
async def test_duplicate_exit_window_expiry_falls_back_to_the_generic_rejection():
    """Unit-level check of `check_duplicate_exit`'s own boundary: once
    `config.MANAGED_EXIT_DUPLICATE_WINDOW_SECONDS` has elapsed since the
    first exit resolved, a second request for the same, still-flat position
    gets the plain "no active lifecycle for this position" rejection again
    -- this guard is explicitly a bounded window, not a permanent one."""
    paper = PaperBroker()
    manager = PositionLifecycleManager(brokers={"paper": paper})
    account = DestinationAccount(account_id="acct1", broker="paper")
    plan = PositionPlan(
        account_id="acct1",
        symbol="AAPL",
        side=Side.BUY,
        planned_quantity=5.0,
        broker="paper",
        initial_stop=48.50,
        entry_signal_id="entry-sig-1",
    )
    manager.start_plan(plan)
    entry_signal = Signal(source="test", symbol="AAPL", side=Side.BUY)
    await paper.place_order(entry_signal, account, 5.0, "AAPL")
    await manager.on_entry_fill(account, "AAPL", 5.0)

    result = await manager.request_exit(account, "AAPL", 5.0, source="provider_exit")
    assert result.status == OrderStatus.FILLED
    assert manager.get_lifecycle("acct1", "AAPL").closed is True

    # Still within the window: recognized as a duplicate.
    within_window = await manager.request_exit(account, "AAPL", 5.0, source="provider_exit")
    assert within_window.status == OrderStatus.REJECTED
    assert "duplicate exit recognized" in within_window.message

    # Force the recorded episode's `closed_at` outside the window, as if
    # real time had passed, and retry.
    record = manager._last_closed_exit[("acct1", "AAPL")]
    record.closed_at = datetime.now(timezone.utc) - timedelta(
        seconds=app_config.MANAGED_EXIT_DUPLICATE_WINDOW_SECONDS + 1
    )

    after_window = await manager.request_exit(account, "AAPL", 5.0, source="provider_exit")
    assert after_window.status == OrderStatus.REJECTED
    assert "duplicate exit recognized" not in after_window.message
    assert "no active lifecycle for this position" in after_window.message


@pytest.mark.asyncio
async def test_duplicate_exit_check_ignores_a_closed_at_recorded_in_the_future():
    """Track 44 mutation-testing gap: `check_duplicate_exit`'s own
    `age_seconds < 0` guard had no direct test -- a clock-skew or
    corrected-timestamp case (the recorded `closed_at` is, for whatever
    reason, slightly AFTER "now") must fall back to the generic rejection
    rather than being treated as a confidently-recognized duplicate with a
    nonsensical negative age."""
    paper = PaperBroker()
    manager = PositionLifecycleManager(brokers={"paper": paper})
    account = DestinationAccount(account_id="acct1", broker="paper")
    plan = PositionPlan(
        account_id="acct1",
        symbol="AAPL",
        side=Side.BUY,
        planned_quantity=5.0,
        broker="paper",
        initial_stop=48.50,
        entry_signal_id="entry-sig-1",
    )
    manager.start_plan(plan)
    entry_signal = Signal(source="test", symbol="AAPL", side=Side.BUY)
    await paper.place_order(entry_signal, account, 5.0, "AAPL")
    await manager.on_entry_fill(account, "AAPL", 5.0)
    result = await manager.request_exit(account, "AAPL", 5.0, source="provider_exit")
    assert result.status == OrderStatus.FILLED

    record = manager._last_closed_exit[("acct1", "AAPL")]
    record.closed_at = datetime.now(timezone.utc) + timedelta(seconds=30)

    assert manager.check_duplicate_exit(account, "AAPL") is None

    retried = await manager.request_exit(account, "AAPL", 5.0, source="provider_exit")
    assert retried.status == OrderStatus.REJECTED
    assert "duplicate exit recognized" not in retried.message
    assert "no active lifecycle for this position" in retried.message
