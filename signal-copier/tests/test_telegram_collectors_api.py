"""Owner-gated HTTP API for the Track 5 Telegram collector registry
(app/main.py's POST/GET /telegram-collectors routes). Mirrors
tests/test_sig01_duplicate_submission_protection.py's own `client`
fixture pattern."""
import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app import config as app_config
from app.db import SignalStore


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(app_config, "OWNER_PASSWORD", "test-owner-password")
    monkeypatch.setattr(app_config, "SESSION_SECRET", "test-session-secret")

    store = SignalStore(tmp_path / "test.db")
    monkeypatch.setattr(main_module, "store", store)
    monkeypatch.setattr(main_module.engine, "store", store)

    test_client = TestClient(main_module.app)
    login = test_client.post("/auth/login", json={"password": "test-owner-password"})
    assert login.status_code == 200
    test_client.headers["X-CSRF-Token"] = login.json()["csrf_token"]
    return test_client


def test_unauthenticated_request_is_rejected():
    with TestClient(main_module.app) as anon_client:
        resp = anon_client.get("/telegram-collectors")
        assert resp.status_code in (401, 403, 503)


def test_register_list_and_get_collector(client):
    with client:
        resp = client.post(
            "/telegram-collectors",
            json={
                "id": "buyalerts",
                "connection_mode": "user_account",
                "identity_ref": "+15551234567",
                "credential_env_var": "TELEGRAM_USER_BUYALERTS_SESSION_PATH",
                "chat_id": "-100123",
                "provider_name": "buyalerts",
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["id"] == "buyalerts"
        assert body["allowed_uses"] == ["private_trading"]
        assert body["health_state"] == "unqualified"

        listed = client.get("/telegram-collectors")
        assert listed.status_code == 200
        assert [c["id"] for c in listed.json()["collectors"]] == ["buyalerts"]

        fetched = client.get("/telegram-collectors/buyalerts")
        assert fetched.status_code == 200
        assert fetched.json()["id"] == "buyalerts"

        missing = client.get("/telegram-collectors/does-not-exist")
        assert missing.status_code == 404


def test_register_with_invalid_connection_mode_is_rejected(client):
    with client:
        resp = client.post(
            "/telegram-collectors",
            json={
                "id": "x",
                "connection_mode": "carrier_pigeon",
                "identity_ref": "x",
                "credential_env_var": "TELEGRAM_BOT_TOKEN",
                "chat_id": "1",
                "provider_name": "x",
            },
        )
        assert resp.status_code == 422


def test_record_qualification_evidence_via_api(client):
    with client:
        client.post(
            "/telegram-collectors",
            json={
                "id": "buyalerts",
                "connection_mode": "user_account",
                "identity_ref": "+1",
                "credential_env_var": "TELEGRAM_USER_BUYALERTS_SESSION_PATH",
                "chat_id": "-100123",
                "provider_name": "buyalerts",
            },
        )
        resp = client.post(
            "/telegram-collectors/buyalerts/qualification-evidence",
            json={"evidence": {"observed_message_id": "1", "method": "manual_owner_confirmation"}, "noforwards": False},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["health_state"] == "healthy_qualified"

        missing = client.post(
            "/telegram-collectors/does-not-exist/qualification-evidence",
            json={"evidence": {}},
        )
        assert missing.status_code == 404
