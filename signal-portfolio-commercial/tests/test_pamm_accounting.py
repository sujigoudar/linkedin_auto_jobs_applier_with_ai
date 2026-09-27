"""app/services/pamm_accounting.py -- pure function tests, no database
needed."""
from decimal import Decimal

import pytest

from app.services.pamm_accounting import (
    CashflowDuringSimpleIntervalError,
    InvalidDealingPriceError,
    compute_simple_hwm_fee,
    next_high_water_mark,
    units_for_cashflow,
)


def test_fee_is_zero_below_the_high_water_mark():
    fee = compute_simple_hwm_fee(Decimal("95"), Decimal("100"), Decimal("0.2"), had_cashflow=False)
    assert fee == Decimal("0")


def test_fee_is_charged_only_on_the_amount_above_the_high_water_mark():
    fee = compute_simple_hwm_fee(Decimal("110"), Decimal("100"), Decimal("0.2"), had_cashflow=False)
    assert fee == Decimal("2")  # 20% of (110 - 100)


def test_cashflow_during_the_interval_is_rejected_not_silently_approximated():
    with pytest.raises(CashflowDuringSimpleIntervalError):
        compute_simple_hwm_fee(Decimal("110"), Decimal("100"), Decimal("0.2"), had_cashflow=True)


def test_high_water_mark_never_decreases_after_a_losing_interval():
    new_hwm = next_high_water_mark(Decimal("100"), Decimal("90"))
    assert new_hwm == Decimal("100")


def test_high_water_mark_rises_after_a_new_peak():
    new_hwm = next_high_water_mark(Decimal("100"), Decimal("108"))  # post-fee NAV
    assert new_hwm == Decimal("108")


def test_a_full_charge_then_carry_cycle_never_double_charges():
    """A losing month after a winning one charges zero, and the next
    winning month is measured against the CARRIED-FORWARD high-water
    mark, not the lower intervening NAV."""
    hwm = Decimal("100")
    fee1 = compute_simple_hwm_fee(Decimal("110"), hwm, Decimal("0.2"), had_cashflow=False)
    hwm = next_high_water_mark(hwm, Decimal("110") - fee1)

    fee2 = compute_simple_hwm_fee(Decimal("105"), hwm, Decimal("0.2"), had_cashflow=False)
    assert fee2 == Decimal("0")
    hwm = next_high_water_mark(hwm, Decimal("105") - fee2)
    assert hwm == Decimal("108")  # unchanged from post-fee NAV of the winning month


def test_units_for_a_deposit_are_amount_over_dealing_nav():
    units = units_for_cashflow(Decimal("1000"), Decimal("10"))
    assert units == Decimal("100")


def test_a_non_positive_dealing_price_is_rejected():
    with pytest.raises(InvalidDealingPriceError):
        units_for_cashflow(Decimal("1000"), Decimal("0"))
    with pytest.raises(InvalidDealingPriceError):
        units_for_cashflow(Decimal("1000"), Decimal("-5"))
