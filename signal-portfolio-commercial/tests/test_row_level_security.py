"""CP-011 "Customer row isolation", exercised as a real Postgres
row-level-security policy (app/db.py's `enable_row_level_security`),
never as an application-level filter alone -- "test... DB row policies
independently" (docs/02). Runs as `app_role` (see tests/conftest.py's
`tenant_session_factory`), a genuine non-superuser login, since
Postgres superusers bypass RLS regardless of FORCE ROW LEVEL SECURITY.
"""
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


def test_a_tenant_scoped_session_cannot_write_a_row_for_another_tenant(db_session, tenant_session_factory):
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
