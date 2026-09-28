from datetime import datetime, timedelta, timezone

import pytest

from app.backtest.fit_simulator import simulate_provider_fit
from app.backtest.models import HistoricalBar, PriceHistoryProvider
from app.backtest.replay import TradeOutcome
from app.db import SignalStore
from app.models import AssetClass, Side, Signal


class _FakeProvider(PriceHistoryProvider):
    def __init__(self, bars_by_symbol: dict[str, list[HistoricalBar]]):
        self.bars_by_symbol = bars_by_symbol

    def get_bars(self, symbol, start, end):
        return [b for b in self.bars_by_symbol.get(symbol, []) if start <= b.timestamp <= end]


def _bar(dt, o, h, l, c):
    return HistoricalBar(timestamp=dt, open=o, high=h, low=l, close=c)


@pytest.fixture
def store(tmp_path):
    return SignalStore(tmp_path / "test.db")


def test_trade_priced_above_max_per_trade_does_not_fit(store):
    now = datetime(2024, 2, 1, tzinfo=timezone.utc)
    entry_time = now - timedelta(days=1)
    signal = Signal(
        source="alerts_guy", symbol="AAPL", side=Side.BUY, asset_class=AssetClass.EQUITY,
        quantity=10.0, price=5000.0, stop_loss=4900.0, take_profit=5200.0,
    )
    signal.received_at = entry_time
    store.save_signal(signal)

    provider = _FakeProvider({"AAPL": [_bar(entry_time + timedelta(hours=1), 5010, 5210, 5000, 5200)]})

    report = simulate_provider_fit(
        store, provider, source="alerts_guy", account_size=25_000.0, max_per_trade=2_500.0,
        lookback_days=90, now=now,
    )

    assert report.total == 1
    assert report.fit_count == 0
    assert report.fit_percentage == 0.0
    assert report.trades[0].fits is False
    assert report.trades[0].pnl is None


def test_fitting_trade_is_rescaled_to_max_per_trade_and_pnl_scales_linearly(store):
    """A source trading 10 shares at $100 (a $1000 position) with a
    $2500 max_per_trade should replicate the FULL 10-share size (it
    already fits) -- and a WIN's rescaled P&L must match the ratio
    exactly, proving the linear P&L scaling is correct."""
    now = datetime(2024, 2, 1, tzinfo=timezone.utc)
    entry_time = now - timedelta(days=1)
    signal = Signal(
        source="alerts_guy", symbol="AAPL", side=Side.BUY, asset_class=AssetClass.EQUITY,
        quantity=10.0, price=100.0, stop_loss=95.0, take_profit=110.0,
    )
    signal.received_at = entry_time
    store.save_signal(signal)

    provider = _FakeProvider({"AAPL": [_bar(entry_time + timedelta(hours=1), 101, 111, 100, 110)]})

    report = simulate_provider_fit(
        store, provider, source="alerts_guy", account_size=25_000.0, max_per_trade=2_500.0,
        lookback_days=90, now=now,
    )

    trade = report.trades[0]
    assert trade.fits is True
    assert trade.outcome == TradeOutcome.WIN
    assert trade.simulated_quantity == pytest.approx(10.0)  # already fits, replicated in full
    assert trade.pnl == pytest.approx((110.0 - 100.0) * 10.0)
    assert report.total_pnl == pytest.approx(100.0)


def test_fitting_trade_over_budget_is_capped_and_pnl_scales_down(store):
    """A source trading 100 shares at $100 (a $10,000 position) against a
    $2500 max_per_trade must be capped at 25 shares, and the WIN's P&L
    scaled down to exactly 25% of the source's own recorded P&L."""
    now = datetime(2024, 2, 1, tzinfo=timezone.utc)
    entry_time = now - timedelta(days=1)
    signal = Signal(
        source="alerts_guy", symbol="AAPL", side=Side.BUY, asset_class=AssetClass.EQUITY,
        quantity=100.0, price=100.0, stop_loss=95.0, take_profit=110.0,
    )
    signal.received_at = entry_time
    store.save_signal(signal)

    provider = _FakeProvider({"AAPL": [_bar(entry_time + timedelta(hours=1), 101, 111, 100, 110)]})

    report = simulate_provider_fit(
        store, provider, source="alerts_guy", account_size=25_000.0, max_per_trade=2_500.0,
        lookback_days=90, now=now,
    )

    trade = report.trades[0]
    assert trade.fits is True
    assert trade.simulated_quantity == pytest.approx(25.0)
    # source's own recorded pnl would be (110-100)*100 = 1000; rescaled by 25/100 = 250
    assert trade.pnl == pytest.approx(250.0)


def test_equity_curve_and_worst_drawdown_reflect_rescaled_pnl(store):
    now = datetime(2024, 3, 1, tzinfo=timezone.utc)
    win_time = now - timedelta(days=5)
    loss_time = now - timedelta(days=3)

    win_signal = Signal(
        source="alerts_guy", symbol="AAPL", side=Side.BUY, asset_class=AssetClass.EQUITY,
        quantity=10.0, price=100.0, stop_loss=95.0, take_profit=110.0,
    )
    win_signal.received_at = win_time
    store.save_signal(win_signal)

    loss_signal = Signal(
        source="alerts_guy", symbol="MSFT", side=Side.BUY, asset_class=AssetClass.EQUITY,
        quantity=10.0, price=200.0, stop_loss=190.0, take_profit=220.0,
    )
    loss_signal.received_at = loss_time
    store.save_signal(loss_signal)

    provider = _FakeProvider({
        "AAPL": [_bar(win_time + timedelta(hours=1), 101, 111, 100, 110)],  # WIN +100
        "MSFT": [_bar(loss_time + timedelta(hours=1), 199, 200, 189, 191)],  # LOSS -100
    })

    report = simulate_provider_fit(
        store, provider, source="alerts_guy", account_size=25_000.0, max_per_trade=2_500.0,
        lookback_days=90, now=now,
    )

    curve = report.equity_curve
    assert [round(v, 2) for _, v in curve] == [100.0, 0.0]
    assert report.worst_drawdown == pytest.approx(-100.0)


def test_missing_account_size_or_max_per_trade_raises(store):
    provider = _FakeProvider({})
    with pytest.raises(ValueError):
        simulate_provider_fit(store, provider, source="x", account_size=0, max_per_trade=100)
    with pytest.raises(ValueError):
        simulate_provider_fit(store, provider, source="x", account_size=1000, max_per_trade=0)
