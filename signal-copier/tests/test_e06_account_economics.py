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
    assert econ.closing_fill_win_rate == 1.0
    assert econ.completed_trade_win_rate == 1.0  # deprecated alias, same value
    assert econ.completed_lifecycle_win_rate == 1.0


def test_losing_trade_is_not_counted_as_a_win(store):
    t0 = datetime.now(timezone.utc)
    _fill(store, "acct1", "AAPL", Side.BUY, 10.0, 100.0, t0)
    _fill(store, "acct1", "AAPL", Side.SELL, 10.0, 95.0, t0 + timedelta(minutes=1))

    econ = compute_account_economics(store, "acct1")

    assert econ.realized_pnl == pytest.approx(-50.0)
    assert econ.closing_fill_win_rate == 0.0
    assert econ.completed_lifecycle_win_rate == 0.0


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
    # The flip closes the old (winning) episode -- it's completed even
    # though the symbol still has an open position afterward.
    assert se.completed_episodes == 1
    assert se.winning_episodes == 1


def test_short_side_realizes_pnl_in_the_correct_direction(store):
    t0 = datetime.now(timezone.utc)
    _fill(store, "acct1", "AAPL", Side.SELL, 10.0, 100.0, t0)  # open short
    _fill(store, "acct1", "AAPL", Side.BUY, 10.0, 90.0, t0 + timedelta(minutes=1))  # cover, profit

    econ = compute_account_economics(store, "acct1")

    assert econ.realized_pnl == pytest.approx(100.0)  # short profits when price falls
    assert econ.closing_fill_win_rate == 1.0


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
    assert econ.closing_fill_win_rate is None
    assert econ.completed_lifecycle_win_rate is None


def test_multiple_partial_exits_count_as_one_completed_episode(store):
    """Three separate reducing fills close the same position -- three
    closing_fills, but exactly one completed_lifecycle episode."""
    t0 = datetime.now(timezone.utc)
    _fill(store, "acct1", "AAPL", Side.BUY, 10.0, 100.0, t0)
    _fill(store, "acct1", "AAPL", Side.SELL, 4.0, 110.0, t0 + timedelta(minutes=1))
    _fill(store, "acct1", "AAPL", Side.SELL, 3.0, 105.0, t0 + timedelta(minutes=2))
    _fill(store, "acct1", "AAPL", Side.SELL, 3.0, 120.0, t0 + timedelta(minutes=3))

    econ = compute_account_economics(store, "acct1")
    se = econ.per_symbol["AAPL"]

    assert se.closing_fills == 3
    assert se.completed_episodes == 1
    assert se.winning_episodes == 1
    assert se.open_quantity == 0.0


def test_a_net_zero_episode_is_breakeven_not_a_win_or_a_loss(store):
    t0 = datetime.now(timezone.utc)
    _fill(store, "acct1", "AAPL", Side.BUY, 10.0, 100.0, t0)
    _fill(store, "acct1", "AAPL", Side.SELL, 10.0, 100.0, t0 + timedelta(minutes=1))

    econ = compute_account_economics(store, "acct1")
    se = econ.per_symbol["AAPL"]

    assert se.completed_episodes == 1
    assert se.winning_episodes == 0
    assert se.breakeven_episodes == 1
    assert se.losing_episodes == 0
    assert se.completed_lifecycle_win_rate == 0.0


def test_an_open_position_is_not_counted_as_a_completed_episode(store):
    t0 = datetime.now(timezone.utc)
    _fill(store, "acct1", "AAPL", Side.BUY, 10.0, 100.0, t0)

    econ = compute_account_economics(store, "acct1")
    se = econ.per_symbol["AAPL"]

    assert se.completed_episodes == 0
    assert se.completed_lifecycle_win_rate is None


def test_closing_fill_and_lifecycle_win_rates_differ_when_a_losing_partial_exit_is_offset(store):
    """A position closed by one losing partial exit and one winning
    partial exit is net profitable as a single episode, even though half
    its closing fills were individually losers -- this is exactly the
    distinction closing_fill_win_rate and completed_lifecycle_win_rate
    must not conflate."""
    t0 = datetime.now(timezone.utc)
    _fill(store, "acct1", "AAPL", Side.BUY, 10.0, 100.0, t0)
    _fill(store, "acct1", "AAPL", Side.SELL, 5.0, 90.0, t0 + timedelta(minutes=1))  # losing fill
    _fill(store, "acct1", "AAPL", Side.SELL, 5.0, 130.0, t0 + timedelta(minutes=2))  # winning fill

    econ = compute_account_economics(store, "acct1")
    se = econ.per_symbol["AAPL"]

    assert se.closing_fills == 2
    assert se.winning_closing_fills == 1
    assert se.closing_fill_win_rate == 0.5

    assert se.completed_episodes == 1
    assert se.winning_episodes == 1
    assert se.completed_lifecycle_win_rate == 1.0
