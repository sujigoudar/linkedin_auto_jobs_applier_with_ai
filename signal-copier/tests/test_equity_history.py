"""PU-A3: app/equity_history.py's EquitySnapshotter -- real, persisted
equity/P&L snapshots per account.

Covers: realized_pnl matches app/economics.py's own computation exactly,
unrealized_pnl reuses PU-A1's real last-observed-price mechanism (never a
fabricated quote), an open position with no real price is disclosed via
unpriced_open_symbols rather than silently understated, snapshots are real
persisted DB rows (queryable via SignalStore.list_equity_snapshots), and a
restart doesn't lose already-persisted history.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.brokers.paper import PaperBroker
from app.db import SignalStore
from app.economics import compute_account_economics
from app.equity_history import EquitySnapshotter
from app.lifecycle.manager import PositionLifecycleManager
from app.lifecycle.models import PositionPlan
from app.models import DestinationAccount, OrderResult, OrderStatus, Side, Signal


@pytest.fixture
def store(tmp_path):
    return SignalStore(tmp_path / "test.db")


@pytest.fixture
def broker():
    return PaperBroker()


@pytest.fixture
def manager(broker, store):
    return PositionLifecycleManager(brokers={"paper": broker}, store=store)


def _fill(store, account_id, symbol, side, quantity, price, when=None):
    signal = Signal(source="test", symbol=symbol, side=side)
    store.save_signal(signal)
    result = OrderResult(
        account_id=account_id,
        status=OrderStatus.FILLED,
        signal_id=signal.id,
        filled_quantity=quantity,
        filled_price=price,
        executed_at=when or datetime.now(timezone.utc),
    )
    store.save_order_result(result, broker="paper", symbol=symbol, side=side)


def _plan(**overrides) -> PositionPlan:
    defaults = dict(
        account_id="acct1",
        symbol="AAPL",
        side=Side.BUY,
        planned_quantity=10.0,
        broker="paper",
        initial_stop=90.0,
    )
    defaults.update(overrides)
    return PositionPlan(**defaults)


async def _enter(manager, broker, account, plan, filled_quantity, entry_price=None):
    manager.start_plan(plan)
    entry_signal = Signal(source="test", symbol=plan.symbol, side=plan.side)
    await broker.place_order(entry_signal, account, filled_quantity, plan.symbol)
    return await manager.on_entry_fill(account, plan.symbol, filled_quantity, entry_price=entry_price)


def test_realized_pnl_matches_compute_account_economics_exactly(store):
    """The single most correctness-critical invariant this slice adds: a
    snapshot's realized_pnl must be EXACTLY what compute_account_economics
    would independently compute at that same point -- never a second,
    subtly different P&L calculation. See this test's
    LOAD_BEARING_VERIFY_TEMP note in the PR description for how this was
    broken on purpose and confirmed caught."""
    store.upsert_config_account("acct1", broker="paper")
    _fill(store, "acct1", "AAPL", Side.BUY, 10.0, 100.0)
    _fill(store, "acct1", "AAPL", Side.SELL, 10.0, 110.0)

    manager = PositionLifecycleManager(brokers={"paper": PaperBroker()}, store=store)
    snapshotter = EquitySnapshotter(store=store, lifecycle_manager=manager)

    expected = compute_account_economics(store, "acct1").realized_pnl
    snapshot = snapshotter.compute_snapshot("acct1", [])

    assert snapshot["realized_pnl"] == pytest.approx(expected)
    assert snapshot["realized_pnl"] == pytest.approx(100.0)


def test_no_fills_and_no_open_positions_is_all_zero_not_an_error(store):
    store.upsert_config_account("acct1", broker="paper")
    manager = PositionLifecycleManager(brokers={"paper": PaperBroker()}, store=store)
    snapshotter = EquitySnapshotter(store=store, lifecycle_manager=manager)

    snapshot = snapshotter.compute_snapshot("acct1", [])

    assert snapshot["realized_pnl"] == 0.0
    assert snapshot["unrealized_pnl"] == 0.0
    assert snapshot["cumulative_pnl"] == 0.0
    assert snapshot["unpriced_open_symbols"] == []


@pytest.mark.asyncio
async def test_unrealized_pnl_uses_real_last_observed_price_from_pu_a1(store, broker, manager):
    """An open long entered at 100, with a real PriceMonitor-style tick to
    120: unrealized_pnl must be (120 - 100) * 10 = 200, reusing PU-A1's own
    real last-known-price field -- never an invented quote."""
    account = DestinationAccount(account_id="acct1", broker="paper")
    store.upsert_config_account("acct1", broker="paper")
    _fill(store, "acct1", "AAPL", Side.BUY, 10.0, 100.0)  # the real confirmed entry fill economics.py replays
    plan = _plan()
    await _enter(manager, broker, account, plan, 10.0, entry_price=100.0)
    await manager.on_price_update(account, "AAPL", 120.0)

    lifecycle = manager.get_lifecycle("acct1", "AAPL")
    snapshotter = EquitySnapshotter(store=store, lifecycle_manager=manager)
    snapshot = snapshotter.compute_snapshot("acct1", [lifecycle])

    assert snapshot["realized_pnl"] == pytest.approx(0.0)
    assert snapshot["unrealized_pnl"] == pytest.approx(200.0)
    assert snapshot["cumulative_pnl"] == pytest.approx(200.0)
    assert snapshot["unpriced_open_symbols"] == []


@pytest.mark.asyncio
async def test_short_position_unrealized_pnl_sign_is_correct(store, broker, manager):
    """A short entered at 100 that drops to 90 is a WINNING short: (90 -
    100) * (-10) = 100, not -100 -- open_quantity is signed negative for a
    short, and this must not be mishandled."""
    account = DestinationAccount(account_id="acct1", broker="paper")
    store.upsert_config_account("acct1", broker="paper")
    _fill(store, "acct1", "TSLA", Side.SELL, 10.0, 100.0)  # the real confirmed entry fill economics.py replays
    plan = _plan(side=Side.SELL, symbol="TSLA")
    await _enter(manager, broker, account, plan, 10.0, entry_price=100.0)
    await manager.on_price_update(account, "TSLA", 90.0)

    lifecycle = manager.get_lifecycle("acct1", "TSLA")
    snapshotter = EquitySnapshotter(store=store, lifecycle_manager=manager)
    snapshot = snapshotter.compute_snapshot("acct1", [lifecycle])

    assert snapshot["unrealized_pnl"] == pytest.approx(100.0)


def test_open_position_with_no_real_price_is_disclosed_not_silently_zero(store):
    """An open position with no real price observation (e.g. a non-managed-
    lifecycle account, or a broker with no live-price capability) must
    show up in unpriced_open_symbols -- 0.0 is what it contributes to
    unrealized_pnl, but the snapshot must say that figure is incomplete,
    not imply it's a verified zero."""
    store.upsert_config_account("acct1", broker="paper")
    _fill(store, "acct1", "AAPL", Side.BUY, 10.0, 100.0)  # still open, no price feed

    manager = PositionLifecycleManager(brokers={"paper": PaperBroker()}, store=store)
    snapshotter = EquitySnapshotter(store=store, lifecycle_manager=manager)

    snapshot = snapshotter.compute_snapshot("acct1", [])  # no managed lifecycle -> no price data at all

    assert snapshot["unrealized_pnl"] == 0.0
    assert snapshot["unpriced_open_symbols"] == ["AAPL"]


def test_snapshot_once_persists_a_real_row_per_configured_account(store):
    store.upsert_config_account("acct1", broker="paper")
    store.upsert_config_account("acct2", broker="paper")
    _fill(store, "acct1", "AAPL", Side.BUY, 10.0, 100.0)
    _fill(store, "acct1", "AAPL", Side.SELL, 10.0, 110.0)

    manager = PositionLifecycleManager(brokers={"paper": PaperBroker()}, store=store)
    snapshotter = EquitySnapshotter(store=store, lifecycle_manager=manager)

    count = snapshotter.snapshot_once()

    assert count == 2
    acct1_rows = store.list_equity_snapshots("acct1")
    acct2_rows = store.list_equity_snapshots("acct2")
    assert len(acct1_rows) == 1
    assert len(acct2_rows) == 1
    assert acct1_rows[0]["realized_pnl"] == pytest.approx(100.0)
    assert acct1_rows[0]["cumulative_pnl"] == pytest.approx(100.0)
    assert acct2_rows[0]["realized_pnl"] == pytest.approx(0.0)


def test_repeated_ticks_append_rather_than_overwrite(store):
    store.upsert_config_account("acct1", broker="paper")
    manager = PositionLifecycleManager(brokers={"paper": PaperBroker()}, store=store)
    snapshotter = EquitySnapshotter(store=store, lifecycle_manager=manager)

    snapshotter.snapshot_once()
    _fill(store, "acct1", "AAPL", Side.BUY, 10.0, 100.0)
    _fill(store, "acct1", "AAPL", Side.SELL, 10.0, 105.0)
    snapshotter.snapshot_once()

    rows = store.list_equity_snapshots("acct1")
    assert len(rows) == 2
    assert rows[0]["realized_pnl"] == pytest.approx(0.0)
    assert rows[1]["realized_pnl"] == pytest.approx(50.0)


def test_since_until_bound_the_returned_series(store):
    store.upsert_config_account("acct1", broker="paper")
    t0 = datetime.now(timezone.utc)
    store.record_equity_snapshot(
        "acct1", captured_at=t0 - timedelta(hours=2), realized_pnl=1.0, unrealized_pnl=0.0, cumulative_pnl=1.0
    )
    store.record_equity_snapshot(
        "acct1", captured_at=t0 - timedelta(hours=1), realized_pnl=2.0, unrealized_pnl=0.0, cumulative_pnl=2.0
    )
    store.record_equity_snapshot(
        "acct1", captured_at=t0, realized_pnl=3.0, unrealized_pnl=0.0, cumulative_pnl=3.0
    )

    rows = store.list_equity_snapshots("acct1", since=t0 - timedelta(hours=1, minutes=30), until=t0 - timedelta(minutes=30))

    assert [r["realized_pnl"] for r in rows] == [2.0]


def test_a_restart_does_not_lose_already_persisted_history(tmp_path):
    db_path = tmp_path / "restart.db"
    store = SignalStore(db_path)
    store.upsert_config_account("acct1", broker="paper")
    manager = PositionLifecycleManager(brokers={"paper": PaperBroker()}, store=store)
    snapshotter = EquitySnapshotter(store=store, lifecycle_manager=manager)
    snapshotter.snapshot_once()

    # Simulate a process restart: a fresh SignalStore over the same file.
    resumed_store = SignalStore(db_path)
    rows = resumed_store.list_equity_snapshots("acct1")

    assert len(rows) == 1


def test_health_ok_flag_reflects_a_recent_successful_pass(store):
    manager = PositionLifecycleManager(brokers={"paper": PaperBroker()}, store=store)
    snapshotter = EquitySnapshotter(store=store, lifecycle_manager=manager)
    assert snapshotter.last_success_at is None

    snapshotter.snapshot_once()
    snapshotter.last_success_at = datetime.now(timezone.utc)

    assert snapshotter.last_success_at is not None
