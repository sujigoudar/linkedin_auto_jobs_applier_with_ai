"""Track 11 -- compute_margin_for_period's "unavailable when costs
haven't been entered" discipline: margin is computed for a currency
ONLY when both real revenue and at least one real recorded cost exist
for that currency in the same period; otherwise that currency is
reported in `currencies_missing_costs`, NEVER as a number computed
against zero."""
from datetime import datetime, timedelta, timezone

from app.models.billing import ProductTier, Subscription, SubscriptionState
from app.models.operating_cost import OperatingCostCategory
from app.services.business_economics import compute_margin_for_period
from app.services.operating_cost import create_operating_cost

_PERIOD_START = datetime(2026, 1, 1, tzinfo=timezone.utc)
_PERIOD_END = datetime(2026, 1, 31, 23, 59, 59, tzinfo=timezone.utc)
_MID_PERIOD = datetime(2026, 1, 15, tzinfo=timezone.utc)


def _subscription(tenant_id, price_cents, currency="usd", current_period_end=_MID_PERIOD):
    return Subscription(
        tenant_id=tenant_id,
        tier=ProductTier.ALERTS_ONE,
        state=SubscriptionState.ACTIVE_PAID,
        price_cents=price_cents,
        currency=currency,
        current_period_end=current_period_end,
    )


def test_margin_unavailable_when_no_costs_recorded_for_the_period(db_session):
    db_session.add(_subscription("tenant-a", 9900))
    db_session.commit()

    result = compute_margin_for_period(
        db_session, tenant_id="tenant-a", period_start=_PERIOD_START, period_end=_PERIOD_END
    )
    assert result.rows == []
    assert result.currencies_missing_costs == ["usd"]


def test_margin_computed_when_both_revenue_and_costs_exist(db_session):
    db_session.add(_subscription("tenant-a", 9900))
    db_session.commit()
    create_operating_cost(
        db_session, tenant_id="tenant-a", category=OperatingCostCategory.INFRASTRUCTURE, vendor="AWS",
        amount_cents=3000, period_start=_PERIOD_START, period_end=_PERIOD_END,
    )
    db_session.commit()

    result = compute_margin_for_period(
        db_session, tenant_id="tenant-a", period_start=_PERIOD_START, period_end=_PERIOD_END
    )
    assert result.currencies_missing_costs == []
    assert len(result.rows) == 1
    row = result.rows[0]
    assert row.currency == "usd"
    assert row.revenue_cents == 9900
    assert row.cost_cents == 3000
    assert row.margin_cents == 6900


def test_margin_never_mixes_currencies(db_session):
    db_session.add(_subscription("tenant-a", 9900, currency="usd"))
    db_session.add(_subscription("tenant-a", 5000, currency="eur"))
    db_session.commit()
    # Only USD costs are recorded -- EUR revenue must stay "unavailable",
    # never margined against a USD cost or against zero.
    create_operating_cost(
        db_session, tenant_id="tenant-a", category=OperatingCostCategory.INFRASTRUCTURE, vendor="AWS",
        amount_cents=3000, currency="usd", period_start=_PERIOD_START, period_end=_PERIOD_END,
    )
    db_session.commit()

    result = compute_margin_for_period(
        db_session, tenant_id="tenant-a", period_start=_PERIOD_START, period_end=_PERIOD_END
    )
    assert result.currencies_missing_costs == ["eur"]
    assert len(result.rows) == 1
    assert result.rows[0].currency == "usd"


def test_margin_is_scoped_to_the_requested_period(db_session):
    # Revenue whose current_period_end falls OUTSIDE the requested
    # window must not be counted -- costs recorded for the window alone
    # must not be margined against unrelated revenue.
    outside_window = _PERIOD_END + timedelta(days=60)
    db_session.add(_subscription("tenant-a", 9900, current_period_end=outside_window))
    db_session.commit()
    create_operating_cost(
        db_session, tenant_id="tenant-a", category=OperatingCostCategory.INFRASTRUCTURE, vendor="AWS",
        amount_cents=3000, period_start=_PERIOD_START, period_end=_PERIOD_END,
    )
    db_session.commit()

    result = compute_margin_for_period(
        db_session, tenant_id="tenant-a", period_start=_PERIOD_START, period_end=_PERIOD_END
    )
    assert result.rows == []
    assert result.currencies_missing_costs == []


def test_margin_is_scoped_to_the_callers_own_tenant(db_session):
    db_session.add(_subscription("tenant-a", 9900))
    db_session.add(_subscription("tenant-b", 50000))
    db_session.commit()
    create_operating_cost(
        db_session, tenant_id="tenant-a", category=OperatingCostCategory.INFRASTRUCTURE, vendor="AWS",
        amount_cents=3000, period_start=_PERIOD_START, period_end=_PERIOD_END,
    )
    create_operating_cost(
        db_session, tenant_id="tenant-b", category=OperatingCostCategory.INFRASTRUCTURE, vendor="OCI",
        amount_cents=100000, period_start=_PERIOD_START, period_end=_PERIOD_END,
    )
    db_session.commit()

    result = compute_margin_for_period(
        db_session, tenant_id="tenant-a", period_start=_PERIOD_START, period_end=_PERIOD_END
    )
    assert len(result.rows) == 1
    assert result.rows[0].revenue_cents == 9900
    assert result.rows[0].cost_cents == 3000
