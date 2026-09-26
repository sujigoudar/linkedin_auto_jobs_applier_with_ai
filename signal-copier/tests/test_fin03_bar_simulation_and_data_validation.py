"""FIN-03: the bar simulator could claim a stop/target filled at a price
the bar never actually reached, and HistoricalBar/CsvPriceHistoryProvider
accepted non-finite OHLC values and silently resolved conflicting
duplicate-timestamp rows.

Reproduces the audit's exact 5 cases (test_adapter_research_audit.py):
test_nonfinite_bar_values_rejected[nan/inf/-inf],
test_gap_stop_does_not_claim_unavailable_stop_price,
test_duplicate_timestamp_conflict_rejected.
"""
from datetime import datetime, timezone

import pytest

from app.backtest.models import CsvPriceHistoryProvider, HistoricalBar
from app.backtest.simulator import BarOutcome, simulate_bar_fill
from app.models import Side


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_audits_exact_case_nonfinite_bar_values_rejected(value):
    with pytest.raises(ValueError):
        HistoricalBar(datetime.now(timezone.utc), value, value, value, value)


def test_audits_exact_case_gap_stop_does_not_claim_unavailable_stop_price():
    """Held long position: bar opens 90, ranges [88, 92], never gets
    anywhere near a stop resting at 95 -- the open (90) already satisfies
    "gapped through" (open <= 95), but 95 itself was never a real observed
    price this bar, so the fill must not be claimed at 95."""
    bar = HistoricalBar(datetime(2026, 9, 1, 14, tzinfo=timezone.utc), 90, 92, 88, 91)
    result = simulate_bar_fill(Side.BUY, bar, 95, 105)
    assert result.fill_price != 95
    assert result.outcome == BarOutcome.STOP_ONLY
    assert result.fill_price == 90  # falls back to the bar's own observed open


def test_short_stop_below_the_whole_bar_range_falls_back_to_open():
    """The symmetric case for a short: the open already gaps through a
    stop resting below the bar's own traded range (open >= stop, but the
    stop itself was never actually a price this bar touched)."""
    bar = HistoricalBar(datetime(2026, 9, 1, 14, tzinfo=timezone.utc), 90, 92, 88, 91)
    result = simulate_bar_fill(Side.SELL, bar, stop_price=85, target_price=10)
    assert result.fill_price != 85
    assert result.outcome == BarOutcome.STOP_ONLY
    assert result.fill_price == 90


def test_gap_through_a_level_still_within_the_bars_range_uses_the_level():
    """Confirms the fix didn't break the documented, already-correct case:
    when the gapped-through level IS within the bar's own range, the level
    itself is still used (matches test_backtest_simulator.py's existing
    coverage)."""
    bar = HistoricalBar(datetime(2026, 9, 1, 14, tzinfo=timezone.utc), 90, 106, 89, 95)
    result = simulate_bar_fill(Side.BUY, bar, stop_price=95, target_price=105)
    assert result.outcome == BarOutcome.STOP_ONLY
    assert result.fill_price == 95


def test_stop_genuinely_within_the_bars_range_is_still_correctly_hit():
    """Confirms the fix didn't overcorrect into never firing a real hit."""
    bar = HistoricalBar(datetime(2026, 9, 1, 14, tzinfo=timezone.utc), 100, 101, 94, 98)
    result = simulate_bar_fill(Side.BUY, bar, stop_price=95, target_price=105)
    assert result.outcome == BarOutcome.STOP_ONLY
    assert result.fill_price == 95


def test_audits_exact_case_duplicate_timestamp_conflict_rejected(tmp_path):
    path = tmp_path / "dup.csv"
    path.write_text(
        "timestamp,open,high,low,close\n"
        "2026-09-01T14:00:00Z,100,110,99,109\n"
        "2026-09-01T14:00:00Z,100,101,90,91\n"
    )
    with pytest.raises(ValueError):
        CsvPriceHistoryProvider({"AAPL": path}).get_bars(
            "AAPL", datetime(2026, 9, 1, tzinfo=timezone.utc), datetime(2026, 9, 2, tzinfo=timezone.utc)
        )


def test_exact_duplicate_row_is_harmless_not_a_conflict(tmp_path):
    """The same row appearing twice (e.g. an export re-run) is not a
    conflict -- and must not be double-counted as two bars."""
    path = tmp_path / "same.csv"
    path.write_text(
        "timestamp,open,high,low,close\n"
        "2026-09-01T14:00:00Z,100,110,99,109\n"
        "2026-09-01T14:00:00Z,100,110,99,109\n"
    )
    bars = CsvPriceHistoryProvider({"AAPL": path}).get_bars(
        "AAPL", datetime(2026, 9, 1, tzinfo=timezone.utc), datetime(2026, 9, 2, tzinfo=timezone.utc)
    )
    assert len(bars) == 1
