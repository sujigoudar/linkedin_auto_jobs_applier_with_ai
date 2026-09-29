"""CP-011 "Customer row isolation", exercised as a real Postgres
row-level-security policy (app/db.py's `enable_row_level_security`),
never as an application-level filter alone -- "test... DB row policies
independently" (docs/02). Runs as `app_role` (see tests/conftest.py's
`tenant_session_factory`), a genuine non-superuser login, since
Postgres superusers bypass RLS regardless of FORCE ROW LEVEL SECURITY.
"""
import pytest
from sqlalchemy.exc import DBAPIError
from sqlalchemy import text

from app.db import set_tenant_scope
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
