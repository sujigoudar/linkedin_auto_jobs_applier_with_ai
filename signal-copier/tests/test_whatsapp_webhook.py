"""WhatsApp signal source (app/sources/whatsapp.py, app/main.py's
/whatsapp/webhook routes) -- the official WhatsApp Business Cloud API,
verified with a plain HMAC-SHA256 over the raw request body (Meta's
`X-Hub-Signature-256`), the same trust model as /sms/twilio's Twilio
signature: a valid signature only proves the request transited Meta with
the right app secret, not that the specific sender is authorized to
submit trading instructions -- that's WHATSAPP_ALLOWED_FROM_NUMBERS, a
separate, fail-closed check.
"""
from __future__ import annotations

import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app import config as app_config
from app.db import SignalStore

APP_SECRET = "test-app-secret"


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(app_config, "OWNER_PASSWORD", "")
    monkeypatch.setattr(app_config, "SESSION_SECRET", "")
    # A fresh store per test -- otherwise idempotency-key state (and
    # anything else) leaks across tests in this file (and beyond) via
    # app.main's module-level `store` global, silently masking exactly
    # the kind of regression these tests exist to catch.
    store = SignalStore(tmp_path / "test.db")
    monkeypatch.setattr(main_module, "store", store)
    return TestClient(main_module.app)


def _signed_post(client, payload: dict, secret: str = APP_SECRET):
    body = json.dumps(payload).encode("utf-8")
    signature = "sha256=" + hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return client.post(
        "/whatsapp/webhook", content=body, headers={"X-Hub-Signature-256": signature, "Content-Type": "application/json"}
    )


def _text_message_payload(*, from_number: str, body: str, message_id: str = "wamid.TEST1") -> dict:
    return {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "messages": [
                                {"id": message_id, "from": from_number, "type": "text", "text": {"body": body}}
                            ]
                        }
                    }
                ]
            }
        ]
    }


# --- GET verification handshake ---


def test_verify_handshake_fails_closed_when_unconfigured(client):
    with client:
        response = client.get("/whatsapp/webhook", params={"hub.mode": "subscribe", "hub.verify_token": "x"})
    assert response.status_code == 503
    assert "WHATSAPP_VERIFY_TOKEN" in response.json()["detail"]


def test_verify_handshake_rejects_wrong_token(client, monkeypatch):
    monkeypatch.setattr(app_config, "WHATSAPP_VERIFY_TOKEN", "correct-token")
    with client:
        response = client.get(
            "/whatsapp/webhook",
            params={"hub.mode": "subscribe", "hub.verify_token": "wrong-token", "hub.challenge": "1234"},
        )
    assert response.status_code == 403


def test_verify_handshake_echoes_challenge_for_the_correct_token(client, monkeypatch):
    monkeypatch.setattr(app_config, "WHATSAPP_VERIFY_TOKEN", "correct-token")
    with client:
        response = client.get(
            "/whatsapp/webhook",
            params={"hub.mode": "subscribe", "hub.verify_token": "correct-token", "hub.challenge": "1234"},
        )
    assert response.status_code == 200
    assert response.text == "1234"


# --- POST message delivery ---


def test_ingress_fails_closed_with_no_app_secret_configured(client):
    with client:
        response = _signed_post(client, _text_message_payload(from_number="15550001111", body="AAPL buy"))
    assert response.status_code == 503
    assert "WHATSAPP_APP_SECRET" in response.json()["detail"]


def test_ingress_fails_closed_with_no_authorized_senders_configured(client, monkeypatch):
    monkeypatch.setattr(app_config, "WHATSAPP_APP_SECRET", APP_SECRET)
    monkeypatch.setattr(app_config, "WHATSAPP_ALLOWED_FROM_NUMBERS", [])
    with client:
        response = _signed_post(client, _text_message_payload(from_number="15550001111", body="AAPL buy"))
    assert response.status_code == 503
    assert "WHATSAPP_ALLOWED_FROM_NUMBERS" in response.json()["detail"]


def test_ingress_rejects_an_invalid_signature(client, monkeypatch):
    monkeypatch.setattr(app_config, "WHATSAPP_APP_SECRET", APP_SECRET)
    monkeypatch.setattr(app_config, "WHATSAPP_ALLOWED_FROM_NUMBERS", ["15550001111"])
    with client:
        response = _signed_post(
            client, _text_message_payload(from_number="15550001111", body="AAPL buy"), secret="wrong-secret"
        )
    assert response.status_code == 401


def test_a_genuine_but_unauthorized_sender_is_skipped_not_rejected(client, monkeypatch):
    """Unlike Twilio's one-message-per-POST webhook, a real, correctly
    signed WhatsApp delivery from an unauthorized sender must not make
    the whole batch fail -- Meta expects 200 for anything correctly
    signed, regardless of downstream business decisions."""
    monkeypatch.setattr(app_config, "WHATSAPP_APP_SECRET", APP_SECRET)
    monkeypatch.setattr(app_config, "WHATSAPP_ALLOWED_FROM_NUMBERS", ["15559998888"])
    with client:
        response = _signed_post(
            # a body that DOES parse (matches the accepted-sender test) --
            # otherwise this would "pass" even with the authorization check
            # deleted, since an unparseable body is skipped for an
            # unrelated reason either way.
            client,
            _text_message_payload(from_number="15550001111", body="AAPL buy 5"),  # not the allow-listed number
        )
    assert response.status_code == 200
    assert response.json()["processed"] == 0


def test_ingress_accepts_an_authorized_sender(client, monkeypatch):
    monkeypatch.setattr(app_config, "WHATSAPP_APP_SECRET", APP_SECRET)
    monkeypatch.setattr(app_config, "WHATSAPP_ALLOWED_FROM_NUMBERS", ["15550001111"])
    with client:
        response = _signed_post(
            client, _text_message_payload(from_number="15550001111", body="AAPL buy 5")
        )
    assert response.status_code == 200
    assert response.json()["processed"] == 1


def test_non_text_messages_are_skipped(client, monkeypatch):
    monkeypatch.setattr(app_config, "WHATSAPP_APP_SECRET", APP_SECRET)
    monkeypatch.setattr(app_config, "WHATSAPP_ALLOWED_FROM_NUMBERS", ["15550001111"])
    payload = {
        "entry": [
            {"changes": [{"value": {"messages": [{"id": "wamid.IMG1", "from": "15550001111", "type": "image"}]}}]}
        ]
    }
    with client:
        response = _signed_post(client, payload)
    assert response.status_code == 200
    assert response.json()["processed"] == 0


def test_a_status_only_delivery_with_no_messages_is_a_no_op(client, monkeypatch):
    """Delivery/read receipts arrive as `statuses`, not `messages` -- must
    not raise or be treated as an unparseable signal."""
    monkeypatch.setattr(app_config, "WHATSAPP_APP_SECRET", APP_SECRET)
    monkeypatch.setattr(app_config, "WHATSAPP_ALLOWED_FROM_NUMBERS", ["15550001111"])
    payload = {"entry": [{"changes": [{"value": {"statuses": [{"id": "wamid.X", "status": "delivered"}]}}]}]}
    with client:
        response = _signed_post(client, payload)
    assert response.status_code == 200
    assert response.json()["processed"] == 0


def test_a_redelivered_message_is_not_processed_twice(client, monkeypatch):
    monkeypatch.setattr(app_config, "WHATSAPP_APP_SECRET", APP_SECRET)
    monkeypatch.setattr(app_config, "WHATSAPP_ALLOWED_FROM_NUMBERS", ["15550001111"])
    payload = _text_message_payload(from_number="15550001111", body="AAPL buy 5", message_id="wamid.DUP1")
    with client:
        first = _signed_post(client, payload)
        second = _signed_post(client, payload)  # Meta redelivering the exact same message id

    assert first.status_code == 200 and first.json()["processed"] == 1
    assert second.status_code == 200 and second.json()["processed"] == 0
