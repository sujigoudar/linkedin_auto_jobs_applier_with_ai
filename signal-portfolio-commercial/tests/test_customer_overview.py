"""CU-01 "Customer overview" -- app/services/customer_overview.py's own
tests. Real Postgres, real tenant-scoped session."""
from app.models.product import Product, ProductLifecycleState
from app.models.tenancy import Membership, MembershipRole, Tenant, UserIdentity
from app.services.copy_mandate import cancel_copy_mandate, create_copy_mandate_draft
from app.services.customer_overview import get_customer_overview
from app.services.platform_connection import create_platform_connection, disconnect_platform_connection
from app.services.portfolio_selection import create_portfolio_selection


def _seed_membership(db_session, *, tenant_id="tenant-a", user_id="user-a"):
    if db_session.get(Tenant, tenant_id) is None:
        db_session.add(Tenant(tenant_id=tenant_id, display_name="Tenant", environment="LOCAL_SIM"))
        db_session.flush()
    db_session.add(UserIdentity(user_id=user_id, email=f"{user_id}@example.com"))
    db_session.flush()
    db_session.add(Membership(tenant_id=tenant_id, user_id=user_id, role=MembershipRole.CUSTOMER))
    db_session.commit()


def _published_product(db_session, *, tenant_id="tenant-a", slug="overview-product"):
    product = Product(
        tenant_id=tenant_id, product_name="Overview Product", slug=slug, lifecycle_state=ProductLifecycleState.PUBLISHED
    )
    db_session.add(product)
    db_session.commit()
    return product


def test_overview_is_empty_before_any_selection(db_session):
    _seed_membership(db_session)
    overview = get_customer_overview(db_session, tenant_id="tenant-a", user_id="user-a")
    assert overview.rows == []
    assert overview.selected_portfolios_count == 0


def test_a_selection_with_no_mandate_requires_creating_one(db_session):
    _seed_membership(db_session)
    product = _published_product(db_session)
    create_portfolio_selection(db_session, tenant_id="tenant-a", user_id="user-a", product_id=product.product_id)
    db_session.commit()

    overview = get_customer_overview(db_session, tenant_id="tenant-a", user_id="user-a")
    assert overview.selected_portfolios_count == 1
    assert overview.rows[0].mandate is None
    assert overview.rows[0].required_action == "Create a copy mandate"
    assert overview.rows[0].product_name == "Overview Product"
    assert overview.rows[0].product_slug == "overview-product"


def test_a_mandate_whose_connection_has_since_been_disconnected_requires_reconnecting(db_session):
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

    overview = get_customer_overview(db_session, tenant_id="tenant-a", user_id="user-a")
    assert overview.rows[0].mandate is not None
    assert overview.rows[0].connection is None
    assert overview.rows[0].required_action == "Reconnect platform -- the connected account for this mandate is missing"


def test_a_mandate_with_a_declared_connection_requires_no_action(db_session):
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

    overview = get_customer_overview(db_session, tenant_id="tenant-a", user_id="user-a")
    assert overview.rows[0].connection is not None
    assert overview.rows[0].required_action == "No action required"


def test_a_cancelled_mandate_is_excluded_so_the_row_falls_back_to_no_mandate(db_session):
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

    overview = get_customer_overview(db_session, tenant_id="tenant-a", user_id="user-a")
    assert overview.rows[0].mandate is None
    assert overview.rows[0].required_action == "Create a copy mandate"


def test_overview_is_scoped_to_the_requesting_tenant_and_user(db_session):
    _seed_membership(db_session, tenant_id="tenant-a", user_id="user-a")
    _seed_membership(db_session, tenant_id="tenant-b", user_id="user-b")
    product = _published_product(db_session, tenant_id="tenant-a")
    create_portfolio_selection(db_session, tenant_id="tenant-a", user_id="user-a", product_id=product.product_id)
    db_session.commit()

    overview = get_customer_overview(db_session, tenant_id="tenant-b", user_id="user-b")
    assert overview.rows == []
