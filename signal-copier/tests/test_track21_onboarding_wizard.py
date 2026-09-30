"""Track 21: the "+Add Signal Provider" onboarding wizard's backend --
`POST /providers` / `POST /sources` / `POST /connections` (thin wrappers
over app/db.py's SignalStore.register_provider/register_source/
register_connection) and the field-discovery route `GET /connections/
catalog/{connection_type}/setup-fields` (app/connection_catalog.py's
get_connection_setup_fields).
"""
import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app import config as app_config
from app.db import SignalStore


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(app_config, "OWNER_PASSWORD", "test-owner-password")
    monkeypatch.setattr(app_config, "SESSION_SECRET", "test-session-secret")

    test_store = SignalStore(tmp_path / "t21_api.db")
    monkeypatch.setattr(main_module, "store", test_store)
    monkeypatch.setattr(main_module.engine, "store", test_store)

    test_client = TestClient(main_module.app)
    login = test_client.post("/auth/login", json={"password": "test-owner-password"})
    assert login.status_code == 200
    test_client.headers["X-CSRF-Token"] = login.json()["csrf_token"]
    return test_client


# --- POST /providers ---------------------------------------------------


def test_unauthenticated_creation_routes_are_rejected():
    with TestClient(main_module.app) as anon_client:
        assert anon_client.post("/providers", json={"provider_id": "p1", "display_name": "P1"}).status_code in (
            401,
            403,
            503,
        )
        assert anon_client.post(
            "/sources", json={"source_id": "s1", "provider_id": "p1", "platform": "telegram"}
        ).status_code in (401, 403, 503)
        assert anon_client.post(
            "/connections", json={"connection_id": "c1", "connection_type": "telegram_bot"}
        ).status_code in (401, 403, 503)


def test_create_provider_success(client):
    with client:
        resp = client.post(
            "/providers",
            json={
                "provider_id": "acme_trading",
                "display_name": "Acme Trading Desk",
                "website": "https://acme.example",
                "classification": "signal_provider",
                "asset_classes": ["crypto"],
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["id"] == "acme_trading"
        assert body["display_name"] == "Acme Trading Desk"
        assert body["asset_classes"] == ["crypto"]

        # Re-registering the SAME provider_id idempotently re-describes it
        # (register_provider's own documented ON CONFLICT upsert), never a 409.
        resp2 = client.post(
            "/providers",
            json={"provider_id": "acme_trading", "display_name": "Acme Trading Desk (renamed)"},
        )
        assert resp2.status_code == 200
        assert resp2.json()["display_name"] == "Acme Trading Desk (renamed)"


def test_create_provider_invalid_classification_is_422(client):
    with client:
        resp = client.post(
            "/providers",
            json={"provider_id": "bad_provider", "display_name": "Bad", "classification": "not-a-real-classification"},
        )
        assert resp.status_code == 422
        assert "classification" in resp.json()["detail"]


def test_create_provider_empty_display_name_is_422(client):
    with client:
        resp = client.post("/providers", json={"provider_id": "p2", "display_name": ""})
        assert resp.status_code == 422


# --- POST /sources -------------------------------------------------------


def test_create_source_success_after_provider_exists(client):
    with client:
        client.post("/providers", json={"provider_id": "p3", "display_name": "P3"})
        resp = client.post(
            "/sources",
            json={
                "source_id": "p3_telegram",
                "provider_id": "p3",
                "platform": "telegram",
                "url_or_reference": "-1001234567890",
                "role": "PRIMARY",
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["id"] == "p3_telegram"
        assert body["provider_id"] == "p3"
        assert body["role"] == "PRIMARY"


def test_create_source_unknown_provider_is_404(client):
    with client:
        resp = client.post(
            "/sources",
            json={"source_id": "orphan_source", "provider_id": "does-not-exist", "platform": "telegram"},
        )
        assert resp.status_code == 404
        assert "does-not-exist" in resp.json()["detail"]


def test_create_source_unknown_connection_is_404(client):
    with client:
        client.post("/providers", json={"provider_id": "p4", "display_name": "P4"})
        resp = client.post(
            "/sources",
            json={
                "source_id": "p4_source",
                "provider_id": "p4",
                "platform": "telegram",
                "connection_id": "no-such-connection",
            },
        )
        assert resp.status_code == 404
        assert "no-such-connection" in resp.json()["detail"]


def test_create_source_invalid_role_is_422(client):
    with client:
        client.post("/providers", json={"provider_id": "p5", "display_name": "P5"})
        resp = client.post(
            "/sources",
            json={"source_id": "p5_source", "provider_id": "p5", "platform": "telegram", "role": "NOT_A_REAL_ROLE"},
        )
        assert resp.status_code == 422


# --- POST /connections ----------------------------------------------------


def test_create_connection_success(client):
    with client:
        resp = client.post(
            "/connections",
            json={
                "connection_id": "c_telegram_1",
                "connection_type": "telegram_bot",
                "display_name": "Main Telegram bot",
                "credential_reference": "TELEGRAM_MAIN_BOT_TOKEN",
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["id"] == "c_telegram_1"
        assert body["connection_type"] == "telegram_bot"
        # register_connection's own default-capabilities fallback (Track 19)
        # fires because `capabilities` was never given.
        assert body["capabilities"]


def test_create_connection_raw_credential_is_422(client):
    with client:
        resp = client.post(
            "/connections",
            json={
                "connection_id": "c_bad",
                "connection_type": "telegram_bot",
                # Looks like a real Telegram bot token, not an env var NAME --
                # connections.py's looks_like_raw_credential heuristic refuses it.
                "credential_reference": "123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw",
            },
        )
        assert resp.status_code == 422


def test_full_wizard_sequence_provider_then_connection_then_source(client):
    """The exact three-call sequence the onboarding wizard (tr17.js)
    drives for its Advanced Add flow."""
    with client:
        p = client.post("/providers", json={"provider_id": "seq_provider", "display_name": "Sequenced Provider"})
        assert p.status_code == 200

        c = client.post(
            "/connections",
            json={
                "connection_id": "seq_connection",
                "connection_type": "webhook",
                "display_name": "Generic webhook",
            },
        )
        assert c.status_code == 200

        s = client.post(
            "/sources",
            json={
                "source_id": "seq_source",
                "provider_id": "seq_provider",
                "platform": "webhook",
                "connection_id": "seq_connection",
                "url_or_reference": "seq_provider_webhook",
            },
        )
        assert s.status_code == 200
        assert s.json()["connection_id"] == "seq_connection"


# --- GET /connections/catalog/{connection_type}/setup-fields --------------


def test_setup_fields_for_telegram_bot_is_real_and_field_shaped(client):
    with client:
        resp = client.get("/connections/catalog/telegram_bot/setup-fields")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "implemented"
        names = {f["name"] for f in body["fields"]}
        assert "bot_token_env_var" in names
        assert "chat_id" in names
        for f in body["fields"]:
            assert "required" in f
            assert "label" in f


def test_setup_fields_for_webhook_never_fabricates_a_generated_secret(client):
    """Hard rule 11: the generic webhook route is authenticated by a
    single, deployment-wide WEBHOOK_SHARED_SECRET env var (app/main.py's
    POST /webhook/{source_name}), never a secret generated per
    connection -- the setup-fields response must not claim otherwise."""
    with client:
        resp = client.get("/connections/catalog/webhook/setup-fields")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "implemented"
        field_names = {f["name"] for f in body["fields"]}
        assert "source_name" in field_names
        # The honest disclaimer says a secret is NOT generated per connection --
        # it must not instead claim one IS generated/provisioned for the user.
        help_text = " ".join(f.get("help", "") for f in body["fields"]).lower()
        assert "never fabricates" in help_text or "not a secret generated" in help_text
        assert "here is your generated secret" not in help_text


@pytest.mark.parametrize("not_implemented_type", ["gmail_oauth", "sms_api", "android_sms", "make", "zapier", "n8n"])
def test_setup_fields_for_not_implemented_types_are_honest(client, not_implemented_type):
    with client:
        resp = client.get(f"/connections/catalog/{not_implemented_type}/setup-fields")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "not_implemented"
        assert body["fields"] is None
        assert body["notes"]  # honest explanation, never silently empty


def test_setup_fields_unknown_connection_type_is_404(client):
    with client:
        resp = client.get("/connections/catalog/does-not-exist/setup-fields")
        assert resp.status_code == 404
