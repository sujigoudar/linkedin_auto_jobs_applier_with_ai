"""WP-45: Readiness and autonomy checklist -- per-account readiness items.

Tests the extended GET /system/readiness endpoint that includes per-account
readiness checklist items for autonomous operation.
"""
import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app import config as app_config
from app.db import SignalStore


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Client with auth configured."""
    monkeypatch.setattr(app_config, "OWNER_PASSWORD", "test-owner-password")
    monkeypatch.setattr(app_config, "SESSION_SECRET", "test-session-secret")
    # Disable standby mode so writer lease is "held"
    monkeypatch.setattr(app_config, "STANDBY_MODE", False)

    store = SignalStore(tmp_path / "test.db")
    monkeypatch.setattr(main_module, "store", store)
    monkeypatch.setattr(main_module.engine, "store", store)

    test_client = TestClient(main_module.app)
    login = test_client.post("/auth/login", json={"password": "test-owner-password"})
    assert login.status_code == 200
    # Set CSRF token for authenticated requests
    test_client.headers["X-CSRF-Token"] = login.json()["csrf_token"]
    return test_client


def test_readiness_includes_accounts_list(client):
    """GET /system/readiness includes per-account readiness items."""
    response = client.get("/system/readiness")
    assert response.status_code == 200
    data = response.json()
    assert "accounts" in data, "Missing 'accounts' field in readiness response"
    assert isinstance(data["accounts"], list), "'accounts' should be a list"


def test_readiness_empty_accounts_list_when_no_accounts(client):
    """GET /system/readiness returns empty accounts list when no accounts configured."""
    response = client.get("/system/readiness")
    assert response.status_code == 200
    data = response.json()
    assert data["accounts"] == [], "Should have no accounts when none configured"


def test_readiness_account_blocked_on_sizing(client):
    """Account without sizing configured is blocked on 'sizing' item."""
    # Create account with no sizing configuration
    account_data = {
        "account_id": "test-account",
        "broker": "paper",
        "enabled": True,
        "managed_lifecycle": False,
    }
    response = client.post("/accounts", json=account_data)
    assert response.status_code == 200

    # Check readiness
    response = client.get("/system/readiness")
    assert response.status_code == 200
    data = response.json()
    assert len(data["accounts"]) == 1
    account = data["accounts"][0]
    assert account["account_id"] == "test-account"

    # Find the sizing item
    sizing_item = next((item for item in account["items"] if item["key"] == "sizing"), None)
    assert sizing_item is not None, "Missing 'sizing' readiness item"
    assert sizing_item["status"] == "blocked", "Sizing should be blocked when not configured"
    assert sizing_item["reason"] == "No sizing configured"
    assert sizing_item["fix_route"] == "#/trade/accounts"


def test_readiness_account_ok_with_fixed_quantity(client):
    """Account with fixed_quantity configured is OK on 'sizing' item."""
    # Create account with fixed quantity
    account_data = {
        "account_id": "test-account",
        "broker": "paper",
        "enabled": True,
        "managed_lifecycle": False,
        "fixed_quantity": 10,
    }
    response = client.post("/accounts", json=account_data)
    assert response.status_code == 200

    # Check readiness
    response = client.get("/system/readiness")
    assert response.status_code == 200
    data = response.json()
    account = data["accounts"][0]

    sizing_item = next((item for item in account["items"] if item["key"] == "sizing"), None)
    assert sizing_item["status"] == "ok", "Sizing should be OK with fixed_quantity"
    assert "fixed_quantity=10" in sizing_item["reason"]


def test_readiness_account_ok_with_multiplier(client):
    """Account with multiplier configured is OK on 'sizing' item."""
    # Create account with custom multiplier
    account_data = {
        "account_id": "test-account",
        "broker": "paper",
        "enabled": True,
        "managed_lifecycle": False,
        "multiplier": 2.0,
    }
    response = client.post("/accounts", json=account_data)
    assert response.status_code == 200

    # Check readiness
    response = client.get("/system/readiness")
    assert response.status_code == 200
    data = response.json()
    account = data["accounts"][0]

    sizing_item = next((item for item in account["items"] if item["key"] == "sizing"), None)
    assert sizing_item["status"] == "ok", "Sizing should be OK with custom multiplier"
    assert "multiplier=2" in sizing_item["reason"]


def test_readiness_account_loss_limit_not_tracked(client):
    """Account without loss limit is 'not_tracked' on loss_limit item."""
    account_data = {
        "account_id": "test-account",
        "broker": "paper",
        "enabled": True,
        "fixed_quantity": 10,
    }
    response = client.post("/accounts", json=account_data)
    assert response.status_code == 200

    response = client.get("/system/readiness")
    data = response.json()
    account = data["accounts"][0]

    loss_item = next((item for item in account["items"] if item["key"] == "loss_limit"), None)
    assert loss_item["status"] == "not_tracked"


def test_readiness_account_loss_limit_configured_when_set(client):
    """Account with loss_limit configured shows the limit in readiness."""
    account_data = {
        "account_id": "test-account",
        "broker": "paper",
        "enabled": True,
        "fixed_quantity": 10,
        "daily_loss_limit_percent": 5.0,
    }
    response = client.post("/accounts", json=account_data)
    assert response.status_code == 200

    response = client.get("/system/readiness")
    data = response.json()
    account = data["accounts"][0]

    loss_item = next((item for item in account["items"] if item["key"] == "loss_limit"), None)
    assert loss_item["status"] == "ok", "Loss limit configured should show as ok"
    assert "5" in loss_item["reason"]


def test_readiness_paper_broker_entries_admissible(client):
    """Paper broker has entries_admissible = True."""
    account_data = {
        "account_id": "test-account",
        "broker": "paper",
        "enabled": True,
        "fixed_quantity": 10,
    }
    response = client.post("/accounts", json=account_data)
    assert response.status_code == 200

    response = client.get("/system/readiness")
    data = response.json()
    account = data["accounts"][0]

    entries_item = next((item for item in account["items"] if item["key"] == "entries_admissible"), None)
    assert entries_item["status"] == "ok"


def test_readiness_paper_broker_route_qualification_ok(client):
    """Paper broker route qualification is always 'ok'."""
    account_data = {
        "account_id": "test-account",
        "broker": "paper",
        "enabled": True,
        "fixed_quantity": 10,
    }
    response = client.post("/accounts", json=account_data)
    assert response.status_code == 200

    response = client.get("/system/readiness")
    data = response.json()
    account = data["accounts"][0]

    qual_item = next((item for item in account["items"] if item["key"] == "route_qualification"), None)
    assert qual_item["status"] == "ok"
    assert "paper" in qual_item["reason"]


def test_readiness_buying_power_ok_with_paper_balance_capability(client):
    """Paper broker has balance capability, so buying_power is OK."""
    account_data = {
        "account_id": "test-account",
        "broker": "paper",
        "enabled": True,
        "fixed_quantity": 10,
    }
    response = client.post("/accounts", json=account_data)
    assert response.status_code == 200

    response = client.get("/system/readiness")
    data = response.json()
    account = data["accounts"][0]

    bp_item = next((item for item in account["items"] if item["key"] == "buying_power"), None)
    assert bp_item["status"] == "ok"


def test_readiness_writer_lease_ok_when_active(client):
    """Writer lease status is 'ok' when not in standby mode."""
    account_data = {
        "account_id": "test-account",
        "broker": "paper",
        "enabled": True,
        "fixed_quantity": 10,
    }
    response = client.post("/accounts", json=account_data)
    assert response.status_code == 200

    response = client.get("/system/readiness")
    data = response.json()
    account = data["accounts"][0]

    writer_item = next((item for item in account["items"] if item["key"] == "writer_lease"), None)
    assert writer_item["status"] == "ok"


def test_readiness_alerts_path_configured(client):
    """Alerts path item is present in readiness."""
    account_data = {
        "account_id": "test-account",
        "broker": "paper",
        "enabled": True,
        "fixed_quantity": 10,
    }
    response = client.post("/accounts", json=account_data)
    assert response.status_code == 200

    response = client.get("/system/readiness")
    data = response.json()
    account = data["accounts"][0]

    alerts_item = next((item for item in account["items"] if item["key"] == "alerts_path"), None)
    assert alerts_item is not None
    assert alerts_item["status"] in ("ok", "not_tracked")


def test_readiness_multiple_accounts(client):
    """Readiness includes multiple configured accounts."""
    # Create multiple accounts
    for i in range(3):
        account_data = {
            "account_id": f"test-account-{i}",
            "broker": "paper",
            "enabled": True,
            "fixed_quantity": 10,
        }
        response = client.post("/accounts", json=account_data)
        assert response.status_code == 200

    response = client.get("/system/readiness")
    data = response.json()
    assert len(data["accounts"]) == 3
    account_ids = [a["account_id"] for a in data["accounts"]]
    assert "test-account-0" in account_ids
    assert "test-account-1" in account_ids
    assert "test-account-2" in account_ids


def test_readiness_account_items_structure(client):
    """Per-account items have required structure."""
    account_data = {
        "account_id": "test-account",
        "broker": "paper",
        "enabled": True,
        "fixed_quantity": 10,
    }
    response = client.post("/accounts", json=account_data)
    assert response.status_code == 200

    response = client.get("/system/readiness")
    data = response.json()
    account = data["accounts"][0]

    # Verify structure
    assert "account_id" in account
    assert "items" in account
    assert isinstance(account["items"], list)
    assert len(account["items"]) > 0

    # Check each item has required fields
    for item in account["items"]:
        assert "key" in item
        assert "status" in item
        assert "reason" in item
        assert item["status"] in ("ok", "blocked", "not_tracked")


def test_readiness_unknown_broker_blocked_on_entries_admissible(client):
    """Account with unknown broker is blocked on entries_admissible."""
    account_data = {
        "account_id": "test-account",
        "broker": "unknown-broker",
        "enabled": True,
        "fixed_quantity": 10,
    }
    response = client.post("/accounts", json=account_data)
    assert response.status_code == 200

    response = client.get("/system/readiness")
    data = response.json()
    account = data["accounts"][0]

    entries_item = next((item for item in account["items"] if item["key"] == "entries_admissible"), None)
    assert entries_item["status"] == "blocked"


def test_readiness_accounts_have_fix_routes(client):
    """Blocked items have fix_route pointing to the account editor."""
    account_data = {
        "account_id": "test-account",
        "broker": "paper",
        "enabled": True,
        # No sizing configured to trigger a block
    }
    response = client.post("/accounts", json=account_data)
    assert response.status_code == 200

    response = client.get("/system/readiness")
    data = response.json()
    account = data["accounts"][0]

    sizing_item = next((item for item in account["items"] if item["key"] == "sizing"), None)
    assert sizing_item["fix_route"] == "#/trade/accounts"
