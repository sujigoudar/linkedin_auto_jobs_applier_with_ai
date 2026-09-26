"""E07 (bounded): app/backtest/cost_stress.py -- slippage/fee stress over
an existing BacktestReport."""
from datetime import datetime, timezone

import pytest

from app.backtest.cost_stress import apply_cost_stress
from app.backtest.replay import BacktestReport, ReplayedTrade, TradeOutcome
from app.models import Side


def _win_trade(side=Side.BUY, entry_price=100.0, exit_price=110.0, quantity=10.0):
    pnl = (exit_price - entry_price) * quantity if side == Side.BUY else (entry_price - exit_price) * quantity
    return ReplayedTrade(
        signal_id="sig-1",
        source="test",
        symbol="AAPL",
        side=side,
        analyst=None,
        entry_time=datetime(2024, 1, 1, tzinfo=timezone.utc),
        entry_price=entry_price,
        quantity=quantity,
        stop_price=95.0,
        target_price=exit_price,
        outcome=TradeOutcome.WIN,
        exit_time=datetime(2024, 1, 2, tzinfo=timezone.utc),
        exit_price=exit_price,
        pnl=pnl,
    )


def test_slippage_reduces_pnl_for_a_long_win():
    report = BacktestReport(trades=[_win_trade(side=Side.BUY, entry_price=100.0, exit_price=110.0, quantity=10.0)])
    raw_pnl = report.total_pnl

    stressed = apply_cost_stress(report, slippage_bps=100.0)  # 1%

    assert stressed.total_pnl < raw_pnl
    # exit pushed down by 1%: 110 * 0.99 = 108.9 -> pnl = (108.9-100)*10 = 89.0
    assert stressed.trades[0].pnl == pytest.approx(89.0)


def test_slippage_reduces_pnl_for_a_short_win_too():
    """A short's exit must be pushed UP (worse fill), not down."""
    report = BacktestReport(trades=[_win_trade(side=Side.SELL, entry_price=110.0, exit_price=100.0, quantity=10.0)])
    raw_pnl = report.total_pnl

    stressed = apply_cost_stress(report, slippage_bps=100.0)

    assert stressed.total_pnl < raw_pnl


def test_fee_per_trade_is_subtracted_from_pnl():
    report = BacktestReport(trades=[_win_trade()])
    stressed = apply_cost_stress(report, fee_per_trade=5.0)
    assert stressed.trades[0].pnl == pytest.approx(report.trades[0].pnl - 5.0)


def test_cost_stress_can_flip_a_win_into_a_loss():
    # A tiny raw win, big enough slippage to flip its sign.
    report = BacktestReport(trades=[_win_trade(entry_price=100.0, exit_price=100.5, quantity=10.0)])
    stressed = apply_cost_stress(report, slippage_bps=1000.0)  # 10% -- clearly flips a 0.5% move
    assert stressed.trades[0].outcome == TradeOutcome.LOSS


def test_unresolved_trades_are_left_untouched():
    unresolved = ReplayedTrade(
        signal_id="sig-2",
        source="test",
        symbol="AAPL",
        side=Side.BUY,
        analyst=None,
        entry_time=datetime(2024, 1, 1, tzinfo=timezone.utc),
        entry_price=100.0,
        quantity=10.0,
        stop_price=95.0,
        target_price=110.0,
        outcome=TradeOutcome.STILL_OPEN,
    )
    report = BacktestReport(trades=[unresolved])
    stressed = apply_cost_stress(report, slippage_bps=100.0, fee_per_trade=5.0)
    assert stressed.trades[0] == unresolved


def test_zero_stress_parameters_leave_pnl_unchanged():
    report = BacktestReport(trades=[_win_trade()])
    stressed = apply_cost_stress(report, slippage_bps=0.0, fee_per_trade=0.0)
    assert stressed.trades[0].pnl == pytest.approx(report.trades[0].pnl)


def test_original_report_is_never_mutated():
    report = BacktestReport(trades=[_win_trade()])
    original_pnl = report.trades[0].pnl
    apply_cost_stress(report, slippage_bps=500.0, fee_per_trade=10.0)
    assert report.trades[0].pnl == original_pnl
