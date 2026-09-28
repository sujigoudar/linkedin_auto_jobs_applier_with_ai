"""P0 (batch C): app/drawdown_governor.py's daily and peak-equity
drawdown circuit breakers -- wired into app/engine.py's real new-entry
admission path (PAUSE_NEW_ENTRIES / REQUIRE_REVIEW) and its real sizing
path (REDUCE_NEW_SIZE, via app/risk.py's size_for_account).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.brokers.paper import PaperBroker
from app.db import SignalStore
from app.drawdown_governor import (
    DrawdownAction,
    DrawdownGovernor,
    DrawdownGovernorConfig,
    DrawdownThresholds,
    compute_account_equity,
)
from app.engine import SignalCopierEngine
from app.lifecycle.manager import PositionLifecycleManager
from app.models import DestinationAccount, OrderResult, OrderStatus, Side, Signal
from app.routing import RoutingConfig, RoutingRule

SOURCE = "tradingview"


def _fill(store, account_id, symbol, side, quantity, price, when):
    signal = Signal(source=SOURCE, symbol=symbol, side=side)
    store.save_signal(signal)
    result = OrderResult(
        account_id=account_id, status=OrderStatus.FILLED, signal_id=signal.id, filled_quantity=quantity,
        filled_price=price, executed_at=when,
    )
    store.save_order_result(result, broker="paper", symbol=symbol, side=side, applied_quantity=quantity)
    return signal


def _engine(store, account, broker, *, drawdown_governor=None):
    routing = RoutingConfig(rules=[RoutingRule(source=SOURCE, destinations=["acct1"])], accounts={"acct1": account})
    lifecycle_manager = PositionLifecycleManager(brokers={"paper": broker}, store=store)
    return SignalCopierEngine(
        routing=routing, brokers={"paper": broker}, store=store, lifecycle_manager=lifecycle_manager,
        drawdown_governor=drawdown_governor,
    )


# --- compute_account_equity: reuses app/economics.py's own fields for both components ---


def test_equity_is_pure_realized_pnl_once_flat(tmp_path):
    store = SignalStore(tmp_path / "test.db")
    t0 = datetime.now(timezone.utc)
    _fill(store, "acct1", "AAPL", Side.BUY, 10.0, 100.0, t0)
    _fill(store, "acct1", "AAPL", Side.SELL, 10.0, 110.0, t0 + timedelta(minutes=1))

    assert compute_account_equity(store, "acct1") == pytest.approx(100.0)  # 10 * (110 - 100)


def test_equity_includes_unrealized_marked_at_last_fill_price_for_an_open_position(tmp_path):
    store = SignalStore(tmp_path / "test.db")
    t0 = datetime.now(timezone.utc)
    _fill(store, "acct1", "AAPL", Side.BUY, 10.0, 100.0, t0)  # opens long 10 @ avg cost 100
    _fill(store, "acct1", "AAPL", Side.BUY, 5.0, 110.0, t0 + timedelta(minutes=1))  # adds 5 @ 110

    # average_cost = (100*10 + 110*5) / 15 = 103.333..., last_fill_price = 110
    # unrealized = (110 - 103.333...) * 15 = 100.0, realized_pnl = 0 (nothing closed yet)
    assert compute_account_equity(store, "acct1") == pytest.approx(100.0)


def test_equity_with_no_history_at_all_is_zero(tmp_path):
    store = SignalStore(tmp_path / "test.db")
    assert compute_account_equity(store, "never-traded") == 0.0


# --- DrawdownThresholds.action_for: most-severe-threshold-first ---


def test_action_for_picks_the_most_severe_threshold_crossed():
    thresholds = DrawdownThresholds(warn=10.0, reduce_new_size=50.0, pause_new_entries=100.0, require_review=200.0)
    assert thresholds.action_for(5.0) is DrawdownAction.NONE
    assert thresholds.action_for(10.0) is DrawdownAction.WARN
    assert thresholds.action_for(60.0) is DrawdownAction.REDUCE_NEW_SIZE
    assert thresholds.action_for(150.0) is DrawdownAction.PAUSE_NEW_ENTRIES
    assert thresholds.action_for(500.0) is DrawdownAction.REQUIRE_REVIEW


# --- DrawdownGovernor.evaluate: both dimensions, real persisted baselines ---


def test_start_of_day_drawdown_is_measured_against_the_first_reading_of_the_day(tmp_path):
    store = SignalStore(tmp_path / "test.db")
    governor = DrawdownGovernor(store)
    t0 = datetime.now(timezone.utc)

    first_eval = governor.evaluate("acct1", now=t0)
    assert first_eval.equity == 0.0
    assert first_eval.start_of_day_equity == 0.0
    assert first_eval.start_of_day_drawdown == 0.0

    # A realized loss later the same day.
    _fill(store, "acct1", "AAPL", Side.BUY, 10.0, 100.0, t0 + timedelta(minutes=1))
    _fill(store, "acct1", "AAPL", Side.SELL, 10.0, 90.0, t0 + timedelta(minutes=2))  # -100 realized

    second_eval = governor.evaluate("acct1", now=t0 + timedelta(minutes=3))
    assert second_eval.equity == pytest.approx(-100.0)
    assert second_eval.start_of_day_equity == pytest.approx(0.0)  # locked from the first reading, never re-anchored
    assert second_eval.start_of_day_drawdown == pytest.approx(100.0)


def test_start_of_day_baseline_does_not_reset_on_a_later_lower_reading_the_same_day(tmp_path):
    store = SignalStore(tmp_path / "test.db")
    governor = DrawdownGovernor(store)
    t0 = datetime.now(timezone.utc)

    _fill(store, "acct1", "AAPL", Side.BUY, 10.0, 100.0, t0)
    _fill(store, "acct1", "AAPL", Side.SELL, 10.0, 120.0, t0 + timedelta(minutes=1))  # +200, first reading is +200
    governor.evaluate("acct1", now=t0 + timedelta(minutes=2))

    _fill(store, "acct1", "MSFT", Side.BUY, 10.0, 50.0, t0 + timedelta(minutes=3))
    _fill(store, "acct1", "MSFT", Side.SELL, 10.0, 40.0, t0 + timedelta(minutes=4))  # -100, running total +100

    later = governor.evaluate("acct1", now=t0 + timedelta(minutes=5))
    assert later.start_of_day_equity == pytest.approx(200.0)  # still the FIRST reading, not re-anchored to +100
    assert later.equity == pytest.approx(100.0)
    assert later.start_of_day_drawdown == pytest.approx(100.0)  # 200 - 100


def test_peak_equity_ratchets_up_and_never_down(tmp_path):
    store = SignalStore(tmp_path / "test.db")
    governor = DrawdownGovernor(store)
    t0 = datetime.now(timezone.utc)

    _fill(store, "acct1", "AAPL", Side.BUY, 10.0, 100.0, t0)
    _fill(store, "acct1", "AAPL", Side.SELL, 10.0, 150.0, t0 + timedelta(minutes=1))  # +500, new peak
    peak_eval = governor.evaluate("acct1", now=t0 + timedelta(minutes=2))
    assert peak_eval.peak_equity == pytest.approx(500.0)
    assert peak_eval.peak_equity_drawdown == 0.0

    _fill(store, "acct1", "MSFT", Side.BUY, 10.0, 50.0, t0 + timedelta(minutes=3))
    _fill(store, "acct1", "MSFT", Side.SELL, 10.0, 40.0, t0 + timedelta(minutes=4))  # -100, running total +400
    drawdown_eval = governor.evaluate("acct1", now=t0 + timedelta(minutes=5))
    assert drawdown_eval.equity == pytest.approx(400.0)
    assert drawdown_eval.peak_equity == pytest.approx(500.0)  # peak did NOT fall to 400
    assert drawdown_eval.peak_equity_drawdown == pytest.approx(100.0)


def test_peak_equity_persists_across_a_simulated_restart(tmp_path):
    db_path = tmp_path / "test.db"
    store1 = SignalStore(db_path)
    t0 = datetime.now(timezone.utc)
    _fill(store1, "acct1", "AAPL", Side.BUY, 10.0, 100.0, t0)
    _fill(store1, "acct1", "AAPL", Side.SELL, 10.0, 150.0, t0 + timedelta(minutes=1))
    DrawdownGovernor(store1).evaluate("acct1", now=t0 + timedelta(minutes=2))

    # Simulated restart: brand new SignalStore + brand new DrawdownGovernor
    # over the same on-disk file, no shared Python object.
    store2 = SignalStore(db_path)
    _fill(store2, "acct1", "MSFT", Side.BUY, 10.0, 50.0, t0 + timedelta(minutes=3))
    _fill(store2, "acct1", "MSFT", Side.SELL, 10.0, 40.0, t0 + timedelta(minutes=4))
    governor2 = DrawdownGovernor(store2)
    result = governor2.evaluate("acct1", now=t0 + timedelta(minutes=5))

    assert result.peak_equity == pytest.approx(500.0)  # remembered from before the "restart"
    assert result.equity == pytest.approx(400.0)
    assert result.peak_equity_drawdown == pytest.approx(100.0)


# --- WARN: no admission-control effect ---


@pytest.mark.asyncio
async def test_warn_action_never_blocks_or_resizes_a_new_entry(tmp_path):
    store = SignalStore(tmp_path / "test.db")
    broker = PaperBroker()
    account = DestinationAccount(account_id="acct1", broker="paper")
    t0 = datetime.now(timezone.utc)
    _fill(store, "acct1", "AAPL", Side.BUY, 10.0, 100.0, t0)
    _fill(store, "acct1", "AAPL", Side.SELL, 10.0, 90.0, t0 + timedelta(minutes=1))  # -100 realized today

    config = DrawdownGovernorConfig(start_of_day=DrawdownThresholds(warn=1.0))  # far below the 100 loss -> WARN
    governor = DrawdownGovernor(store, config)
    engine = _engine(store, account, broker, drawdown_governor=governor)

    signal = Signal(source=SOURCE, symbol="MSFT", side=Side.BUY, quantity=10.0, price=10.0)
    results = await engine.handle_signal(signal)
    assert results[0].status == OrderStatus.FILLED
    assert results[0].filled_quantity == 10.0  # untouched size


# --- REDUCE_NEW_SIZE: a real sizing-path effect, not just a logged intent ---


@pytest.mark.asyncio
async def test_reduce_new_size_actually_shrinks_the_real_order_quantity(tmp_path):
    store = SignalStore(tmp_path / "test.db")
    broker = PaperBroker()
    account = DestinationAccount(account_id="acct1", broker="paper")
    t0 = datetime.now(timezone.utc)

    config = DrawdownGovernorConfig(
        start_of_day=DrawdownThresholds(reduce_new_size=50.0, reduce_new_size_multiplier=0.25)
    )
    governor = DrawdownGovernor(store, config)
    governor.evaluate("acct1", now=t0)  # locks today's start-of-day baseline at equity=0

    _fill(store, "acct1", "AAPL", Side.BUY, 10.0, 100.0, t0 + timedelta(minutes=1))
    _fill(store, "acct1", "AAPL", Side.SELL, 10.0, 90.0, t0 + timedelta(minutes=2))  # -100 realized after the baseline

    engine = _engine(store, account, broker, drawdown_governor=governor)

    signal = Signal(source=SOURCE, symbol="MSFT", side=Side.BUY, quantity=10.0, price=10.0)
    results = await engine.handle_signal(signal)

    assert results[0].status == OrderStatus.FILLED
    assert results[0].filled_quantity == pytest.approx(2.5)  # 10 * 0.25, the REAL quantity sent to the broker
    assert broker.positions["acct1"]["MSFT"] == pytest.approx(2.5)  # the broker actually received the reduced size


# --- PAUSE_NEW_ENTRIES: genuinely blocks a real admission attempt end-to-end ---


@pytest.mark.asyncio
async def test_pause_new_entries_blocks_a_real_new_entry_through_the_engine(tmp_path):
    store = SignalStore(tmp_path / "test.db")
    broker = PaperBroker()
    account = DestinationAccount(account_id="acct1", broker="paper")
    t0 = datetime.now(timezone.utc)

    config = DrawdownGovernorConfig(start_of_day=DrawdownThresholds(pause_new_entries=100.0))
    governor = DrawdownGovernor(store, config)
    governor.evaluate("acct1", now=t0)  # locks today's start-of-day baseline at equity=0

    _fill(store, "acct1", "AAPL", Side.BUY, 10.0, 100.0, t0 + timedelta(minutes=1))
    _fill(store, "acct1", "AAPL", Side.SELL, 10.0, 80.0, t0 + timedelta(minutes=2))  # -200 realized after the baseline

    engine = _engine(store, account, broker, drawdown_governor=governor)

    signal = Signal(source=SOURCE, symbol="MSFT", side=Side.BUY, quantity=10.0, price=10.0)
    results = await engine.handle_signal(signal)

    assert results[0].status == OrderStatus.REJECTED
    assert "pause_new_entries" in results[0].message
    assert broker.positions.get("acct1", {}).get("MSFT", 0.0) == 0.0  # never reached the broker


@pytest.mark.asyncio
async def test_pause_new_entries_does_not_block_closing_an_existing_position(tmp_path):
    """Never touches existing risk -- only ever blocks a NEW admission.
    A CLOSE signal never even reaches the drawdown-governor admission
    check (see app/engine.py's `_handle_signal`: CLOSE is resolved before
    `_check_new_entry_admission` is ever called), so an account under
    PAUSE_NEW_ENTRIES can still flatten a position it already holds."""
    store = SignalStore(tmp_path / "test.db")
    broker = PaperBroker()
    account = DestinationAccount(account_id="acct1", broker="paper")
    t0 = datetime.now(timezone.utc)
    _fill(store, "acct1", "AAPL", Side.BUY, 10.0, 100.0, t0)
    _fill(store, "acct1", "AAPL", Side.SELL, 10.0, 80.0, t0 + timedelta(minutes=1))  # -200 realized today
    # An OPEN position this account still holds, tracked in `positions`.
    store.record_fill("acct1", "MSFT", Side.BUY, 5.0)

    config = DrawdownGovernorConfig(start_of_day=DrawdownThresholds(pause_new_entries=100.0))
    governor = DrawdownGovernor(store, config)
    engine = _engine(store, account, broker, drawdown_governor=governor)

    close_signal = Signal(source=SOURCE, symbol="MSFT", side=Side.CLOSE)
    results = await engine.handle_signal(close_signal)
    assert results[0].status == OrderStatus.FILLED  # closing existing risk is never blocked


# --- REQUIRE_REVIEW: sticky, never auto-clears, blocks entries end-to-end ---


@pytest.mark.asyncio
async def test_require_review_blocks_a_real_new_entry_through_the_engine(tmp_path):
    store = SignalStore(tmp_path / "test.db")
    broker = PaperBroker()
    account = DestinationAccount(account_id="acct1", broker="paper")
    t0 = datetime.now(timezone.utc)

    config = DrawdownGovernorConfig(start_of_day=DrawdownThresholds(require_review=200.0))
    governor = DrawdownGovernor(store, config)
    governor.evaluate("acct1", now=t0)  # locks today's start-of-day baseline at equity=0

    _fill(store, "acct1", "AAPL", Side.BUY, 10.0, 100.0, t0 + timedelta(minutes=1))
    _fill(store, "acct1", "AAPL", Side.SELL, 10.0, 50.0, t0 + timedelta(minutes=2))  # -500 realized after the baseline

    engine = _engine(store, account, broker, drawdown_governor=governor)

    signal = Signal(source=SOURCE, symbol="MSFT", side=Side.BUY, quantity=10.0, price=10.0)
    results = await engine.handle_signal(signal)

    assert results[0].status == OrderStatus.REJECTED
    assert "require_review" in results[0].message
    assert broker.positions.get("acct1", {}).get("MSFT", 0.0) == 0.0


def test_require_review_never_auto_clears_even_once_equity_fully_recovers(tmp_path):
    store = SignalStore(tmp_path / "test.db")
    t0 = datetime.now(timezone.utc)

    config = DrawdownGovernorConfig(start_of_day=DrawdownThresholds(require_review=200.0))
    governor = DrawdownGovernor(store, config)
    governor.evaluate("acct1", now=t0)  # locks today's start-of-day baseline at equity=0

    _fill(store, "acct1", "AAPL", Side.BUY, 10.0, 100.0, t0 + timedelta(minutes=1))
    _fill(store, "acct1", "AAPL", Side.SELL, 10.0, 50.0, t0 + timedelta(minutes=2))  # -500, crosses review

    triggered = governor.evaluate("acct1", now=t0 + timedelta(minutes=3))
    assert triggered.action is DrawdownAction.REQUIRE_REVIEW
    assert triggered.review_required is True

    # Equity fully recovers (a big realized win) -- drawdown is now well
    # under every configured threshold.
    _fill(store, "acct1", "MSFT", Side.BUY, 10.0, 100.0, t0 + timedelta(minutes=3))
    _fill(store, "acct1", "MSFT", Side.SELL, 10.0, 200.0, t0 + timedelta(minutes=4))  # +1000

    recovered = governor.evaluate("acct1", now=t0 + timedelta(minutes=5))
    assert recovered.start_of_day_drawdown == 0.0  # the raw drawdown number genuinely recovered
    assert recovered.action is DrawdownAction.REQUIRE_REVIEW  # but the sticky flag is still in effect
    assert recovered.review_required is True


def test_require_review_only_clears_via_the_explicit_clear_review_call(tmp_path):
    store = SignalStore(tmp_path / "test.db")
    t0 = datetime.now(timezone.utc)

    config = DrawdownGovernorConfig(start_of_day=DrawdownThresholds(require_review=200.0))
    governor = DrawdownGovernor(store, config)
    governor.evaluate("acct1", now=t0)  # locks today's start-of-day baseline at equity=0

    _fill(store, "acct1", "AAPL", Side.BUY, 10.0, 100.0, t0 + timedelta(minutes=1))
    _fill(store, "acct1", "AAPL", Side.SELL, 10.0, 50.0, t0 + timedelta(minutes=2))

    governor.evaluate("acct1", now=t0 + timedelta(minutes=3))
    assert governor.store.get_drawdown_review("acct1")["review_required"] is True

    governor.clear_review("acct1")
    assert governor.store.get_drawdown_review("acct1")["review_required"] is False

    after_clear = governor.evaluate("acct1", now=t0 + timedelta(minutes=4))
    # Drawdown is still above the review threshold, so a fresh crossing
    # sets it right back to True -- clear_review() lifted the PRIOR
    # sticky flag, it doesn't disable the threshold itself.
    assert after_clear.review_required is True


def test_require_review_state_persists_across_a_simulated_restart(tmp_path):
    db_path = tmp_path / "test.db"
    store1 = SignalStore(db_path)
    t0 = datetime.now(timezone.utc)
    config = DrawdownGovernorConfig(start_of_day=DrawdownThresholds(require_review=200.0))
    governor1 = DrawdownGovernor(store1, config)
    governor1.evaluate("acct1", now=t0)  # locks today's start-of-day baseline at equity=0

    _fill(store1, "acct1", "AAPL", Side.BUY, 10.0, 100.0, t0 + timedelta(minutes=1))
    _fill(store1, "acct1", "AAPL", Side.SELL, 10.0, 50.0, t0 + timedelta(minutes=2))
    governor1.evaluate("acct1", now=t0 + timedelta(minutes=3))

    # Simulated restart.
    store2 = SignalStore(db_path)
    governor2 = DrawdownGovernor(store2, config)
    result = governor2.evaluate("acct1", now=t0 + timedelta(minutes=4))
    assert result.review_required is True  # remembered from before the "restart", not reset


# --- Scope: account-level only (see app/drawdown_governor.py's own docstring) ---


def test_governor_is_scoped_per_account_independently(tmp_path):
    store = SignalStore(tmp_path / "test.db")
    t0 = datetime.now(timezone.utc)

    config = DrawdownGovernorConfig(start_of_day=DrawdownThresholds(require_review=200.0))
    governor = DrawdownGovernor(store, config)
    governor.evaluate("acct1", now=t0)  # locks acct1's baseline at equity=0

    _fill(store, "acct1", "AAPL", Side.BUY, 10.0, 100.0, t0 + timedelta(minutes=1))
    _fill(store, "acct1", "AAPL", Side.SELL, 10.0, 50.0, t0 + timedelta(minutes=2))  # acct1 down big

    acct1_eval = governor.evaluate("acct1", now=t0 + timedelta(minutes=3))
    acct2_eval = governor.evaluate("acct2", now=t0 + timedelta(minutes=3))  # never traded

    assert acct1_eval.action is DrawdownAction.REQUIRE_REVIEW
    assert acct2_eval.action is DrawdownAction.NONE
