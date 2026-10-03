"""WP-06: partial account updates must not clobber safety fields

F-03 — a plain CLOSE never carries SL/TP/targets.
"""
import pytest
from fastapi.testclient import TestClient

from app.brokers.paper import PaperBroker
from app.db import SignalStore
from app.models import OrderStatus


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
    monkeypatch.setattr(main_module.engine.capital_allocator, "store", store)
    # Fresh PaperBroker for each test
    monkeypatch.setitem(main_module.brokers, "paper", PaperBroker())
    main_module.routing_config.accounts.clear()
    main_module.routing_config.rules.clear()
    main_module.provider_registry.providers.clear()
    c = TestClient(main_module.app)
    login = c.post("/auth/login", json={"password": "test-owner-password"})
    assert login.status_code == 200
    c.headers["X-CSRF-Token"] = login.json()["csrf_token"]
    c.headers["X-Webhook-Secret"] = "test-webhook-secret"
    return c


def test_patch_preserves_safety_fields(client):
    """F-03: PATCH {"enabled": false} preserves exclusive_writer_qualified,
    risk_percent_of_equity, qualification_level unchanged"""
    # Create account with safety fields set
    create_resp = client.post(
        "/accounts",
        json={
            "account_id": "test_acc",
            "broker": "paper",
            "exclusive_writer_qualified": True,
            "risk_percent_of_equity": 0.5,
            "qualification_level": "paper",
        },
    )
    assert create_resp.status_code == 200

    # Verify initial state
    get_resp = client.get("/accounts")
    account = next((a for a in get_resp.json()["accounts"] if a["account_id"] == "test_acc"), None)
    assert account is not None
    assert account["exclusive_writer_qualified"] is True
    assert account["risk_percent_of_equity"] == 0.5
    assert account["qualification_level"] == "paper"
    assert account["enabled"] is True

    # Patch only enabled field
    patch_resp = client.patch("/accounts/test_acc", json={"enabled": False})
    assert patch_resp.status_code == 200

    # Verify safety fields are unchanged
    get_resp = client.get("/accounts")
    account = next((a for a in get_resp.json()["accounts"] if a["account_id"] == "test_acc"), None)
    assert account is not None
    assert account["exclusive_writer_qualified"] is True
    assert account["risk_percent_of_equity"] == 0.5
    assert account["qualification_level"] == "paper"
    assert account["enabled"] is False


def test_patch_unknown_account_404(client):
    """PATCH on unknown account returns 404"""
    patch_resp = client.patch("/accounts/nonexistent", json={"enabled": False})
    assert patch_resp.status_code == 404
    assert "not found" in patch_resp.json()["detail"].lower()


def test_patch_broker_change_with_exposure_409(client):
    """PATCH {"broker": "other"} on account with open position returns 409"""
    # Create account and rule
    client.post(
        "/accounts",
        json={"account_id": "a1", "broker": "paper", "max_notional_exposure": 100000},
    )
    client.post("/routing-rules", json={"source": "tv", "destinations": ["a1"]})

    # Create a signal that opens a position (needs writer lease startup)
    with client:
        entry_resp = client.post(
            "/webhook/tv",
            json={"symbol": "AAPL", "side": "buy", "quantity": 10, "price": 50.0},
        )
        assert entry_resp.status_code == 200
        assert entry_resp.json()["orders"][0]["status"] == OrderStatus.FILLED.value

        # Try to change broker - should be refused with 409
        patch_resp = client.patch("/accounts/a1", json={"broker": "alpaca"})
        assert patch_resp.status_code == 409
        assert "open position" in patch_resp.json()["detail"].lower()


def test_patch_managed_lifecycle_change_with_exposure_409(client):
    """PATCH {"managed_lifecycle": true} on account with exposure returns 409"""
    # Create account with managed_lifecycle=false and open position
    client.post(
        "/accounts",
        json={"account_id": "a1", "broker": "paper", "managed_lifecycle": False, "max_notional_exposure": 100000},
    )
    client.post("/routing-rules", json={"source": "tv", "destinations": ["a1"]})

    # Create an entry to establish exposure (needs writer lease startup)
    with client:
        entry_resp = client.post(
            "/webhook/tv",
            json={"symbol": "AAPL", "side": "buy", "quantity": 10, "price": 50.0},
        )
        assert entry_resp.status_code == 200
        assert entry_resp.json()["orders"][0]["status"] == OrderStatus.FILLED.value

        # Try to flip managed_lifecycle - should be refused with 409
        patch_resp = client.patch("/accounts/a1", json={"managed_lifecycle": True})
        assert patch_resp.status_code == 409
        assert "exposure" in patch_resp.json()["detail"].lower()


def test_patch_without_auth_rejected(client):
    """PATCH without auth returns 401/403"""
    # Remove CSRF token to trigger auth check
    del client.headers["X-CSRF-Token"]

    patch_resp = client.patch("/accounts/a1", json={"enabled": False})
    assert patch_resp.status_code in (401, 403)


def test_patch_multiple_fields(client):
    """PATCH can update multiple fields at once"""
    # Create initial account
    client.post(
        "/accounts",
        json={
            "account_id": "test_acc",
            "broker": "paper",
            "multiplier": 1.0,
            "max_notional_exposure": 50000.0,
            "enabled": True,
        },
    )

    # Patch multiple fields
    patch_resp = client.patch(
        "/accounts/test_acc",
        json={
            "multiplier": 2.0,
            "max_notional_exposure": 100000.0,
            "enabled": False,
        },
    )
    assert patch_resp.status_code == 200

    # Verify all fields updated
    get_resp = client.get("/accounts")
    account = next((a for a in get_resp.json()["accounts"] if a["account_id"] == "test_acc"), None)
    assert account is not None
    assert account["multiplier"] == 2.0
    assert account["max_notional_exposure"] == 100000.0
    assert account["enabled"] is False


def test_post_still_full_replace(client):
    """POST /accounts still does full replace (test that we didn't break it)"""
    # Create initial account
    client.post(
        "/accounts",
        json={
            "account_id": "test_acc",
            "broker": "paper",
            "risk_percent_of_equity": 0.5,
            "qualification_level": "paper",
            "exclusive_writer_qualified": True,
        },
    )

    # Verify initial state
    get_resp = client.get("/accounts")
    account = next((a for a in get_resp.json()["accounts"] if a["account_id"] == "test_acc"), None)
    assert account is not None
    assert account["exclusive_writer_qualified"] is True
    assert account["risk_percent_of_equity"] == 0.5
    assert account["qualification_level"] == "paper"

    # Use POST to update with only some fields (full-replace semantics)
    # This SHOULD clobber the missing fields to defaults
    post_resp = client.post(
        "/accounts",
        json={
            "account_id": "test_acc",
            "broker": "paper",
            # Omit risk_percent_of_equity, qualification_level, exclusive_writer_qualified
        },
    )
    assert post_resp.status_code == 200

    # Verify fields reverted to defaults
    get_resp = client.get("/accounts")
    account = next((a for a in get_resp.json()["accounts"] if a["account_id"] == "test_acc"), None)
    assert account is not None
    assert account["exclusive_writer_qualified"] is False  # default
    assert account["risk_percent_of_equity"] is None  # default
    assert account["qualification_level"] is None  # default
