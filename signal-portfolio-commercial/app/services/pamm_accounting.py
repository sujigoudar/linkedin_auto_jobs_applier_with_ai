"""PAMM unit/NAV accounting and the simple test-only high-water-mark
fee, per spec/docs/07_managed_accounts_pamm_mam.md's "PAMM/unit
accounting" section. Simulation only, same scope boundary as
app/services/mam_allocation.py -- "Production uses the actual
contractual broker rule instead."

The fee formula here is explicitly the spec's OWN "simple test-only
no-cashflow interval" case: "fee = positive part of (pre-fee NAV -
previous fee-adjusted HWM) x approved rate, new HWM = max(previous HWM,
post-fee NAV)... this simple formula must reject those inputs [deposits/
withdrawals] rather than generate a wrong fee." `compute_simple_hwm_fee`
therefore takes an explicit `had_cashflow` flag and raises rather than
computing anything when it's True -- a real deposit/withdrawal-aware
fee (unit-series/equalization) is out of scope here, not silently
approximated.
"""
from __future__ import annotations

from decimal import ROUND_HALF_EVEN, Decimal

#: Currency minor-unit precision this simulation-only fee is quantized
#: to -- two decimal places (cents), per this module's own docstring:
#: a real deployment would quantize to the actual contractual broker
#: rule's own precision/rounding convention instead.
_FEE_QUANTIZE = Decimal("0.01")


class CashflowDuringSimpleIntervalError(Exception):
    pass


def compute_simple_hwm_fee(pre_fee_nav: Decimal, previous_hwm: Decimal, rate: Decimal, *, had_cashflow: bool) -> Decimal:
    """The simple, no-cashflow-interval high-water-mark performance fee.
    Trading losses carry forward (a below-HWM interval simply charges
    zero, never a negative fee): "new HWM = max(previous HWM, post-fee
    NAV)" -- see `next_high_water_mark` below, which a caller applies
    to `pre_fee_nav - fee` (the post-fee NAV) after this call.

    The raw `positive_part * rate` product is an arbitrary-precision
    Decimal that can carry far more digits than any real currency has
    minor units (e.g. a rate of 1/3 against a clean NAV). Quantized here
    to 2 decimal places (cents) using ROUND_HALF_EVEN ("banker's
    rounding" -- ties round to the nearest even digit, e.g. 0.125 ->
    0.12 and 0.135 -> 0.14) so this simulation never reports a fee with
    sub-cent precision that no real broker statement would ever show;
    production uses the actual contractual broker rule's own rounding
    convention instead (this module's own docstring above)."""
    if had_cashflow:
        raise CashflowDuringSimpleIntervalError(
            "a deposit or withdrawal occurred during this interval -- the simple no-cashflow HWM formula "
            "does not apply; use a real unit-series/equalization-aware calculation instead of guessing"
        )
    positive_part = max(Decimal(0), pre_fee_nav - previous_hwm)
    return (positive_part * rate).quantize(_FEE_QUANTIZE, rounding=ROUND_HALF_EVEN)


def next_high_water_mark(previous_hwm: Decimal, post_fee_nav: Decimal) -> Decimal:
    """A high-water mark never decreases -- a losing interval (or an
    interval below the prior peak) simply carries the old HWM forward."""
    return max(previous_hwm, post_fee_nav)


class InvalidDealingPriceError(Exception):
    pass


def units_for_cashflow(amount: Decimal, dealing_nav_per_unit: Decimal) -> Decimal:
    """Units bought (a deposit) or consumed (a withdrawal) at the
    approved dealing NAV -- the same conversion either direction; the
    caller's own bookkeeping decides sign/direction. Refuses a
    non-positive dealing price outright rather than dividing by zero or
    returning a nonsensical negative/undefined unit count."""
    if dealing_nav_per_unit <= 0:
        raise InvalidDealingPriceError(f"dealing NAV per unit must be positive, got {dealing_nav_per_unit}")
    return amount / dealing_nav_per_unit
