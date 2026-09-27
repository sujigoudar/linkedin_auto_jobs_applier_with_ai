"""GET/PUT/DELETE /providers/subscriptions, /providers/value,
/providers/candidates, and POST /providers/candidates/promote --
app/main.py's HTTP surface over app/provider_value.py/app/provider_scout.py."""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app import config as app_config
from app.db import SignalStore
from app.models import OrderResult, OrderStatus, Side, Signal


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(app_config, "OWNER_PASSWORD", "test-owner-password")
    monkeypatch.setattr(app_config, "SESSION_SECRET", "test-session-secret")
    store = SignalStore(tmp_path / "test.db")
    monkeypatch.setattr(main_module, "store", store)

    test_client = TestClient(main_module.app)
    login = test_client.post("/auth/login", json={"password": "test-owner-password"})
    test_client.headers["X-CSRF-Token"] = login.json()["csrf_token"]
    return test_client, store


def _fill(store, account_id, symbol, side, quantity, price, when, *, source="test"):
    signal = Signal(source=source, symbol=symbol, side=side)
    store.save_signal(signal)
    result = OrderResult(
        account_id=account_id, status=OrderStatus.FILLED, signal_id=signal.id,
        filled_quantity=quantity, filled_price=price, executed_at=when,
    )
    store.save_order_result(result, broker="paper", symbol=symbol, side=side)


def _round_trip(store, source, price_in=100.0, price_out=110.0, *, when=None):
    t0 = when or datetime.now(timezone.utc)
    _fill(store, "acct1", "AAPL", Side.BUY, 10.0, price_in, t0, source=source)
    _fill(store, "acct1", "AAPL", Side.SELL, 10.0, price_out, t0 + timedelta(minutes=1), source=source)


# --- Subscription CRUD ---


def test_create_and_list_subscription(client):
    test_client, _ = client
    with test_client:
        response = test_client.put(
            "/providers/goldrun/subscription",
            json={"display_name": "GoldRun Signals", "cost_amount": 49.99, "billing_cycle": "monthly"},
        )
        assert response.status_code == 200

        listing = test_client.get("/providers/subscriptions")
    assert listing.status_code == 200
    rows = listing.json()["subscriptions"]
    assert len(rows) == 1
    assert rows[0]["provider_id"] == "goldrun"
    assert rows[0]["cost_amount"] == 49.99


def test_subscription_rejects_invalid_billing_cycle(client):
    test_client, _ = client
    with test_client:
        response = test_client.put("/providers/goldrun/subscription", json={"billing_cycle": "weekly"})
    assert response.status_code == 422


def test_subscription_rejects_malformed_date(client):
    test_client, _ = client
    with test_client:
        response = test_client.put("/providers/goldrun/subscription", json={"renewal_date": "not-a-date"})
    assert response.status_code == 422


def test_delete_subscription(client):
    test_client, _ = client
    with test_client:
        test_client.put("/providers/goldrun/subscription", json={})
        response = test_client.delete("/providers/goldrun/subscription")
        listing = test_client.get("/providers/subscriptions")
    assert response.status_code == 200
    assert listing.json()["subscriptions"] == []


def test_subscribing_a_provider_clears_its_candidate_rows(client):
    test_client, store = client
    store.upsert_provider_candidate(
        source="goldrun", analyst=None, asset_class="equity", closing_fills=12,
        winning_closing_fills=8, realized_pnl=500.0, win_rate=0.67, profit_factor=2.0,
        recommendation="promote",
    )
    with test_client:
        test_client.put("/providers/goldrun/subscription", json={"cost_amount": 10.0})
        candidates = test_client.get("/providers/candidates")
    assert candidates.json()["candidates"] == []


# --- Value report ---


def test_value_endpoint_returns_computed_breakdown(client):
    test_client, store = client
    _round_trip(store, "provider_a")
    with test_client:
        response = test_client.get("/providers/value")
    assert response.status_code == 200
    rows = response.json()["providers"]
    assert any(r["source"] == "provider_a" and r["realized_pnl"] == pytest.approx(100.0) for r in rows)


def test_value_endpoint_filters_by_source(client):
    test_client, store = client
    _round_trip(store, "provider_a")
    _round_trip(store, "provider_b")
    with test_client:
        response = test_client.get("/providers/value", params={"source": "provider_a"})
    rows = response.json()["providers"]
    assert len(rows) == 1
    assert rows[0]["source"] == "provider_a"


# --- Candidates + promote ---


def test_candidates_endpoint_lists_scout_snapshot(client):
    test_client, store = client
    store.upsert_provider_candidate(
        source="freechannel", analyst=None, asset_class="equity", closing_fills=15,
        winning_closing_fills=10, realized_pnl=300.0, win_rate=0.67, profit_factor=2.5,
        recommendation="promote",
    )
    with test_client:
        response = test_client.get("/providers/candidates")
    assert response.status_code == 200
    rows = response.json()["candidates"]
    assert rows[0]["source"] == "freechannel"
    assert rows[0]["recommendation"] == "promote"


def test_promote_creates_subscription_and_clears_candidate(client):
    test_client, store = client
    store.upsert_provider_candidate(
        source="freechannel", analyst=None, asset_class="equity", closing_fills=15,
        winning_closing_fills=10, realized_pnl=300.0, win_rate=0.67, profit_factor=2.5,
        recommendation="promote",
    )
    with test_client:
        response = test_client.post("/providers/candidates/promote", json={"source": "freechannel"})
        subscriptions = test_client.get("/providers/subscriptions")
        candidates = test_client.get("/providers/candidates")

    assert response.status_code == 200
    subs = subscriptions.json()["subscriptions"]
    assert len(subs) == 1
    assert subs[0]["provider_id"] == "freechannel"
    assert subs[0]["cost_amount"] == 0.0  # promoted from free, defaults to free
    assert candidates.json()["candidates"] == []


def test_promote_does_not_touch_routing_or_provider_settings(client):
    """Promoting is a cost/value-tracking decision only -- it must never
    silently start routing signals anywhere or change sizing/enablement,
    which stay owned entirely by the existing /routing-rules and
    /providers/{id} endpoints."""
    test_client, store = client
    with test_client:
        test_client.post("/providers/candidates/promote", json={"source": "freechannel"})
        routing_rules = test_client.get("/routing-rules")
        provider_overrides = test_client.get("/providers")
    assert routing_rules.json()["routing_rules"] == []
    assert provider_overrides.json()["providers"] == []
