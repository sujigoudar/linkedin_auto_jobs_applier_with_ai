"""ALLOC-04: the decisive regression through the REAL application: webhook
ingestion -> parsing -> routing -> allocation -> admission -> paper broker
-> order journal -> API serialization, with three eligible accounts."""
import pytest
from fastapi.testclient import TestClient

from app.db import SignalStore


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
    # the allocator keeps its own store reference (durable reservations)
    monkeypatch.setattr(main_module.engine.capital_allocator, "store", store)
    main_module.routing_config.accounts.clear()
    main_module.routing_config.rules.clear()
    main_module.provider_registry.providers.clear()
    c = TestClient(main_module.app)
    login = c.post("/auth/login", json={"password": "test-owner-password"})
    assert login.status_code == 200
    c.headers["X-CSRF-Token"] = login.json()["csrf_token"]
    c.headers["X-Webhook-Secret"] = "test-webhook-secret"
    return c


def _accounts(client):
    for a in ("a1", "a2", "a3"):
        assert client.post("/accounts", json={"account_id": a, "broker": "paper"}).status_code == 200


def _webhook(client, **kw):
    body = {"symbol": "BTCUSDT", "side": "buy", "quantity": 0.5, "price": 65000}
    body.update(kw)
    return client.post("/webhook/tradingview", json=body)


def test_one_webhook_three_eligible_accounts_one_order(client):
    with client:
        _accounts(client)
        rule = client.post(
            "/routing-rules", json={"source": "tradingview", "destinations": ["a1", "a2", "a3"]}
        )
        assert rule.status_code == 200
        response = _webhook(client)
        assert response.status_code == 200
        orders = response.json()["orders"]
        assert [o["account_id"] for o in orders] == ["a1"]
        intents = client.get("/allocation-intents").json()["allocation_intents"]
        assert len(intents) == 1
        assert intents[0]["selected_account_id"] == "a1"
        assert intents[0]["state"] == "committed"
        persisted = client.get("/orders").json()
        flat = persisted["orders"] if isinstance(persisted, dict) else persisted
        assert len(flat) == 1


def test_delivery_mode_round_trips_and_replicate_fans_out(client):
    with client:
        _accounts(client)
        created = client.post(
            "/routing-rules",
            json={"source": "tradingview", "destinations": ["a1", "a2", "a3"], "delivery_mode": "replicate"},
        )
        assert created.status_code == 200
        listed = client.get("/routing-rules").json()["routing_rules"]
        assert listed[0]["delivery_mode"] == "replicate"
        orders = _webhook(client).json()["orders"]
        assert sorted(o["account_id"] for o in orders) == ["a1", "a2", "a3"]


def test_invalid_delivery_mode_is_rejected(client):
    with client:
        r = client.post(
            "/routing-rules", json={"source": "tradingview", "destinations": ["a1"], "delivery_mode": "broadcast"}
        )
        assert r.status_code == 422


def test_strategy_budget_endpoints_and_enforcement_through_webhook(client):
    with client:
        _accounts(client)
        client.post("/routing-rules", json={"source": "tradingview", "destinations": ["a1", "a2", "a3"]})
        put = client.put("/strategy-budgets/tradingview", json={"max_notional": 40000})
        assert put.status_code == 200
        assert client.put("/strategy-budgets/tradingview", json={"max_notional": -1}).status_code == 422

        first = _webhook(client).json()["orders"]  # notional 32,500
        assert first[0]["status"] == "filled"
        second = _webhook(client, symbol="ETHUSDT").json()["orders"]  # would exceed 40,000 on ANY account
        assert second and all(o["status"] == "rejected" for o in second)

        budgets = client.get("/strategy-budgets").json()["strategy_budgets"]
        assert budgets[0]["strategy_key"] == "tradingview" and budgets[0]["max_notional"] == 40000
        assert client.delete("/strategy-budgets/tradingview").status_code == 200
        assert client.get("/strategy-budgets").json()["strategy_budgets"] == []


def test_allocation_endpoints_require_owner_auth(client):
    anon = TestClient(client.app)
    assert anon.get("/allocation-intents").status_code in (401, 403)
    assert anon.get("/strategy-budgets").status_code in (401, 403)
    assert anon.put("/strategy-budgets/x", json={"max_notional": 1}).status_code in (401, 403)
