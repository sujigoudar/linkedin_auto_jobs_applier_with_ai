"""C06: per-IP rate limiting on the webhook/SMS ingress routes
(app/rate_limit.py). The Limiter is a module-level singleton shared by
every test in the whole pytest session, so every test here resets it
before AND after, to avoid both inheriting stale counts from an earlier
test and leaking its own into a later one.
"""
import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app import config as app_config
from app.db import SignalStore
from app.models import DestinationAccount
from app.rate_limit import limiter


@pytest.fixture(autouse=True)
def _reset_limiter():
    limiter.reset()
    yield
    limiter.reset()


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(app_config, "WEBHOOK_SHARED_SECRET", "test-webhook-secret")
    store = SignalStore(tmp_path / "test.db")
    monkeypatch.setattr(main_module, "store", store)
    monkeypatch.setattr(main_module.engine, "store", store)
    main_module.routing_config.accounts.clear()
    main_module.routing_config.accounts["acct1"] = DestinationAccount(account_id="acct1", broker="paper")
    main_module.routing_config.rules.clear()
    from app.routing import RoutingRule

    main_module.routing_config.rules.append(RoutingRule(source="tradingview", destinations=["acct1"]))
    return TestClient(main_module.app)


def _post_webhook(client):
    return client.post(
        "/webhook/tradingview",
        json={"symbol": "AAPL", "side": "buy", "quantity": 1.0},
        headers={"X-Webhook-Secret": "test-webhook-secret"},
    )


def test_requests_under_the_limit_all_succeed(client):
    with client:
        for _ in range(5):
            response = _post_webhook(client)
            assert response.status_code == 200


def test_exceeding_the_limit_returns_429(client):
    with client:
        # INGRESS_RATE_LIMIT is 30/minute -- send one more than that from
        # the same (test) client IP.
        responses = [_post_webhook(client) for _ in range(31)]
    statuses = [r.status_code for r in responses]
    assert statuses[:30] == [200] * 30
    assert statuses[30] == 429


def test_whatsapp_verify_handshake_is_rate_limited(client, monkeypatch):
    """GET /whatsapp/webhook (Meta's one-time subscription-verify
    handshake) must be rate limited the same as its POST sibling --
    defense-in-depth even though it's normally called once at setup."""
    monkeypatch.setattr(app_config, "WHATSAPP_VERIFY_TOKEN", "test-verify-token")
    with client:
        responses = [
            client.get(
                "/whatsapp/webhook",
                params={"hub.mode": "subscribe", "hub.verify_token": "test-verify-token", "hub.challenge": "1234"},
            )
            for _ in range(31)
        ]
    statuses = [r.status_code for r in responses]
    assert statuses[:30] == [200] * 30
    assert statuses[30] == 429


def test_rate_limit_is_per_route_not_shared_across_endpoints(client, monkeypatch):
    """Exhausting the webhook route's limit must not also block the SMS
    route -- each is limited independently."""
    monkeypatch.setattr(app_config, "TWILIO_AUTH_TOKEN", "")  # SMS ingress stays unconfigured -- fine, only checking status isn't 429
    with client:
        for _ in range(30):
            assert _post_webhook(client).status_code == 200
        assert _post_webhook(client).status_code == 429

        sms_response = client.post("/sms/twilio", data={"Body": "BUY AAPL", "From": "+15551234567"})
    assert sms_response.status_code != 429
