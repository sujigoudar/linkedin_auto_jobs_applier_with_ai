"""app/services/business_economics.py's own tests: the original
pure-function compute_unit_contribution/compute_break_even tests (no
database needed), plus AD-12 "Business economics and royalties"'s
get_business_economics tests (real Postgres, real tenant-scoped
session)."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.models.billing import ProductTier, Subscription, SubscriptionState
from app.services.business_economics import compute_break_even, compute_unit_contribution, get_business_economics


def test_unit_contribution_nets_out_taxes_refunds_and_processor_fees():
    result = compute_unit_contribution(
        revenue=Decimal("100"),
        taxes=Decimal("5"),
        refunds=Decimal("2"),
        processor_fees=Decimal("3"),
        variable_costs=Decimal("20"),
        royalties=Decimal("10"),
    )
    # 100 - 5 - 2 - 3 - 20 - 10 = 60
    assert result.value == Decimal("60")
    assert result.is_warning is False


def test_a_zero_contribution_is_a_warning():
    result = compute_unit_contribution(
        revenue=Decimal("10"),
        taxes=Decimal("0"),
        refunds=Decimal("0"),
        processor_fees=Decimal("0"),
        variable_costs=Decimal("10"),
        royalties=Decimal("0"),
    )
    assert result.value == Decimal("0")
    assert result.is_warning is True


def test_a_negative_contribution_is_a_warning():
    result = compute_unit_contribution(
        revenue=Decimal("10"),
        taxes=Decimal("0"),
        refunds=Decimal("0"),
        processor_fees=Decimal("0"),
        variable_costs=Decimal("50"),
        royalties=Decimal("0"),
    )
    assert result.value < 0
    assert result.is_warning is True


def test_break_even_divides_fixed_costs_by_positive_contribution():
    contribution = compute_unit_contribution(
        revenue=Decimal("100"),
        taxes=Decimal("0"),
        refunds=Decimal("0"),
        processor_fees=Decimal("0"),
        variable_costs=Decimal("50"),
        royalties=Decimal("0"),
    )
    result = compute_break_even(approved_fixed_costs=Decimal("500"), unit_contribution=contribution)
    assert result.units_to_break_even == Decimal("10")
    assert result.is_warning is False


def test_break_even_is_a_warning_not_a_number_when_contribution_is_non_positive():
    contribution = compute_unit_contribution(
        revenue=Decimal("10"),
        taxes=Decimal("0"),
        refunds=Decimal("0"),
        processor_fees=Decimal("0"),
        variable_costs=Decimal("50"),
        royalties=Decimal("0"),
    )
    result = compute_break_even(approved_fixed_costs=Decimal("500"), unit_contribution=contribution)
    assert result.units_to_break_even is None
    assert result.is_warning is True


def _subscription(tenant_id, state, price_cents, currency="usd"):
    return Subscription(
        tenant_id=tenant_id,
        tier=ProductTier.ALERTS_ONE,
        state=state,
        price_cents=price_cents,
        currency=currency,
        current_period_end=datetime.now(timezone.utc) + timedelta(days=30),
    )


def test_no_subscriptions_means_no_revenue_rows(db_session):
    economics = get_business_economics(db_session, tenant_id="tenant-a")
    assert economics.revenue_by_currency == []


def test_active_paid_subscriptions_are_booked_as_revenue(db_session):
    db_session.add(_subscription("tenant-a", SubscriptionState.ACTIVE_PAID, 3900))
    db_session.add(_subscription("tenant-a", SubscriptionState.ACTIVE_PAID, 9900))
    db_session.commit()

    economics = get_business_economics(db_session, tenant_id="tenant-a")
    assert len(economics.revenue_by_currency) == 1
    assert economics.revenue_by_currency[0].currency == "usd"
    assert economics.revenue_by_currency[0].booked_revenue_cents == 13800


def test_unpaid_subscriptions_are_never_counted_as_revenue(db_session):
    db_session.add(_subscription("tenant-a", SubscriptionState.PENDING_PAYMENT, 3900))
    db_session.add(_subscription("tenant-a", SubscriptionState.TRIAL_AUTHORIZED, 9900))
    db_session.commit()

    economics = get_business_economics(db_session, tenant_id="tenant-a")
    assert economics.revenue_by_currency == []


def test_disputed_subscriptions_are_never_counted_as_revenue(db_session):
    db_session.add(_subscription("tenant-a", SubscriptionState.DISPUTED, 3900))
    db_session.commit()

    economics = get_business_economics(db_session, tenant_id="tenant-a")
    assert economics.revenue_by_currency == []


def test_revenue_is_scoped_to_the_callers_own_tenant(db_session):
    db_session.add(_subscription("tenant-a", SubscriptionState.ACTIVE_PAID, 3900))
    db_session.add(_subscription("tenant-b", SubscriptionState.ACTIVE_PAID, 99999))
    db_session.commit()

    economics = get_business_economics(db_session, tenant_id="tenant-a")
    assert len(economics.revenue_by_currency) == 1
    assert economics.revenue_by_currency[0].booked_revenue_cents == 3900


def test_revenue_is_grouped_by_currency_never_mixed(db_session):
    db_session.add(_subscription("tenant-a", SubscriptionState.ACTIVE_PAID, 3900, currency="usd"))
    db_session.add(_subscription("tenant-a", SubscriptionState.ACTIVE_PAID, 5000, currency="eur"))
    db_session.commit()

    economics = get_business_economics(db_session, tenant_id="tenant-a")
    by_currency = {row.currency: row.booked_revenue_cents for row in economics.revenue_by_currency}
    assert by_currency == {"usd": 3900, "eur": 5000}
