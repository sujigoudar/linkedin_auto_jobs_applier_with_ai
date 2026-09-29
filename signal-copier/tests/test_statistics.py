"""Phase A5: app/statistics.py -- rolling volatility/Sharpe-equivalent/
Sortino-equivalent/max-drawdown(+duration) and pairwise correlation,
computed from a real per-account `cumulative_pnl` snapshot series.

Every expected number below is hand-computed in the test itself from a
known input series (never a library black-box comparison alone), per
this slice's own standing rule. See `test_max_drawdown_is_real_peak_to_
trough_not_first_last_approximation` for this module's load-bearing
invariant.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import pytest

from app.db import SignalStore
from app.statistics import (
    MIN_CORRELATION_SAMPLES,
    compute_max_drawdown,
    compute_pairwise_correlation,
    compute_rolling_stats,
)

ACCOUNT_A = "acct_a"
ACCOUNT_B = "acct_b"


@pytest.fixture
def store(tmp_path):
    return SignalStore(tmp_path / "test.db")


def _seed(store, account_id, cumulative_pnls, *, start=None, step=timedelta(hours=1)):
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


# ---------------------------------------------------------------------------
# Rolling volatility / Sharpe-equivalent / Sortino-equivalent
# ---------------------------------------------------------------------------


def test_rolling_stats_hand_computed_mean_volatility_sharpe(store):
    # cumulative_pnl series -> deltas [-10, -5, 10, -15, 20]
    snapshots = _seed(store, ACCOUNT_A, [100.0, 90.0, 85.0, 95.0, 80.0, 100.0])

    result = compute_rolling_stats(ACCOUNT_A, snapshots, window=10)

    assert result.sample_count == 6
    # mean = (-10 - 5 + 10 - 15 + 20) / 5 = 0 / 5 = 0.0
    assert result.mean_pnl_delta == pytest.approx(0.0)
    # sample stdev (n-1=4): variance = (100+25+100+225+400)/4 = 212.5
    expected_stdev = math.sqrt(212.5)
    assert result.volatility_pnl_delta == pytest.approx(expected_stdev)
    # sharpe-equivalent = mean / stdev = 0 / stdev = 0.0 (implicit 0 risk-free rate)
    assert result.sharpe_equivalent == pytest.approx(0.0)
    # downside deltas [-10, -5, -15]; sample stdev (n-1=2): mean=-10,
    # variance = (0 + 25 + 25) / 2 = 25 -> stdev = 5
    # sortino-equivalent = mean(all deltas) / downside_stdev = 0 / 5 = 0.0
    assert result.sortino_equivalent == pytest.approx(0.0)


def test_rolling_stats_hand_computed_nonzero_sharpe(store):
    # deltas [10, -5, 10] -> mean = 15/3 = 5
    snapshots = _seed(store, ACCOUNT_A, [0.0, 10.0, 5.0, 15.0])

    result = compute_rolling_stats(ACCOUNT_A, snapshots, window=10)

    assert result.mean_pnl_delta == pytest.approx(5.0)
    # sample stdev (n-1=2): variance = (25 + 100 + 25) / 2 = 75
    expected_stdev = math.sqrt(75.0)
    assert result.volatility_pnl_delta == pytest.approx(expected_stdev)
    assert result.sharpe_equivalent == pytest.approx(5.0 / expected_stdev)
    # only one downside delta (-5) -- below the 2-sample floor for a
    # non-degenerate downside stdev, so sortino must be omitted (None),
    # never a fabricated 0/inf.
    assert result.sortino_equivalent is None


def test_rolling_stats_window_uses_only_the_last_n_snapshots(store):
    # 6 snapshots, but window=3 -> only the last 3 (values 95, 80, 100;
    # deltas [-15, 20]) should be used.
    snapshots = _seed(store, ACCOUNT_A, [100.0, 90.0, 85.0, 95.0, 80.0, 100.0])

    result = compute_rolling_stats(ACCOUNT_A, snapshots, window=3)

    assert result.sample_count == 3
    assert result.mean_pnl_delta == pytest.approx((-15.0 + 20.0) / 2)
    assert result.window_start == snapshots[3]["captured_at"] if isinstance(
        snapshots[3]["captured_at"], datetime
    ) else datetime.fromisoformat(snapshots[3]["captured_at"])


def test_rolling_stats_insufficient_data_returns_none_not_a_fabricated_number(store):
    # A single snapshot has zero deltas -- every derived stat must be
    # None, never a misleadingly precise number from no real movement.
    snapshots = _seed(store, ACCOUNT_A, [42.0])

    result = compute_rolling_stats(ACCOUNT_A, snapshots, window=10)

    assert result.sample_count == 1
    assert result.mean_pnl_delta is None
    assert result.volatility_pnl_delta is None
    assert result.sharpe_equivalent is None
    assert result.sortino_equivalent is None
    assert result.max_drawdown is None
    assert result.max_drawdown_duration_seconds is None


def test_rolling_stats_zero_snapshots_returns_none_everywhere(store):
    result = compute_rolling_stats(ACCOUNT_A, [], window=10)

    assert result.sample_count == 0
    assert result.window_start is None
    assert result.window_end is None
    assert result.mean_pnl_delta is None
    assert result.max_drawdown is None


def test_rolling_stats_invalid_window_raises(store):
    with pytest.raises(ValueError):
        compute_rolling_stats(ACCOUNT_A, [], window=0)


# ---------------------------------------------------------------------------
# Max drawdown -- the load-bearing invariant
# ---------------------------------------------------------------------------


def test_max_drawdown_is_real_peak_to_trough_not_first_last_approximation(store):
    """LOAD-BEARING: the series starts and ends at the SAME value (100),
    so a naive first-vs-last (or first-vs-min-of-endpoints) computation
    would report a drawdown of 0 -- completely missing the real interior
    peak-to-trough drop of 20 (peak 100 at t0, trough 80 at t4, 4 hours
    later). This is exactly the failure mode this module's own docstring
    calls out."""
    snapshots = _seed(store, ACCOUNT_A, [100.0, 90.0, 85.0, 95.0, 80.0, 100.0])

    result = compute_max_drawdown(snapshots)

    assert result is not None
    max_drawdown, duration_seconds = result
    assert max_drawdown == pytest.approx(20.0)
    assert duration_seconds == pytest.approx(4 * 3600.0)  # t4 - t0, 1-hour spacing


def test_max_drawdown_recovers_new_peak_after_trough(store):
    # peak 100 -> trough 50 (drawdown 50) -> new peak 150 -> trough 120
    # (drawdown 30, smaller) -- overall max drawdown must be 50, not 30.
    snapshots = _seed(store, ACCOUNT_A, [100.0, 50.0, 150.0, 120.0])

    result = compute_max_drawdown(snapshots)

    assert result is not None
    max_drawdown, _duration = result
    assert max_drawdown == pytest.approx(50.0)


def test_max_drawdown_insufficient_data_returns_none(store):
    snapshots = _seed(store, ACCOUNT_A, [100.0])
    assert compute_max_drawdown(snapshots) is None
    assert compute_max_drawdown([]) is None


# ---------------------------------------------------------------------------
# Pairwise correlation
# ---------------------------------------------------------------------------


def test_correlation_hand_computed_perfect_positive(store):
    # account_b's cumulative_pnl is exactly 2x account_a's at every
    # matching timestamp -- Pearson r must be exactly 1.0.
    values_a = [float(i) for i in range(MIN_CORRELATION_SAMPLES)]
    values_b = [2.0 * v for v in values_a]
    snapshots_a = _seed(store, ACCOUNT_A, values_a)
    snapshots_b = _seed(store, ACCOUNT_B, values_b)

    result = compute_pairwise_correlation(ACCOUNT_A, snapshots_a, ACCOUNT_B, snapshots_b)

    assert result.sample_count == MIN_CORRELATION_SAMPLES
    assert result.correlation == pytest.approx(1.0)


def test_correlation_hand_computed_perfect_negative(store):
    values_a = [float(i) for i in range(MIN_CORRELATION_SAMPLES)]
    values_b = [-3.0 * v + 7.0 for v in values_a]
    snapshots_a = _seed(store, ACCOUNT_A, values_a)
    snapshots_b = _seed(store, ACCOUNT_B, values_b)

    result = compute_pairwise_correlation(ACCOUNT_A, snapshots_a, ACCOUNT_B, snapshots_b)

    assert result.correlation == pytest.approx(-1.0)


def test_correlation_below_minimum_samples_is_none_not_a_fabricated_zero(store):
    # One fewer than the documented minimum overlapping-sample threshold.
    n = MIN_CORRELATION_SAMPLES - 1
    values_a = [float(i) for i in range(n)]
    values_b = [2.0 * v for v in values_a]
    snapshots_a = _seed(store, ACCOUNT_A, values_a)
    snapshots_b = _seed(store, ACCOUNT_B, values_b)

    result = compute_pairwise_correlation(ACCOUNT_A, snapshots_a, ACCOUNT_B, snapshots_b)

    assert result.sample_count == n
    assert result.correlation is None


def test_correlation_only_counts_real_overlapping_timestamps(store):
    # account_b's series starts 5 hours later than account_a's, so only
    # the last (MIN_CORRELATION_SAMPLES) of account_a's timestamps
    # actually overlap.
    start_a = datetime(2024, 1, 1, tzinfo=timezone.utc)
    start_b = start_a + timedelta(hours=5)
    values = [float(i) for i in range(MIN_CORRELATION_SAMPLES + 5)]
    snapshots_a = _seed(store, ACCOUNT_A, values, start=start_a)
    snapshots_b = _seed(store, ACCOUNT_B, values, start=start_b)

    result = compute_pairwise_correlation(ACCOUNT_A, snapshots_a, ACCOUNT_B, snapshots_b)

    assert result.sample_count == MIN_CORRELATION_SAMPLES
    assert result.correlation == pytest.approx(1.0)


def test_correlation_zero_variance_series_is_none_not_a_fabricated_value(store):
    # account_a is perfectly flat (zero variance) -- Pearson's r is
    # mathematically undefined here, must be None, never 0/NaN-as-zero.
    values_a = [5.0] * MIN_CORRELATION_SAMPLES
    values_b = [float(i) for i in range(MIN_CORRELATION_SAMPLES)]
    snapshots_a = _seed(store, ACCOUNT_A, values_a)
    snapshots_b = _seed(store, ACCOUNT_B, values_b)

    result = compute_pairwise_correlation(ACCOUNT_A, snapshots_a, ACCOUNT_B, snapshots_b)

    assert result.correlation is None
