"""Track 59: Mutation testing regression suite for statistics.py and signal_correlation.py.

Covers critical correctness invariants and edge cases discovered through
mutation testing.  These tests are designed to catch mutations that would
silently change behavior in ways that don't appear through the existing test
suite.

Every test below is hand-written to specifically target a correctness issue,
never a library-test comparison.
"""
from datetime import datetime, timezone, timedelta
import pytest
import math

from app.db import SignalStore
from app.models import Signal, Side, AssetClass, OptionContractSpec
from app.statistics import (
    compute_rolling_stats,
    compute_max_drawdown,
    compute_pairwise_correlation,
    MIN_CORRELATION_SAMPLES,
)
from app.signal_correlation import (
    fingerprint_key,
    prices_within_tolerance,
    within_timestamp_window,
    classify_candidate,
    CorrelationOutcome,
)


@pytest.fixture
def store(tmp_path):
    return SignalStore(tmp_path / "test.db")


def _seed(store, account_id, cumulative_pnls, *, start=None, step=timedelta(hours=1)):
    """Helper to seed equity snapshots."""
    start = start or datetime(2024, 1, 1, tzinfo=timezone.utc)
    for i, pnl in enumerate(cumulative_pnls):
        store.record_equity_snapshot(
            account_id,
            captured_at=start + i * step,
            realized_pnl=pnl,
            unrealized_pnl=0.0,
            cumulative_pnl=pnl,
        )
    return store.list_equity_snapshots(account_id, limit=10000)


# =============================================================================
# STATISTICS.PY REGRESSIONS
# =============================================================================

class TestMaxDrawdownLoadBearing:
    """The load-bearing invariant: max drawdown must use real peak-to-trough
    tracking, not any first/last/min approximation."""

    def test_max_drawdown_interior_peak_recovery_is_real(self, store):
        """Regression: the series starts AND ends at 100, so first-vs-last
        would wrongly report 0. Interior peak (100) to trough (80) is 20."""
        snapshots = _seed(store, "acct_a", [100.0, 90.0, 85.0, 95.0, 80.0, 100.0])
        result = compute_max_drawdown(snapshots)
        assert result is not None
        max_dd, duration = result
        # This is the assertion that breaks if the peak-tracking walk is
        # replaced with first-vs-anything or first-vs-min.
        assert max_dd == pytest.approx(20.0), "Peak-to-trough walk must catch interior drawdown"

    def test_max_drawdown_multiple_peaks_tracks_highest(self, store):
        """Regression: when there are multiple peaks, track ALL of them and
        find the largest drawdown from any of them."""
        # Start at 100, drop to 50 (DD=50), recover to 120, drop to 90 (DD=30),
        # recover to 150, drop to 140 (DD=10). Max should be 50, not 30 or 10.
        snapshots = _seed(store, "acct_a", [100.0, 50.0, 120.0, 90.0, 150.0, 140.0])
        result = compute_max_drawdown(snapshots)
        assert result is not None
        max_dd, _ = result
        assert max_dd == pytest.approx(50.0), "Must find largest drawdown across all peaks"


class TestVolatilityCalculation:
    """Volatility (stdev) calculation must use the sample formula, not population."""

    def test_volatility_uses_sample_stdev(self, store):
        """Regression: sample stdev uses n-1 divisor, not n."""
        # deltas [10, -10] -> mean=0
        # population stdev: sqrt((100+100)/2) = 10
        # sample stdev: sqrt((100+100)/1) = 14.14...
        # The hand-computed expected value was 14.14 in the existing test
        snapshots = _seed(store, "acct_a", [0.0, 10.0, 0.0])
        result = compute_rolling_stats("acct_a", snapshots, window=10)

        expected_sample_stdev = math.sqrt(200.0)  # (100+100)/(2-1) -> sum=200
        assert result.volatility_pnl_delta == pytest.approx(expected_sample_stdev)


class TestSortinoBoundary:
    """Sortino must be None when there aren't enough downside deltas, never 0/inf."""

    def test_sortino_none_with_only_one_downside_delta(self, store):
        """Regression: with only one downside delta, stdev is undefined (not zero).
        Sortino must be None, not attempted."""
        # deltas [10, -5, 10] -> only one negative, below 2-sample threshold
        snapshots = _seed(store, "acct_a", [0.0, 10.0, 5.0, 15.0])
        result = compute_rolling_stats("acct_a", snapshots, window=10)
        assert result.sortino_equivalent is None, "Below 2-sample downside floor"

    def test_sortino_none_with_no_downside_deltas(self, store):
        """Regression: perfectly profitable period has no downside, so Sortino
        is undefined (not zero or infinity)."""
        # All positive deltas
        snapshots = _seed(store, "acct_a", [0.0, 5.0, 12.0, 20.0])
        result = compute_rolling_stats("acct_a", snapshots, window=10)
        # 3 deltas, 0 negative: below threshold OR none at all
        # The module docstring says "None when zero downside deltas"
        # The code checks len(downside) >= _MIN_DELTAS_FOR_VOLATILITY
        # With 0 downside, len(downside) = 0 < 2, so must be None
        assert result.sortino_equivalent is None, "No downside deltas -> undefined Sortino"


class TestCorrelationMinimumSample:
    """Correlation must be None below the 10-sample threshold, never 0/NaN."""

    def test_correlation_none_below_ten_samples(self, store):
        """Regression: fewer than MIN_CORRELATION_SAMPLES (10) overlapping
        snapshots means correlation is omitted, not a fabricated 0 or NaN."""
        values_a = [float(i) for i in range(9)]  # 9 samples
        values_b = [2.0 * v for v in values_a]
        snapshots_a = _seed(store, "acct_a", values_a)
        snapshots_b = _seed(store, "acct_b", values_b)

        result = compute_pairwise_correlation("acct_a", snapshots_a, "acct_b", snapshots_b)
        assert result.sample_count == 9
        assert result.correlation is None, "Below minimum sample threshold must be None"

    def test_correlation_exact_at_ten_samples(self, store):
        """Regression: exactly at the threshold (10), correlation is computed."""
        values_a = [float(i) for i in range(MIN_CORRELATION_SAMPLES)]
        values_b = [2.0 * v for v in values_a]
        snapshots_a = _seed(store, "acct_a", values_a)
        snapshots_b = _seed(store, "acct_b", values_b)

        result = compute_pairwise_correlation("acct_a", snapshots_a, "acct_b", snapshots_b)
        assert result.sample_count == MIN_CORRELATION_SAMPLES
        assert result.correlation is not None
        assert result.correlation == pytest.approx(1.0)


# =============================================================================
# SIGNAL_CORRELATION.PY REGRESSIONS
# =============================================================================

class TestFingerprintKeyStability:
    """Fingerprint key must be stable: same fields -> same hash."""

    def test_fingerprint_key_case_insensitive_source(self):
        """Regression: source name must be normalized to lowercase."""
        sig_upper = Signal(source="BuyAlerts", symbol="BTC", side=Side.BUY)
        sig_lower = Signal(source="buyalerts", symbol="BTC", side=Side.BUY)
        assert fingerprint_key(sig_upper) == fingerprint_key(sig_lower)

    def test_fingerprint_key_case_insensitive_symbol(self):
        """Regression: symbol must be normalized to uppercase."""
        sig_lower = Signal(source="provider", symbol="btc", side=Side.BUY)
        sig_upper = Signal(source="provider", symbol="BTC", side=Side.BUY)
        assert fingerprint_key(sig_lower) == fingerprint_key(sig_upper)

    def test_fingerprint_key_empty_source_vs_whitespace(self):
        """Regression: empty source ("") and whitespace-only source should
        produce the same fingerprint (both normalize to "")."""
        sig_empty = Signal(source="", symbol="BTC", side=Side.BUY)
        sig_space = Signal(source="   ", symbol="BTC", side=Side.BUY)
        assert fingerprint_key(sig_empty) == fingerprint_key(sig_space)

    def test_fingerprint_key_none_source_vs_empty(self):
        """Regression: None source and empty string should hash the same."""
        sig_none = Signal(source=None, symbol="BTC", side=Side.BUY)
        sig_empty = Signal(source="", symbol="BTC", side=Side.BUY)
        assert fingerprint_key(sig_none) == fingerprint_key(sig_empty)

    def test_fingerprint_key_differs_on_side(self):
        """Regression: side must be part of the fingerprint."""
        sig_buy = Signal(source="provider", symbol="BTC", side=Side.BUY)
        sig_sell = Signal(source="provider", symbol="BTC", side=Side.SELL)
        assert fingerprint_key(sig_buy) != fingerprint_key(sig_sell)

    def test_fingerprint_key_includes_option_fields(self):
        """Regression: option fields (strike, expiry, right) must differentiate."""
        base = Signal(
            source="opts", symbol="SPY", side=Side.BUY,
            asset_class=AssetClass.OPTION,
            option=OptionContractSpec(underlying="SPY", expiry="2026-09-18",
                                     strike=450.0, right="call")
        )
        diff_strike = Signal(
            source="opts", symbol="SPY", side=Side.BUY,
            asset_class=AssetClass.OPTION,
            option=OptionContractSpec(underlying="SPY", expiry="2026-09-18",
                                     strike=460.0, right="call")
        )
        assert fingerprint_key(base) != fingerprint_key(diff_strike)


class TestPriceTolerance:
    """Price tolerance must use relative-band logic, never exact equality."""

    def test_price_tolerance_relative_band(self):
        """Regression: tolerance is relative to the max of the two prices."""
        # 100 vs 100.4: difference=0.4, max=100.4, ratio=0.4/100.4≈0.00398 < 0.5%
        assert prices_within_tolerance(100.0, 100.4, tolerance_pct=0.005) is True
        # 100 vs 110: difference=10, max=110, ratio=10/110≈0.0909 > 0.5%
        assert prices_within_tolerance(100.0, 110.0, tolerance_pct=0.005) is False

    def test_price_tolerance_zero_or_negative_fails(self):
        """Regression: zero or negative prices must fail tolerance check
        (non-positive prices are never valid trade data)."""
        assert prices_within_tolerance(0.0, 100.0, tolerance_pct=0.5) is False
        assert prices_within_tolerance(100.0, -5.0, tolerance_pct=0.5) is False
        assert prices_within_tolerance(-10.0, -5.0, tolerance_pct=0.5) is False

    def test_price_tolerance_both_positive_required(self):
        """Regression: only when BOTH prices are strictly positive does
        tolerance matter."""
        # Boundary: both 0 vs both positive
        assert prices_within_tolerance(0.0, 0.0, tolerance_pct=0.5) is False
        assert prices_within_tolerance(100.0, 100.0, tolerance_pct=0.5) is True


class TestTimestampWindow:
    """Timestamp window must handle both naive and aware datetimes."""

    def test_within_timestamp_window_naive_aware_handling(self):
        """Regression: naive datetimes (no tzinfo) must be treated as UTC."""
        now_aware = datetime.now(timezone.utc)
        later_naive = (now_aware + timedelta(minutes=5)).replace(tzinfo=None)
        # Should be treated as equal timezone, so 5-minute difference fits
        assert within_timestamp_window(now_aware, later_naive, window_seconds=600) is True
        # But 5 minutes > 60 seconds, so tight window fails
        assert within_timestamp_window(now_aware, later_naive, window_seconds=60) is False

    def test_within_timestamp_window_boundary_seconds(self):
        """Regression: exactly at window boundary should pass (<=)."""
        now = datetime.now(timezone.utc)
        exactly_600s_later = now + timedelta(seconds=600)
        assert within_timestamp_window(now, exactly_600s_later, window_seconds=600) is True

        just_over = now + timedelta(seconds=601)
        assert within_timestamp_window(now, just_over, window_seconds=600) is False


class TestClassifyCandidate:
    """Classify candidate must handle price/timing/side checks in order."""

    def test_classify_returns_none_without_prices(self):
        """Regression: missing price on either side means 'not eligible to compare'."""
        now = datetime.now(timezone.utc)
        assert classify_candidate(
            new_price=None, new_side="buy", new_received_at=now,
            candidate_price=100.0, candidate_side="buy", candidate_received_at=now,
        ) is None
        assert classify_candidate(
            new_price=100.0, new_side="buy", new_received_at=now,
            candidate_price=None, candidate_side="buy", candidate_received_at=now,
        ) is None

    def test_classify_returns_none_outside_timestamp_window(self):
        """Regression: timestamps outside window -> 'not eligible', not conflicting."""
        now = datetime.now(timezone.utc)
        far = now + timedelta(hours=6)
        # Even with perfect price/side agreement, far timestamps mean None
        assert classify_candidate(
            new_price=100.0, new_side="buy", new_received_at=now,
            candidate_price=100.0, candidate_side="buy", candidate_received_at=far,
            window_seconds=900,
        ) is None

    def test_classify_conflicting_side_mismatch(self):
        """Regression: side disagreement is CONFLICTING, not None."""
        now = datetime.now(timezone.utc)
        outcome = classify_candidate(
            new_price=100.0, new_side="buy", new_received_at=now,
            candidate_price=100.0, candidate_side="sell", candidate_received_at=now,
        )
        assert outcome is CorrelationOutcome.CONFLICTING

    def test_classify_conflicting_price_outside_tolerance(self):
        """Regression: price disagreement beyond tolerance is CONFLICTING."""
        now = datetime.now(timezone.utc)
        outcome = classify_candidate(
            new_price=100.0, new_side="buy", new_received_at=now,
            candidate_price=110.0, candidate_side="buy", candidate_received_at=now,
            price_tolerance_pct=0.005,  # 0.5%
        )
        assert outcome is CorrelationOutcome.CONFLICTING

    def test_classify_corroborating_agreement(self):
        """Regression: matching side AND price within tolerance is CORROBORATING."""
        now = datetime.now(timezone.utc)
        outcome = classify_candidate(
            new_price=100.0, new_side="buy", new_received_at=now,
            candidate_price=100.4, candidate_side="buy", candidate_received_at=now,
            price_tolerance_pct=0.005,  # 0.5%
        )
        assert outcome is CorrelationOutcome.CORROBORATING
