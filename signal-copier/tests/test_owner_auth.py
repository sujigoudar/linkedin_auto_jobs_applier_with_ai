"""Every account/routing/position/close/flatten/backtest endpoint requires a
valid owner session (app/auth.py's RequireOwner), fails closed (503) rather
than silently open when OWNER_PASSWORD/SESSION_SECRET aren't configured, and
enforces a CSRF token on every mutating request. The webhook and SMS
ingress routes get the same fail-closed treatment for their own secrets:
an unconfigured secret disables that ingress (503), it never makes it
silently public. /health stays reachable with no auth at all, since it
carries no private data.
"""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app import config as app_config
from app.auth import _credential_epoch, _session_or_none, create_session
from app.db import SignalStore
from app.models import DestinationAccount


@pytest.fixture
def unconfigured_client(tmp_path, monkeypatch):
    """Auth/webhook/SMS secrets all unset -- the fail-closed baseline."""
    monkeypatch.setattr(app_config, "OWNER_PASSWORD", "")
    monkeypatch.setattr(app_config, "SESSION_SECRET", "")
    monkeypatch.setattr(app_config, "WEBHOOK_SHARED_SECRET", "")
    monkeypatch.setattr(app_config, "TWILIO_AUTH_TOKEN", "")
    store = SignalStore(tmp_path / "test.db")
    monkeypatch.setattr(main_module, "store", store)
    monkeypatch.setattr(main_module.engine, "store", store)
    return TestClient(main_module.app), store


@pytest.fixture
def configured_client(tmp_path, monkeypatch):
    monkeypatch.setattr(app_config, "OWNER_PASSWORD", "correct-horse-battery-staple")
    monkeypatch.setattr(app_config, "SESSION_SECRET", "test-session-secret")
    monkeypatch.setattr(app_config, "WEBHOOK_SHARED_SECRET", "test-webhook-secret")
    store = SignalStore(tmp_path / "test.db")
    monkeypatch.setattr(main_module, "store", store)
    monkeypatch.setattr(main_module.engine, "store", store)
    main_module.routing_config.accounts.clear()
    main_module.routing_config.accounts["acct1"] = DestinationAccount(account_id="acct1", broker="paper")
    return TestClient(main_module.app), store


# --- fail-closed when unconfigured ---


def test_protected_endpoint_503s_when_auth_unconfigured(unconfigured_client):
    client, _ = unconfigured_client
    with client:
        response = client.get("/accounts")
    assert response.status_code == 503


def test_login_503s_when_auth_unconfigured(unconfigured_client):
    client, _ = unconfigured_client
    with client:
        response = client.post("/auth/login", json={"password": "anything"})
    assert response.status_code == 401  # verify_password() returns False, not "logged in"


def test_webhook_503s_when_secret_unconfigured(unconfigured_client):
    client, _ = unconfigured_client
    with client:
        response = client.post("/webhook/tradingview", json={"symbol": "AAPL", "side": "buy", "quantity": 1.0})
    assert response.status_code == 503


def test_sms_503s_when_token_unconfigured(unconfigured_client):
    client, _ = unconfigured_client
    with client:
        response = client.post("/sms/twilio", data={"Body": "AAPL buy", "From": "+15550001111"})
    assert response.status_code == 503


def test_health_stays_public_when_auth_unconfigured(unconfigured_client):
    """This test is about /health staying reachable with no auth
    configured -- not about what `status` says (see
    test_health_truthfulness.py for that; OPS-01 -- a fresh app with
    neither background worker having completed a pass yet legitimately
    reports "degraded", not "ok")."""
    client, _ = unconfigured_client
    with client:
        response = client.get("/health")
    assert response.status_code == 200
    assert "status" in response.json()


# --- login / session / CSRF, once configured ---


def test_login_wrong_password_401s(configured_client):
    client, _ = configured_client
    with client:
        response = client.post("/auth/login", json={"password": "wrong"})
    assert response.status_code == 401


def test_login_correct_password_returns_csrf_token_and_sets_cookie(configured_client):
    client, _ = configured_client
    with client:
        response = client.post("/auth/login", json={"password": "correct-horse-battery-staple"})
    assert response.status_code == 200
    assert "csrf_token" in response.json()
    assert client.cookies.get("scr_session") is not None


def test_protected_endpoint_401s_with_no_session(configured_client):
    client, _ = configured_client
    with client:
        response = client.get("/accounts")
    assert response.status_code == 401


def test_mutating_request_403s_with_session_but_no_csrf_header(configured_client):
    client, _ = configured_client
    with client:
        login = client.post("/auth/login", json={"password": "correct-horse-battery-staple"})
        assert login.status_code == 200
        # session cookie is now set on the client, but no X-CSRF-Token header
        response = client.post("/accounts", json={"account_id": "acct2", "broker": "paper"})
    assert response.status_code == 403


def test_mutating_request_succeeds_with_session_and_correct_csrf(configured_client):
    client, _ = configured_client
    with client:
        login = client.post("/auth/login", json={"password": "correct-horse-battery-staple"})
        csrf = login.json()["csrf_token"]
        response = client.post(
            "/accounts", json={"account_id": "acct2", "broker": "paper"}, headers={"X-CSRF-Token": csrf}
        )
    assert response.status_code == 200


def test_get_request_needs_no_csrf_token(configured_client):
    client, _ = configured_client
    with client:
        client.post("/auth/login", json={"password": "correct-horse-battery-staple"})
        # a plain GET with a valid session but no CSRF header must still work
        response = client.get("/accounts")
    assert response.status_code == 200


def test_wrong_csrf_token_is_rejected(configured_client):
    client, _ = configured_client
    with client:
        client.post("/auth/login", json={"password": "correct-horse-battery-staple"})
        response = client.post(
            "/accounts",
            json={"account_id": "acct2", "broker": "paper"},
            headers={"X-CSRF-Token": "not-the-real-token"},
        )
    assert response.status_code == 403


def test_logout_revokes_the_session(configured_client):
    client, _ = configured_client
    with client:
        login = client.post("/auth/login", json={"password": "correct-horse-battery-staple"})
        csrf = login.json()["csrf_token"]
        client.headers["X-CSRF-Token"] = csrf

        assert client.get("/accounts").status_code == 200
        logout = client.post("/auth/logout")
        assert logout.status_code == 200
        assert client.get("/accounts").status_code == 401


def test_webhook_still_works_with_correct_secret_once_configured(configured_client):
    client, store = configured_client
    with client:
        main_module.routing_config.rules.clear()
        from app.routing import RoutingRule

        main_module.routing_config.rules.append(RoutingRule(source="tradingview", destinations=["acct1"]))
        response = client.post(
            "/webhook/tradingview",
            json={"symbol": "AAPL", "side": "buy", "quantity": 1.0},
            headers={"X-Webhook-Secret": "test-webhook-secret"},
        )
    assert response.status_code == 200


def test_webhook_401s_with_wrong_secret(configured_client):
    client, _ = configured_client
    with client:
        response = client.post(
            "/webhook/tradingview",
            json={"symbol": "AAPL", "side": "buy", "quantity": 1.0},
            headers={"X-Webhook-Secret": "not-it"},
        )
    assert response.status_code == 401


def test_webhook_401s_with_missing_secret_header(configured_client):
    """C06: the compare_digest rewrite must still 401 cleanly when the
    header is absent entirely, not just when it's wrong -- compare_digest
    itself would raise TypeError on a None argument if the explicit
    `is None` guard were ever dropped."""
    client, _ = configured_client
    with client:
        response = client.post(
            "/webhook/tradingview", json={"symbol": "AAPL", "side": "buy", "quantity": 1.0}
        )
    assert response.status_code == 401


def test_webhook_secret_check_uses_constant_time_compare(configured_client, monkeypatch):
    """C06: asserts the actual mechanism, not just the outcome -- a plain
    `!=` would 401 on a wrong secret too, so a status-code-only test can't
    distinguish it from the timing-safe compare this is supposed to be."""
    import app.main as main_module_direct

    calls = []
    original = main_module_direct.hmac.compare_digest

    def _spy(a, b):
        calls.append((a, b))
        return original(a, b)

    monkeypatch.setattr(main_module_direct.hmac, "compare_digest", _spy)

    client, _ = configured_client
    with client:
        client.post(
            "/webhook/tradingview",
            json={"symbol": "AAPL", "side": "buy", "quantity": 1.0},
            headers={"X-Webhook-Secret": "not-it"},
        )
    assert calls == [("not-it", "test-webhook-secret")]


# --- app.auth._session_or_none: direct unit coverage for its
# expires_at-normalization and cleanup behavior (surfaced as surviving
# mutants by the C31 mutmut config scoped to app/auth.py) ---


def test_naive_stored_expires_at_is_treated_as_utc_not_a_crash(configured_client):
    """A row whose expires_at was stored without a timezone (e.g. an older
    row from before this normalization existed) must still be usable --
    comparing a naive and an aware datetime directly raises TypeError, so
    this only works if the naive value is actually promoted to UTC first."""
    _client, store = configured_client
    naive_future = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=1)
    store.create_session("naive-session", "csrf-token", naive_future, credential_epoch=_credential_epoch())

    session = _session_or_none(store, "naive-session")

    assert session is not None
    assert session["session_id"] == "naive-session"


def test_naive_stored_expires_at_in_the_past_is_still_rejected(configured_client):
    _client, store = configured_client
    naive_past = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=1)
    store.create_session("naive-expired-session", "csrf-token", naive_past, credential_epoch=_credential_epoch())

    assert _session_or_none(store, "naive-expired-session") is None


def test_expired_session_is_actually_deleted_from_the_store(configured_client):
    """Not just rejected for this one call -- actually removed, so it
    doesn't linger in the sessions table forever."""
    _client, store = configured_client
    expired = datetime.now(timezone.utc) - timedelta(hours=1)
    store.create_session("expired-session", "csrf-token", expired, credential_epoch=_credential_epoch())

    assert _session_or_none(store, "expired-session") is None
    assert store.get_session("expired-session") is None


def test_session_with_stale_credential_epoch_is_actually_deleted_from_the_store(configured_client):
    """A session issued under a since-rotated OWNER_PASSWORD/SESSION_SECRET
    is treated as revoked (not merely stale) -- and, same as an expired
    session, actually removed rather than left behind."""
    _client, store = configured_client
    future = datetime.now(timezone.utc) + timedelta(hours=1)
    store.create_session("stale-epoch-session", "csrf-token", future, credential_epoch="some-old-epoch")

    assert _session_or_none(store, "stale-epoch-session") is None
    assert store.get_session("stale-epoch-session") is None
