"""AD-10 "Publication intent and cohort detail" --
app/services/publication_admin.py's own tests. Runs through
`tenant_session_factory` + `set_tenant_scope` like
tests/test_operations_overview.py, since PublicationIntent's own
tenant scoping is a join through PortfolioVersion, which IS RLS-scoped.
"""
from datetime import datetime, timedelta, timezone

from app.db import set_tenant_scope
from app.models.portfolio_version import PortfolioVersion
from app.models.publication import (
    Environment,
    PublicationAction,
    PublicationIntent,
    PublicationState,
    QuantityBasis,
)
from app.models.tenancy import Tenant
from app.services.publication_admin import get_publication_intent_detail


def _seed_tenants(db_session):
    db_session.add_all(
        [
            Tenant(tenant_id="tenant-a", display_name="A", environment="LOCAL_SIM"),
            Tenant(tenant_id="tenant-b", display_name="B", environment="LOCAL_SIM"),
        ]
    )
    db_session.commit()


def _portfolio_version(session, *, tenant_id, portfolio_id, portfolio_version_id="pv-1"):
    now = datetime.now(timezone.utc)
    pv = PortfolioVersion(
        portfolio_version_id=portfolio_version_id,
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


def test_get_publication_intent_detail_is_none_for_an_unknown_intent(db_session, tenant_session_factory):
    _seed_tenants(db_session)
    session = tenant_session_factory()
    try:
        set_tenant_scope(session, "tenant-a")
        assert get_publication_intent_detail(session, "nonexistent-intent", tenant_id="tenant-a") is None
    finally:
        session.close()


def test_get_publication_intent_detail_returns_the_real_intent(db_session, tenant_session_factory):
    _seed_tenants(db_session)
    session = tenant_session_factory()
    try:
        set_tenant_scope(session, "tenant-a")
        _portfolio_version(session, tenant_id="tenant-a", portfolio_id="P01")
        intent = _intent(portfolio_version_id="pv-1")
        session.add(intent)
        session.commit()
        set_tenant_scope(session, "tenant-a")

        detail = get_publication_intent_detail(session, intent.intent_id, tenant_id="tenant-a")
        assert detail is not None
        assert detail.intent.intent_id == intent.intent_id
        assert detail.portfolio_version.portfolio_id == "P01"
    finally:
        session.close()


def test_get_publication_intent_detail_is_none_for_a_cross_tenant_intent(db_session, tenant_session_factory):
    _seed_tenants(db_session)
    session_a = tenant_session_factory()
    try:
        set_tenant_scope(session_a, "tenant-a")
        _portfolio_version(session_a, tenant_id="tenant-a", portfolio_id="P01")
        intent = _intent(portfolio_version_id="pv-1")
        session_a.add(intent)
        session_a.commit()
        intent_id = intent.intent_id
    finally:
        session_a.close()

    session_b = tenant_session_factory()
    try:
        set_tenant_scope(session_b, "tenant-b")
        assert get_publication_intent_detail(session_b, intent_id, tenant_id="tenant-b") is None
    finally:
        session_b.rollback()
        session_b.close()
