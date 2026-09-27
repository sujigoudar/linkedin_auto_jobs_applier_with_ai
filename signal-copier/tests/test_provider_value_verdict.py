"""app/provider_value.py's verdict decision tree and its
`_cycles_elapsed` cost-to-date estimate, in isolation from the FIFO
replay itself."""
from datetime import date

import pytest

from app.provider_value import (
    VERDICT_CANCEL_CANDIDATE,
    VERDICT_INSUFFICIENT_DATA,
    VERDICT_KEEP,
    VERDICT_UNDERPERFORMING_FREE,
    ProviderValue,
    _cycles_elapsed,
    _verdict,
)


def _totals(*, closing_fills, winning_closing_fills, realized_pnl, gross_profit=0.0, gross_loss=0.0):
    return ProviderValue(
        source="x", analyst=None, asset_class="",
        closing_fills=closing_fills, winning_closing_fills=winning_closing_fills,
        realized_pnl=realized_pnl, gross_profit=gross_profit, gross_loss=gross_loss,
    )


def test_below_min_sample_size_is_insufficient_data_regardless_of_performance():
    totals = _totals(closing_fills=3, winning_closing_fills=3, realized_pnl=1000.0, gross_profit=1000.0)
    verdict, _ = _verdict(totals, None, min_sample_size=10, win_rate_threshold=0.4, profit_factor_threshold=1.0)
    assert verdict == VERDICT_INSUFFICIENT_DATA


def test_good_performance_with_no_subscription_is_keep():
    totals = _totals(closing_fills=20, winning_closing_fills=15, realized_pnl=500.0, gross_profit=600.0, gross_loss=100.0)
    verdict, _ = _verdict(totals, None, min_sample_size=10, win_rate_threshold=0.4, profit_factor_threshold=1.0)
    assert verdict == VERDICT_KEEP


def test_poor_performance_with_no_subscription_is_underperforming_free():
    totals = _totals(closing_fills=20, winning_closing_fills=2, realized_pnl=-500.0, gross_profit=50.0, gross_loss=550.0)
    verdict, _ = _verdict(totals, None, min_sample_size=10, win_rate_threshold=0.4, profit_factor_threshold=1.0)
    assert verdict == VERDICT_UNDERPERFORMING_FREE


def test_poor_performance_with_a_paid_subscription_and_negative_net_value_is_cancel_candidate():
    totals = _totals(closing_fills=20, winning_closing_fills=2, realized_pnl=-500.0, gross_profit=50.0, gross_loss=550.0)
    subscription = {"cost_amount": 50.0, "billing_cycle": "monthly", "subscribed_since": date.today().isoformat()}
    verdict, detail = _verdict(totals, subscription, min_sample_size=10, win_rate_threshold=0.4, profit_factor_threshold=1.0)
    assert verdict == VERDICT_CANCEL_CANDIDATE
    assert detail["net_value"] < 0


def test_high_profit_factor_alone_is_not_enough_to_flag_underperforming():
    """"Underperforming" requires BOTH win_rate and profit_factor to be
    poor -- a low win rate with a strongly positive profit_factor (a few
    big winners, many small losers) is a legitimate, still-good trading
    pattern, not one this heuristic should flag."""
    totals = _totals(closing_fills=20, winning_closing_fills=2, realized_pnl=500.0, gross_profit=550.0, gross_loss=50.0)
    subscription = {"cost_amount": 10.0, "billing_cycle": "monthly", "subscribed_since": date.today().isoformat()}
    verdict, _ = _verdict(totals, subscription, min_sample_size=10, win_rate_threshold=0.4, profit_factor_threshold=1.0)
    assert verdict == VERDICT_KEEP


def test_underperforming_but_still_net_positive_after_cost_is_kept_not_cancelled():
    """The performance thresholds alone don't cancel a subscription -- the
    cost still has to actually not be worth it (net_value < 0). A
    profit_factor of 1.4 is "underperforming" against a 1.5 threshold even
    though realized_pnl is still positive (140 gross profit, 100 gross
    loss -- a genuinely different scenario than the profit_factor<1 case,
    where realized_pnl is mathematically guaranteed negative)."""
    totals = _totals(closing_fills=20, winning_closing_fills=2, realized_pnl=40.0, gross_profit=140.0, gross_loss=100.0)
    subscription = {"cost_amount": 10.0, "billing_cycle": "monthly", "subscribed_since": date.today().isoformat()}
    verdict, detail = _verdict(totals, subscription, min_sample_size=10, win_rate_threshold=0.4, profit_factor_threshold=1.5)
    assert verdict == VERDICT_KEEP
    assert detail["net_value"] == pytest.approx(30.0)  # 40 realized - 10 cost


def test_free_subscription_cost_amount_zero_is_treated_the_same_as_no_subscription():
    totals = _totals(closing_fills=20, winning_closing_fills=2, realized_pnl=-500.0, gross_profit=50.0, gross_loss=550.0)
    subscription = {"cost_amount": 0.0, "billing_cycle": "free", "subscribed_since": date.today().isoformat()}
    verdict, _ = _verdict(totals, subscription, min_sample_size=10, win_rate_threshold=0.4, profit_factor_threshold=1.0)
    assert verdict == VERDICT_UNDERPERFORMING_FREE


# --- _cycles_elapsed ---


def test_monthly_cycles_elapsed_counts_the_current_partial_month_as_one():
    assert _cycles_elapsed("2026-01-15", "monthly", now=date(2026, 1, 20)) == 1


def test_monthly_cycles_elapsed_counts_full_months_since():
    assert _cycles_elapsed("2026-01-15", "monthly", now=date(2026, 4, 20)) == 4


def test_annual_cycles_elapsed():
    assert _cycles_elapsed("2024-06-01", "annual", now=date(2026, 6, 2)) == 3


def test_one_time_is_always_exactly_one_cycle():
    assert _cycles_elapsed("2020-01-01", "one_time", now=date(2026, 1, 1)) == 1


def test_free_billing_cycle_is_zero_cycles():
    assert _cycles_elapsed("2020-01-01", "free", now=date(2026, 1, 1)) == 0


def test_a_subscribed_since_date_in_the_future_is_zero_cycles_not_negative():
    assert _cycles_elapsed("2030-01-01", "monthly", now=date(2026, 1, 1)) == 0
