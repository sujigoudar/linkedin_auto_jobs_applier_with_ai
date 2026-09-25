"""Proves the actual claim: an account/routing-rule/provider added or
changed through the API takes effect on the very next signal -- no
restart, no YAML edit, no process reload. Every test here exercises the
real app.main module (module-level `store`/`routing_config`/`engine`),
not a fresh isolated engine, since the whole point is the live in-place
mutation of those module-level singletons.
"""
import pytest
from fastapi.testclient import TestClient

from app.db import SignalStore
from app.models import OrderStatus, Signal, Side


@pytest.fixture
def client(tmp_path, monkeypatch):
    import app.main as main_module
    from app import config as app_config

    monkeypatch.setattr(app_config, "OWNER_PASSWORD", "test-owner-password")
    monkeypatch.setattr(app_config, "SESSION_SECRET", "test-session-secret")
    monkeypatch.setattr(app_config, "WEBHOOK_SHARED_SECRET", "test-webhook-secret")

    store = SignalStore(tmp_path / "test.db")
    monkeypatch.setattr(main_module, "store", store)
    # engine.store was bound at construction time (module import), before this
    # fixture's store existed -- repoint it so signals processed through the
    # app actually land in the same store the CRUD endpoints (and this
    # fixture's assertions) read from.
    monkeypatch.setattr(main_module.engine, "store", store)
    # start every test from a clean slate regardless of what earlier tests
    # (or a real config/*.yaml seed) left in the shared module-level objects
    main_module.routing_config.accounts.clear()
    main_module.routing_config.rules.clear()
    main_module.provider_registry.providers.clear()

    test_client = TestClient(main_module.app)
    login = test_client.post("/auth/login", json={"password": "test-owner-password"})
    assert login.status_code == 200
    # Real browsers send the session cookie automatically and attach the CSRF
    # token themselves (see dashboard.html); a default header here does the
    # same for every call this client makes, matching how a real client
    # behaves rather than special-casing each request in this file.
    test_client.headers["X-CSRF-Token"] = login.json()["csrf_token"]
    test_client.headers["X-Webhook-Secret"] = "test-webhook-secret"
    return test_client


def test_account_created_via_api_is_immediately_usable_no_restart(client):
    with client:
        create = client.post(
            "/accounts",
            json={"account_id": "acct1", "broker": "paper", "multiplier": 2.0},
        )
        assert create.status_code == 200

        rule = client.post(
            "/routing-rules", json={"source": "tradingview", "destinations": ["acct1"]}
        )
        assert rule.status_code == 200

        # no restart between the writes above and this signal
        response = client.post(
            "/webhook/tradingview",
            json={"symbol": "BTCUSDT", "side": "buy", "quantity": 5.0, "price": 65000},
        )
        assert response.status_code == 200
        orders = response.json()["orders"]
        assert orders[0]["status"] == "filled"


def test_account_listed_after_creation(client):
    with client:
        client.post("/accounts", json={"account_id": "acct1", "broker": "paper"})
        response = client.get("/accounts")

    accounts = response.json()["accounts"]
    assert len(accounts) == 1
    assert accounts[0]["account_id"] == "acct1"
    assert accounts[0]["broker"] == "paper"


def test_deleting_an_account_removes_it_from_routing_immediately(client):
    with client:
        client.post("/accounts", json={"account_id": "acct1", "broker": "paper"})
        client.post("/routing-rules", json={"source": "tradingview", "destinations": ["acct1"]})
        client.delete("/accounts/acct1")

        response = client.post(
            "/webhook/tradingview", json={"symbol": "BTCUSDT", "side": "buy", "quantity": 1.0}
        )

    # the rule still points at acct1, but the account is gone -- no orders fire
    assert response.json()["orders"] == []


def test_updating_a_routing_rule_changes_destinations_live(client):
    with client:
        client.post("/accounts", json={"account_id": "acct1", "broker": "paper"})
        client.post("/accounts", json={"account_id": "acct2", "broker": "paper"})
        created = client.post("/routing-rules", json={"source": "tradingview", "destinations": ["acct1"]})
        rule_id = created.json()["id"]

        client.put(
            f"/routing-rules/{rule_id}",
            json={"source": "tradingview", "destinations": ["acct2"], "symbol_filter": None},
        )

        response = client.post(
            "/webhook/tradingview", json={"symbol": "BTCUSDT", "side": "buy", "quantity": 1.0}
        )

    orders = response.json()["orders"]
    assert len(orders) == 1
    assert orders[0]["account_id"] == "acct2"


def test_deleting_a_routing_rule_stops_routing_to_it(client):
    with client:
        client.post("/accounts", json={"account_id": "acct1", "broker": "paper"})
        created = client.post("/routing-rules", json={"source": "tradingview", "destinations": ["acct1"]})
        rule_id = created.json()["id"]
        client.delete(f"/routing-rules/{rule_id}")

        response = client.post(
            "/webhook/tradingview", json={"symbol": "BTCUSDT", "side": "buy", "quantity": 1.0}
        )

    assert response.json()["orders"] == []


def test_provider_override_created_via_api_applies_to_the_next_signal(client):
    with client:
        client.post("/accounts", json={"account_id": "acct1", "broker": "paper", "multiplier": 1.0})
        client.post("/routing-rules", json={"source": "tradingview", "destinations": ["acct1"]})
        client.post("/providers/tradingview", json={"display_name": "TV", "multiplier": 0.25})

        response = client.post(
            "/webhook/tradingview", json={"symbol": "BTCUSDT", "side": "buy", "quantity": 100.0}
        )

    orders = response.json()["orders"]
    assert orders[0]["status"] == "filled"
    # 100 * 0.25 (provider override), not 100 * 1.0 (account default)
    # confirmed via /positions since the webhook response doesn't include quantity
    with client:
        positions = client.get("/positions").json()["positions"]
    assert positions[0]["net_quantity"] == 25.0


def test_analyst_override_created_via_api_wins_over_provider(client):
    with client:
        client.post("/accounts", json={"account_id": "acct1", "broker": "paper"})
        client.post("/routing-rules", json={"source": "tradingview", "destinations": ["acct1"]})
        client.post("/providers/tradingview", json={"multiplier": 0.25})
        client.post("/providers/tradingview/analysts/alice", json={"multiplier": 1.0})

        response = client.post(
            "/webhook/tradingview",
            json={"symbol": "BTCUSDT", "side": "buy", "quantity": 100.0, "analyst": "alice"},
        )

    assert response.status_code == 200
    with client:
        positions = client.get("/positions").json()["positions"]
    assert positions[0]["net_quantity"] == 100.0  # analyst override, not the provider's 0.25


def test_deleting_a_provider_reverts_to_account_defaults(client):
    with client:
        client.post("/accounts", json={"account_id": "acct1", "broker": "paper", "multiplier": 1.0})
        client.post("/routing-rules", json={"source": "tradingview", "destinations": ["acct1"]})
        client.post("/providers/tradingview", json={"multiplier": 0.25})
        client.delete("/providers/tradingview")

        response = client.post(
            "/webhook/tradingview", json={"symbol": "BTCUSDT", "side": "buy", "quantity": 100.0}
        )

    with client:
        positions = client.get("/positions").json()["positions"]
    assert positions[0]["net_quantity"] == 100.0  # back to the account's own multiplier of 1.0


def test_config_persists_across_a_fresh_store_reload(tmp_path):
    """Simulates a real restart: a second SignalStore instance pointed at
    the same file must see everything created through the API."""
    from app.routing import load_routing_config_from_store

    store = SignalStore(tmp_path / "persisted.db")
    store.upsert_config_account(account_id="acct1", broker="paper", multiplier=3.0)
    store.insert_config_routing_rule(source="tradingview", destinations=["acct1"])

    reloaded_store = SignalStore(tmp_path / "persisted.db")
    routing_config = load_routing_config_from_store(reloaded_store)

    assert "acct1" in routing_config.accounts
    assert routing_config.accounts["acct1"].multiplier == 3.0
    assert routing_config.rules[0].destinations == ["acct1"]
