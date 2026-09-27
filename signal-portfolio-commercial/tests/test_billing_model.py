"""app/models/billing.py -- basic persistence and RLS scoping."""
from datetime import datetime, timedelta, timezone

from app.db import set_tenant_scope
from app.models.billing import ProductTier, Subscription, SubscriptionState, TEST_MODE_MONTHLY_PRICE_CENTS


def _subscription(tenant_id, subscription_id, tier=ProductTier.ALERTS_ONE):
    return Subscription(
        subscription_id=subscription_id,
        tenant_id=tenant_id,
        tier=tier,
        state=SubscriptionState.ACTIVE_PAID,
        price_cents=TEST_MODE_MONTHLY_PRICE_CENTS[tier],
        currency="usd",
        current_period_end=datetime.now(timezone.utc) + timedelta(days=30),
    )


def test_a_subscription_persists_its_fields(db_session):
    db_session.add(_subscription("tenant-a", "sub-1"))
    db_session.commit()

    fetched = db_session.get(Subscription, "sub-1")
    assert fetched.tier == ProductTier.ALERTS_ONE
    assert fetched.price_cents == 3900
    assert fetched.state == SubscriptionState.ACTIVE_PAID


def test_subscriptions_are_row_level_security_scoped(db_session, tenant_session_factory):
    db_session.add(_subscription("tenant-a", "sub-a"))
    db_session.add(_subscription("tenant-b", "sub-b"))
    db_session.commit()

    session = tenant_session_factory()
    try:
        set_tenant_scope(session, "tenant-a")
        rows = session.query(Subscription).all()
        assert [r.subscription_id for r in rows] == ["sub-a"]
    finally:
        session.rollback()
        session.close()


def test_every_tier_has_a_defined_test_mode_price():
    for tier in ProductTier:
        assert tier in TEST_MODE_MONTHLY_PRICE_CENTS
        assert TEST_MODE_MONTHLY_PRICE_CENTS[tier] >= 0
