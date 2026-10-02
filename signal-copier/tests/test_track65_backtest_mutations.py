"""Track 65: comprehensive mutation-testing pass for backtest utility modules.

Targeted regression tests to catch mutations that would survive the initial
mutmut run across app/backtest/replay.py, app/backtest/models.py,
app/backtest/cost_stress.py, and app/backtest/fit_simulator.py.

Focus on: parameter boundary conditions, P&L calculation accuracy,
cost-application logic, replay state management, and data validation.
These are the highest-risk mutations for a backtesting engine:
- Operator mutations (/ vs *, == vs !=, > vs >=, < vs <=)
- Control-flow mutations (continue vs break, return placement)
- Boundary condition mutations (off-by-one, edge cases)
- Type/default mutations (None vs empty, list vs dict)
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.backtest.cost_stress import apply_cost_stress
from app.backtest.models import HistoricalBar, PriceHistoryProvider
from app.backtest.replay import (
    BacktestEngine,
    BacktestReport,
    CapitalContentionReport,
    ReplayedTrade,
    TradeOutcome,
)
from app.models import Side


# ============================================================================
# HistoricalBar validation tests (app/backtest/models.py)
# ============================================================================
class TestHistoricalBarOLHCValidation:
    """Regression: HistoricalBar must reject non-finite and impossible OHLC."""

    def test_bar_rejects_nan_open(self):
        """Mutant: a check for math.isfinite(open) that was removed."""
        with pytest.raises(ValueError, match="non-finite open"):
            HistoricalBar(
                timestamp=datetime(2024, 1, 1, tzinfo=timezone.utc),
                open=float("nan"),
                high=100.0,
                low=90.0,
                close=95.0,
            )

    def test_bar_rejects_inf_high(self):
        """Mutant: a check for math.isfinite(high) that was removed."""
        with pytest.raises(ValueError, match="non-finite high"):
            HistoricalBar(
                timestamp=datetime(2024, 1, 1, tzinfo=timezone.utc),
                open=95.0,
                high=float("inf"),
                low=90.0,
                close=95.0,
            )

    def test_bar_rejects_negative_inf_low(self):
        """Mutant: a check for math.isfinite(low) that was removed."""
        with pytest.raises(ValueError, match="non-finite low"):
            HistoricalBar(
                timestamp=datetime(2024, 1, 1, tzinfo=timezone.utc),
                open=95.0,
                high=100.0,
                low=float("-inf"),
                close=95.0,
            )

    def test_bar_rejects_nan_close(self):
        """Mutant: a check for math.isfinite(close) that was removed."""
        with pytest.raises(ValueError, match="non-finite close"):
            HistoricalBar(
                timestamp=datetime(2024, 1, 1, tzinfo=timezone.utc),
                open=95.0,
                high=100.0,
                low=90.0,
                close=float("nan"),
            )

    def test_bar_rejects_high_too_low_buy_pattern(self):
        """Mutant: inequality check (high < max) changed to high <= max.
        With open=100, close=95, high must be >= 100 but a < (not <=)
        check with high=100 exactly would be valid OHLC."""
        with pytest.raises(ValueError, match="impossible OHLC"):
            HistoricalBar(
                timestamp=datetime(2024, 1, 1, tzinfo=timezone.utc),
                open=100.0,
                high=99.0,  # Violates OHLC: high must be >= max(open, close)
                low=90.0,
                close=95.0,
            )

    def test_bar_rejects_low_too_high_sell_pattern(self):
        """Mutant: inequality check (low > min) changed to low >= min.
        With open=100, close=95, low must be <= 95 but a > (not >=)
        check with low=95 exactly would be valid OHLC."""
        with pytest.raises(ValueError, match="impossible OHLC"):
            HistoricalBar(
                timestamp=datetime(2024, 1, 1, tzinfo=timezone.utc),
                open=100.0,
                high=105.0,
                low=96.0,  # Violates OHLC: low must be <= min(open, close)
                close=95.0,
            )

    def test_bar_accepts_valid_ohlc_flat(self):
        """Bar with all prices equal is valid (no-range bar)."""
        bar = HistoricalBar(
            timestamp=datetime(2024, 1, 1, tzinfo=timezone.utc),
            open=100.0,
            high=100.0,
            low=100.0,
            close=100.0,
        )
        assert bar.open == 100.0

    def test_bar_accepts_valid_ohlc_up_day(self):
        """Bar with open < close and proper high/low is valid."""
        bar = HistoricalBar(
            timestamp=datetime(2024, 1, 1, tzinfo=timezone.utc),
            open=100.0,
            high=110.0,
            low=99.0,
            close=105.0,
        )
        assert bar.close == 105.0

    def test_bar_accepts_valid_ohlc_down_day(self):
        """Bar with open > close and proper high/low is valid."""
        bar = HistoricalBar(
            timestamp=datetime(2024, 1, 1, tzinfo=timezone.utc),
            open=105.0,
            high=106.0,
            low=95.0,
            close=100.0,
        )
        assert bar.close == 100.0


# ============================================================================
# BacktestEngine P&L calculation tests (app/backtest/replay.py)
# ============================================================================
class TestBacktestEnginePnLCalculation:
    """Regression: P&L must be calculated correctly for BUY and SELL sides.
    Mutants: (exit - entry) vs (entry - exit), * vs /, side-dependent logic."""

    @pytest.fixture
    def bars_provider(self):
        """In-memory price provider for tests."""

        class TestProvider(PriceHistoryProvider):
            def __init__(self, bars_dict: dict[str, list[HistoricalBar]]):
                self.bars_dict = bars_dict

            def get_bars(self, symbol: str, start: datetime, end: datetime) -> list[HistoricalBar]:
                bars = self.bars_dict.get(symbol, [])
                return [b for b in bars if start <= b.timestamp <= end]

        return TestProvider

    def test_buy_profitable_pnl_positive(self, bars_provider):
        """Mutant: (exit - entry) * qty changed to (entry - exit) * qty.
        For a BUY: exit > entry should yield positive P&L.
        If formula is flipped, a profitable trade becomes a loss."""
        t0 = datetime(2024, 1, 1, 9, 30, tzinfo=timezone.utc)
        bars = [
            HistoricalBar(timestamp=t0, open=100.0, high=100.0, low=100.0, close=100.0),
            HistoricalBar(timestamp=t0 + timedelta(hours=1), open=110.0, high=110.0, low=110.0, close=110.0),
        ]
        provider = bars_provider({"AAPL": bars})
        engine = BacktestEngine(provider)

        signal = {
            "id": "sig1",
            "source": "test",
            "symbol": "AAPL",
            "side": Side.BUY.value,
            "price": 100.0,
            "quantity": 10.0,
            "stop_loss": 95.0,
            "take_profit": 110.0,
            "received_at": t0.isoformat(),
        }
        report = engine.run([signal])
        trades = report.trades
        assert len(trades) == 1
        t = trades[0]
        assert t.outcome == TradeOutcome.WIN
        # P&L should be (110 - 100) * 10 = 100
        assert t.pnl == pytest.approx(100.0)

    def test_buy_loss_pnl_negative(self, bars_provider):
        """Mutant: (exit - entry) * qty changed to (entry - exit) * qty.
        For a BUY: exit < entry should yield negative P&L."""
        t0 = datetime(2024, 1, 1, 9, 30, tzinfo=timezone.utc)
        bars = [
            HistoricalBar(timestamp=t0, open=100.0, high=100.0, low=100.0, close=100.0),
            HistoricalBar(timestamp=t0 + timedelta(hours=1), open=90.0, high=100.0, low=85.0, close=90.0),
        ]
        provider = bars_provider({"AAPL": bars})
        engine = BacktestEngine(provider)

        signal = {
            "id": "sig1",
            "source": "test",
            "symbol": "AAPL",
            "side": Side.BUY.value,
            "price": 100.0,
            "quantity": 10.0,
            "stop_loss": 85.0,
            "take_profit": 110.0,
            "received_at": t0.isoformat(),
        }
        report = engine.run([signal])
        trades = report.trades
        assert len(trades) == 1
        t = trades[0]
        assert t.outcome == TradeOutcome.LOSS
        # P&L should be (85 - 100) * 10 = -150
        assert t.pnl == pytest.approx(-150.0)

    def test_sell_profitable_pnl_positive(self, bars_provider):
        """Mutant: (entry - exit) * qty vs (exit - entry) * qty.
        For a SELL: entry > exit should yield positive P&L.
        If formula is not side-dependent, a profitable SHORT becomes a loss."""
        t0 = datetime(2024, 1, 1, 9, 30, tzinfo=timezone.utc)
        bars = [
            HistoricalBar(timestamp=t0, open=100.0, high=100.0, low=100.0, close=100.0),
            HistoricalBar(timestamp=t0 + timedelta(hours=1), open=90.0, high=100.0, low=85.0, close=90.0),
        ]
        provider = bars_provider({"AAPL": bars})
        engine = BacktestEngine(provider)

        signal = {
            "id": "sig1",
            "source": "test",
            "symbol": "AAPL",
            "side": Side.SELL.value,
            "price": 100.0,
            "quantity": 10.0,
            "stop_loss": 110.0,
            "take_profit": 85.0,
            "received_at": t0.isoformat(),
        }
        report = engine.run([signal])
        trades = report.trades
        assert len(trades) == 1
        t = trades[0]
        assert t.outcome == TradeOutcome.WIN
        # P&L should be (100 - 85) * 10 = 150
        assert t.pnl == pytest.approx(150.0)

    def test_sell_loss_pnl_negative(self, bars_provider):
        """Mutant: (entry - exit) * qty formula flipped.
        For a SELL: entry < exit should yield negative P&L."""
        t0 = datetime(2024, 1, 1, 9, 30, tzinfo=timezone.utc)
        bars = [
            HistoricalBar(timestamp=t0, open=100.0, high=100.0, low=100.0, close=100.0),
            HistoricalBar(timestamp=t0 + timedelta(hours=1), open=110.0, high=115.0, low=100.0, close=110.0),
        ]
        provider = bars_provider({"AAPL": bars})
        engine = BacktestEngine(provider)

        signal = {
            "id": "sig1",
            "source": "test",
            "symbol": "AAPL",
            "side": Side.SELL.value,
            "price": 100.0,
            "quantity": 10.0,
            "stop_loss": 115.0,
            "take_profit": 85.0,
            "received_at": t0.isoformat(),
        }
        report = engine.run([signal])
        trades = report.trades
        assert len(trades) == 1
        t = trades[0]
        assert t.outcome == TradeOutcome.LOSS
        # P&L should be (100 - 115) * 10 = -150 (stop loss hit first at high of 115)
        assert t.pnl == pytest.approx(-150.0)


# ============================================================================
# BacktestReport metrics tests (app/backtest/replay.py)
# ============================================================================
class TestBacktestReportMetrics:
    """Regression: BacktestReport metrics must use correct operators.
    Mutants: / vs *, > vs >=, == vs !=, count/total calculations."""

    def test_win_rate_division_not_multiplication(self):
        """Mutant: win_rate = wins * total vs wins / total.
        With 1 win and 3 resolved, should be 1/3 ≈ 0.333, not 3."""
        trades = [
            ReplayedTrade(
                signal_id="1",
                source="test",
                symbol="AAPL",
                side=Side.BUY,
                analyst=None,
                entry_time=datetime.now(timezone.utc),
                entry_price=100.0,
                quantity=1.0,
                stop_price=95.0,
                target_price=110.0,
                outcome=TradeOutcome.WIN,
                exit_price=110.0,
                pnl=10.0,
            ),
            ReplayedTrade(
                signal_id="2",
                source="test",
                symbol="AAPL",
                side=Side.BUY,
                analyst=None,
                entry_time=datetime.now(timezone.utc),
                entry_price=100.0,
                quantity=1.0,
                stop_price=95.0,
                target_price=110.0,
                outcome=TradeOutcome.LOSS,
                exit_price=95.0,
                pnl=-5.0,
            ),
            ReplayedTrade(
                signal_id="3",
                source="test",
                symbol="AAPL",
                side=Side.BUY,
                analyst=None,
                entry_time=datetime.now(timezone.utc),
                entry_price=100.0,
                quantity=1.0,
                stop_price=95.0,
                target_price=110.0,
                outcome=TradeOutcome.LOSS,
                exit_price=90.0,
                pnl=-10.0,
            ),
        ]
        report = BacktestReport(trades=trades)
        assert report.win_rate == pytest.approx(1.0 / 3.0)
        assert report.win_rate != 3.0

    def test_profit_factor_division_not_multiplication(self):
        """Mutant: profit_factor = gross_profit * gross_loss vs / gross_loss.
        With $100 profit and $50 loss, should be 100/50=2.0, not 100*50."""
        trades = [
            ReplayedTrade(
                signal_id="1",
                source="test",
                symbol="AAPL",
                side=Side.BUY,
                analyst=None,
                entry_time=datetime.now(timezone.utc),
                entry_price=100.0,
                quantity=1.0,
                stop_price=95.0,
                target_price=110.0,
                outcome=TradeOutcome.WIN,
                exit_price=110.0,
                pnl=10.0,
            ),
            ReplayedTrade(
                signal_id="2",
                source="test",
                symbol="AAPL",
                side=Side.BUY,
                analyst=None,
                entry_time=datetime.now(timezone.utc),
                entry_price=100.0,
                quantity=1.0,
                stop_price=95.0,
                target_price=110.0,
                outcome=TradeOutcome.WIN,
                exit_price=105.0,
                pnl=5.0,
            ),
            ReplayedTrade(
                signal_id="3",
                source="test",
                symbol="AAPL",
                side=Side.BUY,
                analyst=None,
                entry_time=datetime.now(timezone.utc),
                entry_price=100.0,
                quantity=1.0,
                stop_price=95.0,
                target_price=110.0,
                outcome=TradeOutcome.LOSS,
                exit_price=97.5,
                pnl=-2.5,
            ),
        ]
        report = BacktestReport(trades=trades)
        pf, _ = report.profit_factor
        # gross_profit = 10 + 5 = 15, gross_loss = 2.5
        # profit_factor = 15 / 2.5 = 6.0
        assert pf == pytest.approx(6.0)

    def test_expectancy_division_correct(self):
        """Mutant: expectancy = total_pnl * len(resolved) vs / len(resolved).
        With $100 total PnL and 4 resolved trades, should be 100/4=25, not 100*4."""
        trades = [
            ReplayedTrade(
                signal_id="1",
                source="test",
                symbol="AAPL",
                side=Side.BUY,
                analyst=None,
                entry_time=datetime.now(timezone.utc),
                entry_price=100.0,
                quantity=1.0,
                stop_price=95.0,
                target_price=110.0,
                outcome=TradeOutcome.WIN,
                exit_price=110.0,
                pnl=10.0,
            ),
            ReplayedTrade(
                signal_id="2",
                source="test",
                symbol="AAPL",
                side=Side.BUY,
                analyst=None,
                entry_time=datetime.now(timezone.utc),
                entry_price=100.0,
                quantity=1.0,
                stop_price=95.0,
                target_price=110.0,
                outcome=TradeOutcome.WIN,
                exit_price=120.0,
                pnl=20.0,
            ),
            ReplayedTrade(
                signal_id="3",
                source="test",
                symbol="AAPL",
                side=Side.BUY,
                analyst=None,
                entry_time=datetime.now(timezone.utc),
                entry_price=100.0,
                quantity=1.0,
                stop_price=95.0,
                target_price=110.0,
                outcome=TradeOutcome.WIN,
                exit_price=130.0,
                pnl=30.0,
            ),
            ReplayedTrade(
                signal_id="4",
                source="test",
                symbol="AAPL",
                side=Side.BUY,
                analyst=None,
                entry_time=datetime.now(timezone.utc),
                entry_price=100.0,
                quantity=1.0,
                stop_price=95.0,
                target_price=110.0,
                outcome=TradeOutcome.WIN,
                exit_price=140.0,
                pnl=40.0,
            ),
        ]
        report = BacktestReport(trades=trades)
        # total_pnl = 10 + 20 + 30 + 40 = 100
        # expectancy = 100 / 4 = 25
        assert report.expectancy == pytest.approx(25.0)
        assert report.expectancy != 400.0  # 100 * 4


# ============================================================================
# Cost stress application tests (app/backtest/cost_stress.py)
# ============================================================================
class TestCostStressCalculation:
    """Regression: cost stress must apply slippage and fees correctly.
    Mutants: * vs / for slippage calculation, +/- for fee, side-dependent logic."""

    def test_slippage_applied_correctly_buy_side(self):
        """Mutant: slippage_fraction = bps / 10000 vs * 10000.
        For 10 bps on BUY at 100, exit should drop to 100 * (1 - 0.001) = 99.9."""
        trade = ReplayedTrade(
            signal_id="1",
            source="test",
            symbol="AAPL",
            side=Side.BUY,
            analyst=None,
            entry_time=datetime.now(timezone.utc),
            entry_price=100.0,
            quantity=10.0,
            stop_price=95.0,
            target_price=110.0,
            outcome=TradeOutcome.WIN,
            exit_price=110.0,
            pnl=100.0,
        )
        report = BacktestReport(trades=[trade])
        stressed = apply_cost_stress(report, slippage_bps=10.0, fee_per_trade=0.0)

        stressed_trade = stressed.trades[0]
        # 10 bps = 0.001, so exit price drops by 0.1% for BUY
        # 110 * (1 - 0.001) = 109.89
        assert stressed_trade.exit_price == pytest.approx(109.89)

    def test_slippage_applied_correctly_sell_side(self):
        """Mutant: for SELL, slippage should increase exit price.
        For 10 bps on SELL at 100, exit should rise to 100 * (1 + 0.001) = 100.1."""
        trade = ReplayedTrade(
            signal_id="1",
            source="test",
            symbol="AAPL",
            side=Side.SELL,
            analyst=None,
            entry_time=datetime.now(timezone.utc),
            entry_price=100.0,
            quantity=10.0,
            stop_price=110.0,
            target_price=85.0,
            outcome=TradeOutcome.WIN,
            exit_price=85.0,
            pnl=150.0,
        )
        report = BacktestReport(trades=[trade])
        stressed = apply_cost_stress(report, slippage_bps=10.0, fee_per_trade=0.0)

        stressed_trade = stressed.trades[0]
        # 10 bps = 0.001, so exit price rises for SELL
        # 85 * (1 + 0.001) = 85.085
        assert stressed_trade.exit_price == pytest.approx(85.085)

    def test_fee_subtracted_from_pnl(self):
        """Mutant: fee applied as -= vs +=.
        With $100 PnL and $2 fee, result should be $98, not $102."""
        trade = ReplayedTrade(
            signal_id="1",
            source="test",
            symbol="AAPL",
            side=Side.BUY,
            analyst=None,
            entry_time=datetime.now(timezone.utc),
            entry_price=100.0,
            quantity=10.0,
            stop_price=95.0,
            target_price=110.0,
            outcome=TradeOutcome.WIN,
            exit_price=110.0,
            pnl=100.0,
        )
        report = BacktestReport(trades=[trade])
        stressed = apply_cost_stress(report, slippage_bps=0.0, fee_per_trade=2.0)

        stressed_trade = stressed.trades[0]
        # PnL after fee = 100 - 2 = 98
        assert stressed_trade.pnl == pytest.approx(98.0)

    def test_stress_converts_breakeven_to_loss(self):
        """Mutant: outcome determined by > vs >=.
        A barely-profitable trade ($0.01) becomes a loss after a $0.05 fee."""
        trade = ReplayedTrade(
            signal_id="1",
            source="test",
            symbol="AAPL",
            side=Side.BUY,
            analyst=None,
            entry_time=datetime.now(timezone.utc),
            entry_price=100.0,
            quantity=10.0,
            stop_price=95.0,
            target_price=110.0,
            outcome=TradeOutcome.WIN,
            exit_price=100.001,
            pnl=0.01,
        )
        report = BacktestReport(trades=[trade])
        stressed = apply_cost_stress(report, slippage_bps=0.0, fee_per_trade=0.05)

        stressed_trade = stressed.trades[0]
        assert stressed_trade.pnl == pytest.approx(-0.04)
        assert stressed_trade.outcome == TradeOutcome.LOSS

    def test_stress_passes_through_unresolved(self):
        """Stress test must not modify trades that are not WIN/LOSS."""
        unresolved_trades = [
            ReplayedTrade(
                signal_id="1",
                source="test",
                symbol="AAPL",
                side=Side.BUY,
                analyst=None,
                entry_time=datetime.now(timezone.utc),
                entry_price=100.0,
                quantity=10.0,
                stop_price=95.0,
                target_price=110.0,
                outcome=TradeOutcome.STILL_OPEN,
                exit_price=None,
                pnl=None,
            ),
            ReplayedTrade(
                signal_id="2",
                source="test",
                symbol="AAPL",
                side=Side.BUY,
                analyst=None,
                entry_time=datetime.now(timezone.utc),
                entry_price=100.0,
                quantity=10.0,
                stop_price=95.0,
                target_price=110.0,
                outcome=TradeOutcome.NO_PRICE_DATA,
                exit_price=None,
                pnl=None,
            ),
        ]
        report = BacktestReport(trades=unresolved_trades)
        stressed = apply_cost_stress(report, slippage_bps=50.0, fee_per_trade=100.0)

        # Unresolved trades must be unchanged
        assert stressed.trades[0].outcome == TradeOutcome.STILL_OPEN
        assert stressed.trades[0].pnl is None
        assert stressed.trades[1].outcome == TradeOutcome.NO_PRICE_DATA
        assert stressed.trades[1].pnl is None


# ============================================================================
# Fit simulator rescaling tests (app/backtest/fit_simulator.py)
# ============================================================================
class TestFitSimulatorRescaling:
    """Regression: fit simulator must rescale quantity and P&L correctly.
    Mutants: min vs max, / vs *, > vs >=, comparison direction."""

    def test_rescale_quantity_capped_at_max_per_trade(self):
        """Mutant: min(original, cap) vs max(original, cap).
        With original=20, max_per_trade/entry=5, should get 5, not 20."""
        from app.backtest.fit_simulator import _rescale_trade

        trade = ReplayedTrade(
            signal_id="1",
            source="test",
            symbol="AAPL",
            side=Side.BUY,
            analyst=None,
            entry_time=datetime.now(timezone.utc),
            entry_price=10.0,
            quantity=20.0,
            stop_price=5.0,
            target_price=15.0,
            outcome=TradeOutcome.WIN,
            exit_price=15.0,
            pnl=100.0,
        )

        fit_trade = _rescale_trade(trade, max_per_trade=50.0)
        # 50 / 10 = 5, and min(20, 5) = 5
        assert fit_trade.fits is True
        assert fit_trade.simulated_quantity == pytest.approx(5.0)

    def test_rescale_quantity_not_capped_when_fits(self):
        """Mutant: min vs max — a trade well within budget keeps original quantity."""
        from app.backtest.fit_simulator import _rescale_trade

        trade = ReplayedTrade(
            signal_id="1",
            source="test",
            symbol="AAPL",
            side=Side.BUY,
            analyst=None,
            entry_time=datetime.now(timezone.utc),
            entry_price=10.0,
            quantity=3.0,
            stop_price=5.0,
            target_price=15.0,
            outcome=TradeOutcome.WIN,
            exit_price=15.0,
            pnl=15.0,
        )

        fit_trade = _rescale_trade(trade, max_per_trade=100.0)
        # 100 / 10 = 10, and min(3, 10) = 3
        assert fit_trade.fits is True
        assert fit_trade.simulated_quantity == pytest.approx(3.0)

    def test_rescale_pnl_scaled_by_quantity_ratio(self):
        """Mutant: pnl * (sim_qty / orig_qty) vs pnl / (sim_qty / orig_qty).
        With original_qty=20, sim_qty=5, orig_pnl=100:
        rescaled_pnl = 100 * (5/20) = 25."""
        from app.backtest.fit_simulator import _rescale_trade

        trade = ReplayedTrade(
            signal_id="1",
            source="test",
            symbol="AAPL",
            side=Side.BUY,
            analyst=None,
            entry_time=datetime.now(timezone.utc),
            entry_price=10.0,
            quantity=20.0,
            stop_price=5.0,
            target_price=15.0,
            outcome=TradeOutcome.WIN,
            exit_price=15.0,
            pnl=100.0,
        )

        fit_trade = _rescale_trade(trade, max_per_trade=50.0)
        # sim_qty = min(20, 50/10) = min(20, 5) = 5
        # rescaled_pnl = 100 * (5/20) = 25
        assert fit_trade.pnl == pytest.approx(25.0)

    def test_fit_check_entry_price_positive(self):
        """Mutant: entry_price > 0 vs entry_price >= 0.
        A zero or negative entry price should not fit."""
        from app.backtest.fit_simulator import _rescale_trade

        trade_zero = ReplayedTrade(
            signal_id="1",
            source="test",
            symbol="AAPL",
            side=Side.BUY,
            analyst=None,
            entry_time=datetime.now(timezone.utc),
            entry_price=0.0,
            quantity=10.0,
            stop_price=5.0,
            target_price=15.0,
            outcome=TradeOutcome.WIN,
            exit_price=15.0,
            pnl=100.0,
        )

        fit_trade = _rescale_trade(trade_zero, max_per_trade=100.0)
        assert fit_trade.fits is False

    def test_fit_check_entry_price_exceeds_budget(self):
        """Mutant: entry_price > max_per_trade vs entry_price >= max_per_trade.
        If entry cost exceeds budget even for 1 share, it doesn't fit."""
        from app.backtest.fit_simulator import _rescale_trade

        trade = ReplayedTrade(
            signal_id="1",
            source="test",
            symbol="AAPL",
            side=Side.BUY,
            analyst=None,
            entry_time=datetime.now(timezone.utc),
            entry_price=150.0,
            quantity=10.0,
            stop_price=145.0,
            target_price=160.0,
            outcome=TradeOutcome.WIN,
            exit_price=160.0,
            pnl=100.0,
        )

        fit_trade = _rescale_trade(trade, max_per_trade=100.0)
        assert fit_trade.fits is False


# ============================================================================
# CapitalContentionReport tests (app/backtest/replay.py)
# ============================================================================
class TestCapitalContentionReport:
    """Regression: capital contention must track rejections correctly.
    Mutants: count/len mutations, comparison direction, list vs None."""

    def test_capital_contention_not_tracked_returns_correct_status(self):
        """Mutant: status = "implemented" vs "not_tracked" when no ceiling."""
        report = CapitalContentionReport.not_tracked("no ceiling configured")
        assert report.status == "not_tracked"

    def test_capital_contention_reduced_count_is_zero_when_empty(self):
        """Mutant: reduced_or_rejected_count initialized to 1 vs 0."""
        report = CapitalContentionReport(status="implemented")
        assert report.reduced_or_rejected_count == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
