from datetime import datetime, timedelta, timezone

import pytest

from app.backtest.models import HistoricalBar, PriceHistoryProvider
from app.backtest.replay import BacktestEngine, TradeOutcome
from app.models import Side


class _FakeProvider(PriceHistoryProvider):
    def __init__(self, bars_by_symbol: dict[str, list[HistoricalBar]]):
        self.bars_by_symbol = bars_by_symbol

    def get_bars(self, symbol, start, end):
        return [b for b in self.bars_by_symbol.get(symbol, []) if start <= b.timestamp <= end]


def _bar(day, o, h, l, c):
    return HistoricalBar(timestamp=datetime(2024, 1, day, tzinfo=timezone.utc), open=o, high=h, low=l, close=c)


def _signal_row(**overrides):
    defaults = dict(
        id="sig-1",
        source="tradingview",
        symbol="AAPL",
        side="buy",
        analyst=None,
        quantity=10.0,
        price=100.0,
        stop_loss=95.0,
        take_profit=110.0,
        received_at=datetime(2024, 1, 1, tzinfo=timezone.utc).isoformat(),
        raw={},
    )
    defaults.update(overrides)
    return defaults


def test_trade_resolves_to_win_when_target_hit_first():
    bars = [_bar(2, 101, 111, 100, 110)]  # target=110 hit
    provider = _FakeProvider({"AAPL": bars})
    engine = BacktestEngine(provider)

    report = engine.run([_signal_row()])

    trade = report.trades[0]
    assert trade.outcome == TradeOutcome.WIN
    assert trade.exit_price == 110.0
    assert trade.pnl == pytest.approx((110.0 - 100.0) * 10.0)


def test_trade_resolves_to_loss_when_stop_hit_first():
    bars = [_bar(2, 99, 100, 94, 96)]  # stop=95 hit
    provider = _FakeProvider({"AAPL": bars})
    engine = BacktestEngine(provider)

    report = engine.run([_signal_row()])

    trade = report.trades[0]
    assert trade.outcome == TradeOutcome.LOSS
    assert trade.exit_price == 95.0
    assert trade.pnl == pytest.approx((95.0 - 100.0) * 10.0)


def test_short_trade_pnl_sign_is_correct():
    row = _signal_row(side="sell", stop_loss=105.0, take_profit=90.0)
    bars = [_bar(2, 99, 100, 89, 91)]  # target=90 hit for a short
    provider = _FakeProvider({"AAPL": bars})
    engine = BacktestEngine(provider)

    report = engine.run([row])

    trade = report.trades[0]
    assert trade.outcome == TradeOutcome.WIN
    assert trade.pnl == pytest.approx((100.0 - 90.0) * 10.0)


def test_ambiguous_bar_does_not_get_guessed_into_a_win_or_loss():
    bars = [_bar(2, 100, 112, 93, 100)]  # both stop and target inside range
    provider = _FakeProvider({"AAPL": bars})
    engine = BacktestEngine(provider)

    report = engine.run([_signal_row()])

    trade = report.trades[0]
    assert trade.outcome == TradeOutcome.AMBIGUOUS
    assert trade.pnl is None


def test_no_price_data_when_provider_has_nothing_for_the_symbol():
    provider = _FakeProvider({})
    engine = BacktestEngine(provider)

    report = engine.run([_signal_row()])

    assert report.trades[0].outcome == TradeOutcome.NO_PRICE_DATA


def test_still_open_when_horizon_runs_out_without_resolution():
    bars = [_bar(2, 100, 102, 98, 101)]  # never touches stop or target
    provider = _FakeProvider({"AAPL": bars})
    engine = BacktestEngine(provider, max_hold=timedelta(days=5))

    report = engine.run([_signal_row()])

    assert report.trades[0].outcome == TradeOutcome.STILL_OPEN


def test_no_exit_levels_when_signal_has_neither_stop_nor_target():
    row = _signal_row(stop_loss=None, take_profit=None)
    provider = _FakeProvider({"AAPL": [_bar(2, 100, 200, 50, 150)]})
    engine = BacktestEngine(provider)

    report = engine.run([row])

    assert report.trades[0].outcome == TradeOutcome.NO_EXIT_LEVELS


def test_close_signals_are_not_replayed():
    row = _signal_row(side="close")
    provider = _FakeProvider({"AAPL": [_bar(2, 100, 200, 50, 150)]})
    engine = BacktestEngine(provider)

    report = engine.run([row])

    assert report.trades[0].outcome == TradeOutcome.NOT_REPLAYED


def test_profit_factor_is_none_not_infinite_when_no_losses():
    win_bars = [_bar(2, 101, 111, 100, 110)]
    provider = _FakeProvider({"AAPL": win_bars})
    engine = BacktestEngine(provider)

    report = engine.run([_signal_row()])

    value, note = report.profit_factor
    assert value is None
    assert "no losing trades" in note


def test_summary_reports_every_outcome_bucket_not_just_resolved():
    rows = [
        _signal_row(id="win", stop_loss=95.0, take_profit=110.0),
        _signal_row(id="ambiguous", stop_loss=95.0, take_profit=110.0),
        _signal_row(id="no-data", symbol="MSFT"),
    ]
    provider = _FakeProvider(
        {
            "AAPL": [
                _bar(2, 101, 111, 100, 110),  # resolves "win" as a WIN
            ],
        }
    )
    engine = BacktestEngine(provider)
    # give the ambiguous one its own bar by re-running separately since both
    # AAPL signals share the same provider bars above (win resolves first bar)
    ambiguous_provider = _FakeProvider({"AAPL": [_bar(2, 100, 112, 93, 100)]})

    win_report = engine.run([rows[0]])
    ambiguous_report = BacktestEngine(ambiguous_provider).run([rows[1]])
    no_data_report = engine.run([rows[2]])

    assert win_report.summary()["wins"] == 1
    assert ambiguous_report.summary()["ambiguous"] == 1
    assert no_data_report.summary()["no_price_data"] == 1
