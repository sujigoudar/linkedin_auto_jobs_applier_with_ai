"""Track 20: mobile devices as first-class infrastructure -- the
"Settings -> Mobile Devices" / "Settings -> Mobile Devices -> Signal
Phone -> Apps -> Whop" backend (app/notification_bridge.py's
`NotificationBridgeDevice` metadata fields and new `MobileAppConfig`,
app/db.py's new columns/table, app/main.py's `/mobile-devices/...`
routes and the `device_metadata` extension to
`POST /ingest/notification-bridge/{device_id}`).

Mirrors tests/test_notification_bridge_api.py's own `client` fixture
pattern."""
import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app import config as app_config
from app.db import SignalStore
from app.notification_bridge import (
    NotificationBridgeError,
    validate_device_app_lists,
    validate_mobile_app_config,
)
from app.phone_escalation import DeniedAppPackageError, MockPhoneControlAdapter


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


def _register_device(client, app_packages=("com.whop.whop",)):
    resp = client.post(
        "/notification-bridge/devices",
        json={"device_id": "phone-1", "app_packages": list(app_packages)},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Device-metadata fields stay honestly null until reported
# ---------------------------------------------------------------------------


def test_fresh_device_has_all_track20_metadata_fields_null(client):
    with client:
        device = _register_device(client)
        for field in (
            "device_name",
            "platform",
            "model",
            "os_version",
            "agent_version",
            "network_status",
            "battery_level",
            "is_charging",
            "notification_permission_granted",
            "accessibility_permission_granted",
            "screen_control_capability",
            "ai_agent_capability",
        ):
            assert device[field] is None, f"{field} should be honestly None on a fresh device, got {device[field]!r}"
        assert device["allowed_apps"] == []
        assert device["blocked_apps"] == []


def test_ingest_with_device_metadata_populates_reported_fields_only(client):
    with client:
        device = _register_device(client)
        token = device["pairing_token"]
        resp = client.post(
            "/ingest/notification-bridge/phone-1",
            json={
                "events": [],
                "device_metadata": {
                    "platform": "android",
                    "battery_level": 73,
                    "is_charging": False,
                    "notification_permission_granted": True,
                    # accessibility_permission_granted deliberately omitted
                },
            },
            headers=_auth_headers(token),
        )
        assert resp.status_code == 200, resp.text

        fetched = client.get("/mobile-devices/phone-1").json()
        assert fetched["platform"] == "android"
        assert fetched["battery_level"] == 73
        assert fetched["is_charging"] is False
        assert fetched["notification_permission_granted"] is True
        # never fabricated/guessed just because a sibling field was reported
        assert fetched["accessibility_permission_granted"] is None
        assert fetched["screen_control_capability"] is None
        assert fetched["ai_agent_capability"] is None


def test_later_ingest_without_metadata_never_clobbers_previously_reported_fields(client):
    with client:
        device = _register_device(client)
        token = device["pairing_token"]
        client.post(
            "/ingest/notification-bridge/phone-1",
            json={"events": [], "device_metadata": {"battery_level": 50, "model": "Pixel 7"}},
            headers=_auth_headers(token),
        )
        # A later heartbeat-only call that reports nothing at all.
        resp = client.post(
            "/ingest/notification-bridge/phone-1", json={"events": []}, headers=_auth_headers(token)
        )
        assert resp.status_code == 200, resp.text

        fetched = client.get("/mobile-devices/phone-1").json()
        assert fetched["battery_level"] == 50
        assert fetched["model"] == "Pixel 7"


# ---------------------------------------------------------------------------
# Backward compatibility: ingest without the new optional fields keeps
# working exactly as before.
# ---------------------------------------------------------------------------


def test_ingest_with_no_device_metadata_key_at_all_still_works(client):
    """The pre-Track-20 request shape (`{"events": [...]}`, no
    `device_metadata` key at all) -- an existing Android build must keep
    working completely unchanged."""
    with client:
        device = _register_device(client)
        token = device["pairing_token"]
        resp = client.post(
            "/ingest/notification-bridge/phone-1",
            json={
                "events": [
                    {
                        "app_package": "com.whop.whop",
                        "notification_key": "key-1",
                        "posted_at": "2026-09-30T12:00:00Z",
                        "title": "BUY BTCUSDT",
                        "text": "BUY BTCUSDT @ 65000",
                        "content_completeness": "complete",
                    }
                ]
            },
            headers=_auth_headers(token),
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["results"][0]["app_package"] == "com.whop.whop"

        fetched = client.get("/mobile-devices/phone-1").json()
        # heartbeat still recorded, no metadata fabricated
        assert fetched["last_heartbeat_at"] is not None
        assert fetched["platform"] is None
        assert fetched["battery_level"] is None


def test_empty_events_batch_with_no_metadata_still_counts_as_heartbeat(client):
    with client:
        device = _register_device(client)
        token = device["pairing_token"]
        resp = client.post("/ingest/notification-bridge/phone-1", json={"events": []}, headers=_auth_headers(token))
        assert resp.status_code == 200, resp.text
        fetched = client.get("/mobile-devices/phone-1").json()
        assert fetched["last_heartbeat_at"] is not None


# ---------------------------------------------------------------------------
# Device-level blocked_apps composes with (never overrides) the global
# broker/banking deny-list -- structural proof.
# ---------------------------------------------------------------------------


def test_allowed_apps_cannot_be_registered_with_a_globally_denied_package():
    with pytest.raises(NotificationBridgeError):
        validate_device_app_lists(allowed_apps=["com.robinhood.android"], blocked_apps=[])


def test_allowed_apps_and_blocked_apps_cannot_overlap():
    with pytest.raises(NotificationBridgeError):
        validate_device_app_lists(allowed_apps=["com.example.app"], blocked_apps=["com.example.app"])


def test_ordinary_allowed_and_blocked_apps_validate_cleanly():
    validate_device_app_lists(allowed_apps=["com.whop.whop"], blocked_apps=["com.mybank.app"])


def test_patch_mobile_device_rejects_globally_denied_package_in_allowed_apps(client):
    with client:
        _register_device(client)
        resp = client.patch(
            "/mobile-devices/phone-1",
            json={"allowed_apps": ["com.robinhood.android"]},
        )
        assert resp.status_code == 422, resp.text


@pytest.mark.asyncio
async def test_denied_app_package_blocked_even_if_present_in_device_allowed_apps():
    """The STRUCTURAL proof the user's own spec asked for: even if a
    device's `allowed_apps` somehow included a globally-denied package
    (bypassing `validate_device_app_lists`, e.g. a row written directly),
    `PhoneControlAdapter.open_app` -- the actual real enforcement point,
    per app/phone_escalation.py's own docstring -- refuses it anyway,
    because it never even reads a device's `allowed_apps` at all. The
    global deny-list wins structurally, not by convention."""
    adapter = MockPhoneControlAdapter()
    # Simulate a device config where the broker package is (wrongly, or
    # via a bypassed write) present in allowed_apps.
    device_allowed_apps = ["com.robinhood.android", "com.whop.whop"]
    assert "com.robinhood.android" in device_allowed_apps  # sanity: it IS "allowed" on this device

    with pytest.raises(DeniedAppPackageError):
        await adapter.open_app("com.robinhood.android")

    # A non-denied package on the same allowed_apps list opens fine --
    # proving the refusal above was about the GLOBAL deny-list, not a
    # blanket failure.
    await adapter.open_app("com.whop.whop")
    assert adapter.opened_packages == ["com.whop.whop"]


def test_patch_mobile_device_updates_name_and_blocked_apps(client):
    with client:
        _register_device(client)
        resp = client.patch(
            "/mobile-devices/phone-1",
            json={"device_name": "Signal Phone", "blocked_apps": ["com.mybank.app"]},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["device_name"] == "Signal Phone"
        assert body["blocked_apps"] == ["com.mybank.app"]
        assert body["allowed_apps"] == []  # untouched (None was passed)


def test_patch_mobile_device_unknown_device_is_404(client):
    with client:
        resp = client.patch("/mobile-devices/does-not-exist", json={"device_name": "x"})
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Per-app config CRUD
# ---------------------------------------------------------------------------


def test_create_mobile_app_config_requires_package_in_device_app_packages(client):
    with client:
        _register_device(client, app_packages=("com.whop.whop",))
        resp = client.post(
            "/mobile-devices/phone-1/apps/com.other.app",
            json={"display_name": "Other"},
        )
        assert resp.status_code == 422, resp.text


def test_create_and_fetch_mobile_app_config(client):
    with client:
        _register_device(client, app_packages=("com.whop.whop",))
        resp = client.post(
            "/mobile-devices/phone-1/apps/com.whop.whop",
            json={
                "display_name": "Whop",
                "capture_notifications": True,
                "active_retrieval_allowed": True,
                "retrieval_mode": "escalation_capable",
                "notification_title_patterns": ["New trade posted"],
                "max_navigation_steps": 5,
                "timeout_seconds": 20,
                "screenshot_retention": "debug_only",
                "content_extraction_schema": {"symbol": "string", "side": "string"},
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["display_name"] == "Whop"
        assert body["active_retrieval_allowed"] is True
        assert body["retrieval_mode"] == "escalation_capable"
        assert body["screenshot_retention"] == "debug_only"
        assert body["content_extraction_schema"] == {"symbol": "string", "side": "string"}
        # provider_mapping is composed from the device row, not forked
        assert body["provider_mapping"] == {}

        fetched = client.get("/mobile-devices/phone-1/apps/com.whop.whop")
        assert fetched.status_code == 200
        assert fetched.json()["display_name"] == "Whop"

        listed = client.get("/mobile-devices/phone-1/apps")
        assert listed.status_code == 200
        assert len(listed.json()["apps"]) == 1


def test_mobile_app_config_composes_provider_mapping_from_device_row(client):
    with client:
        resp = client.post(
            "/notification-bridge/devices",
            json={
                "device_id": "phone-1",
                "app_packages": ["com.whop.whop"],
                "provider_mapping": {
                    "com.whop.whop": {
                        "provider_name": "whop-default",
                        "rules": [{"title_pattern": "ACME", "provider_name": "acme-signals"}],
                    }
                },
            },
        )
        assert resp.status_code == 200, resp.text
        client.post("/mobile-devices/phone-1/apps/com.whop.whop", json={"display_name": "Whop"})

        fetched = client.get("/mobile-devices/phone-1/apps/com.whop.whop").json()
        assert fetched["provider_mapping"]["provider_name"] == "whop-default"
        assert fetched["provider_mapping"]["rules"][0]["provider_name"] == "acme-signals"


def test_update_mobile_app_config_is_idempotent_replace(client):
    with client:
        _register_device(client, app_packages=("com.whop.whop",))
        client.post("/mobile-devices/phone-1/apps/com.whop.whop", json={"display_name": "Whop v1"})
        resp = client.post("/mobile-devices/phone-1/apps/com.whop.whop", json={"display_name": "Whop v2"})
        assert resp.status_code == 200
        assert resp.json()["display_name"] == "Whop v2"
        listed = client.get("/mobile-devices/phone-1/apps").json()["apps"]
        assert len(listed) == 1


def test_get_mobile_app_config_unknown_package_is_404(client):
    with client:
        _register_device(client, app_packages=("com.whop.whop",))
        resp = client.get("/mobile-devices/phone-1/apps/com.whop.whop")
        assert resp.status_code == 404


def test_get_mobile_app_config_unknown_device_is_404(client):
    with client:
        resp = client.get("/mobile-devices/no-such-device/apps/com.whop.whop")
        assert resp.status_code == 404


def test_invalid_retrieval_mode_is_rejected():
    with pytest.raises(NotificationBridgeError):
        validate_mobile_app_config(
            device_app_packages=["com.whop.whop"],
            package_name="com.whop.whop",
            retrieval_mode="not_a_real_mode",
            max_navigation_steps=5,
            timeout_seconds=10,
            screenshot_retention="none",
        )


def test_invalid_screenshot_retention_is_rejected():
    with pytest.raises(NotificationBridgeError):
        validate_mobile_app_config(
            device_app_packages=["com.whop.whop"],
            package_name="com.whop.whop",
            retrieval_mode="notification_only",
            max_navigation_steps=5,
            timeout_seconds=10,
            screenshot_retention="forever",
        )


def test_unauthenticated_mobile_devices_routes_are_rejected():
    with TestClient(main_module.app) as anon_client:
        resp = anon_client.get("/mobile-devices")
        assert resp.status_code in (401, 403, 503)


# ---------------------------------------------------------------------------
# Test App route: honest not-available response, never a fabricated success
# ---------------------------------------------------------------------------


def test_test_app_route_returns_honest_unavailable_never_a_fabricated_success(client):
    with client:
        _register_device(client, app_packages=("com.whop.whop",))
        client.post("/mobile-devices/phone-1/apps/com.whop.whop", json={"display_name": "Whop"})

        resp = client.post("/mobile-devices/phone-1/apps/com.whop.whop/test")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "unavailable"
        assert body["reason"] == "no_real_phone_control_backend"
        assert body["what_it_saw"] is None
        assert body["extracted_content"] is None
        assert "NotImplementedError" in body["detail"] or "no physical Android device" in body["detail"]
        # Never claims a candidate/success status.
        assert body["status"] != "candidate"


def test_test_app_route_requires_device_and_config_to_exist(client):
    with client:
        resp = client.post("/mobile-devices/no-such-device/apps/com.whop.whop/test")
        assert resp.status_code == 404

        _register_device(client, app_packages=("com.whop.whop",))
        resp = client.post("/mobile-devices/phone-1/apps/com.whop.whop/test")
        assert resp.status_code == 404  # no app config registered yet


def test_test_app_route_is_owner_gated():
    with TestClient(main_module.app) as anon_client:
        resp = anon_client.post("/mobile-devices/phone-1/apps/com.whop.whop/test")
        assert resp.status_code in (401, 403, 503)


# ---------------------------------------------------------------------------
# Device-level active_retrieval_allowed AND-gates with the global
# phone_escalation_configs capability_state (additive, not a replacement).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_device_level_active_retrieval_allowed_false_forces_capability_disabled(client):
    from app.notification_bridge import ContentCompleteness

    with client:
        _register_device(client, app_packages=("com.whop.whop",))
        # Global capability promoted through shadow to enabled...
        client.post(
            "/phone-escalation/configs",
            json={"app_package": "com.whop.whop", "provider_name": "whop"},
        )
        client.post("/phone-escalation/configs/com.whop.whop/promote", json={"target_state": "shadow"})
        client.post("/phone-escalation/configs/com.whop.whop/promote", json={"target_state": "enabled"})
        # ...but THIS device's own per-app config explicitly disallows it.
        client.post(
            "/mobile-devices/phone-1/apps/com.whop.whop",
            json={"active_retrieval_allowed": False},
        )

        result = await main_module._evaluate_phone_escalation_for_event(
            device_id="phone-1",
            app_package="com.whop.whop",
            notification_key="key-1",
            content_hash="hash-1",
            completeness=ContentCompleteness.POINTER_ONLY,
        )
        assert result is not None
        assert result["disposition"] == "capability_disabled"


@pytest.mark.asyncio
async def test_device_level_blocked_apps_forces_capability_disabled_even_if_globally_enabled(client):
    from app.notification_bridge import ContentCompleteness

    with client:
        _register_device(client, app_packages=("com.whop.whop",))
        client.post(
            "/phone-escalation/configs",
            json={"app_package": "com.whop.whop", "provider_name": "whop"},
        )
        client.post("/phone-escalation/configs/com.whop.whop/promote", json={"target_state": "shadow"})
        client.post("/phone-escalation/configs/com.whop.whop/promote", json={"target_state": "enabled"})
        client.patch("/mobile-devices/phone-1", json={"blocked_apps": ["com.whop.whop"]})

        result = await main_module._evaluate_phone_escalation_for_event(
            device_id="phone-1",
            app_package="com.whop.whop",
            notification_key="key-1",
            content_hash="hash-1",
            completeness=ContentCompleteness.POINTER_ONLY,
        )
        assert result is not None
        assert result["disposition"] == "capability_disabled"
