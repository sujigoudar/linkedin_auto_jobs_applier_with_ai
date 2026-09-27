"""Real HTTP tests against app/main.py's FastAPI app, using the same
disposable Postgres session as every other test (the DB dependency is
overridden to the test's own `db_session`; authentication is exercised
for real against app/services/auth.py's actual JWT issuer/decoder)."""
import hashlib
import hmac
import time

import pytest
from fastapi.testclient import TestClient

from app import config
from app.api.dependencies import get_db_session
from app.main import create_app
from app.models.tenancy import MembershipRole
from app.models.webhook_event import ProcessedWebhookEvent
from app.services.auth import issue_token


@pytest.fixture
def client(db_session):
    app = create_app()

    def override_get_db_session():
        yield db_session

    app.dependency_overrides[get_db_session] = override_get_db_session
    return TestClient(app)


def test_healthz_reports_ok(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_me_requires_a_bearer_token(client):
    response = client.get("/api/v1/me")
    assert response.status_code == 401


def test_me_rejects_a_tampered_token(client):
    token = issue_token("tenant-a", "user-a", MembershipRole.OWNER)
    tampered = token[:-1] + ("A" if token[-1] != "A" else "B")
    response = client.get("/api/v1/me", headers={"Authorization": f"Bearer {tampered}"})
    assert response.status_code == 401


def test_me_returns_the_real_decoded_scope(client):
    token = issue_token("tenant-a", "user-a", MembershipRole.OWNER)
    response = client.get("/api/v1/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert response.json() == {"tenant_id": "tenant-a", "user_id": "user-a", "role": "owner"}


def _sign(payload: bytes, secret: str) -> str:
    ts = int(time.time())
    signed_payload = f"{ts}.".encode() + payload
    signature = hmac.new(secret.encode(), signed_payload, hashlib.sha256).hexdigest()
    return f"t={ts},v1={signature}"


def test_stripe_webhook_accepts_a_correctly_signed_event(client, db_session):
    payload = b'{"id": "evt_api_test_1", "type": "customer.subscription.updated"}'
    header = _sign(payload, config.STRIPE_WEBHOOK_SECRET)

    response = client.post(
        "/api/v1/billing/webhook/stripe",
        content=payload,
        headers={"stripe-signature": header, "content-type": "application/json"},
    )
    assert response.status_code == 200
    assert response.json() == {"received": True, "processed": True}

    assert db_session.get(ProcessedWebhookEvent, "evt_api_test_1") is not None


def test_stripe_webhook_rejects_a_bad_signature(client):
    payload = b'{"id": "evt_api_test_2"}'
    header = _sign(payload, "wrong-secret")

    response = client.post(
        "/api/v1/billing/webhook/stripe",
        content=payload,
        headers={"stripe-signature": header, "content-type": "application/json"},
    )
    assert response.status_code == 400


def test_stripe_webhook_is_idempotent_on_replay(client):
    payload = b'{"id": "evt_api_test_3"}'
    header = _sign(payload, config.STRIPE_WEBHOOK_SECRET)

    first = client.post(
        "/api/v1/billing/webhook/stripe",
        content=payload,
        headers={"stripe-signature": header, "content-type": "application/json"},
    )
    second = client.post(
        "/api/v1/billing/webhook/stripe",
        content=payload,
        headers={"stripe-signature": _sign(payload, config.STRIPE_WEBHOOK_SECRET), "content-type": "application/json"},
    )
    assert first.json()["processed"] is True
    assert second.json()["processed"] is False


def test_stripe_webhook_rejects_a_missing_event_id(client):
    payload = b'{"type": "customer.subscription.updated"}'
    header = _sign(payload, config.STRIPE_WEBHOOK_SECRET)

    response = client.post(
        "/api/v1/billing/webhook/stripe",
        content=payload,
        headers={"stripe-signature": header, "content-type": "application/json"},
    )
    assert response.status_code == 400
