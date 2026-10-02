"""TR-11 (Routing and allocation rules) redesign: routing graph +
"if this signal arrived now" dry-run simulator + before-you-save position
impact -- see app/static/views/tr11.js's module docstring.

The load-bearing invariant this file protects: `POST /routing-rules/
simulate` must call the REAL matching function (`RoutingConfig.evaluate`,
which `destinations_for`/`app/engine.py`'s real signal-ingestion path also
calls), never a reimplemented approximation that could silently diverge
from what a real signal would actually do. `test_simulate_precedence_
matches_the_real_engines_own_precedence` seeds two overlapping rules with
real precedence (a catch-all rule inserted BEFORE a more specific one) and
asserts the simulator's `final_destinations` exactly matches what a real
webhook delivery of the identical signal produces -- see this file's
report for the temporary-break-then-restore verification performed
against this exact test before committing (an earlier version of
`RoutingConfig.evaluate` iterating `reversed(self.rules)` — a stale/
wrong-order matcher — was manually substituted in, this test failed as
expected, then the fix was restored and this test went green again).
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


def test_new_endpoints_require_owner_session(monkeypatch, tmp_path):
    import app.main as main_module
    from app import config as app_config

    monkeypatch.setattr(app_config, "OWNER_PASSWORD", "test-owner-password")
    monkeypatch.setattr(app_config, "SESSION_SECRET", "test-session-secret")
    store = SignalStore(tmp_path / "test.db")
    monkeypatch.setattr(main_module, "store", store)
    anon = TestClient(main_module.app)
    assert anon.post("/routing-rules/simulate", json={"source": "x", "symbol": "BTCUSDT"}).status_code == 401
    assert anon.post(
        "/routing-rules/position-impact", json={"source": "x", "destinations": []}
    ).status_code == 401


def test_simulate_precedence_matches_the_real_engines_own_precedence(client):
    with client:
        client.post("/accounts", json={"account_id": "acct_catchall", "broker": "paper"})
        client.post("/accounts", json={"account_id": "acct_btc", "broker": "paper"})

        # Real precedence per app/routing.py: rules evaluate in ascending
        # rule id / DB insertion order. Insert the catch-all FIRST and the
        # symbol-filtered rule SECOND -- both name a *different* account, so
        # this exercises real per-rule fan-out/ordering (not just SIG-01
        # dedup, which is already covered elsewhere).
        client.post(
            "/routing-rules", json={"source": "tradingview", "destinations": ["acct_catchall"]}
        )
        client.post(
            "/routing-rules",
            json={"source": "tradingview", "destinations": ["acct_btc"], "symbol_filter": ["BTCUSDT"]},
        )

        sim = client.post(
            "/routing-rules/simulate",
            json={"source": "tradingview", "symbol": "BTCUSDT", "side": "buy", "quantity": 1.0},
        )
        assert sim.status_code == 200
        body = sim.json()

        # Every real rule was evaluated, in real DB order, and both really
        # matched (same source, and the BTCUSDT symbol_filter matches).
        assert [r["id"] for r in body["rules_evaluated"]] == [1, 2]
        assert body["rules_evaluated"][0]["matched"] is True
        assert body["rules_evaluated"][0]["admitted_accounts"] == ["acct_catchall"]
        assert body["rules_evaluated"][1]["matched"] is True
        assert body["rules_evaluated"][1]["admitted_accounts"] == ["acct_btc"]

        # ALLOC-01: both rules are `single` mode, so they merge into ONE
        # ordered pool and exactly one account (the first eligible, in
        # priority order) is selected; the other is eligible-not-selected.
        assert body["final_destinations"] == ["acct_catchall"]
        assert body["allocation"]["selected_account_id"] == "acct_catchall"
        assert body["allocation"]["eligible_single_pool"] == ["acct_catchall", "acct_btc"]
        statuses = {a["account_id"]: a["allocation"]["status"] for a in body["accounts"]}
        assert statuses == {"acct_catchall": "selected", "acct_btc": "eligible_not_selected"}

        # Cross-check against the REAL engine: send the identical signal for
        # real via the webhook endpoint and confirm it actually reaches the
        # same destinations the simulator said it would.
        real = client.post(
            "/webhook/tradingview",
            json={"symbol": "BTCUSDT", "side": "buy", "quantity": 1.0},
            headers={"X-Webhook-Secret": "test-webhook-secret"},
        )
        assert real.status_code == 200
        real_accounts = {o["account_id"] for o in real.json()["orders"]}
        assert real_accounts == set(body["final_destinations"])


def test_simulate_a_non_matching_symbol_filter_is_reported_as_not_matched(client):
    with client:
        client.post("/accounts", json={"account_id": "acct1", "broker": "paper"})
        client.post(
            "/routing-rules",
            json={"source": "tradingview", "destinations": ["acct1"], "symbol_filter": ["ETHUSDT"]},
        )
        sim = client.post(
            "/routing-rules/simulate", json={"source": "tradingview", "symbol": "BTCUSDT", "side": "buy"}
        )
        body = sim.json()
        assert body["rules_evaluated"][0]["matched"] is False
        assert "symbol_filter" in body["rules_evaluated"][0]["reason"]
        assert body["final_destinations"] == []


def test_simulate_reports_a_disabled_destination_account_as_rejected(client):
    with client:
        client.post("/accounts", json={"account_id": "acct1", "broker": "paper", "enabled": False})
        client.post("/routing-rules", json={"source": "tradingview", "destinations": ["acct1"]})
        sim = client.post(
            "/routing-rules/simulate", json={"source": "tradingview", "symbol": "BTCUSDT", "side": "buy"}
        )
        body = sim.json()
        # destinations_for(include_disabled=False) never even returns this
        # account for a BUY, so it shows up as a paused destination on its
        # matching rule, not in `accounts`/`final_destinations` at all.
        assert body["rules_evaluated"][0]["paused_accounts"] == ["acct1"]
        assert body["accounts"] == []
        assert body["final_destinations"] == []

        # But a CLOSE must still be able to reach it (EXE-10) -- same real
        # include_disabled=True behaviour the engine itself uses for closes.
        sim_close = client.post(
            "/routing-rules/simulate", json={"source": "tradingview", "symbol": "BTCUSDT", "side": "close"}
        )
        close_body = sim_close.json()
        assert close_body["rules_evaluated"][0]["admitted_accounts"] == ["acct1"]
        assert close_body["final_destinations"] == ["acct1"]


def test_simulate_capital_reservation_check_matches_the_real_admission_gate_and_leaves_no_side_effect(client):
    with client:
        client.post(
            "/accounts",
            json={"account_id": "acct1", "broker": "paper", "max_notional_exposure": 50.0},
        )
        client.post("/routing-rules", json={"source": "tradingview", "destinations": ["acct1"]})

        sim = client.post(
            "/routing-rules/simulate",
            json={"source": "tradingview", "symbol": "BTCUSDT", "side": "buy", "quantity": 1.0, "price": 100.0},
        )
        body = sim.json()
        capital = body["accounts"][0]["capital_reservation"]
        assert capital["status"] == "would_reject"
        assert "ceiling" in capital["reason"]
        assert body["accounts"][0]["would_receive_this_signal"] is False
        assert body["final_destinations"] == []

        # No side effect: the real shared CapitalAllocator's pending
        # reservation for this account is back to zero after the dry run.
        alloc = client.get("/capital-allocation").json()
        acct1 = next(a for a in alloc["accounts"] if a["account_id"] == "acct1")
        assert acct1["reserved_notional"] == 0.0

        # Cross-check: a real signal with the same shape is really rejected
        # by the real engine for the same reason.
        real = client.post(
            "/webhook/tradingview",
            json={"symbol": "BTCUSDT", "side": "buy", "quantity": 1.0, "price": 100.0},
            headers={"X-Webhook-Secret": "test-webhook-secret"},
        )
        assert real.json()["orders"][0]["status"] == "rejected"
        assert "ceiling" in real.json()["orders"][0]["message"]


def test_simulate_never_creates_a_signal_or_order(client):
    with client:
        client.post("/accounts", json={"account_id": "acct1", "broker": "paper"})
        client.post("/routing-rules", json={"source": "tradingview", "destinations": ["acct1"]})

        before_signals = client.get("/signals").json()
        before_positions = client.get("/positions").json()

        client.post(
            "/routing-rules/simulate",
            json={"source": "tradingview", "symbol": "BTCUSDT", "side": "buy", "quantity": 1.0, "price": 100.0},
        )

        assert client.get("/signals").json() == before_signals
        assert client.get("/positions").json() == before_positions


def test_simulate_deduplication_is_honestly_not_tracked(client):
    with client:
        client.post("/accounts", json={"account_id": "acct1", "broker": "paper"})
        client.post("/routing-rules", json={"source": "tradingview", "destinations": ["acct1"]})
        sim = client.post(
            "/routing-rules/simulate", json={"source": "tradingview", "symbol": "BTCUSDT", "side": "buy"}
        )
        assert sim.json()["deduplication"]["status"] == "not_tracked"


# --- Position impact: real open positions vs a pending rule edit ---


def test_position_impact_flags_an_edit_that_removes_the_only_exit_path(client):
    with client:
        client.post("/accounts", json={"account_id": "acct1", "broker": "paper"})
        rule = client.post(
            "/routing-rules", json={"source": "tradingview", "destinations": ["acct1"]}
        )
        rule_id = rule.json()["id"]

        opened = client.post(
            "/webhook/tradingview",
            json={"symbol": "BTCUSDT", "side": "buy", "quantity": 1.0},
            headers={"X-Webhook-Secret": "test-webhook-secret"},
        )
        assert opened.json()["orders"][0]["status"] == "filled"

        # Pending edit removes acct1 from the rule's destinations entirely.
        impact = client.post(
            "/routing-rules/position-impact",
            json={"rule_id": rule_id, "source": "tradingview", "destinations": [], "symbol_filter": None},
        )
        assert impact.status_code == 200
        positions = impact.json()["positions"]
        assert len(positions) == 1
        assert positions[0]["account_id"] == "acct1"
        assert positions[0]["origin_source"] == "tradingview"
        assert positions[0]["would_route_now"] == {"entry": True, "close": True}
        assert positions[0]["would_route_after_save"] == {"entry": False, "close": False}
        assert positions[0]["impact"] == "exit_path_removed"


def test_position_impact_reports_unaffected_when_the_edit_does_not_touch_this_account(client):
    with client:
        client.post("/accounts", json={"account_id": "acct1", "broker": "paper"})
        client.post("/accounts", json={"account_id": "acct2", "broker": "paper"})
        rule = client.post(
            "/routing-rules", json={"source": "tradingview", "destinations": ["acct1", "acct2"]}
        )
        rule_id = rule.json()["id"]
        client.post(
            "/webhook/tradingview",
            json={"symbol": "BTCUSDT", "side": "buy", "quantity": 1.0},
            headers={"X-Webhook-Secret": "test-webhook-secret"},
        )

        # Pending edit only narrows the symbol filter to a DIFFERENT symbol
        # for a hypothetical second account -- acct1's own BTCUSDT position
        # keeps exactly the same entry/exit routing either way here since we
        # keep it in destinations with no symbol_filter change... instead
        # exercise the truly-unaffected case: destinations unchanged.
        impact = client.post(
            "/routing-rules/position-impact",
            json={"rule_id": rule_id, "source": "tradingview", "destinations": ["acct1", "acct2"], "symbol_filter": None},
        )
        positions = {p["account_id"]: p for p in impact.json()["positions"]}
        assert positions["acct1"]["impact"] == "unaffected"


def test_position_impact_ignores_positions_from_a_different_provider(client):
    with client:
        client.post("/accounts", json={"account_id": "acct1", "broker": "paper"})
        client.post("/routing-rules", json={"source": "telegram", "destinations": ["acct1"]})
        rule = client.post(
            "/routing-rules", json={"source": "tradingview", "destinations": ["acct1"]}
        )
        rule_id = rule.json()["id"]
        client.post(
            "/webhook/telegram",
            json={"symbol": "ETHUSDT", "side": "buy", "quantity": 1.0},
            headers={"X-Webhook-Secret": "test-webhook-secret"},
        )

        # Editing the (different) tradingview rule must not claim the
        # telegram-originated position as in scope.
        impact = client.post(
            "/routing-rules/position-impact",
            json={"rule_id": rule_id, "source": "tradingview", "destinations": [], "symbol_filter": None},
        )
        assert impact.json()["positions"] == []
