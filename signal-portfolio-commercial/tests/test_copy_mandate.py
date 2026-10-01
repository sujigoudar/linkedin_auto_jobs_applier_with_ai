"""CU-09 "Copy setup and mandate wizard" / CU-10 "Pause copying and
position handoff" -- app/services/copy_mandate.py's own tests. Real
Postgres, real tenant-scoped session."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.models.billing import ProductTier, Subscription, SubscriptionState
from app.models.eligibility import CustomerType, EligibilityAssessment
from app.models.product import Product, ProductLifecycleState
from app.models.tenancy import Membership, MembershipRole, Tenant, UserIdentity
from app.services.copy_mandate import (
    InvalidCopyMandateError,
    MandateNotEligibleForCancellationError,
    cancel_copy_mandate,
    create_copy_mandate_draft,
    get_own_copy_mandate,
    list_own_copy_mandates,
)
from app.services.notification_preferences import save_notification_preferences
from app.services.platform_connection import create_platform_connection
from app.services.portfolio_selection import create_portfolio_selection


def _seed_membership(db_session, *, tenant_id="tenant-a", user_id="user-a", onboarded=True):
    """`onboarded=True` (the default, matching every pre-existing test's
    own assumption that a customer here may draft a mandate) also seeds
    every real signal `app/services/onboarding_progress.py` reads --
    email verified, eligibility facts confirmed, an authorizing
    subscription and saved alert preferences -- so this fixture's
    customers reach `ALERT_PREFERENCES_SET` exactly the way a real
    customer would, never via a stage column set directly."""
    if db_session.get(Tenant, tenant_id) is None:
        db_session.add(Tenant(tenant_id=tenant_id, display_name="Tenant", environment="LOCAL_SIM"))
        db_session.flush()
    email_verified_at = datetime.now(timezone.utc) if onboarded else None
    db_session.add(UserIdentity(user_id=user_id, email=f"{user_id}@example.com", email_verified_at=email_verified_at))
    db_session.flush()
    db_session.add(Membership(tenant_id=tenant_id, user_id=user_id, role=MembershipRole.CUSTOMER))
    db_session.commit()

    if onboarded:
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


def _published_product(db_session, *, tenant_id="tenant-a", slug="mandate-product"):
    product = Product(
        tenant_id=tenant_id, product_name="Mandate Product", slug=slug, lifecycle_state=ProductLifecycleState.PUBLISHED
    )
    db_session.add(product)
    db_session.commit()
    return product


def _eligible_selection_and_connection(db_session, *, tenant_id="tenant-a", user_id="user-a"):
    product = _published_product(db_session, tenant_id=tenant_id)
    selection = create_portfolio_selection(db_session, tenant_id=tenant_id, user_id=user_id, product_id=product.product_id)
    connection = create_platform_connection(
        db_session,
        tenant_id=tenant_id,
        user_id=user_id,
        platform="collective2",
        environment="local_simulation",
        masked_account_label="Test ****1234",
    )
    db_session.commit()
    return selection, connection


def test_list_own_copy_mandates_is_empty_before_any_are_created(db_session):
    _seed_membership(db_session)
    assert list_own_copy_mandates(db_session, tenant_id="tenant-a", user_id="user-a") == []


def test_create_copy_mandate_draft_rejects_an_unowned_selection(db_session):
    _seed_membership(db_session)
    _, connection = _eligible_selection_and_connection(db_session)
    with pytest.raises(InvalidCopyMandateError, match="SELECTION_NOT_OWNED_OR_NOT_ACTIVE"):
        create_copy_mandate_draft(
            db_session,
            tenant_id="tenant-a",
            user_id="user-a",
            selection_id="nonexistent-selection",
            connection_id=connection.connection_id,
            allocation_amount="100",
            allocation_currency="USD",
            max_trade_risk=None,
            max_loss=None,
            start_mode="new_entries_only",
            policy_version_id="policy-1",
            consent_version="consent-1",
        )


def test_create_copy_mandate_draft_rejects_a_cancelled_selection(db_session):
    from app.services.portfolio_selection import cancel_portfolio_selection

    _seed_membership(db_session)
    selection, connection = _eligible_selection_and_connection(db_session)
    cancel_portfolio_selection(db_session, selection)
    db_session.commit()

    with pytest.raises(InvalidCopyMandateError, match="SELECTION_NOT_OWNED_OR_NOT_ACTIVE"):
        create_copy_mandate_draft(
            db_session,
            tenant_id="tenant-a",
            user_id="user-a",
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


def test_create_copy_mandate_draft_rejects_an_unowned_connection(db_session):
    _seed_membership(db_session)
    selection, _ = _eligible_selection_and_connection(db_session)
    with pytest.raises(InvalidCopyMandateError, match="CONNECTION_NOT_OWNED_OR_NOT_DECLARED"):
        create_copy_mandate_draft(
            db_session,
            tenant_id="tenant-a",
            user_id="user-a",
            selection_id=selection.selection_id,
            connection_id="nonexistent-connection",
            allocation_amount="100",
            allocation_currency="USD",
            max_trade_risk=None,
            max_loss=None,
            start_mode="new_entries_only",
            policy_version_id="policy-1",
            consent_version="consent-1",
        )


def test_create_copy_mandate_draft_rejects_a_non_positive_allocation(db_session):
    _seed_membership(db_session)
    selection, connection = _eligible_selection_and_connection(db_session)
    with pytest.raises(InvalidCopyMandateError, match="allocation_amount must be positive"):
        create_copy_mandate_draft(
            db_session,
            tenant_id="tenant-a",
            user_id="user-a",
            selection_id=selection.selection_id,
            connection_id=connection.connection_id,
            allocation_amount="0",
            allocation_currency="USD",
            max_trade_risk=None,
            max_loss=None,
            start_mode="new_entries_only",
            policy_version_id="policy-1",
            consent_version="consent-1",
        )


def test_create_copy_mandate_draft_accepts_an_allocation_at_or_below_one_but_still_positive(db_session):
    """The positive-allocation boundary (`<= 0`) had no test between the
    rejected `0` and a comfortably-positive `100` -- an off-by-one mutant
    (`<= 1`) survived by wrongly rejecting a legitimate small allocation
    at or below 1 (e.g. exactly `1`, or a fractional `0.5`)."""
    _seed_membership(db_session)
    selection, connection = _eligible_selection_and_connection(db_session)
    mandate = create_copy_mandate_draft(
        db_session,
        tenant_id="tenant-a",
        user_id="user-a",
        selection_id=selection.selection_id,
        connection_id=connection.connection_id,
        allocation_amount="1",
        allocation_currency="USD",
        max_trade_risk=None,
        max_loss=None,
        start_mode="new_entries_only",
        policy_version_id="policy-1",
        consent_version="consent-1",
    )
    assert mandate.allocation_amount == Decimal("1")


def test_create_copy_mandate_draft_rejects_an_unknown_start_mode(db_session):
    _seed_membership(db_session)
    selection, connection = _eligible_selection_and_connection(db_session)
    with pytest.raises(InvalidCopyMandateError, match="start_mode"):
        create_copy_mandate_draft(
            db_session,
            tenant_id="tenant-a",
            user_id="user-a",
            selection_id=selection.selection_id,
            connection_id=connection.connection_id,
            allocation_amount="100",
            allocation_currency="USD",
            max_trade_risk=None,
            max_loss=None,
            start_mode="replay_history",
            policy_version_id="policy-1",
            consent_version="consent-1",
        )


def test_create_then_reload_persists_the_real_mandate_draft(db_session):
    _seed_membership(db_session)
    selection, connection = _eligible_selection_and_connection(db_session)
    create_copy_mandate_draft(
        db_session,
        tenant_id="tenant-a",
        user_id="user-a",
        selection_id=selection.selection_id,
        connection_id=connection.connection_id,
        allocation_amount="500.00",
        allocation_currency="USD",
        max_trade_risk=None,
        max_loss=None,
        start_mode="new_entries_only",
        policy_version_id="policy-1",
        consent_version="consent-1",
    )
    db_session.commit()

    mandates = list_own_copy_mandates(db_session, tenant_id="tenant-a", user_id="user-a")
    assert len(mandates) == 1
    assert mandates[0].state.value == "draft"
    assert mandates[0].allocation_currency == "USD"


def test_get_own_copy_mandate_is_none_for_a_cross_tenant_mandate(db_session):
    _seed_membership(db_session, tenant_id="tenant-a", user_id="user-a")
    _seed_membership(db_session, tenant_id="tenant-b", user_id="user-b")
    selection, connection = _eligible_selection_and_connection(db_session)
    mandate = create_copy_mandate_draft(
        db_session,
        tenant_id="tenant-a",
        user_id="user-a",
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
    db_session.commit()

    assert get_own_copy_mandate(db_session, mandate.mandate_id, tenant_id="tenant-b", user_id="user-b") is None


def test_get_own_copy_mandate_returns_the_mandate_for_its_true_owner(db_session):
    """The positive path `test_get_own_copy_mandate_is_none_for_a_cross_tenant_mandate`
    never exercises: a customer fetching a mandate they actually own must
    get the real row back, not `None`. Without this, mutating the
    ownership check's `!=` to `==` (always-true, so the guard fires even
    for the true owner) or dropping the `session.get` lookup entirely
    (always `None`) are both invisible -- they only ever over-block, never
    leak, so the cross-tenant-only test above can't catch them."""
    _seed_membership(db_session)
    selection, connection = _eligible_selection_and_connection(db_session)
    mandate = create_copy_mandate_draft(
        db_session,
        tenant_id="tenant-a",
        user_id="user-a",
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
    db_session.commit()

    fetched = get_own_copy_mandate(db_session, mandate.mandate_id, tenant_id="tenant-a", user_id="user-a")
    assert fetched is not None
    assert fetched.mandate_id == mandate.mandate_id


def test_get_own_copy_mandate_is_none_for_a_different_tenant_sharing_the_same_user_id(db_session):
    """A real cross-tenant leak that the existing cross-tenant test can't
    catch because it uses a different `user_id` too: if the ownership
    check's `or` chain ever regresses to `and` on its first clause
    (operator precedence: `(mandate is None and tenant mismatch) or user
    mismatch`), a request naming the WRONG tenant but the SAME `user_id`
    as the true owner sails through -- a different tenant's customer
    reading another tenant's real-money mandate just by knowing/sharing a
    user_id. This is exactly the kind of misdirection this module's own
    docstring is about guarding against."""
    _seed_membership(db_session, tenant_id="tenant-a", user_id="user-a")
    selection, connection = _eligible_selection_and_connection(db_session, tenant_id="tenant-a", user_id="user-a")
    mandate = create_copy_mandate_draft(
        db_session,
        tenant_id="tenant-a",
        user_id="user-a",
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
    db_session.commit()

    # Same user_id as the true owner, but a DIFFERENT tenant_id.
    assert get_own_copy_mandate(db_session, mandate.mandate_id, tenant_id="tenant-b", user_id="user-a") is None


def test_create_copy_mandate_draft_persists_the_risk_limits_it_was_given(db_session):
    """`max_trade_risk`/`max_loss` are real-money risk limits a customer
    sets on their own mandate -- nothing previously asserted that a
    supplied value actually lands on the persisted row rather than being
    silently dropped (e.g. a decimal-parsing step that never assigns its
    result)."""
    _seed_membership(db_session)
    selection, connection = _eligible_selection_and_connection(db_session)
    mandate = create_copy_mandate_draft(
        db_session,
        tenant_id="tenant-a",
        user_id="user-a",
        selection_id=selection.selection_id,
        connection_id=connection.connection_id,
        allocation_amount="100",
        allocation_currency="USD",
        max_trade_risk="25.50",
        max_loss="75.00",
        start_mode="new_entries_only",
        policy_version_id="policy-1",
        consent_version="consent-1",
    )
    db_session.commit()

    assert mandate.max_trade_risk == Decimal("25.50")
    assert mandate.max_loss == Decimal("75.00")


@pytest.mark.parametrize(
    "field_name,error_match",
    [
        ("allocation_currency", "allocation_currency is required"),
        ("policy_version_id", "policy_version_id is required"),
        ("consent_version", "consent_version is required"),
    ],
)
def test_create_copy_mandate_draft_rejects_a_whitespace_only_required_field(db_session, field_name, error_match):
    """Each of these required-string guards is `if not value or not
    value.strip(): raise`. A whitespace-only string ("   ") is truthy
    (`not value` is False) so only the `.strip()` half of the guard can
    ever catch it -- which means the `or` connecting the two halves must
    stay an `or`: were it ever weakened to `and`, a whitespace-only value
    would short-circuit past the guard entirely and a mandate would be
    drafted with a blank required field."""
    _seed_membership(db_session)
    selection, connection = _eligible_selection_and_connection(db_session)
    kwargs = dict(
        tenant_id="tenant-a",
        user_id="user-a",
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
    kwargs[field_name] = "   "
    with pytest.raises(InvalidCopyMandateError, match=error_match):
        create_copy_mandate_draft(db_session, **kwargs)


def test_cancel_copy_mandate_sets_cancelled_state(db_session):
    _seed_membership(db_session)
    selection, connection = _eligible_selection_and_connection(db_session)
    mandate = create_copy_mandate_draft(
        db_session,
        tenant_id="tenant-a",
        user_id="user-a",
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
    db_session.commit()

    cancel_copy_mandate(db_session, mandate)
    db_session.commit()
    assert mandate.state.value == "cancelled"


def test_cancel_copy_mandate_refuses_a_non_draft_mandate(db_session):
    _seed_membership(db_session)
    selection, connection = _eligible_selection_and_connection(db_session)
    mandate = create_copy_mandate_draft(
        db_session,
        tenant_id="tenant-a",
        user_id="user-a",
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
    db_session.commit()
    cancel_copy_mandate(db_session, mandate)
    db_session.commit()

    with pytest.raises(MandateNotEligibleForCancellationError):
        cancel_copy_mandate(db_session, mandate)


def test_create_copy_mandate_draft_rejects_a_cross_tenant_selection(db_session):
    _seed_membership(db_session, tenant_id="tenant-a", user_id="user-a")
    _seed_membership(db_session, tenant_id="tenant-b", user_id="user-b")
    selection, connection = _eligible_selection_and_connection(db_session, tenant_id="tenant-a", user_id="user-a")

    with pytest.raises(InvalidCopyMandateError, match="SELECTION_NOT_OWNED_OR_NOT_ACTIVE"):
        create_copy_mandate_draft(
            db_session,
            tenant_id="tenant-b",
            user_id="user-b",
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
