from datetime import datetime, timezone

from app.backtest.models import HistoricalBar
from app.backtest.simulator import BarOutcome, simulate_bar_fill
from app.models import Side


def _bar(o, h, l, c):
    return HistoricalBar(timestamp=datetime(2024, 1, 1, tzinfo=timezone.utc), open=o, high=h, low=l, close=c)


def test_long_stop_only_hit():
    bar = _bar(100, 101, 94, 98)  # low breaches stop=95, high never reaches target=105
    result = simulate_bar_fill(Side.BUY, bar, stop_price=95, target_price=105)
    assert result.outcome == BarOutcome.STOP_ONLY
    assert result.fill_price == 95


def test_long_target_only_hit():
    bar = _bar(100, 106, 99, 104)
    result = simulate_bar_fill(Side.BUY, bar, stop_price=95, target_price=105)
    assert result.outcome == BarOutcome.TARGET_ONLY
    assert result.fill_price == 105


def test_long_neither_hit():
    bar = _bar(100, 102, 98, 101)
    result = simulate_bar_fill(Side.BUY, bar, stop_price=95, target_price=105)
    assert result.outcome == BarOutcome.NEITHER


def test_long_both_hit_in_same_bar_is_ambiguous_not_guessed():
    """The exact worked example from the design: long at 100, stop 95,
    target 105, bar high=106 low=94 -- order can't be known from OHLC."""
    bar = _bar(100, 106, 94, 100)
    result = simulate_bar_fill(Side.BUY, bar, stop_price=95, target_price=105)
    assert result.outcome == BarOutcome.AMBIGUOUS
    assert result.fill_price is None


def test_long_gap_through_stop_at_open_resolves_even_if_target_also_in_range():
    # open already below the stop -- the stop was touched (gapped through)
    # before anything else in the bar could happen, even though the target
    # is also technically within [low, high].
    bar = _bar(90, 106, 89, 95)
    result = simulate_bar_fill(Side.BUY, bar, stop_price=95, target_price=105)
    assert result.outcome == BarOutcome.STOP_ONLY
    assert result.fill_price == 95


def test_long_gap_through_both_at_open_is_ambiguous():
    # a single point (open) can't be both a stop breach and a target breach
    # unless stop and target coincide with the open in the same instant --
    # exercised here by placing both thresholds exactly at the open price.
    bar = _bar(100, 106, 94, 100)
    result = simulate_bar_fill(Side.BUY, bar, stop_price=100, target_price=100)
    assert result.outcome == BarOutcome.AMBIGUOUS


def test_short_stop_only_hit():
    bar = _bar(100, 106, 99, 104)  # high breaches short stop=105
    result = simulate_bar_fill(Side.SELL, bar, stop_price=105, target_price=95)
    assert result.outcome == BarOutcome.STOP_ONLY
    assert result.fill_price == 105


def test_short_target_only_hit():
    bar = _bar(100, 101, 94, 96)  # low breaches short target=95
    result = simulate_bar_fill(Side.SELL, bar, stop_price=105, target_price=95)
    assert result.outcome == BarOutcome.TARGET_ONLY
    assert result.fill_price == 95


def test_short_both_hit_is_ambiguous():
    bar = _bar(100, 106, 94, 100)
    result = simulate_bar_fill(Side.SELL, bar, stop_price=105, target_price=95)
    assert result.outcome == BarOutcome.AMBIGUOUS


def test_only_stop_configured_no_target():
    bar = _bar(100, 101, 94, 98)
    result = simulate_bar_fill(Side.BUY, bar, stop_price=95, target_price=None)
    assert result.outcome == BarOutcome.STOP_ONLY


def test_neither_level_configured_is_neither():
    bar = _bar(100, 150, 50, 100)
    result = simulate_bar_fill(Side.BUY, bar, stop_price=None, target_price=None)
    assert result.outcome == BarOutcome.NEITHER


def test_impossible_ohlc_bar_rejected():
    import pytest

    with pytest.raises(ValueError):
        HistoricalBar(timestamp=datetime.now(timezone.utc), open=100, high=99, low=98, close=100)
