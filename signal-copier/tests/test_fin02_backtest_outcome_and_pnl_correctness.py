"""FIN-02: BacktestEngine classified outcome purely by WHICH level fired
(target -> WIN, stop -> LOSS) rather than the actual economic result, let a
missing quantity silently become a fabricated exact-zero-P&L "win", and
computed an option's cash P&L with plain share-style math despite having
no contract multiplier to make that number meaningful.

Reproduces the audit's exact 3 cases (test_adapter_research_audit.py):
test_profitable_stop_not_counted_as_loss,
test_missing_quantity_not_scored_as_resolved_zero_pnl_win,
test_missing_contract_multiplier_blocks_option_cash_pnl.
"""
from datetime import datetime, timezone

from app.backtest.models import HistoricalBar, PriceHistoryProvider
from app.backtest.replay import BacktestEngine, TradeOutcome


class _History(PriceHistoryProvider):
    def __init__(self, bars):
        self.bars = bars

    def get_bars(self, symbol, start, end):
        return self.bars


def _row(**overrides):
    defaults = dict(
        id="1",
        source="audit",
        symbol="AAPL",
        side="buy",
        price=100.0,
        quantity=1.0,
        stop_loss=95.0,
        take_profit=105.0,
        received_at="2026-09-01T14:00:00+00:00",
    )
    defaults.update(overrides)
    return defaults


def test_audits_exact_case_profitable_stop_not_counted_as_loss():
    """A stop that fires ABOVE the entry price (e.g. a trailing/breakeven
    stop) is a real profit -- must not be labeled LOSS just because it was
    'the stop' that fired rather than 'the target'."""
    bar = HistoricalBar(datetime(2026, 9, 1, 14, tzinfo=timezone.utc), 111, 112, 108, 109)
    report = BacktestEngine(_History([bar])).run([_row(stop_loss=110, take_profit=120)])
    trade = report.trades[0]
    assert trade.pnl > 0
    assert trade.outcome == TradeOutcome.WIN


def test_a_losing_stop_is_still_correctly_labeled_loss():
    bar = HistoricalBar(datetime(2026, 9, 1, 14, tzinfo=timezone.utc), 98, 99, 94, 95)
    report = BacktestEngine(_History([bar])).run([_row(price=100, stop_loss=95, take_profit=110)])
    trade = report.trades[0]
    assert trade.pnl < 0
    assert trade.outcome == TradeOutcome.LOSS


def test_audits_exact_case_missing_quantity_not_scored_as_resolved_zero_pnl_win():
    bar = HistoricalBar(datetime(2026, 9, 1, 14, tzinfo=timezone.utc), 100, 106, 99, 105)
    report = BacktestEngine(_History([bar])).run([_row(quantity=None)])
    assert not report.resolved_trades or report.trades[0].pnl is None
    assert report.trades[0].outcome != TradeOutcome.WIN


def test_audits_exact_case_missing_contract_multiplier_blocks_option_cash_pnl():
    bar = HistoricalBar(datetime(2026, 9, 1, 14, tzinfo=timezone.utc), 1.5, 2.1, 1.4, 2)
    report = BacktestEngine(_History([bar])).run(
        [_row(asset_class="option", price=1.5, stop_loss=1, take_profit=2, quantity=1)]
    )
    assert report.trades[0].pnl is None
    assert report.trades[0].outcome == TradeOutcome.EXIT_UNSCORABLE


def test_unscorable_trades_are_excluded_from_resolved_stats():
    bar = HistoricalBar(datetime(2026, 9, 1, 14, tzinfo=timezone.utc), 100, 106, 99, 105)
    report = BacktestEngine(_History([bar])).run([_row(quantity=None)])
    assert report.resolved_trades == []
    assert report.win_rate is None
    assert report.count(TradeOutcome.EXIT_UNSCORABLE) == 1
