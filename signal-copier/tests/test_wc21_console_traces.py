"""WC-21: Console decision traces and reservation/intent health.

Tests for:
- Decision traces API: GET /signals/{signal_id}/decision
- Reservation health API: GET /operations/reservation-health
- Intent health API: GET /operations/intent-health
- Readiness extension: GET /system/readiness includes workflow key
- Playwright tests for UI integration (TR-03, TR-04, TR-16, TR-20)
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

import app.main as main_module
from app.models import AssetClass, Signal, Side


def _authed_client(monkeypatch, tmp_path, db_name="wc21.db"):
    """Helper to set up authenticated test client with a fresh store."""
    from app import config as app_config
    from app.db import SignalStore

    monkeypatch.setattr(app_config, "OWNER_PASSWORD", "test-owner-password")
    monkeypatch.setattr(app_config, "SESSION_SECRET", "test-session-secret")
    store = SignalStore(str(tmp_path / db_name))
    monkeypatch.setattr(main_module, "store", store)
    return store, TestClient(main_module.app)


# --- API Tests: Authentication and Not Found ---


def test_decision_endpoint_requires_owner_session(monkeypatch, tmp_path):
    """Unauthenticated access to /signals/{signal_id}/decision returns 401."""
    _store, client = _authed_client(monkeypatch, tmp_path)
    response = client.get("/signals/nonexistent/decision")
    assert response.status_code == 401


def test_decision_endpoint_returns_404_for_missing_signal(monkeypatch, tmp_path):
    """GET /signals/{signal_id}/decision returns 404 when signal doesn't exist."""
    _store, client = _authed_client(monkeypatch, tmp_path)
    login = client.post("/auth/login", json={"password": "test-owner-password"})
    assert login.status_code == 200

    response = client.get("/signals/nonexistent/decision")
    assert response.status_code == 404


def test_reservation_health_requires_owner_session(monkeypatch, tmp_path):
    """Unauthenticated access to /operations/reservation-health returns 401."""
    _store, client = _authed_client(monkeypatch, tmp_path)
    response = client.get("/operations/reservation-health")
    assert response.status_code == 401


def test_intent_health_requires_owner_session(monkeypatch, tmp_path):
    """Unauthenticated access to /operations/intent-health returns 401."""
    _store, client = _authed_client(monkeypatch, tmp_path)
    response = client.get("/operations/intent-health")
    assert response.status_code == 401


# --- Decision Traces API ---


def test_decision_endpoint_returns_empty_traces_for_new_signal(monkeypatch, tmp_path):
    """GET /signals/{signal_id}/decision returns empty traces when none persisted."""
    store, client = _authed_client(monkeypatch, tmp_path)

    signal = Signal(
        source="test",
        symbol="AAPL",
        side=Side.BUY,
        asset_class=AssetClass.EQUITY,
    )
    store.save_signal(signal)

    login = client.post("/auth/login", json={"password": "test-owner-password"})
    assert login.status_code == 200

    response = client.get(f"/signals/{signal.id}/decision")
    assert response.status_code == 200
    body = response.json()

    assert body["signal_id"] == signal.id
    assert body["traces"] == []
    assert body["selected_physical_account_id"] is None
    assert body["reservation"] is None
    assert body["intent"] is None
    assert body["outbox"] is None
    assert body["protection"]["state"] == "not_tracked"


def test_decision_endpoint_returns_traces_with_selected_account(monkeypatch, tmp_path):
    """GET /signals/{signal_id}/decision returns decision traces with selected account."""
    store, client = _authed_client(monkeypatch, tmp_path)

    signal = Signal(
        source="test",
        symbol="AAPL",
        side=Side.BUY,
        asset_class=AssetClass.EQUITY,
    )
    store.save_signal(signal)

    # Insert two decision traces: one rejected, one selected
    store.insert_decision_trace(
        signal_id=signal.id,
        physical_account_id="acct-1",
        candidate_rank=1,
        feasible=False,
        reason="insufficient_cash",
        selected=False,
    )
    store.insert_decision_trace(
        signal_id=signal.id,
        physical_account_id="acct-2",
        candidate_rank=2,
        feasible=True,
        reason="OK",
        selected=True,
    )

    login = client.post("/auth/login", json={"password": "test-owner-password"})
    assert login.status_code == 200

    response = client.get(f"/signals/{signal.id}/decision")
    assert response.status_code == 200
    body = response.json()

    assert len(body["traces"]) == 2
    assert body["traces"][0]["physical_account_id"] == "acct-1"
    assert body["traces"][0]["feasible"] is False
    assert body["traces"][0]["selected"] is False
    assert body["traces"][1]["physical_account_id"] == "acct-2"
    assert body["traces"][1]["feasible"] is True
    assert body["traces"][1]["selected"] is True
    assert body["selected_physical_account_id"] == "acct-2"


# --- Reservation Health API ---


def test_reservation_health_empty_database(monkeypatch, tmp_path):
    """GET /operations/reservation-health returns zeros when no reservations."""
    _store, client = _authed_client(monkeypatch, tmp_path)

    login = client.post("/auth/login", json={"password": "test-owner-password"})
    assert login.status_code == 200

    response = client.get("/operations/reservation-health")
    assert response.status_code == 200
    body = response.json()

    assert body["counts_by_state"] == {}
    assert body["held_total_cents"] == 0
    assert body["stale_unknown_held"] == 0
    assert body["oldest_held_age_seconds"] is None


def test_reservation_health_counts_by_state(monkeypatch, tmp_path):
    """GET /operations/reservation-health returns correct counts by state."""
    store, client = _authed_client(monkeypatch, tmp_path)

    # Insert test reservations directly
    import sqlite3
    conn = sqlite3.connect(str(tmp_path / "wc21.db"))
    conn.execute(
        """INSERT INTO budget_reservations
           (reservation_id, opportunity_id, owner, physical_account_id, provider, underlying,
            needed_cash_cents, needed_margin_cents, needed_notional_cents, needed_planned_risk_cents, state)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (str(uuid.uuid4()), "signal-1", "owner-1", "acct-1", "test", "AAPL", 10000, 5000, 100000, 2000, "HELD"),
    )
    conn.execute(
        """INSERT INTO budget_reservations
           (reservation_id, opportunity_id, owner, physical_account_id, provider, underlying,
            needed_cash_cents, needed_margin_cents, needed_notional_cents, needed_planned_risk_cents, state)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (str(uuid.uuid4()), "signal-2", "owner-1", "acct-2", "test", "AAPL", 5000, 2000, 50000, 1000, "HELD"),
    )
    conn.execute(
        """INSERT INTO budget_reservations
           (reservation_id, opportunity_id, owner, physical_account_id, provider, underlying,
            needed_cash_cents, needed_margin_cents, needed_notional_cents, needed_planned_risk_cents, state)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (str(uuid.uuid4()), "signal-3", "owner-1", "acct-1", "test", "AAPL", 3000, 1000, 30000, 500, "RELEASED"),
    )
    conn.commit()
    conn.close()

    # Reload store to see the new data
    from app.db import SignalStore
    store = SignalStore(str(tmp_path / "wc21.db"))
    monkeypatch.setattr(main_module, "store", store)

    login = client.post("/auth/login", json={"password": "test-owner-password"})
    assert login.status_code == 200

    response = client.get("/operations/reservation-health")
    assert response.status_code == 200
    body = response.json()

    assert body["counts_by_state"]["HELD"] == 2
    assert body["counts_by_state"]["RELEASED"] == 1
    assert body["held_total_cents"] == (10000 + 5000) + (5000 + 2000)  # sum of cash+margin for HELD rows
    assert body["oldest_held_age_seconds"] is not None


def test_reservation_health_stale_unknown_held(monkeypatch, tmp_path):
    """GET /operations/reservation-health counts UNKNOWN_HELD older than 15 min."""
    store, client = _authed_client(monkeypatch, tmp_path)

    # Insert one UNKNOWN_HELD from 20 minutes ago (should be stale)
    # and one from 10 minutes ago (should be fresh)
    import sqlite3
    now = datetime.now(timezone.utc)
    stale_time = (now - timedelta(minutes=20)).isoformat()
    fresh_time = (now - timedelta(minutes=10)).isoformat()

    conn = sqlite3.connect(str(tmp_path / "wc21.db"))
    conn.execute(
        """INSERT INTO budget_reservations
           (reservation_id, opportunity_id, owner, physical_account_id, provider, underlying,
            needed_cash_cents, needed_margin_cents, needed_notional_cents, needed_planned_risk_cents, state, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (str(uuid.uuid4()), "signal-1", "owner-1", "acct-1", "test", "AAPL", 10000, 5000, 100000, 2000, "UNKNOWN_HELD", stale_time),
    )
    conn.execute(
        """INSERT INTO budget_reservations
           (reservation_id, opportunity_id, owner, physical_account_id, provider, underlying,
            needed_cash_cents, needed_margin_cents, needed_notional_cents, needed_planned_risk_cents, state, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (str(uuid.uuid4()), "signal-2", "owner-1", "acct-2", "test", "AAPL", 5000, 2000, 50000, 1000, "UNKNOWN_HELD", fresh_time),
    )
    conn.commit()
    conn.close()

    # Reload store
    from app.db import SignalStore
    store = SignalStore(str(tmp_path / "wc21.db"))
    monkeypatch.setattr(main_module, "store", store)

    login = client.post("/auth/login", json={"password": "test-owner-password"})
    assert login.status_code == 200

    response = client.get("/operations/reservation-health")
    assert response.status_code == 200
    body = response.json()

    # Should have 1 stale (the 20-min-old one)
    assert body["stale_unknown_held"] == 1
    assert body["counts_by_state"]["UNKNOWN_HELD"] == 2


# --- Intent Health API ---


def test_intent_health_empty_database(monkeypatch, tmp_path):
    """GET /operations/intent-health returns zeros when no intents."""
    _store, client = _authed_client(monkeypatch, tmp_path)

    login = client.post("/auth/login", json={"password": "test-owner-password"})
    assert login.status_code == 200

    response = client.get("/operations/intent-health")
    assert response.status_code == 200
    body = response.json()

    assert body["outbox_counts_by_state"] == {}
    assert body["dispatching_without_response"] == 0
    assert body["unknown"] == 0
    assert body["oldest_unresponded_age_seconds"] is None


def test_intent_health_with_outbox_items(monkeypatch, tmp_path):
    """GET /operations/intent-health counts outbox states and unresponded items."""
    store, client = _authed_client(monkeypatch, tmp_path)

    # Insert test intents and outbox items
    import sqlite3
    intent_id_1 = str(uuid.uuid4())
    intent_id_2 = str(uuid.uuid4())

    conn = sqlite3.connect(str(tmp_path / "wc21.db"))
    # Insert order_intents
    conn.execute(
        """INSERT INTO order_intents
           (intent_id, opportunity_id, physical_account_id, binding_id, client_correlation_id, policy_hash, quantity, reservation_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (intent_id_1, "signal-1", "acct-1", "bind-1", "corr-1", "hash-1", 100, "res-1"),
    )
    conn.execute(
        """INSERT INTO order_intents
           (intent_id, opportunity_id, physical_account_id, binding_id, client_correlation_id, policy_hash, quantity, reservation_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (intent_id_2, "signal-2", "acct-2", "bind-2", "corr-2", "hash-2", 200, "res-2"),
    )
    # Insert outbox items
    conn.execute(
        """INSERT INTO outbox (item_id, intent_id, state, response_recorded_at)
           VALUES (?, ?, ?, ?)""",
        (str(uuid.uuid4()), intent_id_1, "exported", datetime.now(timezone.utc).isoformat()),
    )
    conn.execute(
        """INSERT INTO outbox (item_id, intent_id, state, response_recorded_at)
           VALUES (?, ?, ?, ?)""",
        (str(uuid.uuid4()), intent_id_2, "outboxed", None),  # No response yet
    )
    conn.commit()
    conn.close()

    # Reload store
    from app.db import SignalStore
    store = SignalStore(str(tmp_path / "wc21.db"))
    monkeypatch.setattr(main_module, "store", store)

    login = client.post("/auth/login", json={"password": "test-owner-password"})
    assert login.status_code == 200

    response = client.get("/operations/intent-health")
    assert response.status_code == 200
    body = response.json()

    assert body["outbox_counts_by_state"]["exported"] == 1
    assert body["outbox_counts_by_state"]["outboxed"] == 1
    assert body["dispatching_without_response"] >= 1  # At least the outboxed one without response
    assert body["oldest_unresponded_age_seconds"] is not None


# --- Readiness Extension ---


def test_readiness_includes_workflow_key(monkeypatch, tmp_path):
    """GET /system/readiness includes workflow key with reservations and intents."""
    _store, client = _authed_client(monkeypatch, tmp_path)

    login = client.post("/auth/login", json={"password": "test-owner-password"})
    assert login.status_code == 200

    response = client.get("/system/readiness")
    assert response.status_code == 200
    body = response.json()

    # Check that workflow key exists with expected structure
    assert "workflow" in body
    assert "reservations" in body["workflow"]
    assert "intents" in body["workflow"]

    # Check reservation structure
    res_health = body["workflow"]["reservations"]
    assert "counts_by_state" in res_health
    assert "held_total_cents" in res_health
    assert "stale_unknown_held" in res_health
    assert "oldest_held_age_seconds" in res_health

    # Check intent structure
    intent_health = body["workflow"]["intents"]
    assert "outbox_counts_by_state" in intent_health
    assert "dispatching_without_response" in intent_health
    assert "unknown" in intent_health
    assert "oldest_unresponded_age_seconds" in intent_health


def test_readiness_all_existing_keys_present(monkeypatch, tmp_path):
    """GET /system/readiness still has all existing keys after workflow addition."""
    _store, client = _authed_client(monkeypatch, tmp_path)

    login = client.post("/auth/login", json={"password": "test-owner-password"})
    assert login.status_code == 200

    response = client.get("/system/readiness")
    assert response.status_code == 200
    body = response.json()

    # Existing keys should all be present
    for key in (
        "liveness",
        "data_readiness",
        "market_data_readiness",
        "trading_authority",
        "protection_readiness",
        "release_status",
        "rollup",
        "standby_mode",
        "accounts",
    ):
        assert key in body, f"Missing existing key: {key}"


# --- Playwright Tests ---
# Real-browser tests for console decision traces and reservation/intent health UI.

import httpx
import pytest
from playwright.async_api import async_playwright

from tests.conftest import resolve_chromium_executable

_CHROMIUM_EXECUTABLE = resolve_chromium_executable()


@pytest.mark.asyncio
async def test_tr05_signal_detail_renders_decision_trace(live_server):
    """TR-05 decision view loads without error for real engine-run signal."""
    base_url = live_server

    client = httpx.Client(base_url=base_url)
    login = client.post("/auth/login", json={"password": "test-owner-pw"})
    assert login.status_code == 200
    csrf_headers = {"X-CSRF-Token": login.json()["csrf_token"]}

    # Create account and routing
    assert client.post(
        "/accounts", json={"account_id": "acct1", "broker": "paper", "multiplier": 1.0, "allow_short": True}, headers=csrf_headers
    ).status_code == 200
    assert client.post(
        "/routing-rules", json={"source": "tradingview", "destinations": ["acct1"]}, headers=csrf_headers
    ).status_code == 200

    # Send signal
    signal_res = client.post(
        "/webhook/tradingview",
        json={"symbol": "BTCUSDT", "side": "buy", "quantity": 1.0},
        headers={"X-Webhook-Secret": "test-webhook-secret"},
    )
    assert signal_res.status_code == 200
    signal_id = signal_res.json().get("signal_id")
    assert signal_id

    # Test with browser
    async with async_playwright() as p:
        browser = await p.chromium.launch(executable_path=_CHROMIUM_EXECUTABLE)
        page = await browser.new_page()
        # Real browser login (the httpx session above is a separate client).
        await page.goto(base_url)
        await page.fill("#login-password", "test-owner-pw")
        await page.click("#login-form button[type=submit]")
        await page.wait_for_selector("#app:not([hidden])", timeout=10000)

        # Navigate to signal detail (decision view) and wait for the real
        # decision-trace table: the engine-run signal has exactly one
        # candidate (acct1) which must be rendered as selected.
        await page.goto(f"{base_url}/#/trade/signals/{signal_id}")
        await page.wait_for_selector("text=selected", timeout=15000)
        body = await page.content()
        assert "acct1" in body
        assert "No candidates evaluated" not in body

        await browser.close()


@pytest.mark.asyncio
async def test_tr16_system_shows_reservation_and_intent_rows(live_server):
    """TR-16 readiness checklist loads without error."""
    base_url = live_server

    client = httpx.Client(base_url=base_url)
    login = client.post("/auth/login", json={"password": "test-owner-pw"})
    assert login.status_code == 200

    # Test with browser
    async with async_playwright() as p:
        browser = await p.chromium.launch(executable_path=_CHROMIUM_EXECUTABLE)
        page = await browser.new_page()
        # Real browser login (the httpx session above is a separate client).
        await page.goto(base_url)
        await page.fill("#login-password", "test-owner-pw")
        await page.click("#login-form button[type=submit]")
        await page.wait_for_selector("#app:not([hidden])", timeout=10000)

        # Navigate to TR-16 (`#/trade/system`) and wait for the two new
        # readiness rows backed by GET /system/readiness["workflow"].
        await page.goto(f"{base_url}/#/trade/system")
        await page.wait_for_selector("text=All budget reservations are current.", timeout=15000)
        body = await page.content()
        assert "All dispatched intents have recorded responses." in body

        await browser.close()


@pytest.mark.asyncio
async def test_tr20_operations_shows_reservations_and_intents_panel(live_server):
    """TR-20 operations center loads without error."""
    base_url = live_server

    client = httpx.Client(base_url=base_url)
    login = client.post("/auth/login", json={"password": "test-owner-pw"})
    assert login.status_code == 200

    # Test with browser
    async with async_playwright() as p:
        browser = await p.chromium.launch(executable_path=_CHROMIUM_EXECUTABLE)
        page = await browser.new_page()
        # Real browser login (the httpx session above is a separate client).
        await page.goto(base_url)
        await page.fill("#login-password", "test-owner-pw")
        await page.click("#login-form button[type=submit]")
        await page.wait_for_selector("#app:not([hidden])", timeout=10000)

        # Navigate to TR-20 and wait for the new panel, fed by
        # GET /operations/reservation-health and /operations/intent-health.
        await page.goto(f"{base_url}/#/trade/operations")
        await page.wait_for_selector("text=Reservations & intents", timeout=15000)
        await page.wait_for_selector("text=Budget reservations", timeout=15000)
        body = await page.content()
        assert "Execution intents" in body

        await browser.close()
