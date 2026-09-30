"""CU-03 "Selected portfolio detail" -- app/services/customer_selection_detail.py's
own tests. Real Postgres, real tenant-scoped session."""
from app.models.product import Product, ProductLifecycleState
from app.models.tenancy import Membership, MembershipRole, Tenant, UserIdentity
from app.services.copy_mandate import create_copy_mandate_draft
from app.services.customer_selection_detail import get_own_selection_detail
from app.services.platform_connection import create_platform_connection, disconnect_platform_connection
from app.services.portfolio_selection import create_portfolio_selection
from tests._onboarding_fixtures import complete_onboarding_prerequisites


def _seed_membership(db_session, *, tenant_id="tenant-a", user_id="user-a"):
    if db_session.get(Tenant, tenant_id) is None:
        db_session.add(Tenant(tenant_id=tenant_id, display_name="Tenant", environment="LOCAL_SIM"))
        db_session.flush()
    db_session.add(UserIdentity(user_id=user_id, email=f"{user_id}@example.com"))
    db_session.flush()
    db_session.add(Membership(tenant_id=tenant_id, user_id=user_id, role=MembershipRole.CUSTOMER))
    db_session.commit()
    #: This file's own `create_copy_mandate_draft` calls now need this
    #: customer's onboarding to have reached `ALERT_PREFERENCES_SET`
    #: (see app/services/onboarding_progress.py's own docstring).
    complete_onboarding_prerequisites(db_session, tenant_id=tenant_id, user_id=user_id)


def _published_product(db_session, *, tenant_id="tenant-a", slug="detail-product"):
    product = Product(
        tenant_id=tenant_id, product_name="Detail Product", slug=slug, lifecycle_state=ProductLifecycleState.PUBLISHED
    )
    db_session.add(product)
    db_session.commit()
    return product


def test_get_own_selection_detail_is_none_for_an_unknown_selection(db_session):
    _seed_membership(db_session)
    assert get_own_selection_detail(db_session, "nonexistent", tenant_id="tenant-a", user_id="user-a") is None


def test_get_own_selection_detail_is_none_for_a_cross_tenant_selection(db_session):
    _seed_membership(db_session, tenant_id="tenant-a", user_id="user-a")
    _seed_membership(db_session, tenant_id="tenant-b", user_id="user-b")
    product = _published_product(db_session, tenant_id="tenant-a")
    selection = create_portfolio_selection(
        db_session, tenant_id="tenant-a", user_id="user-a", product_id=product.product_id
    )
    db_session.commit()

    assert get_own_selection_detail(db_session, selection.selection_id, tenant_id="tenant-b", user_id="user-b") is None


def test_get_own_selection_detail_returns_the_real_portfolio_and_no_mandate(db_session):
    _seed_membership(db_session)
    product = _published_product(db_session)
    selection = create_portfolio_selection(
        db_session, tenant_id="tenant-a", user_id="user-a", product_id=product.product_id
    )
    db_session.commit()

    detail = get_own_selection_detail(db_session, selection.selection_id, tenant_id="tenant-a", user_id="user-a")
    assert detail is not None
    assert detail.portfolio is not None
    assert detail.portfolio.product_name == "Detail Product"
    assert detail.mandate is None
    assert detail.connection is None


def test_get_own_selection_detail_returns_the_real_mandate_and_connection(db_session):
    _seed_membership(db_session)
    product = _published_product(db_session)
    selection = create_portfolio_selection(
        db_session, tenant_id="tenant-a", user_id="user-a", product_id=product.product_id
    )
    connection = create_platform_connection(
        db_session,
        tenant_id="tenant-a",
        user_id="user-a",
        platform="collective2",
        environment="local_simulation",
        masked_account_label="Test ****1234",
    )
    db_session.commit()
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
    db_session.commit()

    detail = get_own_selection_detail(db_session, selection.selection_id, tenant_id="tenant-a", user_id="user-a")
    assert detail is not None
    assert detail.mandate is not None
    assert detail.connection is not None
    assert detail.connection.connection_id == connection.connection_id


def test_get_own_selection_detail_excludes_a_disconnected_connection(db_session):
    _seed_membership(db_session)
    product = _published_product(db_session)
    selection = create_portfolio_selection(
        db_session, tenant_id="tenant-a", user_id="user-a", product_id=product.product_id
    )
    connection = create_platform_connection(
        db_session,
        tenant_id="tenant-a",
        user_id="user-a",
        platform="collective2",
        environment="local_simulation",
        masked_account_label="Test ****1234",
    )
    db_session.commit()
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
    db_session.commit()
    disconnect_platform_connection(db_session, connection)
    db_session.commit()

    detail = get_own_selection_detail(db_session, selection.selection_id, tenant_id="tenant-a", user_id="user-a")
    assert detail is not None
    assert detail.mandate is not None
    assert detail.connection is None
