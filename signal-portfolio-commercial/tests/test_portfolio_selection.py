"""CU-02 "My portfolios" -- app/services/portfolio_selection.py's own
tests. Real Postgres, real tenant-scoped session."""
import pytest

from app.models.product import Product, ProductLifecycleState
from app.models.tenancy import Membership, MembershipRole, Tenant, UserIdentity
from app.services.portfolio_selection import (
    InvalidPortfolioSelectionError,
    cancel_portfolio_selection,
    create_portfolio_selection,
    get_own_portfolio_selection,
    list_own_portfolio_selections,
)


def _seed_membership(db_session, *, tenant_id="tenant-a", user_id="user-a"):
    if db_session.get(Tenant, tenant_id) is None:
        db_session.add(Tenant(tenant_id=tenant_id, display_name="Tenant", environment="LOCAL_SIM"))
        db_session.flush()
    db_session.add(UserIdentity(user_id=user_id, email=f"{user_id}@example.com"))
    db_session.flush()
    db_session.add(Membership(tenant_id=tenant_id, user_id=user_id, role=MembershipRole.CUSTOMER))
    db_session.commit()


def _draft_product(db_session, *, tenant_id="tenant-a", slug="draft-product"):
    product = Product(tenant_id=tenant_id, product_name="Draft Product", slug=slug)
    db_session.add(product)
    db_session.commit()
    return product


def _published_product(db_session, *, tenant_id="tenant-a", slug="published-product"):
    product = Product(
        tenant_id=tenant_id,
        product_name="Published Product",
        slug=slug,
        lifecycle_state=ProductLifecycleState.PUBLISHED,
    )
    db_session.add(product)
    db_session.commit()
    return product


def test_list_own_portfolio_selections_is_empty_before_any_are_created(db_session):
    _seed_membership(db_session)
    assert list_own_portfolio_selections(db_session, tenant_id="tenant-a", user_id="user-a") == []


def test_create_portfolio_selection_rejects_a_draft_product(db_session):
    _seed_membership(db_session)
    product = _draft_product(db_session)
    with pytest.raises(InvalidPortfolioSelectionError, match="PRODUCT_NOT_PUBLISHED_OR_NOT_FOUND"):
        create_portfolio_selection(db_session, tenant_id="tenant-a", user_id="user-a", product_id=product.product_id)


def test_create_portfolio_selection_rejects_an_unknown_product(db_session):
    _seed_membership(db_session)
    with pytest.raises(InvalidPortfolioSelectionError, match="PRODUCT_NOT_PUBLISHED_OR_NOT_FOUND"):
        create_portfolio_selection(db_session, tenant_id="tenant-a", user_id="user-a", product_id="nonexistent")


def test_create_portfolio_selection_rejects_a_cross_tenant_product(db_session):
    _seed_membership(db_session, tenant_id="tenant-a", user_id="user-a")
    _seed_membership(db_session, tenant_id="tenant-b", user_id="user-b")
    product = _published_product(db_session, tenant_id="tenant-b", slug="other-tenant-product")
    with pytest.raises(InvalidPortfolioSelectionError, match="PRODUCT_NOT_PUBLISHED_OR_NOT_FOUND"):
        create_portfolio_selection(db_session, tenant_id="tenant-a", user_id="user-a", product_id=product.product_id)


def test_create_then_reload_persists_the_real_selection(db_session):
    _seed_membership(db_session)
    product = _published_product(db_session)
    create_portfolio_selection(db_session, tenant_id="tenant-a", user_id="user-a", product_id=product.product_id)
    db_session.commit()

    selections = list_own_portfolio_selections(db_session, tenant_id="tenant-a", user_id="user-a")
    assert len(selections) == 1
    assert selections[0].product_id == product.product_id
    assert selections[0].state.value == "active"


def test_create_portfolio_selection_rejects_a_duplicate_active_selection(db_session):
    _seed_membership(db_session)
    product = _published_product(db_session)
    create_portfolio_selection(db_session, tenant_id="tenant-a", user_id="user-a", product_id=product.product_id)
    db_session.commit()

    with pytest.raises(InvalidPortfolioSelectionError, match="ALREADY_SELECTED"):
        create_portfolio_selection(db_session, tenant_id="tenant-a", user_id="user-a", product_id=product.product_id)


def test_get_own_portfolio_selection_is_none_for_a_cross_tenant_selection(db_session):
    _seed_membership(db_session, tenant_id="tenant-a", user_id="user-a")
    _seed_membership(db_session, tenant_id="tenant-b", user_id="user-b")
    product = _published_product(db_session, tenant_id="tenant-a")
    selection = create_portfolio_selection(
        db_session, tenant_id="tenant-a", user_id="user-a", product_id=product.product_id
    )
    db_session.commit()

    assert get_own_portfolio_selection(db_session, selection.selection_id, tenant_id="tenant-b", user_id="user-b") is None


def test_get_own_portfolio_selection_is_none_for_a_different_users_selection_in_the_same_tenant(db_session):
    _seed_membership(db_session, tenant_id="tenant-a", user_id="user-a")
    _seed_membership(db_session, tenant_id="tenant-a", user_id="user-b")
    product = _published_product(db_session, tenant_id="tenant-a")
    selection = create_portfolio_selection(
        db_session, tenant_id="tenant-a", user_id="user-a", product_id=product.product_id
    )
    db_session.commit()

    assert get_own_portfolio_selection(db_session, selection.selection_id, tenant_id="tenant-a", user_id="user-b") is None


def test_cancel_portfolio_selection_sets_cancelled_state(db_session):
    _seed_membership(db_session)
    product = _published_product(db_session)
    selection = create_portfolio_selection(
        db_session, tenant_id="tenant-a", user_id="user-a", product_id=product.product_id
    )
    db_session.commit()

    cancel_portfolio_selection(db_session, selection)
    db_session.commit()
    assert selection.state.value == "cancelled"


def test_cancelling_then_reselecting_the_same_product_is_allowed(db_session):
    _seed_membership(db_session)
    product = _published_product(db_session)
    selection = create_portfolio_selection(
        db_session, tenant_id="tenant-a", user_id="user-a", product_id=product.product_id
    )
    db_session.commit()
    cancel_portfolio_selection(db_session, selection)
    db_session.commit()

    new_selection = create_portfolio_selection(
        db_session, tenant_id="tenant-a", user_id="user-a", product_id=product.product_id
    )
    db_session.commit()
    assert new_selection.state.value == "active"
