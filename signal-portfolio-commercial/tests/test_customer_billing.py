"""app/services/customer_billing.py -- CU-11's real, tenant-scoped billing
read. `get_own_billing_state`/`CustomerBillingState` have no dedicated test
file at all prior to this one: the only coverage was incidental, through
`app/models/billing.py`'s own persistence tests."""
import dataclasses
from datetime import datetime, timedelta, timezone

import pytest

from app.db import set_tenant_scope
from app.models.billing import ProductTier, Subscription, SubscriptionState
from app.services.customer_billing import CustomerBillingState, get_own_billing_state


def _subscription(tenant_id, subscription_id, *, state, created_at, tier=ProductTier.ALERTS_ONE, price_cents=3900):
    return Subscription(
        subscription_id=subscription_id,
        tenant_id=tenant_id,
        tier=tier,
        state=state,
        price_cents=price_cents,
        currency="usd",
        current_period_end=datetime.now(timezone.utc) + timedelta(days=30),
        created_at=created_at,
    )


def test_a_tenant_with_no_subscription_at_all_gets_an_empty_state(db_session):
    state = get_own_billing_state(db_session, tenant_id="tenant-a")

    assert state.subscriptions == []
    assert state.current is None
    assert state.authorizes_new_entry is None
    assert state.authorizes_risk_reducing_management is None


def test_the_current_subscription_is_the_most_recently_created_row_not_the_first_inserted(db_session):
    """`current` must pick by `created_at` recency, never insertion/row order
    -- the dataclass's own docstring is explicit that this is the only real
    signal this build has, since more than one terminal-state row can
    coexist."""
    now = datetime.now(timezone.utc)
    older = _subscription(
        "tenant-a", "sub-older", state=SubscriptionState.ENDED, created_at=now - timedelta(days=60)
    )
    newer = _subscription(
        "tenant-a", "sub-newer", state=SubscriptionState.ACTIVE_PAID, created_at=now - timedelta(days=1)
    )
    # Insert the newer row FIRST, so a bug that trusted insertion order
    # instead of `created_at` would silently pick the wrong one.
    db_session.add(newer)
    db_session.add(older)
    db_session.commit()

    state = get_own_billing_state(db_session, tenant_id="tenant-a")

    assert state.current is not None
    assert state.current.subscription_id == "sub-newer"
    assert [s.subscription_id for s in state.subscriptions] == ["sub-newer", "sub-older"]


def test_a_currently_active_paid_subscription_authorizes_new_entry(db_session):
    now = datetime.now(timezone.utc)
    db_session.add(_subscription("tenant-a", "sub-a", state=SubscriptionState.ACTIVE_PAID, created_at=now))
    db_session.commit()

    state = get_own_billing_state(db_session, tenant_id="tenant-a")

    assert state.authorizes_new_entry is True
    assert state.authorizes_risk_reducing_management is True


def test_a_past_due_subscription_blocks_new_entry_but_still_authorizes_management(db_session):
    """Billing trouble on an already-active subscription must never be
    conflated with a mandate revocation -- see app/services/entitlement.py's
    own entitlement/safety separation, which this property delegates to
    rather than reimplements."""
    now = datetime.now(timezone.utc)
    db_session.add(_subscription("tenant-a", "sub-a", state=SubscriptionState.PAST_DUE, created_at=now))
    db_session.commit()

    state = get_own_billing_state(db_session, tenant_id="tenant-a")

    assert state.authorizes_new_entry is False
    assert state.authorizes_risk_reducing_management is True


def test_a_subscription_that_never_left_pending_payment_authorizes_neither(db_session):
    """PENDING_PAYMENT has never represented an admitted customer
    relationship -- there is no existing exposure to protect, so unlike
    every other non-paid-through state, management is also not
    authorized here (app/services/entitlement.py's own
    `_MANAGEMENT_AUTHORIZED_STATES` carve-out)."""
    now = datetime.now(timezone.utc)
    db_session.add(_subscription("tenant-a", "sub-a", state=SubscriptionState.PENDING_PAYMENT, created_at=now))
    db_session.commit()

    state = get_own_billing_state(db_session, tenant_id="tenant-a")

    assert state.authorizes_new_entry is False
    assert state.authorizes_risk_reducing_management is False


def test_billing_state_is_tenant_scoped_even_bypassing_rls(db_session):
    """`get_own_billing_state` filters explicitly on `tenant_id` in its own
    query -- this must hold even for a session with no RLS tenant scope
    set (e.g. an internal/admin session), not merely rely on RLS as the
    only backstop."""
    now = datetime.now(timezone.utc)
    db_session.add(_subscription("tenant-a", "sub-a", state=SubscriptionState.ACTIVE_PAID, created_at=now))
    db_session.add(_subscription("tenant-b", "sub-b", state=SubscriptionState.ACTIVE_PAID, created_at=now))
    db_session.commit()

    state = get_own_billing_state(db_session, tenant_id="tenant-a")

    assert [s.subscription_id for s in state.subscriptions] == ["sub-a"]


def test_billing_state_also_holds_under_real_row_level_security_scoping(db_session, tenant_session_factory):
    now = datetime.now(timezone.utc)
    db_session.add(_subscription("tenant-a", "sub-a", state=SubscriptionState.ACTIVE_PAID, created_at=now))
    db_session.add(_subscription("tenant-b", "sub-b", state=SubscriptionState.ACTIVE_PAID, created_at=now))
    db_session.commit()

    session = tenant_session_factory()
    try:
        set_tenant_scope(session, "tenant-b")
        state = get_own_billing_state(session, tenant_id="tenant-b")
        assert [s.subscription_id for s in state.subscriptions] == ["sub-b"]
    finally:
        session.rollback()
        session.close()


def test_customer_billing_state_is_constructible_directly_as_a_frozen_dataclass(db_session):
    """Guards the dataclass's own shape -- `CustomerBillingState` is a
    plain, frozen value object over a `subscriptions` list, not something
    that must be built only through `get_own_billing_state`."""
    now = datetime.now(timezone.utc)
    sub = _subscription("tenant-a", "sub-a", state=SubscriptionState.ACTIVE_PAID, created_at=now)
    state = CustomerBillingState(subscriptions=[sub])

    assert state.current is sub
    assert state.authorizes_new_entry is True


def test_customer_billing_state_is_actually_immutable():
    """`@dataclass(frozen=True)` is a real guarantee this build relies
    on for a value object handed back from a read path -- assigning to
    a field must raise, not silently succeed, or callers could mutate a
    "canonical entitlement state" snapshot out from under each other."""
    state = CustomerBillingState(subscriptions=[])

    with pytest.raises(dataclasses.FrozenInstanceError):
        state.subscriptions = []  # type: ignore[misc]
