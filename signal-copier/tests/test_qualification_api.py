"""API-level tests for GET/POST /qualifications (app/qualification.py's
per-exact-route live qualification ladder). Write access is owner-gated
(session + CSRF), same as every other mutation in this build --
release_approved in particular must never be settable by an unauthenticated
caller.
"""
import pytest
from fastapi.testclient import TestClient

from app.db import SignalStore


@pytest.fixture
def client(tmp_path, monkeypatch):
    import app.main as main_module
    from app import config as app_config

    monkeypatch.setattr(app_config, "OWNER_PASSWORD", "test-owner-password")
    monkeypatch.setattr(app_config, "SESSION_SECRET", "test-session-secret")
    monkeypatch.setattr(app_config, "WEBHOOK_SHARED_SECRET", "test-webhook-secret")

    store = SignalStore(tmp_path / "test.db")
    monkeypatch.setattr(main_module, "store", store)
    monkeypatch.setattr(main_module.engine, "store", store)

    test_client = TestClient(main_module.app)
    login = test_client.post("/auth/login", json={"password": "test-owner-password"})
    assert login.status_code == 200
    test_client.headers["X-CSRF-Token"] = login.json()["csrf_token"]
    test_client.headers["X-Webhook-Secret"] = "test-webhook-secret"
    return test_client


def test_post_qualifications_requires_owner_session():
    """No login at all -- must be refused, never silently accepted."""
    import app.main as main_module

    anon_client = TestClient(main_module.app)
    resp = anon_client.post(
        "/qualifications",
        json={
            "adapter_type": "paper",
            "route_key": "paper_main",
            "asset_class": "crypto",
            "product_type": "spot",
            "state": "implemented",
        },
    )
    assert resp.status_code in (401, 403, 503)


def test_post_qualifications_happy_path_then_get(client):
    with client:
        resp = client.post(
            "/qualifications",
            json={
                "adapter_type": "paper",
                "route_key": "paper_main",
                "asset_class": "crypto",
                "product_type": "spot",
                "state": "implemented",
            },
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["state"] == "implemented"

        listing = client.get("/qualifications")
        assert listing.status_code == 200
        routes = listing.json()["routes"]
        assert len(routes) == 1
        assert routes[0]["current_state"] == "implemented"
        assert routes[0]["adapter_type"] == "paper"


def test_post_qualifications_rejects_unknown_adapter_type(client):
    with client:
        resp = client.post(
            "/qualifications",
            json={
                "adapter_type": "not_a_real_broker",
                "route_key": "x",
                "asset_class": "crypto",
                "product_type": "spot",
                "state": "implemented",
            },
        )
        assert resp.status_code == 400
        assert "not a currently-registered broker adapter" in resp.json()["detail"]


def test_post_qualifications_rejects_ladder_skip(client):
    with client:
        resp = client.post(
            "/qualifications",
            json={
                "adapter_type": "paper",
                "route_key": "paper_main",
                "asset_class": "crypto",
                "product_type": "spot",
                "state": "venue_tested",
            },
        )
        assert resp.status_code == 400
        assert "missing prerequisite" in resp.json()["detail"]


def test_post_qualifications_rejects_asset_class_the_adapter_cannot_trade(client):
    """AlpacaBroker declares supported_asset_classes = {equity} -- a
    crypto route on it must be rejected outright, never recorded. Uses
    Alpaca rather than CCXT here since AlpacaBroker has no optional
    third-party dependency and is always registered, unlike CCXTBroker
    (see tests/test_route_qualification.py's own ccxt-specific tests,
    which use pytest.importorskip("ccxt"))."""
    with client:
        resp = client.post(
            "/qualifications",
            json={
                "adapter_type": "alpaca",
                "route_key": "alpaca_main",
                "asset_class": "crypto",
                "product_type": "spot",
                "state": "implemented",
            },
        )
        assert resp.status_code == 400
        assert "does not include" in resp.json()["detail"]


def test_post_qualifications_signalstack_blocked_at_account_entitled(client):
    """The real, live-registered SignalStackBroker's structural feedback
    gap is enforced through the full HTTP write path, not just the pure
    ladder logic tested directly against SignalStore elsewhere."""
    with client:
        for state in ["implemented", "configured", "authenticated"]:
            resp = client.post(
                "/qualifications",
                json={
                    "adapter_type": "signalstack",
                    "route_key": "ss_acct1",
                    "asset_class": "equity",
                    "product_type": "cash_equity",
                    "state": state,
                },
            )
            assert resp.status_code == 200, resp.text

        blocked = client.post(
            "/qualifications",
            json={
                "adapter_type": "signalstack",
                "route_key": "ss_acct1",
                "asset_class": "equity",
                "product_type": "cash_equity",
                "state": "account_entitled",
            },
        )
        assert blocked.status_code == 400
        assert "no real order-status, position-readback, or balance-readback" in blocked.json()["detail"]

        listing = client.get("/qualifications", params={"adapter_type": "signalstack"})
        routes = listing.json()["routes"]
        assert routes[0]["current_state"] == "authenticated"
