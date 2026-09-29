"""CP-011 "Customer row isolation", exercised as a real Postgres
row-level-security policy (app/db.py's `enable_row_level_security`),
never as an application-level filter alone -- "test... DB row policies
independently" (docs/02). Runs as `app_role` (see tests/conftest.py's
`tenant_session_factory`), a genuine non-superuser login, since
Postgres superusers bypass RLS regardless of FORCE ROW LEVEL SECURITY.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy.exc import DBAPIError
from sqlalchemy import text

from app.db import set_tenant_scope
from app.models.portfolio_version import PortfolioVersion, PortfolioVersionSleeve
from app.models.publication import (
    Environment,
    PublicationAction,
    PublicationIntent,
    QuantityBasis,
)
from app.models.sleeve import Sleeve
from app.models.tenancy import CustomerProfile, Membership, MembershipRole, Tenant, UserIdentity


def _seed_two_tenants_with_customers(db_session):
    db_session.add_all(
        [
            Tenant(tenant_id="tenant-a", display_name="A", environment="LOCAL_SIM"),
            Tenant(tenant_id="tenant-b", display_name="B", environment="LOCAL_SIM"),
            UserIdentity(user_id="user-a", email="a@example.com"),
            UserIdentity(user_id="user-b", email="b@example.com"),
        ]
    )
    db_session.flush()
    db_session.add(Membership(tenant_id="tenant-a", user_id="user-a", role=MembershipRole.CUSTOMER))
    db_session.add(Membership(tenant_id="tenant-b", user_id="user-b", role=MembershipRole.CUSTOMER))
    db_session.flush()
    db_session.add(CustomerProfile(tenant_id="tenant-a", user_id="user-a", display_name="A Co", residence_jurisdiction="US"))
    db_session.add(CustomerProfile(tenant_id="tenant-b", user_id="user-b", display_name="B Co", residence_jurisdiction="US"))
    db_session.commit()


def test_a_tenant_scoped_session_only_sees_its_own_rows(db_session, tenant_session_factory):
    _seed_two_tenants_with_customers(db_session)

    session = tenant_session_factory()
    try:
        set_tenant_scope(session, "tenant-a")
        rows = session.query(CustomerProfile).all()
        assert [row.tenant_id for row in rows] == ["tenant-a"]

        set_tenant_scope(session, "tenant-b")
        rows = session.query(CustomerProfile).all()
        assert [row.tenant_id for row in rows] == ["tenant-b"]
    finally:
        session.rollback()
        session.close()


def test_no_tenant_scope_set_means_no_rows_visible_not_all_rows(db_session, tenant_session_factory):
    """Fail-closed: an unset `app.tenant_id` must never fall back to
    "show everything" -- that would make forgetting to call
    `set_tenant_scope` before a query a silent cross-tenant leak instead
    of an empty result."""
    _seed_two_tenants_with_customers(db_session)

    session = tenant_session_factory()
    try:
        rows = session.query(CustomerProfile).all()
        assert rows == []
    finally:
        session.rollback()
        session.close()


def test_a_tenant_scoped_session_cannot_read_a_row_for_another_tenant_by_primary_key(db_session, tenant_session_factory):
    """Renamed from the misleadingly-named original ('...cannot_write...')
    which only ever performed a `session.get` -- a READ, not a write
    attempt, and therefore not evidence RLS blocks writes at all. This
    keeps that real (read) coverage under an accurate name; the write
    claim is now actually tested below."""
    _seed_two_tenants_with_customers(db_session)

    session = tenant_session_factory()
    try:
        set_tenant_scope(session, "tenant-a")
        # Membership isn't itself referenced here, but CustomerProfile IS
        # RLS-protected -- attempting to read tenant-b's row while scoped
        # to tenant-a must come back empty, not raise and not return it.
        other = session.get(CustomerProfile, "tenant-b")
        assert other is None
    finally:
        session.rollback()
        session.close()


def test_a_tenant_scoped_session_cannot_insert_a_row_for_another_tenant(db_session, tenant_session_factory):
    """The actual write-side claim the old (misnamed) test above never
    verified: a session scoped to tenant-a inserting a row whose own
    tenant_id is tenant-c must be rejected by the RLS policy's WITH CHECK
    behavior (no explicit WITH CHECK is defined, so Postgres reuses the
    USING expression for INSERT too -- see app/db.py's `enable_row_level_
    security`), not silently accepted because the FK to memberships is
    otherwise satisfied."""
    _seed_two_tenants_with_customers(db_session)
    db_session.add(Tenant(tenant_id="tenant-c", display_name="C", environment="LOCAL_SIM"))
    db_session.add(UserIdentity(user_id="user-c", email="c@example.com"))
    db_session.flush()
    db_session.add(Membership(tenant_id="tenant-c", user_id="user-c", role=MembershipRole.CUSTOMER))
    db_session.commit()

    session = tenant_session_factory()
    try:
        set_tenant_scope(session, "tenant-a")
        session.add(CustomerProfile(tenant_id="tenant-c", user_id="user-c", display_name="C Co", residence_jurisdiction="US"))
        with pytest.raises(DBAPIError, match="row-level security"):
            session.commit()
    finally:
        session.rollback()
        session.close()

    # And the row genuinely never landed -- not just that the INSERT
    # statement raised while some earlier autoflush half-applied it.
    admin_check = db_session.get(CustomerProfile, "tenant-c")
    assert admin_check is None


def test_a_tenant_scoped_session_cannot_update_another_tenants_row_via_raw_sql(db_session, tenant_session_factory):
    """The UPDATE-side write claim: even a raw SQL UPDATE naming
    tenant-b's row explicitly by primary key (bypassing the ORM's own
    tenant-scoped query entirely) must affect zero rows while scoped to
    tenant-a -- RLS is enforced by Postgres itself on the table, not by
    the ORM happening to filter its own SELECTs."""
    _seed_two_tenants_with_customers(db_session)

    session = tenant_session_factory()
    try:
        set_tenant_scope(session, "tenant-a")
        result = session.execute(
            text("UPDATE customer_profiles SET display_name = 'hijacked' WHERE tenant_id = :tid"),
            {"tid": "tenant-b"},
        )
        assert result.rowcount == 0
        session.commit()
    finally:
        session.rollback()
        session.close()

    # tenant-b's real row is untouched.
    untouched = db_session.get(CustomerProfile, "tenant-b")
    assert untouched.display_name == "B Co"


def _seed_two_tenants_with_publication_data(db_session):
    """One `PortfolioVersion` (+ its one `PortfolioVersionSleeve` row)
    and one `PublicationIntent` per tenant -- the exact shape
    `alembic/versions/85f9e0e6c123_publication_intent_and_sleeve_tenant_id.py`'s
    backfill targets."""
    now = datetime.now(timezone.utc)
    db_session.add_all(
        [
            Tenant(tenant_id="tenant-a", display_name="A", environment="LOCAL_SIM"),
            Tenant(tenant_id="tenant-b", display_name="B", environment="LOCAL_SIM"),
        ]
    )
    db_session.flush()

    for tenant_id, suffix in (("tenant-a", "a"), ("tenant-b", "b")):
        sleeve = Sleeve(
            sleeve_id=f"sleeve-{suffix}",
            tenant_id=tenant_id,
            provider="acme",
            analyst="jane",
            strategy_horizon="swing",
            asset_class="EQUITY",
            parser_version="v1",
            execution_policy_id="ep-1",
            cost_model_id="cm-1",
            capacity_policy_id="cap-1",
            risk_unit_id="ru-1",
            history_origin="acme",
        )
        db_session.add(sleeve)
        db_session.flush()

        pv = PortfolioVersion(
            portfolio_version_id=f"pv-{suffix}",
            tenant_id=tenant_id,
            portfolio_id=f"p-{suffix}",
            version_number=1,
            cash_weight=0,
            research_cutoff=now,
            max_subscriber_capacity=100,
            consent_disclosure_version="v1",
        )
        db_session.add(pv)
        db_session.flush()

        db_session.add(
            PortfolioVersionSleeve(
                portfolio_version_id=f"pv-{suffix}",
                sleeve_id=f"sleeve-{suffix}",
                weight=Decimal("1.0"),
                tenant_id=tenant_id,
            )
        )
        db_session.add(
            PublicationIntent(
                intent_id=f"intent-{suffix}",
                tenant_id=tenant_id,
                environment=Environment.LOCAL_SIM,
                portfolio_version_id=f"pv-{suffix}",
                episode_id="ep-1",
                revision=1,
                action=PublicationAction.OPEN,
                channel="collective2",
                external_strategy_id=f"strategy-{suffix}",
                instrument_id="AAPL",
                quantity="10",
                quantity_basis=QuantityBasis.UNITS,
                price_basis="market",
                policy_hash=f"policy-hash-{suffix}",
                audience_snapshot_hash=f"audience-hash-{suffix}",
                source_revision_ids=[f"src-rev-{suffix}"],
                rights_grant_ids=[f"grant-{suffix}"],
                body_hash=f"body-hash-{suffix}",
                idempotency_key=f"idem-{suffix}",
                valid_from=now,
                expires_at=now + timedelta(hours=1),
            )
        )
    db_session.commit()


def test_a_tenant_scoped_session_cannot_read_another_tenants_publication_intent_by_primary_key(
    db_session, tenant_session_factory
):
    """The RLS backstop `85f9e0e6c123` adds: even though
    `PublicationIntent` is otherwise only ever scoped by an application-
    level join through `PortfolioVersion` (app/services/publication_admin.py,
    customer_alerts.py, operations_overview.py), a direct `session.get`
    by primary key -- bypassing that join entirely -- must still come
    back empty for another tenant's row, not return it."""
    _seed_two_tenants_with_publication_data(db_session)

    session = tenant_session_factory()
    try:
        set_tenant_scope(session, "tenant-a")
        own = session.get(PublicationIntent, "intent-a")
        assert own is not None

        other = session.get(PublicationIntent, "intent-b")
        assert other is None
    finally:
        session.rollback()
        session.close()


def test_a_tenant_scoped_session_cannot_read_another_tenants_publication_intent_via_raw_sql(
    db_session, tenant_session_factory
):
    """Same claim as the primary-key read above, but via a raw SQL
    SELECT naming the other tenant's row explicitly by primary key --
    RLS is enforced by Postgres on the table itself, not merely by the
    ORM's own `get()` happening to filter."""
    _seed_two_tenants_with_publication_data(db_session)

    session = tenant_session_factory()
    try:
        set_tenant_scope(session, "tenant-a")
        rows = session.execute(
            text("SELECT intent_id FROM publication_intents WHERE intent_id = :iid"),
            {"iid": "intent-b"},
        ).all()
        assert rows == []
    finally:
        session.rollback()
        session.close()


def test_a_tenant_scoped_session_cannot_read_another_tenants_portfolio_version_sleeve_by_primary_key(
    db_session, tenant_session_factory
):
    """Same backstop claim for `portfolio_version_sleeves`
    (app/services/portfolio_rights.py otherwise scopes it only via a
    join through `PortfolioVersion`) -- a direct read by its own
    (portfolio_version_id, sleeve_id) primary key for another tenant's
    row must come back empty."""
    _seed_two_tenants_with_publication_data(db_session)

    session = tenant_session_factory()
    try:
        set_tenant_scope(session, "tenant-a")
        own = session.get(PortfolioVersionSleeve, ("pv-a", "sleeve-a"))
        assert own is not None

        other = session.get(PortfolioVersionSleeve, ("pv-b", "sleeve-b"))
        assert other is None
    finally:
        session.rollback()
        session.close()


def test_no_tenant_scope_set_means_no_publication_intents_or_sleeves_visible(db_session, tenant_session_factory):
    """Fail-closed, same claim as
    `test_no_tenant_scope_set_means_no_rows_visible_not_all_rows` above,
    for the two tables this backstop was added to."""
    _seed_two_tenants_with_publication_data(db_session)

    session = tenant_session_factory()
    try:
        assert session.query(PublicationIntent).all() == []
        assert session.query(PortfolioVersionSleeve).all() == []
    finally:
        session.rollback()
        session.close()
