"""AD-01 "Commercial operations overview" -- get_operations_overview's
own tests. Runs through `tenant_session_factory` + `set_tenant_scope`
like tests/test_product_admin.py, since this reuses
`product_admin.list_products`/`compute_publication_blockers` and must
see the real RLS-scoped rows those functions expect.
"""
from datetime import datetime, timedelta, timezone

from app.db import set_tenant_scope
from app.models.billing import Subscription, SubscriptionState
from app.models.portfolio_version import PortfolioVersion
from app.models.publication import (
    Environment,
    PublicationAction,
    PublicationIntent,
    PublicationState,
    QuantityBasis,
)
from app.models.tenancy import Tenant
from app.services.operations_overview import get_operations_overview
from app.services.product_admin import create_draft_product


def _seed_tenants(db_session):
    db_session.add_all(
        [
            Tenant(tenant_id="tenant-a", display_name="A", environment="LOCAL_SIM"),
            Tenant(tenant_id="tenant-b", display_name="B", environment="LOCAL_SIM"),
        ]
    )
    db_session.commit()


def _portfolio_version(session, *, tenant_id, portfolio_id):
    now = datetime.now(timezone.utc)
    pv = PortfolioVersion(
        tenant_id=tenant_id,
        portfolio_id=portfolio_id,
        version_number=1,
        cash_weight=0,
        research_cutoff=now,
        max_subscriber_capacity=100,
        consent_disclosure_version="v1",
    )
    session.add(pv)
    session.flush()
    return pv


def _intent(**overrides):
    now = datetime.now(timezone.utc)
    defaults = dict(
        environment=Environment.LOCAL_SIM,
        portfolio_version_id="pv-1",
        episode_id="ep-1",
        revision=1,
        action=PublicationAction.OPEN,
        channel="collective2",
        external_strategy_id="strategy-1",
        instrument_id="AAPL",
        quantity="10",
        quantity_basis=QuantityBasis.UNITS,
        price_basis="market",
        policy_hash="policy-hash-1",
        audience_snapshot_hash="audience-hash-1",
        source_revision_ids=["src-rev-1"],
        rights_grant_ids=["grant-1"],
        body_hash="body-hash-1",
        idempotency_key="idem-1",
        valid_from=now,
        expires_at=now + timedelta(hours=1),
        state=PublicationState.UNKNOWN,
    )
    defaults.update(overrides)
    return PublicationIntent(**defaults)


def test_overview_is_all_zero_for_a_fresh_tenant(db_session, tenant_session_factory):
    _seed_tenants(db_session)
    session = tenant_session_factory()
    try:
        set_tenant_scope(session, "tenant-a")
        overview = get_operations_overview(session, tenant_id="tenant-a")
        assert overview.eligible_products == []
        assert overview.blocked_products == []
        assert overview.active_subscriptions_count == 0
        assert overview.unknown_publications_count == 0
    finally:
        session.rollback()
        session.close()


def test_a_fresh_draft_product_appears_in_blocked_products_with_its_blockers(
    db_session, tenant_session_factory
):
    _seed_tenants(db_session)
    session = tenant_session_factory()
    try:
        set_tenant_scope(session, "tenant-a")
        product = create_draft_product(session, tenant_id="tenant-a", product_name="A", slug="overview-blocked")
        session.commit()
        set_tenant_scope(session, "tenant-a")

        overview = get_operations_overview(session, tenant_id="tenant-a")
        assert overview.eligible_products == []
        assert len(overview.blocked_products) == 1
        blocked_product, blockers = overview.blocked_products[0]
        assert blocked_product.product_id == product.product_id
        assert "NO_PORTFOLIO_VERSION_SELECTED" in blockers
    finally:
        session.rollback()
        session.close()


def test_active_paid_subscriptions_are_counted_but_other_states_are_not(db_session, tenant_session_factory):
    _seed_tenants(db_session)
    session = tenant_session_factory()
    try:
        set_tenant_scope(session, "tenant-a")
        now = datetime.now(timezone.utc)
        session.add_all(
            [
                Subscription(
                    tenant_id="tenant-a",
                    tier="ALERTS_ONE",
                    state=SubscriptionState.ACTIVE_PAID,
                    price_cents=3900,
                    currency="usd",
                    current_period_end=now + timedelta(days=30),
                ),
                Subscription(
                    tenant_id="tenant-a",
                    tier="ALERTS_ONE",
                    state=SubscriptionState.PENDING_PAYMENT,
                    price_cents=3900,
                    currency="usd",
                    current_period_end=now + timedelta(days=30),
                ),
            ]
        )
        session.commit()
        set_tenant_scope(session, "tenant-a")

        overview = get_operations_overview(session, tenant_id="tenant-a")
        assert overview.active_subscriptions_count == 1
    finally:
        session.rollback()
        session.close()


def test_unknown_state_publications_are_counted_for_this_tenants_own_portfolio_versions_only(
    db_session, tenant_session_factory
):
    _seed_tenants(db_session)
    session = tenant_session_factory()
    try:
        set_tenant_scope(session, "tenant-a")
        pv_a = _portfolio_version(session, tenant_id="tenant-a", portfolio_id="p-a")
        session.add(_intent(portfolio_version_id=pv_a.portfolio_version_id, idempotency_key="idem-unknown-a"))
        session.add(
            _intent(
                portfolio_version_id=pv_a.portfolio_version_id,
                idempotency_key="idem-acked-a",
                state=PublicationState.ACKNOWLEDGED,
                revision=2,
            )
        )
        session.commit()
        set_tenant_scope(session, "tenant-a")

        overview = get_operations_overview(session, tenant_id="tenant-a")
        assert overview.unknown_publications_count == 1
    finally:
        session.rollback()
        session.close()


def test_another_tenants_unknown_publications_are_not_counted(db_session, tenant_session_factory):
    _seed_tenants(db_session)
    session_a = tenant_session_factory()
    try:
        set_tenant_scope(session_a, "tenant-a")
        pv_a = _portfolio_version(session_a, tenant_id="tenant-a", portfolio_id="p-a")
        session_a.commit()
        set_tenant_scope(session_a, "tenant-a")
        pv_a_id = pv_a.portfolio_version_id
    finally:
        session_a.close()

    session_b = tenant_session_factory()
    try:
        set_tenant_scope(session_b, "tenant-b")
        pv_b = _portfolio_version(session_b, tenant_id="tenant-b", portfolio_id="p-b")
        session_b.add(_intent(portfolio_version_id=pv_b.portfolio_version_id, idempotency_key="idem-unknown-b"))
        session_b.commit()
        set_tenant_scope(session_b, "tenant-b")

        overview_b = get_operations_overview(session_b, tenant_id="tenant-b")
        assert overview_b.unknown_publications_count == 1
    finally:
        session_b.rollback()
        session_b.close()

    session_a2 = tenant_session_factory()
    try:
        set_tenant_scope(session_a2, "tenant-a")
        overview_a = get_operations_overview(session_a2, tenant_id="tenant-a")
        assert overview_a.unknown_publications_count == 0
        assert pv_a_id  # sanity: tenant-a's own PV exists but has no intents
    finally:
        session_a2.rollback()
        session_a2.close()
