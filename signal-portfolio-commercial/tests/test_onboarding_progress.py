"""app/services/onboarding_progress.py -- the real wiring that closes
the audit finding: `OnboardingStage`/`advance()` (app/services/
onboarding.py) enforced correct ordering in isolation but were never
consulted by any call site, so a customer who hadn't completed
onboarding could still create a copy-mandate draft. These tests
reproduce that gap against `create_copy_mandate_draft` directly (the
one real call site copy_mandate.py has -- see that module's own
docstring for why there is no second, "activation", call site to gate
in this build) and confirm it is now closed without blocking a
customer who HAS completed the required stages.
"""
from datetime import datetime, timedelta, timezone

import pytest

from app.models.billing import ProductTier, Subscription, SubscriptionState
from app.models.eligibility import CustomerType, EligibilityAssessment
from app.models.onboarding_progress import OnboardingProgress
from app.models.product import Product, ProductLifecycleState
from app.models.tenancy import Membership, MembershipRole, Tenant, UserIdentity
from app.services.copy_mandate import InvalidCopyMandateError, create_copy_mandate_draft
from app.services.notification_preferences import save_notification_preferences
from app.services.onboarding import OnboardingStage
from app.services.onboarding_progress import (
    compute_current_stage,
    has_reached,
    require_stage_at_least,
    sync_onboarding_progress,
)
from app.services.platform_connection import create_platform_connection
from app.services.portfolio_selection import create_portfolio_selection


def _bare_membership(db_session, *, tenant_id="tenant-a", user_id="user-a"):
    """A customer who has signed up and nothing more -- no email
    verification, no eligibility facts, no subscription, no saved
    alert preferences. This is exactly the state the gap description
    says must NOT be able to reach mandate creation."""
    db_session.add(Tenant(tenant_id=tenant_id, display_name="Tenant", environment="LOCAL_SIM"))
    db_session.add(UserIdentity(user_id=user_id, email=f"{user_id}@example.com"))
    db_session.flush()
    db_session.add(Membership(tenant_id=tenant_id, user_id=user_id, role=MembershipRole.CUSTOMER))
    db_session.commit()


def _fully_onboarded_membership(db_session, *, tenant_id="tenant-a", user_id="user-a"):
    db_session.add(Tenant(tenant_id=tenant_id, display_name="Tenant", environment="LOCAL_SIM"))
    db_session.add(
        UserIdentity(user_id=user_id, email=f"{user_id}@example.com", email_verified_at=datetime.now(timezone.utc))
    )
    db_session.flush()
    db_session.add(Membership(tenant_id=tenant_id, user_id=user_id, role=MembershipRole.CUSTOMER))
    db_session.flush()
    db_session.add(
        EligibilityAssessment(
            tenant_id=tenant_id,
            user_id=user_id,
            residence_country="US",
            tax_residence=["US"],
            customer_type=CustomerType.INDIVIDUAL,
            requested_service_modes=["copying"],
            document_versions=["v1"],
            facts_confirmed=True,
        )
    )
    db_session.add(
        Subscription(
            tenant_id=tenant_id,
            tier=ProductTier.PORTFOLIOS_THREE,
            state=SubscriptionState.ACTIVE_PAID,
            price_cents=9900,
            currency="usd",
            current_period_end=datetime.now(timezone.utc) + timedelta(days=30),
        )
    )
    db_session.commit()
    save_notification_preferences(
        db_session,
        tenant_id=tenant_id,
        user_id=user_id,
        email=f"{user_id}@example.com",
        webhook_endpoint_id=None,
        categories=["safety"],
        timezone_name="UTC",
        quiet_start=None,
        quiet_end=None,
        marketing_consent=False,
    )
    db_session.commit()


def _eligible_selection_and_connection(db_session, *, tenant_id="tenant-a", user_id="user-a"):
    product = Product(
        tenant_id=tenant_id, product_name="Mandate Product", slug="onboarding-gate-product",
        lifecycle_state=ProductLifecycleState.PUBLISHED,
    )
    db_session.add(product)
    db_session.commit()
    selection = create_portfolio_selection(db_session, tenant_id=tenant_id, user_id=user_id, product_id=product.product_id)
    connection = create_platform_connection(
        db_session, tenant_id=tenant_id, user_id=user_id, platform="collective2",
        environment="local_simulation", masked_account_label="Test ****1234",
    )
    db_session.commit()
    return selection, connection


def _attempt_draft(db_session, *, tenant_id, user_id, selection, connection):
    return create_copy_mandate_draft(
        db_session,
        tenant_id=tenant_id,
        user_id=user_id,
        selection_id=selection.selection_id,
        connection_id=connection.connection_id,
        allocation_amount="100",
        allocation_currency="USD",
        max_trade_risk=None,
        max_loss=None,
        start_mode="new_entries_only",
        policy_version_id="policy-1",
        consent_version="consent-1",
    )


# -- The reproduction: a customer with none of the mandatory onboarding
# -- signals must be BLOCKED from creating a mandate draft. Before this
# -- module's wiring existed, this attempt succeeded (create_copy_
# -- mandate_draft never consulted OnboardingStage/advance() at all).

def test_mandate_draft_is_blocked_for_a_customer_who_has_not_completed_onboarding(db_session):
    _bare_membership(db_session)
    selection, connection = _eligible_selection_and_connection(db_session)

    with pytest.raises(InvalidCopyMandateError, match="ONBOARDING_INCOMPLETE"):
        _attempt_draft(db_session, tenant_id="tenant-a", user_id="user-a", selection=selection, connection=connection)


def test_mandate_draft_names_the_customers_actual_current_stage_in_the_error(db_session):
    tenant_id, user_id = "tenant-a", "user-a"
    db_session.add(Tenant(tenant_id=tenant_id, display_name="Tenant", environment="LOCAL_SIM"))
    db_session.add(
        UserIdentity(user_id=user_id, email=f"{user_id}@example.com", email_verified_at=datetime.now(timezone.utc))
    )
    db_session.flush()
    db_session.add(Membership(tenant_id=tenant_id, user_id=user_id, role=MembershipRole.CUSTOMER))
    db_session.flush()
    db_session.add(
        EligibilityAssessment(
            tenant_id=tenant_id, user_id=user_id, residence_country="US", tax_residence=["US"],
            customer_type=CustomerType.INDIVIDUAL, requested_service_modes=["copying"],
            document_versions=["v1"], facts_confirmed=True,
        )
    )
    db_session.commit()
    selection, connection = _eligible_selection_and_connection(db_session, tenant_id=tenant_id, user_id=user_id)

    # Email verified, eligibility approved and a real eligible selection
    # are all satisfied -- PRODUCT_SELECTED -- but nothing past it is;
    # the error must name that real current stage, not a generic denial.
    with pytest.raises(InvalidCopyMandateError, match="'PRODUCT_SELECTED'"):
        _attempt_draft(db_session, tenant_id=tenant_id, user_id=user_id, selection=selection, connection=connection)


# -- A customer who HAS completed the required stages must still be able
# -- to create a mandate draft -- this gate must not block everyone.

def test_mandate_draft_succeeds_for_a_customer_who_has_completed_onboarding(db_session):
    _fully_onboarded_membership(db_session)
    selection, connection = _eligible_selection_and_connection(db_session)

    mandate = _attempt_draft(db_session, tenant_id="tenant-a", user_id="user-a", selection=selection, connection=connection)
    db_session.commit()

    assert mandate.state.value == "draft"


def test_mandate_draft_is_blocked_when_alert_preferences_are_the_only_missing_stage(db_session):
    """Everything except the last mandatory stage before the mandate
    wizard (ALERT_PREFERENCES_SET) is satisfied -- entitlement alone is
    not enough, matching `advance()`'s own refusal to let ENTITLEMENT_
    VERIFIED imply ALERT_PREFERENCES_SET."""
    tenant_id, user_id = "tenant-a", "user-a"
    db_session.add(Tenant(tenant_id=tenant_id, display_name="Tenant", environment="LOCAL_SIM"))
    db_session.add(
        UserIdentity(user_id=user_id, email=f"{user_id}@example.com", email_verified_at=datetime.now(timezone.utc))
    )
    db_session.flush()
    db_session.add(Membership(tenant_id=tenant_id, user_id=user_id, role=MembershipRole.CUSTOMER))
    db_session.flush()
    db_session.add(
        EligibilityAssessment(
            tenant_id=tenant_id, user_id=user_id, residence_country="US", tax_residence=["US"],
            customer_type=CustomerType.INDIVIDUAL, requested_service_modes=["copying"],
            document_versions=["v1"], facts_confirmed=True,
        )
    )
    db_session.add(
        Subscription(
            tenant_id=tenant_id, tier=ProductTier.PORTFOLIOS_THREE, state=SubscriptionState.ACTIVE_PAID,
            price_cents=9900, currency="usd", current_period_end=datetime.now(timezone.utc) + timedelta(days=30),
        )
    )
    db_session.commit()
    selection, connection = _eligible_selection_and_connection(db_session, tenant_id=tenant_id, user_id=user_id)

    with pytest.raises(InvalidCopyMandateError, match="ONBOARDING_INCOMPLETE"):
        _attempt_draft(db_session, tenant_id=tenant_id, user_id=user_id, selection=selection, connection=connection)


def test_a_lapsed_subscription_blocks_a_new_mandate_draft_even_after_previous_success(db_session):
    """Entitlement is a LIVE check for a NEW action (matching
    entitlement.authorizes_new_entry's own contract), not a
    once-true-forever badge -- a subscription that later lapses must
    block a further new mandate draft even though this customer once
    reached ALERT_PREFERENCES_SET."""
    tenant_id, user_id = "tenant-a", "user-a"
    _fully_onboarded_membership(db_session, tenant_id=tenant_id, user_id=user_id)
    selection, connection = _eligible_selection_and_connection(db_session, tenant_id=tenant_id, user_id=user_id)

    mandate = _attempt_draft(db_session, tenant_id=tenant_id, user_id=user_id, selection=selection, connection=connection)
    db_session.commit()
    assert mandate.state.value == "draft"

    subscription = db_session.query(Subscription).filter_by(tenant_id=tenant_id).one()
    subscription.state = SubscriptionState.ENDED
    db_session.commit()

    with pytest.raises(InvalidCopyMandateError, match="ONBOARDING_INCOMPLETE"):
        _attempt_draft(db_session, tenant_id=tenant_id, user_id=user_id, selection=selection, connection=connection)

    # But the durable high-water mark this customer already reached is
    # never erased -- "every intermediate state is resumable".
    progress = db_session.get(OnboardingProgress, (tenant_id, user_id))
    assert progress is not None
    assert progress.stage == OnboardingStage.ALERT_PREFERENCES_SET


# -- compute_current_stage / has_reached / require_stage_at_least / sync
# -- their own direct behavior.

def test_compute_current_stage_is_signed_up_for_a_bare_membership(db_session):
    _bare_membership(db_session)
    assert compute_current_stage(db_session, tenant_id="tenant-a", user_id="user-a") == OnboardingStage.SIGNED_UP


def test_compute_current_stage_reaches_alert_preferences_set_when_fully_onboarded(db_session):
    _fully_onboarded_membership(db_session)
    _eligible_selection_and_connection(db_session)
    assert (
        compute_current_stage(db_session, tenant_id="tenant-a", user_id="user-a")
        == OnboardingStage.ALERT_PREFERENCES_SET
    )


def test_has_reached_is_the_canonical_order_comparison():
    assert has_reached(OnboardingStage.ALERT_PREFERENCES_SET, OnboardingStage.ENTITLEMENT_VERIFIED)
    assert not has_reached(OnboardingStage.ENTITLEMENT_VERIFIED, OnboardingStage.ALERT_PREFERENCES_SET)
    assert has_reached(OnboardingStage.SIGNED_UP, OnboardingStage.SIGNED_UP)


def test_require_stage_at_least_raises_with_the_actual_current_stage_named(db_session):
    _bare_membership(db_session)
    with pytest.raises(Exception, match="'SIGNED_UP'"):
        require_stage_at_least(
            db_session, tenant_id="tenant-a", user_id="user-a", required=OnboardingStage.ALERT_PREFERENCES_SET
        )


def test_sync_onboarding_progress_persists_the_high_water_mark(db_session):
    _fully_onboarded_membership(db_session)
    _eligible_selection_and_connection(db_session)
    progress = sync_onboarding_progress(db_session, tenant_id="tenant-a", user_id="user-a")
    db_session.commit()

    assert progress.stage == OnboardingStage.ALERT_PREFERENCES_SET
    reloaded = db_session.get(OnboardingProgress, ("tenant-a", "user-a"))
    assert reloaded.stage == OnboardingStage.ALERT_PREFERENCES_SET
