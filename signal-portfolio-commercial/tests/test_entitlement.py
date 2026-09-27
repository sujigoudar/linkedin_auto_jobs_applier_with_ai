"""app/services/entitlement.py -- the payment/safety separation rule.
Pure function tests (no database needed): every SubscriptionState must
classify consistently under both authorization questions."""
from datetime import datetime, timedelta, timezone

import pytest

from app.models.billing import ProductTier, Subscription, SubscriptionState
from app.services.entitlement import authorizes_new_entry, authorizes_risk_reducing_management


def _subscription(state: SubscriptionState) -> Subscription:
    return Subscription(
        tenant_id="tenant-a",
        tier=ProductTier.ALERTS_ONE,
        state=state,
        price_cents=3900,
        currency="usd",
        current_period_end=datetime.now(timezone.utc) + timedelta(days=1),
    )


@pytest.mark.parametrize(
    "state",
    [SubscriptionState.ACTIVE_PAID, SubscriptionState.CANCEL_AT_PERIOD_END, SubscriptionState.TRIAL_AUTHORIZED],
)
def test_new_entry_authorized_states(state):
    assert authorizes_new_entry(_subscription(state)) is True


@pytest.mark.parametrize(
    "state",
    [
        SubscriptionState.PENDING_PAYMENT,
        SubscriptionState.PAST_DUE,
        SubscriptionState.SUSPENDED_NEW_ENTRIES,
        SubscriptionState.ENDED,
        SubscriptionState.DISPUTED,
        SubscriptionState.MANUAL_REVIEW,
    ],
)
def test_new_entry_not_authorized_states(state):
    assert authorizes_new_entry(_subscription(state)) is False


@pytest.mark.parametrize(
    "state",
    [
        SubscriptionState.TRIAL_AUTHORIZED,
        SubscriptionState.ACTIVE_PAID,
        SubscriptionState.CANCEL_AT_PERIOD_END,
        SubscriptionState.PAST_DUE,
        SubscriptionState.SUSPENDED_NEW_ENTRIES,
        SubscriptionState.ENDED,
        SubscriptionState.DISPUTED,
        SubscriptionState.MANUAL_REVIEW,
    ],
)
def test_risk_reducing_management_is_authorized_for_every_state_except_pending_payment(state):
    """The core safety-separation rule: a payment problem on an already-
    admitted subscription (PAST_DUE, SUSPENDED_NEW_ENTRIES, DISPUTED,
    MANUAL_REVIEW) never revokes the ability to protect/manage exposure
    that already exists."""
    assert authorizes_risk_reducing_management(_subscription(state)) is True


def test_risk_reducing_management_is_not_authorized_for_pending_payment():
    """PENDING_PAYMENT never represented an admitted relationship, so
    there is no pre-existing exposure this state could be revoking
    management authority over."""
    assert authorizes_risk_reducing_management(_subscription(SubscriptionState.PENDING_PAYMENT)) is False


def test_every_subscription_state_is_classified_by_both_functions():
    """No SubscriptionState may silently fall through unclassified --
    every member must produce a definite True/False from both
    functions."""
    for state in SubscriptionState:
        sub = _subscription(state)
        assert authorizes_new_entry(sub) in (True, False)
        assert authorizes_risk_reducing_management(sub) in (True, False)


def test_this_module_never_imports_anything_that_can_close_a_position():
    """Structural guard: entitlement.py must have no dependency on the
    publication/broker-adapter modules that can build a CLOSE/REDUCE
    request -- a payment-driven auto-flatten can only ever be a bug in
    a CALLER, never something this module itself is capable of."""
    import app.services.entitlement as entitlement_module

    forbidden_substrings = ("publication", "collective2_publisher", "etoro_adapter", "copyfactory")
    source = entitlement_module.__file__
    with open(source) as f:
        text = f.read()
    for forbidden in forbidden_substrings:
        assert f"import {forbidden}" not in text and f"from app.services.{forbidden}" not in text
