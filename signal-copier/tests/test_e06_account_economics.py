"""E06: app/economics.py computes realized P&L, cost basis and completed-
trade win rate by replaying an account's own confirmed executions."""
from datetime import datetime, timedelta, timezone

import pytest

from app.db import SignalStore
from app.economics import compute_account_economics
from app.models import OrderResult, OrderStatus, Side, Signal


@pytest.fixture
def store(tmp_path):
    return SignalStore(tmp_path / "test.db")


def _fill(store, account_id, symbol, side, quantity, price, when):
    signal = Signal(source="test", symbol=symbol, side=side)
    store.save_signal(signal)
    result = OrderResult(
        account_id=account_id,
        status=OrderStatus.FILLED,
        signal_id=signal.id,
        filled_quantity=quantity,
        filled_price=price,
        executed_at=when,
    )
    store.save_order_result(result, broker="paper", symbol=symbol, side=side)


def test_simple_round_trip_realizes_expected_pnl(store):
    t0 = datetime.now(timezone.utc)
    _fill(store, "acct1", "AAPL", Side.BUY, 10.0, 100.0, t0)
    _fill(store, "acct1", "AAPL", Side.SELL, 10.0, 110.0, t0 + timedelta(minutes=1))

    econ = compute_account_economics(store, "acct1")

    assert econ.realized_pnl == pytest.approx(100.0)  # 10 * (110 - 100)
    assert econ.per_symbol["AAPL"].open_quantity == 0.0
    assert econ.per_symbol["AAPL"].average_cost is None
    assert econ.per_symbol["AAPL"].closing_fills == 1
    assert econ.completed_trade_win_rate == 1.0


def test_losing_trade_is_not_counted_as_a_win(store):
    t0 = datetime.now(timezone.utc)
    _fill(store, "acct1", "AAPL", Side.BUY, 10.0, 100.0, t0)
    _fill(store, "acct1", "AAPL", Side.SELL, 10.0, 95.0, t0 + timedelta(minutes=1))

    econ = compute_account_economics(store, "acct1")

    assert econ.realized_pnl == pytest.approx(-50.0)
    assert econ.completed_trade_win_rate == 0.0


def test_partial_close_realizes_only_the_closed_portion(store):
    t0 = datetime.now(timezone.utc)
    _fill(store, "acct1", "AAPL", Side.BUY, 10.0, 100.0, t0)
    _fill(store, "acct1", "AAPL", Side.SELL, 4.0, 110.0, t0 + timedelta(minutes=1))

    econ = compute_account_economics(store, "acct1")
    se = econ.per_symbol["AAPL"]

    assert econ.realized_pnl == pytest.approx(40.0)  # 4 * (110 - 100)
    assert se.open_quantity == pytest.approx(6.0)
    assert se.average_cost == pytest.approx(100.0)  # unchanged for the remaining lot


def test_average_cost_updates_on_adding_to_the_same_side(store):
    t0 = datetime.now(timezone.utc)
    _fill(store, "acct1", "AAPL", Side.BUY, 10.0, 100.0, t0)
    _fill(store, "acct1", "AAPL", Side.BUY, 10.0, 120.0, t0 + timedelta(minutes=1))

    econ = compute_account_economics(store, "acct1")
    se = econ.per_symbol["AAPL"]

    assert se.open_quantity == pytest.approx(20.0)
    assert se.average_cost == pytest.approx(110.0)  # (10*100 + 10*120) / 20
    assert econ.realized_pnl == 0.0


def test_flip_through_flat_opens_fresh_position_at_flip_price(store):
    t0 = datetime.now(timezone.utc)
    _fill(store, "acct1", "AAPL", Side.BUY, 10.0, 100.0, t0)
    _fill(store, "acct1", "AAPL", Side.SELL, 15.0, 110.0, t0 + timedelta(minutes=1))

    econ = compute_account_economics(store, "acct1")
    se = econ.per_symbol["AAPL"]

    assert econ.realized_pnl == pytest.approx(100.0)  # 10 * (110 - 100), only the closed part
    assert se.open_quantity == pytest.approx(-5.0)  # flipped short 5
    assert se.average_cost == pytest.approx(110.0)  # the new short lot's own price
    assert se.closing_fills == 1


def test_short_side_realizes_pnl_in_the_correct_direction(store):
    t0 = datetime.now(timezone.utc)
    _fill(store, "acct1", "AAPL", Side.SELL, 10.0, 100.0, t0)  # open short
    _fill(store, "acct1", "AAPL", Side.BUY, 10.0, 90.0, t0 + timedelta(minutes=1))  # cover, profit

    econ = compute_account_economics(store, "acct1")

    assert econ.realized_pnl == pytest.approx(100.0)  # short profits when price falls
    assert econ.completed_trade_win_rate == 1.0


def test_unresolved_close_side_marks_symbol_incomplete_not_zero(store):
    t0 = datetime.now(timezone.utc)
    _fill(store, "acct1", "AAPL", Side.BUY, 10.0, 100.0, t0)
    _fill(store, "acct1", "AAPL", Side.CLOSE, 10.0, 110.0, t0 + timedelta(minutes=1))

    econ = compute_account_economics(store, "acct1")

    assert "AAPL" in econ.incomplete_symbols
    # The unresolved fill must not silently count as a realized gain.
    assert econ.realized_pnl == 0.0


def test_no_orders_yields_empty_report_not_an_error(store):
    econ = compute_account_economics(store, "acct1")

    assert econ.realized_pnl == 0.0
    assert econ.per_symbol == {}
    assert econ.completed_trade_win_rate is None
