"""ADR-0009 -- real, RLS-enforced end-to-end tests for the sign-in/
verify-email membership-bootstrap lookup.

Every OTHER test that drives `/auth/signin` and `/auth/verify`
(tests/test_id01_id02_id03_auth_routes.py) overrides `get_db_session`
with the raw Postgres SUPERUSER `db_session` fixture, which bypasses
row-level security entirely -- exactly why this bug went untested. This
file overrides it instead with a session bound to `app_role` (via
`tenant_session_factory`), the genuine non-superuser, `FORCE ROW LEVEL
SECURITY`-subject login role production actually uses, so a failure
here is a real production-shaped failure, not a fixture artifact.
"""
from fastapi.testclient import TestClient

from app.api.dependencies import get_db_session
from app.main import create_app


def _client_with_rls_session(tenant_session_factory):
    """Returns (TestClient, the single underlying app_role session).
    One shared session across every request in a test -- exactly like a
    real per-request-but-same-connection-pool web session, and the only
    way `set_config(..., true)` (transaction-scoped) calls made by one
    request are still relevant to immediately-following assertions."""
    app = create_app()
    session = tenant_session_factory()

    def override_get_db_session():
        yield session

    app.dependency_overrides[get_db_session] = override_get_db_session
    return TestClient(app, follow_redirects=False), session


def _signup_and_get_verify_token(client):
    import re

    response = client.post(
        "/auth/signup",
        data={
            "email": "rls-bootstrap@example.com",
            "password": "a-real-password-1",
            "tenant_display_name": "RLS Co",
            "accept_terms": "1",
        },
    )
    assert response.status_code == 303, response.text
    location = response.headers["location"]
    match = re.search(r"[?&]token=([A-Za-z0-9_-]+)", location)
    assert match is not None
    return match.group(1)


def test_verify_email_finds_its_own_membership_under_real_rls(tenant_session_factory):
    """Reproduces + proves the fix for the production-blocking bug: under
    real FORCE ROW LEVEL SECURITY (app_role, not the superuser fixture),
    `verify_email_page`'s own `select(Membership).where(Membership.user_id
    == user.user_id)` lookup must find the caller's own just-created
    membership row and log them in -- not silently return zero rows."""
    client, session = _client_with_rls_session(tenant_session_factory)
    try:
        token = _signup_and_get_verify_token(client)

        response = client.get(f"/auth/verify?token={token}")

        assert response.status_code == 200, response.text
        assert "now signed in" in response.text
        assert "verified_no_membership" not in response.text
        assert "cp_session" in response.cookies
    finally:
        session.rollback()
        session.close()


def test_sign_in_finds_its_own_membership_under_real_rls(tenant_session_factory):
    """Same bug, the `sign_in_submit` half: after a real signup+verify,
    signing back in must still find the caller's own membership row
    under real RLS enforcement, not fail with "This identity has no
    tenant membership.\""""
    client, session = _client_with_rls_session(tenant_session_factory)
    try:
        token = _signup_and_get_verify_token(client)
        client.get(f"/auth/verify?token={token}")
        client.cookies.clear()

        response = client.post(
            "/auth/signin",
            data={"email": "rls-bootstrap@example.com", "password": "a-real-password-1"},
        )

        assert response.status_code == 303, response.text
        assert response.headers["location"] == "/app"
        assert "cp_session" in response.cookies
    finally:
        session.rollback()
        session.close()


def test_membership_self_lookup_policy_cannot_read_another_users_membership_row(
    db_session, tenant_session_factory
):
    """Adversarial cross-user test: the new, narrow `membership_self_
    lookup` policy must ONLY ever let a session see the row matching its
    OWN `app.current_user_id` -- never let user A's session read user
    B's membership row by supplying B's user_id in the WHERE clause."""
    from app.db import set_current_user_scope
    from app.models.tenancy import Membership, MembershipRole, Tenant, UserIdentity
    from sqlalchemy import select

    db_session.add_all(
        [
            Tenant(tenant_id="tenant-x", display_name="X", environment="LOCAL_SIM"),
            Tenant(tenant_id="tenant-y", display_name="Y", environment="LOCAL_SIM"),
            UserIdentity(user_id="user-x", email="x@example.com"),
            UserIdentity(user_id="user-y", email="y@example.com"),
        ]
    )
    db_session.flush()
    db_session.add(Membership(tenant_id="tenant-x", user_id="user-x", role=MembershipRole.CUSTOMER))
    db_session.add(Membership(tenant_id="tenant-y", user_id="user-y", role=MembershipRole.CUSTOMER))
    db_session.commit()

    session = tenant_session_factory()
    try:
        # User A's own session context: app.current_user_id = "user-x".
        set_current_user_scope(session, "user-x")

        # User A can find their own row.
        own_row = session.execute(
            select(Membership).where(Membership.user_id == "user-x")
        ).scalars().first()
        assert own_row is not None
        assert own_row.tenant_id == "tenant-x"

        # But querying for user B's row, while still scoped as user A,
        # must return NOTHING -- the policy is keyed off the SESSION'S
        # OWN app.current_user_id, not off whatever user_id happens to
        # appear in the query's WHERE clause.
        other_row = session.execute(
            select(Membership).where(Membership.user_id == "user-y")
        ).scalars().first()
        assert other_row is None
    finally:
        session.rollback()
        session.close()


def test_membership_self_lookup_policy_grants_nothing_when_unset(db_session, tenant_session_factory):
    """Fail-closed, same convention as
    test_row_level_security.py::test_no_tenant_scope_set_means_no_rows_visible_not_all_rows:
    a session that never calls `set_current_user_scope` (i.e. every
    other request path in this codebase) must see NO rows through this
    policy -- not silently fall back to "everything visible.\""""
    from app.models.tenancy import Membership, MembershipRole, Tenant, UserIdentity
    from sqlalchemy import select

    db_session.add_all(
        [
            Tenant(tenant_id="tenant-z", display_name="Z", environment="LOCAL_SIM"),
            UserIdentity(user_id="user-z", email="z@example.com"),
        ]
    )
    db_session.flush()
    db_session.add(Membership(tenant_id="tenant-z", user_id="user-z", role=MembershipRole.CUSTOMER))
    db_session.commit()

    session = tenant_session_factory()
    try:
        rows = session.execute(select(Membership).where(Membership.user_id == "user-z")).scalars().all()
        assert rows == []
    finally:
        session.rollback()
        session.close()
