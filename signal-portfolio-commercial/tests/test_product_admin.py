"""AD-07 "Products and portfolio versions" -- the first dashboard vertical
slice's own service tests. Runs through `tenant_session_factory` (the
genuine non-superuser `app_role` login, see tests/conftest.py) with
`set_tenant_scope` explicitly called, exactly like a real request would,
so these tests exercise the real RLS policies (`app/db.py`'s
`product_visibility`), not just the application-level `tenant_id` filters
in `product_admin.py` alone.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.db import set_tenant_scope
from app.models.portfolio_version import PortfolioVersion, PortfolioVersionSleeve
from app.models.product import Product, ProductLifecycleState
from app.models.rights import RightsGrant, RightsStatus
from app.models.sleeve import Sleeve
from app.models.tenancy import Tenant
from app.services.product_admin import (
    InvalidProductDraftError,
    SlugAlreadyExistsError,
    StaleRevisionError,
    compute_publication_blockers,
    create_draft_product,
    get_product,
    list_products,
    list_published_products,
    update_product_draft,
)


def _seed_tenants(db_session):
    db_session.add_all(
        [
            Tenant(tenant_id="tenant-a", display_name="A", environment="LOCAL_SIM"),
            Tenant(tenant_id="tenant-b", display_name="B", environment="LOCAL_SIM"),
        ]
    )
    db_session.commit()


def test_list_products_is_empty_for_a_fresh_tenant(db_session, tenant_session_factory):
    _seed_tenants(db_session)
    session = tenant_session_factory()
    try:
        set_tenant_scope(session, "tenant-a")
        assert list_products(session, tenant_id="tenant-a") == []
    finally:
        session.rollback()
        session.close()


def test_create_draft_product_persists_and_reloads(db_session, tenant_session_factory):
    _seed_tenants(db_session)
    session = tenant_session_factory()
    try:
        set_tenant_scope(session, "tenant-a")
        product = create_draft_product(
            session, tenant_id="tenant-a", product_name="Swing Alerts", slug="swing-alerts"
        )
        session.commit()
        # `set_tenant_scope` is transaction-local (SET LOCAL semantics) --
        # `commit` ends that transaction, so a session that keeps being
        # used afterward (simulating a second request reusing the scope)
        # must re-assert it, exactly like a real request handler would at
        # the start of its own transaction.
        set_tenant_scope(session, "tenant-a")
        assert product.lifecycle_state == ProductLifecycleState.DRAFT
        assert product.revision == 1

        reloaded = get_product(session, product.product_id, tenant_id="tenant-a")
        assert reloaded is not None
        assert reloaded.product_id == product.product_id
        assert reloaded.revision == 1

        listed = list_products(session, tenant_id="tenant-a")
        assert [p.product_id for p in listed] == [product.product_id]
    finally:
        session.rollback()
        session.close()


def test_create_draft_product_rejects_invalid_slug(db_session, tenant_session_factory):
    _seed_tenants(db_session)
    session = tenant_session_factory()
    try:
        set_tenant_scope(session, "tenant-a")
        with pytest.raises(InvalidProductDraftError):
            create_draft_product(session, tenant_id="tenant-a", product_name="X", slug="Not Valid!")
    finally:
        session.rollback()
        session.close()


def test_create_draft_product_rejects_duplicate_slug(db_session, tenant_session_factory):
    _seed_tenants(db_session)
    session = tenant_session_factory()
    try:
        set_tenant_scope(session, "tenant-a")
        create_draft_product(session, tenant_id="tenant-a", product_name="First", slug="dup-slug")
        session.commit()
        set_tenant_scope(session, "tenant-a")
        with pytest.raises(SlugAlreadyExistsError):
            create_draft_product(session, tenant_id="tenant-a", product_name="Second", slug="dup-slug")
    finally:
        session.rollback()
        session.close()


def test_update_product_draft_with_matching_revision_succeeds(db_session, tenant_session_factory):
    _seed_tenants(db_session)
    session = tenant_session_factory()
    try:
        set_tenant_scope(session, "tenant-a")
        product = create_draft_product(session, tenant_id="tenant-a", product_name="A", slug="a-product")
        session.commit()
        set_tenant_scope(session, "tenant-a")

        updated = update_product_draft(
            session, product, expected_revision=1, product_name="A Renamed", cash_bps=9_000
        )
        session.commit()
        set_tenant_scope(session, "tenant-a")
        assert updated.revision == 2
        assert updated.product_name == "A Renamed"
        assert updated.cash_bps == 9_000
    finally:
        session.rollback()
        session.close()


def test_update_product_draft_with_stale_revision_raises(db_session, tenant_session_factory):
    _seed_tenants(db_session)
    session = tenant_session_factory()
    try:
        set_tenant_scope(session, "tenant-a")
        product = create_draft_product(session, tenant_id="tenant-a", product_name="A", slug="stale-product")
        session.commit()
        set_tenant_scope(session, "tenant-a")

        with pytest.raises(StaleRevisionError):
            update_product_draft(session, product, expected_revision=999, product_name="Nope")
    finally:
        session.rollback()
        session.close()


def test_update_product_draft_rejects_unknown_service_mode(db_session, tenant_session_factory):
    _seed_tenants(db_session)
    session = tenant_session_factory()
    try:
        set_tenant_scope(session, "tenant-a")
        product = create_draft_product(session, tenant_id="tenant-a", product_name="A", slug="svc-mode")
        session.commit()
        set_tenant_scope(session, "tenant-a")

        with pytest.raises(InvalidProductDraftError):
            update_product_draft(session, product, expected_revision=1, service_modes=["not_a_real_mode"])
    finally:
        session.rollback()
        session.close()


def test_get_product_cross_tenant_returns_none(db_session, tenant_session_factory):
    _seed_tenants(db_session)
    session_a = tenant_session_factory()
    try:
        set_tenant_scope(session_a, "tenant-a")
        product = create_draft_product(session_a, tenant_id="tenant-a", product_name="A", slug="cross-tenant")
        session_a.commit()
        set_tenant_scope(session_a, "tenant-a")
        product_id = product.product_id
    finally:
        session_a.close()

    session_b = tenant_session_factory()
    try:
        set_tenant_scope(session_b, "tenant-b")
        assert get_product(session_b, product_id, tenant_id="tenant-b") is None
        assert list_products(session_b, tenant_id="tenant-b") == []
    finally:
        session_b.rollback()
        session_b.close()


def test_product_visibility_rls_hides_another_tenants_unpublished_draft(db_session, tenant_session_factory):
    """The real Postgres policy itself (app/db.py's `product_visibility`),
    exercised with a bare `session.query`/`select` -- not through
    `product_admin.py`'s own `tenant_id` filters, which would mask a
    broken policy underneath. A tenant-a draft must never come back for a
    tenant-b-scoped raw query, at the database layer, independent of any
    application-level filtering."""
    _seed_tenants(db_session)
    session_a = tenant_session_factory()
    try:
        set_tenant_scope(session_a, "tenant-a")
        create_draft_product(session_a, tenant_id="tenant-a", product_name="A", slug="rls-hidden")
        session_a.commit()
    finally:
        session_a.close()

    session_b = tenant_session_factory()
    try:
        set_tenant_scope(session_b, "tenant-b")
        rows = session_b.scalars(select(Product)).all()
        assert rows == []
    finally:
        session_b.rollback()
        session_b.close()


def test_compute_publication_blockers_on_a_fresh_draft_lists_every_missing_prerequisite(
    db_session, tenant_session_factory
):
    _seed_tenants(db_session)
    session = tenant_session_factory()
    try:
        set_tenant_scope(session, "tenant-a")
        product = create_draft_product(session, tenant_id="tenant-a", product_name="A", slug="blockers")
        session.commit()
        set_tenant_scope(session, "tenant-a")

        blockers = compute_publication_blockers(session, product)
        assert "NO_PORTFOLIO_VERSION_SELECTED" in blockers
        assert "NO_SERVICE_MODE_SELECTED" in blockers
        assert "NO_AUDIENCE_POLICY" in blockers
        assert "NO_RESEARCH_EVIDENCE" in blockers
        assert "NO_METHODOLOGY_DOCUMENT" in blockers
    finally:
        session.rollback()
        session.close()


def _seed_portfolio_version_with_one_sleeve(session, *, tenant_id, grant_asset="equity", grant_channel="web"):
    sleeve = Sleeve(
        tenant_id=tenant_id,
        provider="acme-research",
        analyst="jane",
        strategy_horizon="swing",
        asset_class="equity",
        parser_version="v1",
        execution_policy_id="ep-1",
        cost_model_id="cm-1",
        capacity_policy_id="cap-1",
        risk_unit_id="ru-1",
        history_origin="acme-research",
    )
    session.add(sleeve)
    session.flush()

    portfolio_version = PortfolioVersion(
        tenant_id=tenant_id,
        portfolio_id="p-01",
        version_number=1,
        cash_weight=Decimal("0"),
        research_cutoff=datetime.now(timezone.utc),
        max_subscriber_capacity=100,
        consent_disclosure_version="v1",
    )
    session.add(portfolio_version)
    session.flush()

    session.add(
        PortfolioVersionSleeve(
            portfolio_version_id=portfolio_version.portfolio_version_id,
            sleeve_id=sleeve.sleeve_id,
            weight=Decimal("1.0"),
        )
    )
    session.add(
        RightsGrant(
            grant_id="grant-1",
            source_id="acme-research",
            grantee_entity="tenant-a",
            contract_hash="hash",
            status=RightsStatus.GRANTED,
            uses=["COMMERCIAL_ALERTS"],
            channels=[grant_channel],
            jurisdictions=["US"],
            assets=[grant_asset],
            effective_at=datetime.now(timezone.utc) - timedelta(days=1),
            expires_at=datetime.now(timezone.utc) + timedelta(days=365),
            attribution_policy_id="attr-1",
            wind_down_policy_id="wind-1",
            review_id="review-1",
        )
    )
    session.flush()
    return portfolio_version, sleeve


def test_compute_publication_blockers_shrinks_as_a_valid_portfolio_version_is_selected(
    db_session, tenant_session_factory
):
    _seed_tenants(db_session)
    session = tenant_session_factory()
    try:
        set_tenant_scope(session, "tenant-a")
        portfolio_version, _ = _seed_portfolio_version_with_one_sleeve(session, tenant_id="tenant-a")
        product = create_draft_product(session, tenant_id="tenant-a", product_name="A", slug="ready")
        update_product_draft(
            session,
            product,
            expected_revision=1,
            portfolio_version_id=portfolio_version.portfolio_version_id,
            cash_bps=0,
            service_modes=["alerts"],
            audience_policy_id="audience-1",
            research_report_id="report-1",
            methodology_document_id="method-1",
        )
        session.commit()
        set_tenant_scope(session, "tenant-a")

        blockers = compute_publication_blockers(session, product)
        assert blockers == []
    finally:
        session.rollback()
        session.close()


def test_compute_publication_blockers_flags_rights_not_granted_for_a_member_sleeve(
    db_session, tenant_session_factory
):
    _seed_tenants(db_session)
    session = tenant_session_factory()
    try:
        set_tenant_scope(session, "tenant-a")
        # The grant covers a different channel than the check uses ("web"),
        # so it must never satisfy the sleeve's rights requirement.
        portfolio_version, _ = _seed_portfolio_version_with_one_sleeve(
            session, tenant_id="tenant-a", grant_channel="app"
        )
        product = create_draft_product(session, tenant_id="tenant-a", product_name="A", slug="unrighted")
        update_product_draft(
            session,
            product,
            expected_revision=1,
            portfolio_version_id=portfolio_version.portfolio_version_id,
            cash_bps=0,
            service_modes=["alerts"],
            audience_policy_id="audience-1",
            research_report_id="report-1",
            methodology_document_id="method-1",
        )
        session.commit()
        set_tenant_scope(session, "tenant-a")

        blockers = compute_publication_blockers(session, product)
        assert "RIGHTS_NOT_GRANTED_FOR_A_MEMBER_SLEEVE" in blockers
    finally:
        session.rollback()
        session.close()


def test_compute_publication_blockers_flags_weights_not_conserving_to_ten_thousand_bps(
    db_session, tenant_session_factory
):
    _seed_tenants(db_session)
    session = tenant_session_factory()
    try:
        set_tenant_scope(session, "tenant-a")
        portfolio_version, _ = _seed_portfolio_version_with_one_sleeve(session, tenant_id="tenant-a")
        product = create_draft_product(session, tenant_id="tenant-a", product_name="A", slug="bad-weights")
        # sleeve weight is 1.0 (10000 bps) and cash_bps defaults to 10000 --
        # 20000 total, which must not silently conserve.
        update_product_draft(
            session, product, expected_revision=1, portfolio_version_id=portfolio_version.portfolio_version_id
        )
        session.commit()
        set_tenant_scope(session, "tenant-a")

        blockers = compute_publication_blockers(session, product)
        assert "WEIGHTS_DO_NOT_CONSERVE_TO_TEN_THOUSAND_BPS" in blockers
    finally:
        session.rollback()
        session.close()


def test_list_published_products_stays_empty_until_a_product_is_actually_published(
    db_session, tenant_session_factory
):
    _seed_tenants(db_session)
    session = tenant_session_factory()
    try:
        set_tenant_scope(session, "tenant-a")
        create_draft_product(session, tenant_id="tenant-a", product_name="A", slug="still-draft")
        session.commit()

        assert list_published_products(session) == []
    finally:
        session.rollback()
        session.close()


def test_list_published_products_is_visible_across_tenants_once_published(db_session, tenant_session_factory):
    _seed_tenants(db_session)
    session_a = tenant_session_factory()
    try:
        set_tenant_scope(session_a, "tenant-a")
        product = create_draft_product(session_a, tenant_id="tenant-a", product_name="A", slug="publish-me")
        product.lifecycle_state = ProductLifecycleState.PUBLISHED
        session_a.commit()
        # Captured before closing the session -- `product` is detached
        # (and its attributes expired) once `session_a` closes, so reading
        # `product.product_id` afterward would raise DetachedInstanceError.
        product_id = product.product_id
    finally:
        session_a.close()

    # An unscoped session (no set_tenant_scope call at all) is what the
    # anonymous public catalog looks like -- it must still see the
    # published row, per app/db.py's product_visibility policy.
    session_public = tenant_session_factory()
    try:
        published = list_published_products(session_public)
        assert [p.product_id for p in published] == [product_id]
    finally:
        session_public.rollback()
        session_public.close()
