"""Business economics -- CP-071 "Business versus investment P&L" and
CP-036 "Fees and subscription cost": spec/docs/08_products_billing_and_entitlements.md's
"Business economics" section, verbatim formula: "Unit contribution =
revenue net of taxes/refunds/processor fees minus attributable variable
costs and royalties. Break-even uses approved fixed costs divided by
positive unit contribution; negative/unknown values remain a warning."

Deliberately separate from anything in app/models/ledger.py: "Subscription
revenue is not investment P&L" (same doc) -- this module never reads
trading P&L and has no way to blend the two, which is the whole point
of keeping them apart.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class UnitContribution:
    value: Decimal
    #: True when `value` is zero or negative -- "negative/unknown values
    #: remain a warning," never hidden or silently treated as break-even.
    is_warning: bool


def compute_unit_contribution(
    *,
    revenue: Decimal,
    taxes: Decimal,
    refunds: Decimal,
    processor_fees: Decimal,
    variable_costs: Decimal,
    royalties: Decimal,
) -> UnitContribution:
    """revenue net of taxes/refunds/processor fees, minus attributable
    variable costs and royalties. All six inputs are required and none
    default to zero -- an omitted cost is not the same as a verified
    zero cost, and this function has no way to tell the difference if a
    caller silently passes 0 for something it never actually measured."""
    net_revenue = revenue - taxes - refunds - processor_fees
    value = net_revenue - variable_costs - royalties
    return UnitContribution(value=value, is_warning=value <= 0)


@dataclass(frozen=True)
class BreakEvenResult:
    #: Number of units needed to cover fixed costs, or None when
    #: contribution is non-positive -- "negative/unknown values remain a
    #: warning," so there is no finite break-even point to report.
    units_to_break_even: Decimal | None
    is_warning: bool


def compute_break_even(*, approved_fixed_costs: Decimal, unit_contribution: UnitContribution) -> BreakEvenResult:
    """Break-even = approved fixed costs / positive unit contribution.
    Refuses to divide by a non-positive contribution -- there is no
    finite number of units that "breaks even" when each additional unit
    loses money, and reporting one anyway would be worse than reporting
    nothing."""
    if unit_contribution.is_warning:
        return BreakEvenResult(units_to_break_even=None, is_warning=True)
    return BreakEvenResult(units_to_break_even=approved_fixed_costs / unit_contribution.value, is_warning=False)
