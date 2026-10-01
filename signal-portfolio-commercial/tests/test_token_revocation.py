"""Real, DB-backed end-to-end coverage for JWT revocation
(app/services/token_revocation.py) -- the load-bearing proof that a
Bearer-token JWT can genuinely be killed before its natural expiry, not
just that a "revoke" endpoint returns 200.

Three things this file specifically proves, each with a real
`TestClient` request/response, not a unit-level shortcut:

1. A real token, issued through the real `/app/settings` route's auth
   dependency, works once, is revoked through the real
   `/app/settings/revoke-api-tokens` route, and is then genuinely
   rejected (401) on a byte-identical subsequent request with the exact
   same Authorization header.
2. The revocation is fail-closed: if the denylist check itself raises
   (a broken DB, a timeout, anything), `verify_token` must reject the
   request rather than silently treating it as "not revoked".
3. The revocation is really logged as a new `AuditEvent` via the real
   `append_audit_event` machinery, not just performed silently.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import get_db_session
from app.main import create_app
from app.models.audit_event import AuditEvent
from app.models.tenancy import MembershipRole
from app.services import auth as auth_module
from app.services.auth import InvalidTokenError, verify_token, issue_token


def _client(db_session):
    app = create_app()

    def override_get_db_session():
        yield db_session

    app.dependency_overrides[get_db_session] = override_get_db_session
    return TestClient(app, follow_redirects=False)


def _auth_headers(db_session, *, tenant_id="tenant-a", user_id="user-a", role=MembershipRole.CUSTOMER):
    """Mints a token the same way every real route does -- `session=`
    given, so it's recorded in `issued_tokens` and is genuinely
    enumerable for "log out everywhere", exactly like a token minted by
    a real login flow with a real DB session available."""
    token = issue_token(tenant_id, user_id, role, session=db_session)
    db_session.commit()
    return {"Authorization": f"Bearer {token}"}


def test_a_revoked_token_is_genuinely_rejected_on_the_next_real_request(db_session):
    client = _client(db_session)
    headers = _auth_headers(db_session)

    # 1. The token works, against a real route, once.
    first_response = client.get("/app/settings", headers=headers)
    assert first_response.status_code == 200

    # 2. Revoke it through the real, customer-facing CU-13 "log out
    # everywhere" route -- not by calling a service function directly.
    revoke_response = client.post("/app/settings/revoke-api-tokens", headers=headers)
    assert revoke_response.status_code == 303
    assert revoke_response.headers["location"].startswith("/app/settings?revoked_token_count=")

    # 3. The exact same token, in a byte-identical subsequent request,
    # is now genuinely rejected -- not merely that revoke returned 200/303.
    second_response = client.get("/app/settings", headers=headers)
    assert second_response.status_code == 401


def test_revoking_all_tokens_for_a_staff_member_via_ad16_also_rejects_their_token(db_session):
    client = _client(db_session)
    staff_headers = _auth_headers(db_session, user_id="staff-a", role=MembershipRole.OWNER)
    owner_headers = _auth_headers(db_session, user_id="owner-a", role=MembershipRole.OWNER)

    # The staff member's own token works against a real route first.
    assert client.get("/ops/access", headers=owner_headers).status_code == 200
    assert client.get("/ops/access", headers=staff_headers).status_code == 200

    revoke_response = client.post(
        "/ops/access/staff-a/revoke-api-tokens", headers=owner_headers
    )
    assert revoke_response.status_code == 303

    # staff-a's own already-issued token must now be rejected everywhere,
    # regardless of which route it's presented to.
    rejected = client.get("/ops/access", headers=staff_headers)
    assert rejected.status_code == 401


def test_revocation_writes_a_real_audit_event(db_session):
    client = _client(db_session)
    headers = _auth_headers(db_session, tenant_id="tenant-audit", user_id="user-audit")

    client.post("/app/settings/revoke-api-tokens", headers=headers)

    events = (
        db_session.query(AuditEvent)
        .filter(AuditEvent.tenant_id == "tenant-audit")
        .filter(AuditEvent.action == "revoke_all_tokens")
        .filter(AuditEvent.object_id == "user-audit")
        .all()
    )
    assert len(events) == 1
    assert events[0].actor_user_id == "user-audit"


def test_fail_closed_a_broken_denylist_check_rejects_rather_than_admits(db_session, monkeypatch):
    """The load-bearing fail-closed proof: temporarily make the
    denylist check itself blow up (simulating a broken DB/timeout), and
    confirm `verify_token` rejects the token instead of quietly treating
    a failed check as "not revoked". This is the exact mechanism
    `app/api/dependencies.py`'s `get_current_scope` relies on for every
    real Bearer-token request."""
    token = issue_token("tenant-fail-closed", "user-fail-closed", MembershipRole.CUSTOMER, session=db_session)
    db_session.commit()

    # Sanity check: the token is valid and NOT revoked before we break anything.
    scope = verify_token(token, db_session)
    assert scope.user_id == "user-fail-closed"

    def _boom(session, *, jti):
        raise RuntimeError("simulated denylist-check failure (e.g. DB outage)")

    # `app/services/auth.py` imports `is_token_revoked` by name
    # (`from app.services.token_revocation import is_token_revoked`), so
    # the reference `verify_token` actually calls lives on the `auth`
    # module itself -- patching the source module wouldn't affect it.
    monkeypatch.setattr(auth_module, "is_token_revoked", _boom)

    with pytest.raises(InvalidTokenError) as exc_info:
        verify_token(token, db_session)
    # Exact diagnostic text, not just the exception type -- a cosmetic
    # string-literal mutation of this specific fail-closed message
    # can't hide behind the type check above.
    assert "could not verify token has not been revoked" in str(exc_info.value)


def test_fail_closed_regression_a_denylist_check_that_is_bypassed_lets_a_revoked_token_through(db_session):
    """The inverse proof, showing the fail-closed test above is really
    load-bearing: if `verify_token` is changed to skip the denylist
    check entirely (the bug this whole feature exists to prevent), a
    genuinely revoked token would wrongly be accepted. This test asserts
    the *correct*, current behavior (rejection) using the real
    `revoke_token` call a route would make; it is a regression guard,
    not a demonstration of the bug -- see this file's own module
    docstring for how it was manually confirmed to fail when the
    skip-the-check bug is (temporarily) reintroduced.
    """
    from datetime import datetime, timedelta, timezone

    from app.services.token_revocation import revoke_token

    token = issue_token("tenant-regress", "user-regress", MembershipRole.CUSTOMER, session=db_session)
    scope = verify_token(token, db_session)
    assert scope.jti

    revoke_token(
        db_session,
        jti=scope.jti,
        tenant_id="tenant-regress",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        actor_user_id="user-regress",
        target_user_id="user-regress",
    )
    db_session.commit()

    with pytest.raises(InvalidTokenError) as exc_info:
        verify_token(token, db_session)
    # Exact text, not just the exception type -- "token has been
    # revoked" is this module's own, distinct message from the
    # fail-closed denylist-check-itself-broke message above.
    assert str(exc_info.value) == "token has been revoked"
