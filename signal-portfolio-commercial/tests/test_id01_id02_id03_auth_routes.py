"""Real end-to-end HTTP tests for ID-01/ID-02/ID-03's own real routes --
signup, email verification, sign-in, password recovery, logout, all the
way through app/api/dependencies.py's own cookie-authenticated
get_current_scope path (not just the Bearer-token path every other
dashboard test already exercises). Follows tests/test_dashboard_routes.py's
own `_client(db_session)` convention."""
import re

from fastapi.testclient import TestClient

from app.api.dependencies import get_db_session
from app.main import create_app


def _client(db_session):
    app = create_app()

    def override_get_db_session():
        yield db_session

    app.dependency_overrides[get_db_session] = override_get_db_session
    return TestClient(app, follow_redirects=False)


def _extract_query_param(url: str, name: str) -> str:
    # `[^&]+` alone is too greedy against a rendered HTML page (as opposed
    # to a bare redirect Location header): the recovery page embeds the
    # same reset_url_token query param twice with no second `&` anywhere
    # after it, so `[^&]+` would swallow the rest of the document. Tokens
    # here are always `secrets.token_urlsafe(...)` output, which only ever
    # emits `[A-Za-z0-9_-]`, so anchor to that alphabet instead.
    match = re.search(rf"[?&]{name}=([A-Za-z0-9_-]+)", url)
    assert match is not None, f"{name!r} not found in {url!r}"
    return match.group(1)


def test_signup_then_verify_link_logs_in_and_reaches_the_cookie_gated_overview(db_session):
    client = _client(db_session)

    signup_response = client.post(
        "/auth/signup",
        data={"email": "new@example.com", "password": "a-real-password-1", "tenant_display_name": "New Co", "accept_terms": "1"},
    )
    assert signup_response.status_code == 303
    verify_redirect_url = signup_response.headers["location"]
    assert verify_redirect_url.startswith("/auth/verify")
    token = _extract_query_param(verify_redirect_url, "token")

    # Following the pending redirect shows the real verify link on the page
    # (no email provider wired in -- see local_auth.py's own docstring).
    pending_page = client.get(verify_redirect_url)
    assert pending_page.status_code == 200
    assert f"token={token}" in pending_page.text

    # Visiting the real verify link (without pending=1) actually consumes it.
    verify_response = client.get(f"/auth/verify?token={token}")
    assert verify_response.status_code == 200
    assert "now signed in" in verify_response.text
    assert "cp_session" in verify_response.cookies

    # The session cookie set during verification is enough to reach a
    # real cookie-gated page -- proves app/api/dependencies.py's own
    # cookie fallback path, not just the Bearer-token one.
    overview_response = client.get("/app")
    assert overview_response.status_code == 200


def test_verify_with_unknown_token_shows_invalid_state_not_a_500(db_session):
    client = _client(db_session)

    response = client.get("/auth/verify?token=not-a-real-token")

    assert response.status_code == 400
    assert "invalid or has expired" in response.text


def test_sign_in_with_correct_password_sets_session_cookie(db_session):
    client = _client(db_session)
    signup = client.post(
        "/auth/signup",
        data={"email": "signin@example.com", "password": "correct-password-1", "tenant_display_name": "T", "accept_terms": "1"},
    )
    token = _extract_query_param(signup.headers["location"], "token")
    client.get(f"/auth/verify?token={token}")
    client.cookies.clear()  # start the sign-in test unauthenticated

    response = client.post("/auth/signin", data={"email": "signin@example.com", "password": "correct-password-1"})

    assert response.status_code == 303
    assert response.headers["location"] == "/app"
    assert "cp_session" in response.cookies


def test_sign_in_with_wrong_password_shows_error_not_a_500(db_session):
    client = _client(db_session)
    client.post(
        "/auth/signup",
        data={"email": "wrongpw@example.com", "password": "the-real-password", "tenant_display_name": "T", "accept_terms": "1"},
    )
    client.cookies.clear()

    response = client.post("/auth/signin", data={"email": "wrongpw@example.com", "password": "totally-wrong"})

    assert response.status_code == 401
    assert "Incorrect email or password" in response.text
    assert "cp_session" not in response.cookies


def test_signup_without_accepting_terms_is_refused(db_session):
    client = _client(db_session)

    response = client.post(
        "/auth/signup", data={"email": "noterms@example.com", "password": "pw-12345678", "tenant_display_name": "T"},
    )

    assert response.status_code == 400
    assert "accept the terms" in response.text


def test_signup_duplicate_email_does_not_leak_which_check_failed(db_session):
    client = _client(db_session)
    client.post(
        "/auth/signup",
        data={"email": "taken@example.com", "password": "pw-12345678", "tenant_display_name": "T", "accept_terms": "1"},
    )

    response = client.post(
        "/auth/signup",
        data={"email": "taken@example.com", "password": "different-pw", "tenant_display_name": "T2", "accept_terms": "1"},
    )

    assert response.status_code == 400
    assert "Could not create this account" in response.text


def test_recovery_request_then_reset_then_sign_in_with_new_password(db_session):
    client = _client(db_session)
    client.post(
        "/auth/signup",
        data={"email": "recover@example.com", "password": "old-password-1", "tenant_display_name": "T", "accept_terms": "1"},
    )
    client.cookies.clear()

    request_response = client.post("/auth/recovery/request", data={"email": "recover@example.com"})
    assert request_response.status_code == 200
    assert "reset_url_token=" in request_response.text
    reset_token = _extract_query_param(request_response.text, "reset_url_token")

    reset_response = client.post(
        "/auth/recovery/reset", data={"reset_url_token": reset_token, "new_password": "brand-new-password-2"}
    )
    assert reset_response.status_code == 303

    signin_response = client.post("/auth/signin", data={"email": "recover@example.com", "password": "brand-new-password-2"})
    assert signin_response.status_code == 303
    assert "cp_session" in signin_response.cookies


def test_recovery_request_for_unknown_email_shows_the_same_generic_notice(db_session):
    """No email enumeration -- the response body must not reveal whether
    the address has an account."""
    client = _client(db_session)

    response = client.post("/auth/recovery/request", data={"email": "never-existed@example.com"})

    assert response.status_code == 200
    assert "If that email has an account" in response.text
    assert "reset_url_token=" not in response.text


def test_logout_clears_session_and_revokes_cookie_access(db_session):
    client = _client(db_session)
    signup = client.post(
        "/auth/signup",
        data={"email": "logout@example.com", "password": "pw-12345678", "tenant_display_name": "T", "accept_terms": "1"},
    )
    token = _extract_query_param(signup.headers["location"], "token")
    client.get(f"/auth/verify?token={token}")
    assert client.get("/app").status_code == 200

    logout_response = client.post("/auth/logout")
    assert logout_response.status_code == 303

    assert client.get("/app").status_code == 401


def test_cookie_authenticated_mutation_without_csrf_header_is_refused(db_session):
    """The web session cookie path requires a matching X-CSRF-Token on
    every mutation -- same two-part design as signal-copier's own
    app/auth.py, for the same reason (a cookie alone isn't
    CSRF-resistant)."""
    client = _client(db_session)
    signup = client.post(
        "/auth/signup",
        data={"email": "csrf@example.com", "password": "pw-12345678", "tenant_display_name": "T", "accept_terms": "1"},
    )
    token = _extract_query_param(signup.headers["location"], "token")
    client.get(f"/auth/verify?token={token}")

    # get_current_scope's own CSRF check runs as a FastAPI dependency,
    # BEFORE the route body ever executes -- a POST to any cookie-scoped
    # mutation with no X-CSRF-Token must be refused with 403 regardless
    # of whether the target object exists.
    response = client.post("/app/copy/does-not-exist/cancel")

    assert response.status_code == 403
    assert "CSRF" in response.text


def test_cookie_authenticated_mutation_with_wrong_csrf_token_is_refused(db_session):
    """A well-formed but wrong CSRF header (the shape a guessing/timing
    attack would send) must still be flatly rejected -- `get_current_scope`
    compares it against the real token with `hmac.compare_digest`, never
    a short-circuiting `!=`, precisely so a near-miss guess is denied
    exactly like a completely wrong one, in constant time."""
    client = _client(db_session)
    signup = client.post(
        "/auth/signup",
        data={"email": "csrf-wrong@example.com", "password": "pw-12345678", "tenant_display_name": "T", "accept_terms": "1"},
    )
    token = _extract_query_param(signup.headers["location"], "token")
    client.get(f"/auth/verify?token={token}")

    response = client.post(
        "/app/copy/does-not-exist/cancel",
        headers={"X-CSRF-Token": "x" * 43},
    )
    assert response.status_code == 403
    assert "CSRF" in response.text


def test_csrf_comparison_uses_constant_time_compare():
    """Static guard against reintroducing a timing side-channel: the
    cookie-mutation CSRF check must go through `hmac.compare_digest`,
    never a plain `==`/`!=` on the two token strings directly."""
    import inspect

    from app.api import dependencies

    source = inspect.getsource(dependencies.get_current_scope)
    assert "hmac.compare_digest" in source
    assert "csrf_token != web_session.csrf_token" not in source


def test_cookie_authenticated_form_post_with_csrf_field_succeeds(db_session):
    """Regression test for the CSRF-delivery bug: `create_web_session`'s
    own `csrf_token` return value used to be minted and thrown away
    (never set as a cookie, never in any template, never returned
    anywhere a browser could read it back), so EVERY real cookie+form
    POST -- the only way a real, JavaScript-free browser session in this
    app can submit a mutation -- was unconditionally rejected with 403,
    completely untested (every other POST test in this suite, and in
    test_dashboard_routes.py, uses Bearer auth, which this CSRF check
    exempts). This is the one test in the suite that drives the actual
    cookie+form path: pulls the token back out of the now-real `cp_csrf`
    cookie (exactly what `_base.html`'s own injection script does) and
    submits it as a `csrf_token` form field (exactly what a real
    server-rendered `<form method="post">` submit does -- no
    X-CSRF-Token header at all, unlike the negative test above)."""
    client = _client(db_session)
    signup = client.post(
        "/auth/signup",
        data={"email": "csrf-ok@example.com", "password": "pw-12345678", "tenant_display_name": "T", "accept_terms": "1"},
    )
    token = _extract_query_param(signup.headers["location"], "token")
    client.get(f"/auth/verify?token={token}")

    csrf_cookie = client.cookies.get("cp_csrf")
    assert csrf_cookie, "cp_csrf cookie was never set on sign-in/verify -- the actual bug this test guards against"

    response = client.post(
        "/app/settings",
        data={
            "workspace_name": "irrelevant",
            "display_name": "New Name",
            "timezone_name": "UTC",
            "theme": "system",
            "density": "comfortable",
            "number_locale": "en-US",
            "reduce_motion": "system",
            "csrf_token": csrf_cookie,
        },
    )
    assert response.status_code == 303, response.text
    assert response.headers["location"] == "/app/settings"
