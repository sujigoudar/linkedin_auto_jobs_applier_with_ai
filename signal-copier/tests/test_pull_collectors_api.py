"""Owner-gated HTTP API for the Track 6 Slack/Twitter user-context
collector registry (app/main.py's POST/GET /pull-collectors routes).
Mirrors tests/test_telegram_collectors_api.py's own `client` fixture
pattern exactly."""
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
        resp = anon_client.get("/pull-collectors")
        assert resp.status_code in (401, 403, 503)


def test_register_list_and_get_collector(client):
    with client:
        resp = client.post(
            "/pull-collectors",
            json={
                "id": "slack-buyalerts",
                "provider": "slack",
                "auth_mode": "oauth_user_token",
                "identity_ref": "U0123ABC",
                "credential_env_var": "SLACK_USER_BUYALERTS_TOKEN",
                "target_id": "C0123ABC",
                "provider_name": "buyalerts",
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["id"] == "slack-buyalerts"
        assert body["allowed_uses"] == ["private_trading"]
        assert body["health_state"] == "unqualified"

        listed = client.get("/pull-collectors")
        assert listed.status_code == 200
        assert [c["id"] for c in listed.json()["collectors"]] == ["slack-buyalerts"]

        filtered = client.get("/pull-collectors", params={"provider": "twitter"})
        assert filtered.json()["collectors"] == []

        fetched = client.get("/pull-collectors/slack-buyalerts")
        assert fetched.status_code == 200
        assert fetched.json()["id"] == "slack-buyalerts"

        missing = client.get("/pull-collectors/does-not-exist")
        assert missing.status_code == 404


def test_register_with_invalid_provider_is_rejected(client):
    with client:
        resp = client.post(
            "/pull-collectors",
            json={
                "id": "x",
                "provider": "carrier_pigeon",
                "auth_mode": "oauth_user_token",
                "identity_ref": "x",
                "credential_env_var": "SLACK_USER_X_TOKEN",
                "target_id": "1",
                "provider_name": "x",
            },
        )
        assert resp.status_code == 422


def test_record_qualification_evidence_via_api(client):
    with client:
        client.post(
            "/pull-collectors",
            json={
                "id": "twitter-somehandle",
                "provider": "twitter",
                "auth_mode": "oauth2_user_context",
                "identity_ref": "@somehandle",
                "credential_env_var": "TWITTER_USER_SOMEHANDLE_ACCESS_TOKEN",
                "target_id": "9999",
                "provider_name": "somehandle",
            },
        )
        resp = client.post(
            "/pull-collectors/twitter-somehandle/qualification-evidence",
            json={"evidence": {"observed_tweet_id": "1", "method": "manual_owner_confirmation"}},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["health_state"] == "healthy_qualified"

        missing = client.post(
            "/pull-collectors/does-not-exist/qualification-evidence",
            json={"evidence": {}},
        )
        assert missing.status_code == 404
