"""Comprehensive mutation testing for backtest/simulator.py - covers edge cases and all execution paths."""
from datetime import datetime, timezone

import pytest

from app.backtest.models import HistoricalBar
from app.backtest.simulator import BarOutcome, simulate_bar_fill
from app.models import Side


def _bar(o, h, l, c):
    return HistoricalBar(timestamp=datetime(2024, 1, 1, tzinfo=timezone.utc), open=o, high=h, low=l, close=c)


class TestLongFillPriceGapValidation:
    """Tests for the FIN-03 gap validation - fill price must be within [low, high]
    when gapped through, otherwise use bar.open."""

    def test_long_gap_through_stop_within_range_uses_stop_price(self):
        """When gapped through stop at open, and stop is within [low, high], use stop price."""
        bar = _bar(93, 105, 92, 100)  # open=93 < stop=95, stop within [92, 105]
        result = simulate_bar_fill(Side.BUY, bar, stop_price=95, target_price=105)
        assert result.outcome == BarOutcome.STOP_ONLY
        assert result.fill_price == 95

    def test_long_gap_through_stop_above_range_uses_bar_open(self):
        """When gapped through stop at open, but stop is above bar's high, use bar.open."""
        bar = _bar(60, 80, 59, 75)  # open=60 <= stop=95, stop above [59, 80]
        result = simulate_bar_fill(Side.BUY, bar, stop_price=95, target_price=105)
        assert result.outcome == BarOutcome.STOP_ONLY
        assert result.fill_price == 60  # bar.open, not stop price (95 not in [59,80])

    def test_long_gap_through_target_within_range_uses_target_price(self):
        """When gapped through target at open, and target is within [low, high], use target price."""
        bar = _bar(110, 115, 100, 112)  # open=110 >= target=105, target within [100, 115]
        result = simulate_bar_fill(Side.BUY, bar, stop_price=95, target_price=105)
        assert result.outcome == BarOutcome.TARGET_ONLY
        assert result.fill_price == 105

    def test_long_gap_through_target_below_bar_low_uses_bar_open(self):
        """When gapped through target at open, but target is below bar's low, use bar.open."""
        bar = _bar(120, 125, 100, 122)  # open=120 >= target=95, target below [100, 125]
        result = simulate_bar_fill(Side.BUY, bar, stop_price=85, target_price=95)
        assert result.outcome == BarOutcome.TARGET_ONLY
        assert result.fill_price == 120  # bar.open, not target price (95 not in [100,125])


class TestShortFillPriceGapValidation:
    """Tests for SHORT side gap validation and fill price selection."""

    def test_short_gap_through_stop_within_range_uses_stop_price(self):
        """When gapped through stop at open (high), and stop is within [low, high], use stop price."""
        bar = _bar(110, 115, 100, 105)  # open=110 > stop=105, stop within [100, 115]
        result = simulate_bar_fill(Side.SELL, bar, stop_price=105, target_price=95)
        assert result.outcome == BarOutcome.STOP_ONLY
        assert result.fill_price == 105

    def test_short_gap_through_stop_outside_range_uses_bar_open(self):
        """When gapped through stop at open, but stop is outside bar range, use bar.open."""
        bar = _bar(140, 145, 135, 142)  # open=140 >= stop=130, stop below [135, 145]
        result = simulate_bar_fill(Side.SELL, bar, stop_price=130, target_price=95)
        assert result.outcome == BarOutcome.STOP_ONLY
        # open >= stop (140 >= 130), so gapped through
        # stop_hit = 135 <= 130 <= 145 = FALSE, so fill_price = bar.open = 140
        assert result.fill_price == 140

    def test_short_gap_through_target_within_range_uses_target_price(self):
        """When gapped through target at open (low), and target is within [low, high], use target price."""
        bar = _bar(90, 100, 85, 88)  # open=90 <= target=95, target within [85, 100]
        result = simulate_bar_fill(Side.SELL, bar, stop_price=105, target_price=95)
        assert result.outcome == BarOutcome.TARGET_ONLY
        assert result.fill_price == 95

    def test_short_gap_through_target_inside_range(self):
        """When gapped through target at open, and target is inside bar range, use target."""
        bar = _bar(50, 60, 35, 48)  # open=50 <= target=40, target in [35, 60]
        result = simulate_bar_fill(Side.SELL, bar, stop_price=105, target_price=40)
        assert result.outcome == BarOutcome.TARGET_ONLY
        assert result.fill_price == 40  # use target price since it's in range


class TestBoundaryConditions:
    """Tests for exact boundary values (at bar low/high, at open)."""

    def test_long_stop_exactly_at_bar_low(self):
        """Stop exactly at bar.low should be considered hit."""
        bar = _bar(100, 105, 95, 100)
        result = simulate_bar_fill(Side.BUY, bar, stop_price=95, target_price=110)
        assert result.outcome == BarOutcome.STOP_ONLY
        assert result.fill_price == 95

    def test_long_stop_exactly_at_bar_high(self):
        """Stop exactly at bar.high should be considered hit."""
        bar = _bar(100, 105, 90, 100)
        result = simulate_bar_fill(Side.BUY, bar, stop_price=105, target_price=110)
        assert result.outcome == BarOutcome.STOP_ONLY
        assert result.fill_price == 105

    def test_long_target_exactly_at_bar_low(self):
        """Target exactly at bar.low should be considered hit."""
        bar = _bar(100, 110, 95, 100)
        result = simulate_bar_fill(Side.BUY, bar, stop_price=85, target_price=95)
        assert result.outcome == BarOutcome.TARGET_ONLY
        assert result.fill_price == 95

    def test_long_target_exactly_at_bar_high(self):
        """Target exactly at bar.high should be considered hit."""
        bar = _bar(100, 110, 95, 100)
        result = simulate_bar_fill(Side.BUY, bar, stop_price=85, target_price=110)
        assert result.outcome == BarOutcome.TARGET_ONLY
        assert result.fill_price == 110

    def test_long_stop_just_below_bar_low_not_hit(self):
        """Stop just below bar.low should NOT be considered hit."""
        bar = _bar(100, 105, 95.01, 100)
        result = simulate_bar_fill(Side.BUY, bar, stop_price=95, target_price=110)
        assert result.outcome == BarOutcome.NEITHER

    def test_long_stop_just_above_bar_high_gaps_through(self):
        """Stop just above bar.high can still be gapped through if open <= stop."""
        bar = _bar(100, 104.99, 90, 100)
        result = simulate_bar_fill(Side.BUY, bar, stop_price=105, target_price=110)
        # Even though stop is above bar.high, open=100 <= stop=105, so it gapped through
        assert result.outcome == BarOutcome.STOP_ONLY
        assert result.fill_price == 100  # bar.open since stop not in [90, 104.99]

    def test_short_stop_exactly_at_bar_high(self):
        """For short, stop exactly at bar.high should be considered hit."""
        bar = _bar(100, 105, 90, 100)
        result = simulate_bar_fill(Side.SELL, bar, stop_price=105, target_price=85)
        assert result.outcome == BarOutcome.STOP_ONLY
        assert result.fill_price == 105

    def test_short_stop_exactly_at_bar_low(self):
        """For short, stop exactly at bar.low should be considered hit."""
        bar = _bar(100, 110, 95, 100)
        result = simulate_bar_fill(Side.SELL, bar, stop_price=95, target_price=85)
        assert result.outcome == BarOutcome.STOP_ONLY
        assert result.fill_price == 95


class TestGapOpenBoundaryConditions:
    """Tests for open gap conditions with exact boundary values."""

    def test_long_open_exactly_equals_stop_price(self):
        """For long, if open exactly equals stop, it should trigger."""
        bar = _bar(95, 105, 90, 100)  # open=95 == stop=95
        result = simulate_bar_fill(Side.BUY, bar, stop_price=95, target_price=110)
        assert result.outcome == BarOutcome.STOP_ONLY

    def test_long_open_exactly_equals_target_price(self):
        """For long, if open exactly equals target, it should trigger."""
        bar = _bar(110, 115, 105, 108)  # open=110 == target=110
        result = simulate_bar_fill(Side.BUY, bar, stop_price=85, target_price=110)
        assert result.outcome == BarOutcome.TARGET_ONLY

    def test_short_open_exactly_equals_stop_price(self):
        """For short, if open exactly equals stop, it should trigger."""
        bar = _bar(105, 110, 100, 102)  # open=105 == stop=105
        result = simulate_bar_fill(Side.SELL, bar, stop_price=105, target_price=90)
        assert result.outcome == BarOutcome.STOP_ONLY

    def test_short_open_exactly_equals_target_price(self):
        """For short, if open exactly equals target, it should trigger."""
        bar = _bar(85, 90, 80, 82)  # open=85 == target=85
        result = simulate_bar_fill(Side.SELL, bar, stop_price=110, target_price=85)
        assert result.outcome == BarOutcome.TARGET_ONLY

    def test_long_open_just_below_stop_no_gap(self):
        """For long, if open is just below stop (but not past), no gap."""
        bar = _bar(94.99, 105, 90, 100)  # open < stop but still in range
        result = simulate_bar_fill(Side.BUY, bar, stop_price=95, target_price=110)
        # Stop should be hit via the range check, not the gap check
        assert result.outcome == BarOutcome.STOP_ONLY

    def test_long_open_just_above_target_no_gap(self):
        """For long, if open is just above target (but not past), no gap."""
        bar = _bar(110.01, 115, 105, 112)  # open > target but not fully past
        result = simulate_bar_fill(Side.BUY, bar, stop_price=85, target_price=110)
        # Target should be hit via the range check, not the gap check
        assert result.outcome == BarOutcome.TARGET_ONLY


class TestNullTargetAndStop:
    """Tests with None values for stop or target."""

    def test_long_none_stop_target_hit(self):
        """Long with no stop, only target."""
        bar = _bar(100, 120, 95, 110)
        result = simulate_bar_fill(Side.BUY, bar, stop_price=None, target_price=110)
        assert result.outcome == BarOutcome.TARGET_ONLY
        assert result.fill_price == 110

    def test_long_none_target_stop_hit(self):
        """Long with no target, only stop."""
        bar = _bar(100, 105, 85, 90)
        result = simulate_bar_fill(Side.BUY, bar, stop_price=85, target_price=None)
        assert result.outcome == BarOutcome.STOP_ONLY
        assert result.fill_price == 85

    def test_short_none_stop_target_hit(self):
        """Short with no stop, only target."""
        bar = _bar(100, 105, 70, 90)
        result = simulate_bar_fill(Side.SELL, bar, stop_price=None, target_price=80)
        assert result.outcome == BarOutcome.TARGET_ONLY
        assert result.fill_price == 80

    def test_short_none_target_stop_hit(self):
        """Short with no target, only stop."""
        bar = _bar(100, 120, 90, 110)
        result = simulate_bar_fill(Side.SELL, bar, stop_price=110, target_price=None)
        assert result.outcome == BarOutcome.STOP_ONLY
        assert result.fill_price == 110


class TestComplexGapScenarios:
    """Complex scenarios combining multiple conditions."""

    def test_long_gap_through_stop_outside_range_uses_open(self):
        """Long: gap through stop at open, but stop is outside bar range."""
        bar = _bar(60, 80, 59, 75)  # open=60 gaps through stop=95 (stop above range)
        result = simulate_bar_fill(Side.BUY, bar, stop_price=95, target_price=105)
        assert result.outcome == BarOutcome.STOP_ONLY
        assert result.fill_price == 60  # gap-through uses bar.open since stop not in [59,80]

    def test_long_gap_through_target_inside_range(self):
        """Long: gap through target at open, target is inside bar range."""
        bar = _bar(110, 120, 100, 112)  # open=110 gaps through target=105 (target in range)
        result = simulate_bar_fill(Side.BUY, bar, stop_price=95, target_price=105)
        assert result.outcome == BarOutcome.TARGET_ONLY
        assert result.fill_price == 105  # gap-through uses target since it's in [100,120]

    def test_short_gap_through_stop_inside_range(self):
        """Short: gap through stop at open, stop is inside bar range."""
        bar = _bar(120, 125, 110, 115)  # open=120 gaps through stop=115 (stop in range)
        result = simulate_bar_fill(Side.SELL, bar, stop_price=115, target_price=95)
        assert result.outcome == BarOutcome.STOP_ONLY
        assert result.fill_price == 115  # gap-through uses stop since it's in [110,125]

    def test_short_gap_through_target_and_hit_same_outcome(self):
        """Short: gap through target (open <= target) and target_hit both true uses target."""
        bar = _bar(50, 60, 35, 48)  # open=50 <= target=40, target in [35, 60]
        result = simulate_bar_fill(Side.SELL, bar, stop_price=105, target_price=40)
        assert result.outcome == BarOutcome.TARGET_ONLY
        assert result.fill_price == 40  # uses target since both gap and hit are true


class TestSideValidation:
    """Tests for side validation."""

    def test_invalid_side_raises_error(self):
        """Passing an invalid side should raise ValueError."""
        bar = _bar(100, 105, 95, 100)
        with pytest.raises(ValueError, match="needs an entry side"):
            simulate_bar_fill("INVALID", bar, stop_price=95, target_price=105)

    def test_long_side_string_raises_error(self):
        """Passing string instead of Side enum should raise."""
        bar = _bar(100, 105, 95, 100)
        # This depends on how the code handles invalid side - it should fail
        with pytest.raises((ValueError, AttributeError)):
            simulate_bar_fill("BUY", bar, stop_price=95, target_price=105)


class TestAssertionCoverage:
    """Ensure assert statements are exercised."""

    def test_long_gap_through_stop_assertion_fires(self):
        """When gapped_through_stop is True, stop_price should not be None."""
        # This test documents that the assert is there and should always be true
        bar = _bar(90, 106, 89, 95)  # gap through stop
        result = simulate_bar_fill(Side.BUY, bar, stop_price=95, target_price=105)
        # If assertion fails, this would have raised
        assert result.outcome == BarOutcome.STOP_ONLY

    def test_long_gap_through_target_assertion_fires(self):
        """When gapped_through_target is True, target_price should not be None."""
        bar = _bar(110, 120, 105, 115)  # gap through target
        result = simulate_bar_fill(Side.BUY, bar, stop_price=95, target_price=105)
        # If assertion fails, this would have raised
        assert result.outcome == BarOutcome.TARGET_ONLY

    def test_short_gap_through_stop_assertion_fires(self):
        """For short, when gapped_through_stop is True, stop_price should not be None."""
        bar = _bar(115, 120, 105, 110)  # gap through stop for short
        result = simulate_bar_fill(Side.SELL, bar, stop_price=105, target_price=95)
        assert result.outcome == BarOutcome.STOP_ONLY

    def test_short_gap_through_target_assertion_fires(self):
        """For short, when gapped_through_target is True, target_price should not be None."""
        bar = _bar(85, 95, 84, 90)  # gap through target for short
        result = simulate_bar_fill(Side.SELL, bar, stop_price=105, target_price=95)
        assert result.outcome == BarOutcome.TARGET_ONLY
