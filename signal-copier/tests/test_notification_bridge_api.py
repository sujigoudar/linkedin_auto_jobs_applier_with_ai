"""Owner-gated HTTP API for the Track 10 notification-bridge device
registry AND the device-authenticated ingestion endpoint (app/main.py's
POST /notification-bridge/devices, POST /ingest/notification-bridge/
{device_id}). Mirrors tests/test_sig01_duplicate_submission_protection.py's
own `client` fixture pattern (real routing/account wiring so a "live"
classification is verified against a REAL PaperBroker order, not just a
status string)."""
from datetime import datetime, timedelta, timezone

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
    main_module.routing_config.accounts.clear()
    main_module.routing_config.rules.clear()
    main_module.provider_registry.providers.clear()

    test_client = TestClient(main_module.app)
    login = test_client.post("/auth/login", json={"password": "test-owner-password"})
    assert login.status_code == 200
    test_client.headers["X-CSRF-Token"] = login.json()["csrf_token"]
    return test_client


def _register_device(client, app_packages=("com.example.tradingapp",), provider_mapping=None):
    resp = client.post(
        "/notification-bridge/devices",
        json={
            "device_id": "phone-1",
            "app_packages": list(app_packages),
            "provider_mapping": provider_mapping,
        },
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _wire_paper_route(client, source: str):
    # WP-01: entry has no quantity, so add fixed_quantity
    resp = client.post("/accounts", json={"account_id": "acct1", "broker": "paper", "fixed_quantity": 1.0})
    assert resp.status_code == 200, resp.text
    resp = client.post("/routing-rules", json={"source": source, "destinations": ["acct1"]})
    assert resp.status_code == 200, resp.text


def _auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# -- Unauthenticated access -----------------------------------------------


def test_unauthenticated_request_to_owner_routes_is_rejected():
    with TestClient(main_module.app) as anon_client:
        resp = anon_client.get("/notification-bridge/devices")
        assert resp.status_code in (401, 403, 503)


# -- Registration -----------------------------------------------------------


def test_register_returns_pairing_token_exactly_once_and_never_the_hash(client):
    with client:
        device = _register_device(client)
        assert device["device_id"] == "phone-1"
        assert "pairing_token" in device
        assert "pairing_token_hash" not in device
        assert device["health_state"] == "never_paired"

        # A read of the same device afterward never exposes the token
        # again -- it truly was returned exactly once.
        fetched = client.get("/notification-bridge/devices/phone-1")
        assert fetched.status_code == 200
        assert "pairing_token" not in fetched.json()
        assert "pairing_token_hash" not in fetched.json()


def test_register_rejects_device_with_no_app_packages(client):
    with client:
        resp = client.post("/notification-bridge/devices", json={"device_id": "phone-1", "app_packages": []})
        assert resp.status_code == 422


def test_list_and_get_devices(client):
    with client:
        _register_device(client)
        listed = client.get("/notification-bridge/devices")
        assert listed.status_code == 200
        assert [d["device_id"] for d in listed.json()["devices"]] == ["phone-1"]

        missing = client.get("/notification-bridge/devices/does-not-exist")
        assert missing.status_code == 404


# -- Ingestion auth ---------------------------------------------------------


def test_ingest_to_unregistered_device_is_404(client):
    with client:
        resp = client.post("/ingest/notification-bridge/nope", json={"events": []}, headers=_auth_headers("x"))
        assert resp.status_code == 404


def test_ingest_with_no_authorization_header_is_401(client):
    with client:
        _register_device(client)
        resp = client.post("/ingest/notification-bridge/phone-1", json={"events": []})
        assert resp.status_code == 401


def test_ingest_with_wrong_token_is_401(client):
    with client:
        _register_device(client)
        resp = client.post(
            "/ingest/notification-bridge/phone-1", json={"events": []}, headers=_auth_headers("totally-wrong-token")
        )
        assert resp.status_code == 401


def test_ingest_with_correct_token_and_empty_batch_records_heartbeat(client):
    with client:
        device = _register_device(client)
        resp = client.post(
            "/ingest/notification-bridge/phone-1", json={"events": []}, headers=_auth_headers(device["pairing_token"])
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["results"] == []

        fetched = client.get("/notification-bridge/devices/phone-1").json()
        assert fetched["health_state"] == "no_notifications_observed"
        assert fetched["last_heartbeat_at"] is not None


# -- Unauthorized app_package -------------------------------------------


def test_notification_from_unauthorized_app_package_is_rejected_and_flagged(client):
    with client:
        device = _register_device(client, app_packages=("com.authorized.app",))
        now = datetime.now(timezone.utc).isoformat()
        resp = client.post(
            "/ingest/notification-bridge/phone-1",
            json={
                "events": [
                    {
                        "app_package": "com.sneaky.app",
                        "notification_key": "key-1",
                        "posted_at": now,
                        "title": "BUY BTCUSDT",
                        "text": "BUY BTCUSDT",
                        "content_completeness": "complete",
                    }
                ]
            },
            headers=_auth_headers(device["pairing_token"]),
        )
        assert resp.status_code == 200, resp.text
        result = resp.json()["results"][0]
        assert result["classification"] == "rejected_unauthorized_app_package"

        fetched = client.get("/notification-bridge/devices/phone-1").json()
        assert fetched["health_state"] == "unauthorized_app_package"


# -- Live routing, dedup, edit/revision -----------------------------------


def test_complete_fresh_notification_routes_live_and_produces_a_real_paper_order(client):
    with client:
        device = _register_device(
            client, provider_mapping={"com.example.tradingapp": {"provider_name": "buyalerts"}}
        )
        _wire_paper_route(client, "buyalerts")
        now = datetime.now(timezone.utc).isoformat()

        resp = client.post(
            "/ingest/notification-bridge/phone-1",
            json={
                "events": [
                    {
                        "app_package": "com.example.tradingapp",
                        "notification_key": "notif-key-1",
                        "posted_at": now,
                        "title": "BUY BTCUSDT",
                        "text": "BUY BTCUSDT",
                        "content_completeness": "complete",
                    }
                ]
            },
            headers=_auth_headers(device["pairing_token"]),
        )
        assert resp.status_code == 200, resp.text
        result = resp.json()["results"][0]
        assert result["classification"] == "live"
        assert result["signal_id"]

        orders = client.get("/orders").json()["orders"]
        assert len(orders) == 1
        assert orders[0]["status"] == "filled"

        fetched = client.get("/notification-bridge/devices/phone-1").json()
        assert fetched["health_state"] == "healthy_qualified"


def test_exact_retry_of_same_notification_key_and_content_is_a_duplicate_and_does_not_resubmit(client):
    with client:
        device = _register_device(
            client, provider_mapping={"com.example.tradingapp": {"provider_name": "buyalerts"}}
        )
        _wire_paper_route(client, "buyalerts")
        now = datetime.now(timezone.utc).isoformat()
        event = {
            "app_package": "com.example.tradingapp",
            "notification_key": "notif-key-1",
            "posted_at": now,
            "title": "BUY BTCUSDT",
            "text": "BUY BTCUSDT",
            "content_completeness": "complete",
        }

        first = client.post(
            "/ingest/notification-bridge/phone-1", json={"events": [event]}, headers=_auth_headers(device["pairing_token"])
        )
        second = client.post(
            "/ingest/notification-bridge/phone-1", json={"events": [event]}, headers=_auth_headers(device["pairing_token"])
        )
        assert first.json()["results"][0]["classification"] == "live"
        assert second.json()["results"][0]["classification"] == "duplicate_retry"
        assert second.json()["results"][0]["signal_id"] == first.json()["results"][0]["signal_id"]

        orders = client.get("/orders").json()["orders"]
        assert len(orders) == 1  # never resubmitted


def test_same_key_with_changed_content_is_an_edit_not_a_duplicate_or_a_second_signal(client):
    with client:
        device = _register_device(
            client, provider_mapping={"com.example.tradingapp": {"provider_name": "buyalerts"}}
        )
        _wire_paper_route(client, "buyalerts")
        now = datetime.now(timezone.utc).isoformat()

        first = client.post(
            "/ingest/notification-bridge/phone-1",
            json={
                "events": [
                    {
                        "app_package": "com.example.tradingapp",
                        "notification_key": "notif-key-1",
                        "posted_at": now,
                        "title": "BUY BTCUSDT",
                        "text": "BUY BTCUSDT",
                        "content_completeness": "complete",
                    }
                ]
            },
            headers=_auth_headers(device["pairing_token"]),
        )
        second = client.post(
            "/ingest/notification-bridge/phone-1",
            json={
                "events": [
                    {
                        "app_package": "com.example.tradingapp",
                        "notification_key": "notif-key-1",  # SAME key
                        "posted_at": now,
                        "title": "BUY BTCUSDT",
                        "text": "BUY BTCUSDT @ 65000",  # DIFFERENT content
                        "content_completeness": "complete",
                    }
                ]
            },
            headers=_auth_headers(device["pairing_token"]),
        )
        assert first.json()["results"][0]["classification"] == "live"
        second_result = second.json()["results"][0]
        assert second_result["classification"] == "live"
        assert second_result["is_edit"] is True
        assert second_result["revision_seq"] == 2

        events = client.get("/notification-bridge/devices/phone-1/events").json()["events"]
        assert len(events) == 2


# -- Content completeness -----------------------------------------------


@pytest.mark.parametrize("completeness", ["truncated", "title_only"])
def test_incomplete_content_is_never_routed_live(client, completeness):
    with client:
        device = _register_device(
            client, provider_mapping={"com.example.tradingapp": {"provider_name": "buyalerts"}}
        )
        _wire_paper_route(client, "buyalerts")
        now = datetime.now(timezone.utc).isoformat()

        resp = client.post(
            "/ingest/notification-bridge/phone-1",
            json={
                "events": [
                    {
                        "app_package": "com.example.tradingapp",
                        "notification_key": "notif-key-1",
                        "posted_at": now,
                        "title": "New trade posted",
                        "text": "New trade posted" if completeness == "title_only" else "BUY BTCUSDT",
                        "content_completeness": completeness,
                    }
                ]
            },
            headers=_auth_headers(device["pairing_token"]),
        )
        assert resp.status_code == 200, resp.text
        result = resp.json()["results"][0]
        assert result["classification"] == "needs_review_incomplete_content"

        orders = client.get("/orders").json()["orders"]
        assert orders == []  # never routed live


def test_invalid_content_completeness_value_is_rejected_per_event(client):
    with client:
        device = _register_device(client)
        now = datetime.now(timezone.utc).isoformat()
        resp = client.post(
            "/ingest/notification-bridge/phone-1",
            json={
                "events": [
                    {
                        "app_package": "com.example.tradingapp",
                        "notification_key": "notif-key-1",
                        "posted_at": now,
                        "title": "BUY BTCUSDT",
                        "content_completeness": "kind_of_complete",
                    }
                ]
            },
            headers=_auth_headers(device["pairing_token"]),
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["results"][0]["classification"] == "rejected_invalid_content_completeness"


def test_sustained_incomplete_content_flags_device_degraded(client):
    with client:
        device = _register_device(
            client, provider_mapping={"com.example.tradingapp": {"provider_name": "buyalerts"}}
        )
        _wire_paper_route(client, "buyalerts")
        now = datetime.now(timezone.utc).isoformat()
        events = [
            {
                "app_package": "com.example.tradingapp",
                "notification_key": f"notif-key-{i}",
                "posted_at": now,
                "title": "New trade posted",
                "content_completeness": "title_only",
            }
            for i in range(5)
        ]
        resp = client.post(
            "/ingest/notification-bridge/phone-1", json={"events": events}, headers=_auth_headers(device["pairing_token"])
        )
        assert resp.status_code == 200, resp.text
        fetched = client.get("/notification-bridge/devices/phone-1").json()
        assert fetched["health_state"] == "content_completeness_degraded"


# -- Stale backlog vs live -------------------------------------------------


def test_stale_backlog_notification_is_recorded_but_never_routed_live(client, monkeypatch):
    with client:
        device = _register_device(
            client, provider_mapping={"com.example.tradingapp": {"provider_name": "buyalerts"}}
        )
        _wire_paper_route(client, "buyalerts")
        monkeypatch.setattr(app_config, "NOTIFICATION_BRIDGE_STALE_THRESHOLD_SECONDS", 60.0)

        old_posted_at = (datetime.now(timezone.utc) - timedelta(hours=6)).isoformat()
        resp = client.post(
            "/ingest/notification-bridge/phone-1",
            json={
                "events": [
                    {
                        "app_package": "com.example.tradingapp",
                        "notification_key": "backlog-key-1",
                        "posted_at": old_posted_at,
                        "title": "BUY BTCUSDT",
                        "text": "BUY BTCUSDT",
                        "content_completeness": "complete",
                    }
                ]
            },
            headers=_auth_headers(device["pairing_token"]),
        )
        assert resp.status_code == 200, resp.text
        result = resp.json()["results"][0]
        assert result["classification"] == "stale_backlog_import_only"
        assert result["signal_id"]

        orders = client.get("/orders").json()["orders"]
        assert orders == []  # never became a live order


def test_fresh_notification_within_threshold_still_routes_live(client, monkeypatch):
    with client:
        device = _register_device(
            client, provider_mapping={"com.example.tradingapp": {"provider_name": "buyalerts"}}
        )
        _wire_paper_route(client, "buyalerts")
        monkeypatch.setattr(app_config, "NOTIFICATION_BRIDGE_STALE_THRESHOLD_SECONDS", 3600.0)

        recent_posted_at = (datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat()
        resp = client.post(
            "/ingest/notification-bridge/phone-1",
            json={
                "events": [
                    {
                        "app_package": "com.example.tradingapp",
                        "notification_key": "fresh-key-1",
                        "posted_at": recent_posted_at,
                        "title": "BUY BTCUSDT",
                        "text": "BUY BTCUSDT",
                        "content_completeness": "complete",
                    }
                ]
            },
            headers=_auth_headers(device["pairing_token"]),
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["results"][0]["classification"] == "live"

        orders = client.get("/orders").json()["orders"]
        assert len(orders) == 1
