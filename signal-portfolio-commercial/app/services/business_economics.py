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

AD-12 "Business economics and royalties" (dashboard_spec/screens/AD-12.md)
adds `get_business_economics` below: a real, bounded booked-revenue
query from actual `Subscription` rows. `compute_unit_contribution`/
`compute_break_even` above remain unused by that screen for now --
"All six inputs are required and none default to zero" and this build
has no real refunds/processor-fee/variable-cost/royalty data to supply
them with honestly, so AD-12's own Margin panel stays an explicit
UNSUPPORTED rather than calling these with fabricated zeros.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.billing import Subscription, SubscriptionState
from app.models.operating_cost import OperatingCost


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


#: A charge has genuinely been collected (or the customer remains
#: obligated under an already-collected period) in exactly these
#: states. PAST_DUE is included -- the prior period was paid; only the
#: NEXT charge is outstanding. DISPUTED is deliberately excluded: a
#: contested charge is not safely booked.
_REVENUE_RECOGNIZED_STATES: frozenset[SubscriptionState] = frozenset(
    {SubscriptionState.ACTIVE_PAID, SubscriptionState.PAST_DUE, SubscriptionState.CANCEL_AT_PERIOD_END}
)


@dataclass(frozen=True)
class RevenueByCurrency:
    currency: str
    booked_revenue_cents: int


@dataclass(frozen=True)
class BusinessEconomics:
    revenue_by_currency: list[RevenueByCurrency]


def get_business_economics(session: Session, *, tenant_id: str) -> BusinessEconomics:
    """AD-12 "Business economics and royalties" -- Revenue/retention.
    Booked only from real Subscription rows in a genuinely payment-
    recognized state, grouped by currency (never silently summed
    across currencies)."""
    rows = session.execute(
        select(Subscription.currency, func.sum(Subscription.price_cents))
        .where(Subscription.tenant_id == tenant_id, Subscription.state.in_(_REVENUE_RECOGNIZED_STATES))
        .group_by(Subscription.currency)
        .order_by(Subscription.currency)
    ).all()

    revenue_by_currency = [
        RevenueByCurrency(currency=currency, booked_revenue_cents=int(total or Decimal(0))) for currency, total in rows
    ]
    return BusinessEconomics(revenue_by_currency=revenue_by_currency)


#: Track 11 -- AD-12 "Margin": "compute margin = revenue minus costs, but
#: ONLY when both sides have real data for the same period; if costs
#: haven't been entered for a period, margin for that period must show
#: 'unavailable', never a silently-wrong number computed from partial/
#: zero costs." Mirrors `compute_unit_contribution`'s own "an omitted
#: cost is not the same as a verified zero cost" discipline at the top
#: of this module, and account_economics_v2.py's TWR/unrealized-P&L
#: "unavailable, never fabricated" pattern in signal-copier.
@dataclass(frozen=True)
class MarginRow:
    currency: str
    revenue_cents: int
    cost_cents: int
    margin_cents: int


@dataclass(frozen=True)
class MarginResult:
    period_start: datetime
    period_end: datetime
    #: One row per currency that has BOTH a booked-revenue figure and at
    #: least one real recorded cost row overlapping this period.
    rows: list[MarginRow]
    #: Currencies with booked revenue in this period but no recorded
    #: cost row overlapping it -- margin for these is explicitly
    #: unavailable, never computed as revenue-minus-zero.
    currencies_missing_costs: list[str]


def compute_margin_for_period(
    session: Session, *, tenant_id: str, period_start: datetime, period_end: datetime
) -> MarginResult:
    """Revenue: the same real, payment-recognized-state `Subscription`
    rows `get_business_economics` already sums, restricted to
    subscriptions whose `current_period_end` falls inside
    `[period_start, period_end]` -- the one real per-row date this table
    has to anchor "revenue for this period" to (see app/models/billing.py).
    Costs: real `OperatingCost` rows whose own billing period OVERLAPS
    the window (a cost that ran through part of the period counts,
    matching how a real accrual would be recognized). A currency present
    on the revenue side with zero overlapping cost rows is reported in
    `currencies_missing_costs`, never silently margined against zero."""
    if period_end < period_start:
        raise ValueError("period_end must not be before period_start")

    revenue_rows = session.execute(
        select(Subscription.currency, func.sum(Subscription.price_cents))
        .where(
            Subscription.tenant_id == tenant_id,
            Subscription.state.in_(_REVENUE_RECOGNIZED_STATES),
            Subscription.current_period_end >= period_start,
            Subscription.current_period_end <= period_end,
        )
        .group_by(Subscription.currency)
    ).all()
    revenue_by_currency: dict[str, int] = {currency: int(total or Decimal(0)) for currency, total in revenue_rows}

    cost_rows = session.execute(
        select(OperatingCost.currency, func.sum(OperatingCost.amount_cents))
        .where(
            OperatingCost.tenant_id == tenant_id,
            OperatingCost.period_start <= period_end,
            OperatingCost.period_end >= period_start,
        )
        .group_by(OperatingCost.currency)
    ).all()
    cost_by_currency: dict[str, int] = {currency: int(total or 0) for currency, total in cost_rows}

    rows: list[MarginRow] = []
    missing: list[str] = []
    for currency, revenue_cents in sorted(revenue_by_currency.items()):
        cost_cents = cost_by_currency.get(currency)
        if cost_cents is None:
            missing.append(currency)
            continue
        rows.append(
            MarginRow(
                currency=currency, revenue_cents=revenue_cents, cost_cents=cost_cents,
                margin_cents=revenue_cents - cost_cents,
            )
        )
    return MarginResult(period_start=period_start, period_end=period_end, rows=rows, currencies_missing_costs=missing)
