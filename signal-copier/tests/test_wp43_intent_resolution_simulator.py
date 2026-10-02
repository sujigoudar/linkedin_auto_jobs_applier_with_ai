"""WP-43: TR-11 precedence and intent resolution in the simulator.

Tests for:
1. Precedence ordering by rule evaluation order in simulate output
2. Per-account intent resolution showing how SELL intents resolve based on position
3. Intent parameter acceptance and routing
"""
from __future__ import annotations

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
    main_module.routing_config.accounts.clear()
    main_module.routing_config.rules.clear()
    main_module.provider_registry.providers.clear()

    test_client = TestClient(main_module.app)
    login = test_client.post("/auth/login", json={"password": "test-owner-password"})
    assert login.status_code == 200
    test_client.headers["X-CSRF-Token"] = login.json()["csrf_token"]
    test_client.headers["X-Webhook-Secret"] = "test-webhook-secret"
    return test_client


def test_simulate_includes_precedence_in_rules(client):
    """WP-07: rules in simulate response are in precedence order with precedence field."""
    with client:
        client.post("/accounts", json={"account_id": "acct_catchall", "broker": "paper"})
        client.post("/accounts", json={"account_id": "acct_btc", "broker": "paper"})

        # Insert catch-all first, then symbol-filtered
        client.post(
            "/routing-rules", json={"source": "tradingview", "destinations": ["acct_catchall"]}
        )
        client.post(
            "/routing-rules",
            json={"source": "tradingview", "destinations": ["acct_btc"], "symbol_filter": ["BTCUSDT"]},
        )

        sim = client.post(
            "/routing-rules/simulate",
            json={"source": "tradingview", "symbol": "BTCUSDT", "side": "buy"},
        )
        assert sim.status_code == 200
        body = sim.json()

        # WP-07: rules are evaluated in precedence order, and each has a precedence field
        rules = body["rules_evaluated"]
        assert len(rules) == 2
        # Symbol-filtered rule has precedence 0 (more specific, evaluated first)
        assert rules[0]["precedence"] == 0
        assert rules[0]["symbol_filter"] == ["BTCUSDT"]
        # Catch-all rule has precedence 1 (evaluated second)
        assert rules[1]["precedence"] == 1
        assert rules[1]["symbol_filter"] is None


def test_simulate_with_sell_intent_on_long_position_resolves_to_exit(client):
    """WP-43: SELL intent on account with long position resolves to exit."""
    with client:
        client.post(
            "/accounts",
            json={"account_id": "acct1", "broker": "paper", "allow_short": False},
        )
        client.post("/routing-rules", json={"source": "tradingview", "destinations": ["acct1"]})

        # Open a long position first
        opened = client.post(
            "/webhook/tradingview",
            json={"symbol": "AAPL", "side": "buy", "quantity": 10},
            headers={"X-Webhook-Secret": "test-webhook-secret"},
        )
        assert opened.json()["orders"][0]["status"] == "filled"

        # Now simulate SELL on that account with long position
        sim = client.post(
            "/routing-rules/simulate",
            json={
                "source": "tradingview",
                "symbol": "AAPL",
                "side": "sell",
                "intent": "sell",  # WP-43: explicit intent parameter
            },
        )
        assert sim.status_code == 200
        body = sim.json()

        # Should have one account (acct1) with resolved intent "exit"
        assert len(body["accounts"]) == 1
        acct = body["accounts"][0]
        assert acct["account_id"] == "acct1"
        assert acct["intent_resolution"]["resolved_intent"] == "exit"
        assert acct["intent_resolution"]["has_long_position"] is True
        assert acct["intent_resolution"]["allow_short"] is False


def test_simulate_with_sell_intent_on_flat_account_allow_short_false_rejects(client):
    """WP-43: SELL intent on flat account with allow_short=false rejects."""
    with client:
        client.post(
            "/accounts",
            json={"account_id": "acct1", "broker": "paper", "allow_short": False},
        )
        client.post("/routing-rules", json={"source": "tradingview", "destinations": ["acct1"]})

        # Simulate SELL on flat account (no position)
        sim = client.post(
            "/routing-rules/simulate",
            json={
                "source": "tradingview",
                "symbol": "AAPL",
                "side": "sell",
                "intent": "sell",
            },
        )
        assert sim.status_code == 200
        body = sim.json()

        acct = body["accounts"][0]
        assert acct["intent_resolution"]["resolved_intent"] == "rejected: sell with no long position and allow_short=false"
        assert acct["intent_resolution"]["has_long_position"] is False
        assert acct["intent_resolution"]["allow_short"] is False


def test_simulate_with_sell_intent_on_flat_account_allow_short_true_enters_short(client):
    """WP-43: SELL intent on flat account with allow_short=true enters short."""
    with client:
        client.post(
            "/accounts",
            json={"account_id": "acct1", "broker": "paper", "allow_short": True},
        )
        client.post("/routing-rules", json={"source": "tradingview", "destinations": ["acct1"]})

        # Simulate SELL on flat account with allow_short=True
        sim = client.post(
            "/routing-rules/simulate",
            json={
                "source": "tradingview",
                "symbol": "AAPL",
                "side": "sell",
                "intent": "sell",
            },
        )
        assert sim.status_code == 200
        body = sim.json()

        acct = body["accounts"][0]
        assert acct["intent_resolution"]["resolved_intent"] == "entry_short"
        assert acct["intent_resolution"]["has_long_position"] is False
        assert acct["intent_resolution"]["allow_short"] is True


def test_simulate_with_short_intent_respects_allow_short(client):
    """WP-43: Explicit 'short' intent respects allow_short setting."""
    with client:
        # Account that disallows shorts
        client.post(
            "/accounts",
            json={"account_id": "acct_no_short", "broker": "paper", "allow_short": False},
        )
        # Account that allows shorts
        client.post(
            "/accounts",
            json={"account_id": "acct_short", "broker": "paper", "allow_short": True},
        )
        client.post(
            "/routing-rules",
            json={"source": "tradingview", "destinations": ["acct_no_short", "acct_short"]},
        )

        # Simulate explicit "short" intent
        sim = client.post(
            "/routing-rules/simulate",
            json={
                "source": "tradingview",
                "symbol": "AAPL",
                "side": "sell",
                "intent": "short",
            },
        )
        assert sim.status_code == 200
        body = sim.json()

        # Find each account in response
        accts = {a["account_id"]: a for a in body["accounts"]}

        # No-short account rejects the short intent
        assert accts["acct_no_short"]["intent_resolution"]["resolved_intent"] == "rejected: allow_short=false"
        assert accts["acct_no_short"]["intent_resolution"]["allow_short"] is False

        # Short-allowed account accepts it
        assert accts["acct_short"]["intent_resolution"]["resolved_intent"] == "entry_short"
        assert accts["acct_short"]["intent_resolution"]["allow_short"] is True


def test_simulate_with_buy_intent_resolves_to_entry_long(client):
    """WP-43: 'buy' intent resolves to entry_long."""
    with client:
        client.post("/accounts", json={"account_id": "acct1", "broker": "paper"})
        client.post("/routing-rules", json={"source": "tradingview", "destinations": ["acct1"]})

        sim = client.post(
            "/routing-rules/simulate",
            json={
                "source": "tradingview",
                "symbol": "AAPL",
                "side": "buy",
                "intent": "buy",
            },
        )
        assert sim.status_code == 200
        body = sim.json()

        acct = body["accounts"][0]
        assert acct["intent_resolution"]["resolved_intent"] == "entry_long"


def test_simulate_with_close_intent_resolves_to_exit(client):
    """WP-43: 'close' intent resolves to exit."""
    with client:
        client.post("/accounts", json={"account_id": "acct1", "broker": "paper"})
        client.post("/routing-rules", json={"source": "tradingview", "destinations": ["acct1"]})

        sim = client.post(
            "/routing-rules/simulate",
            json={
                "source": "tradingview",
                "symbol": "AAPL",
                "side": "close",
                "intent": "close",
            },
        )
        assert sim.status_code == 200
        body = sim.json()

        acct = body["accounts"][0]
        assert acct["intent_resolution"]["resolved_intent"] == "exit"


def test_simulate_with_reduce_intent_resolves_to_reduce(client):
    """WP-43: 'reduce' intent resolves to reduce."""
    with client:
        client.post("/accounts", json={"account_id": "acct1", "broker": "paper"})
        client.post("/routing-rules", json={"source": "tradingview", "destinations": ["acct1"]})

        sim = client.post(
            "/routing-rules/simulate",
            json={
                "source": "tradingview",
                "symbol": "AAPL",
                "side": "sell",
                "intent": "reduce",
            },
        )
        assert sim.status_code == 200
        body = sim.json()

        acct = body["accounts"][0]
        assert acct["intent_resolution"]["resolved_intent"] == "reduce"


def test_simulate_without_intent_parameter_still_works(client):
    """WP-43: Simulate works without intent parameter (backward compatible)."""
    with client:
        client.post("/accounts", json={"account_id": "acct1", "broker": "paper"})
        client.post("/routing-rules", json={"source": "tradingview", "destinations": ["acct1"]})

        # Call without intent parameter (omitted)
        sim = client.post(
            "/routing-rules/simulate",
            json={"source": "tradingview", "symbol": "AAPL", "side": "buy"},
        )
        assert sim.status_code == 200
        body = sim.json()

        # Should still include intent_resolution with default "entry_long"
        acct = body["accounts"][0]
        assert "intent_resolution" in acct
        assert acct["intent_resolution"]["resolved_intent"] == "entry_long"


def test_intent_resolution_shows_position_state(client):
    """WP-43: Intent resolution output shows has_long_position and allow_short."""
    with client:
        client.post(
            "/accounts",
            json={"account_id": "acct1", "broker": "paper", "allow_short": True},
        )
        client.post("/routing-rules", json={"source": "tradingview", "destinations": ["acct1"]})

        # Open a long position
        client.post(
            "/webhook/tradingview",
            json={"symbol": "AAPL", "side": "buy", "quantity": 5},
            headers={"X-Webhook-Secret": "test-webhook-secret"},
        )

        # Simulate with position held
        sim = client.post(
            "/routing-rules/simulate",
            json={
                "source": "tradingview",
                "symbol": "AAPL",
                "side": "sell",
                "intent": "sell",
            },
        )
        assert sim.status_code == 200
        body = sim.json()

        acct = body["accounts"][0]
        intent_res = acct["intent_resolution"]

        # All relevant fields should be present
        assert "resolved_intent" in intent_res
        assert "has_long_position" in intent_res
        assert "allow_short" in intent_res

        # Position should be detected
        assert intent_res["has_long_position"] is True
        assert intent_res["allow_short"] is True
        # SELL resolves to exit when holding long
        assert intent_res["resolved_intent"] == "exit"
