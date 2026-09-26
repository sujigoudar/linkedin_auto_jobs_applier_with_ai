"""SEC-06: a valid Twilio signature proves the request transited Twilio with
the right account's auth token -- a transport check. It says nothing about
whether the specific sender is allowed to submit trading instructions.
Without a separate sender allow-list, anyone who can text the configured
Twilio number could generate trades."""
import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app import config as app_config


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(app_config, "OWNER_PASSWORD", "")
    monkeypatch.setattr(app_config, "SESSION_SECRET", "")
    return TestClient(main_module.app)


def test_sms_ingress_fails_closed_with_no_authorized_senders_configured(client, monkeypatch):
    monkeypatch.setattr(app_config, "TWILIO_AUTH_TOKEN", "test-token")
    monkeypatch.setattr(app_config, "TWILIO_WEBHOOK_URL", "https://example.test/sms/twilio")
    monkeypatch.setattr(app_config, "TWILIO_ALLOWED_FROM_NUMBERS", [])

    with client:
        response = client.post("/sms/twilio", data={"Body": "AAPL buy", "From": "+15550001111"})

    assert response.status_code == 503
    assert "TWILIO_ALLOWED_FROM_NUMBERS" in response.json()["detail"]


def test_sms_ingress_rejects_a_genuine_but_unauthorized_sender(client, monkeypatch):
    twilio_validator = pytest.importorskip("twilio.request_validator")

    monkeypatch.setattr(app_config, "TWILIO_AUTH_TOKEN", "test-token")
    monkeypatch.setattr(app_config, "TWILIO_WEBHOOK_URL", "https://example.test/sms/twilio")
    monkeypatch.setattr(app_config, "TWILIO_ALLOWED_FROM_NUMBERS", ["+15559998888"])
    monkeypatch.setattr(twilio_validator.RequestValidator, "validate", lambda self, url, params, sig: True)

    with client:
        response = client.post(
            "/sms/twilio",
            data={"Body": "AAPL buy", "From": "+15550001111"},  # a real Twilio sender, not the allow-listed one
            headers={"X-Twilio-Signature": "irrelevant-mocked-valid"},
        )

    assert response.status_code == 403
    assert "not an authorized trading source" in response.json()["detail"]


def test_sms_ingress_accepts_an_authorized_sender(client, monkeypatch):
    twilio_validator = pytest.importorskip("twilio.request_validator")

    monkeypatch.setattr(app_config, "TWILIO_AUTH_TOKEN", "test-token")
    monkeypatch.setattr(app_config, "TWILIO_WEBHOOK_URL", "https://example.test/sms/twilio")
    monkeypatch.setattr(app_config, "TWILIO_ALLOWED_FROM_NUMBERS", ["+15550001111"])
    monkeypatch.setattr(twilio_validator.RequestValidator, "validate", lambda self, url, params, sig: True)

    with client:
        response = client.post(
            "/sms/twilio",
            data={"Body": "AAPL buy 5", "From": "+15550001111"},
            headers={"X-Twilio-Signature": "irrelevant-mocked-valid"},
        )

    assert response.status_code == 200
