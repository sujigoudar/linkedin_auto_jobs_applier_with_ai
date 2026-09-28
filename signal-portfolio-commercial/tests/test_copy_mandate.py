"""CU-09 "Copy setup and mandate wizard" -- app/services/copy_mandate.py's
own tests. Real Postgres, real tenant-scoped session."""
import pytest

from app.models.product import Product, ProductLifecycleState
from app.models.tenancy import Membership, MembershipRole, Tenant, UserIdentity
from app.services.copy_mandate import InvalidCopyMandateError, create_copy_mandate_draft, list_own_copy_mandates
from app.services.platform_connection import create_platform_connection
from app.services.portfolio_selection import create_portfolio_selection


def _seed_membership(db_session, *, tenant_id="tenant-a", user_id="user-a"):
    if db_session.get(Tenant, tenant_id) is None:
        db_session.add(Tenant(tenant_id=tenant_id, display_name="Tenant", environment="LOCAL_SIM"))
        db_session.flush()
    db_session.add(UserIdentity(user_id=user_id, email=f"{user_id}@example.com"))
    db_session.flush()
    db_session.add(Membership(tenant_id=tenant_id, user_id=user_id, role=MembershipRole.CUSTOMER))
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
