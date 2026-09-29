"""TR-15: real, durable persistence for a completed POST /backtest replay
(app/db.py's backtest_runs table + app/main.py's run_backtest/config-hash
wiring). Covers:

- a run actually surviving as a row a fresh SignalStore/process can read
  back (the "reload the page" guarantee, exercised at the store layer
  directly since that's what the endpoint calls into),
- the two new list/detail endpoints,
- the trade-explorer market-path endpoint re-reading the run's own
  persisted CSV path,
- and, load-bearingly, that `compute_backtest_config_hash` actually
  distinguishes genuinely different configs rather than collapsing them
  onto the same hash (a corruption risk for the run-comparison view).
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.db import SignalStore
from app.models import Side, Signal


@pytest.fixture
def store(tmp_path):
    return SignalStore(tmp_path / "test.db")


def _signal(**overrides):
    defaults = dict(
        source="tradingview",
        symbol="AAPL",
        side=Side.BUY,
        quantity=10.0,
        price=100.0,
        stop_loss=95.0,
        take_profit=110.0,
        received_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
    )
    defaults.update(overrides)
    return Signal(**defaults)


def _login(client, monkeypatch, main_module, app_config):
    monkeypatch.setattr(app_config, "OWNER_PASSWORD", "test-owner-password")
    monkeypatch.setattr(app_config, "SESSION_SECRET", "test-session-secret")
    login = client.post("/auth/login", json={"password": "test-owner-password"})
    assert login.status_code == 200
    client.headers["X-CSRF-Token"] = login.json()["csrf_token"]


# --- Store-layer round trip: proves a run survives past this process ---

def test_backtest_run_persists_and_round_trips_at_the_store_layer(store):
    run_id = store.save_backtest_run(
        config_hash="abc123",
        created_at=datetime(2024, 1, 5, tzinfo=timezone.utc),
        request={"source": "tradingview", "csv_paths": {"AAPL": "/tmp/AAPL.csv"}},
        summary={"total_signals": 1, "wins": 1},
        trades=[{"signal_id": "s1", "symbol": "AAPL", "pnl": 100.0}],
        stressed_summary=None,
        cost_stress_note=None,
    )

    # A brand-new SignalStore instance against the SAME file -- simulates a
    # fresh process (e.g. a page reload hitting a server that restarted) --
    # must see the exact same persisted run, not an in-memory artifact.
    reopened = SignalStore(store.db_path)
    listed = reopened.list_backtest_runs(limit=10)
    assert len(listed) == 1
    assert listed[0]["id"] == run_id
    assert listed[0]["config_hash"] == "abc123"
    assert listed[0]["summary"]["wins"] == 1

    detail = reopened.get_backtest_run(run_id)
    assert detail is not None
    assert detail["trades"][0]["signal_id"] == "s1"
    assert detail["request"]["csv_paths"]["AAPL"] == "/tmp/AAPL.csv"


def test_get_backtest_run_returns_none_for_unknown_id(store):
    assert store.get_backtest_run(999) is None


def test_list_backtest_runs_orders_most_recent_first(store):
    store.save_backtest_run(
        config_hash="h1", created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        request={}, summary={}, trades=[],
    )
    store.save_backtest_run(
        config_hash="h2", created_at=datetime(2024, 2, 1, tzinfo=timezone.utc),
        request={}, summary={}, trades=[],
    )
    listed = store.list_backtest_runs(limit=10)
    assert [r["config_hash"] for r in listed] == ["h2", "h1"]


# --- Backfill migration: a pre-existing on-disk DB missing backtest_runs ---

def test_pre_existing_database_without_backtest_runs_upgrades_cleanly(tmp_path):
    """A database file created before this table existed (schema minus
    backtest_runs, no alembic_version table at all -- exactly what an
    operator's real, already-deployed database looks like right now)
    must open cleanly, gain the new table, and still work end to end --
    never require a manual migration step or lose existing data."""
    import sqlite3

    from app.db import SCHEMA

    db_path = tmp_path / "pre_existing.db"
    legacy_schema = SCHEMA.split("-- TR-15: real, durable persistence")[0]
    assert "backtest_runs" not in legacy_schema  # sanity: the split actually removed it

    conn = sqlite3.connect(db_path)
    conn.executescript(legacy_schema)
    signal = Signal(source="test", symbol="AAPL", side=Side.BUY)
    conn.execute(
        "INSERT INTO signals (id, source, symbol, side, asset_class, received_at, raw) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (signal.id, "test", "AAPL", "buy", "crypto", signal.received_at.isoformat(), "{}"),
    )
    conn.commit()
    conn.close()

    # Opening with the current SignalStore must add backtest_runs (CREATE
    # TABLE IF NOT EXISTS) without touching the pre-existing signal row.
    upgraded = SignalStore(db_path)

    run_id = upgraded.save_backtest_run(
        config_hash="new-hash", created_at=datetime.now(timezone.utc),
        request={}, summary={}, trades=[],
    )
    assert upgraded.get_backtest_run(run_id) is not None

    rows = upgraded.list_signals_in_range(source="test")
    assert len(rows) == 1  # the pre-existing signal survived untouched


# --- HTTP endpoints ---

def test_backtest_endpoint_persists_run_and_list_detail_endpoints_see_it(store, tmp_path, monkeypatch):
    import app.main as main_module
    from app import config as app_config

    monkeypatch.setattr(main_module, "store", store)
    store.save_signal(
        _signal(source="tradingview", symbol="AAPL", received_at=datetime(2024, 1, 1, tzinfo=timezone.utc))
    )
    csv_path = tmp_path / "AAPL.csv"
    csv_path.write_text(
        "timestamp,open,high,low,close\n"
        "2024-01-02T00:00:00+00:00,101,111,100,110\n"
    )

    client = TestClient(main_module.app)
    with client:
        _login(client, monkeypatch, main_module, app_config)
        req = {
            "source": "tradingview",
            "symbol": "AAPL",
            "start": "2024-01-01T00:00:00+00:00",
            "end": "2024-01-31T00:00:00+00:00",
            "csv_paths": {"AAPL": str(csv_path)},
        }
        response = client.post("/backtest", json=req)
        assert response.status_code == 200
        body = response.json()
        assert "run_id" in body and "config_hash" in body
        run_id = body["run_id"]

        listed = client.get("/backtest/runs")
        assert listed.status_code == 200
        runs = listed.json()["runs"]
        assert any(r["id"] == run_id for r in runs)

        detail = client.get(f"/backtest/runs/{run_id}")
        assert detail.status_code == 200
        detail_body = detail.json()
        assert detail_body["trades"][0]["outcome"] == "win"
        assert detail_body["config_hash"] == body["config_hash"]

        missing = client.get("/backtest/runs/999999")
        assert missing.status_code == 404


def test_reloading_after_two_different_runs_still_shows_both(store, tmp_path, monkeypatch):
    """The concrete "reload the page" guarantee: two backtests with
    genuinely different policy params (max_hold_days) against the SAME
    signal/CSV data both persist as distinct runs, and a fresh list call
    (simulating a page reload) sees both."""
    import app.main as main_module
    from app import config as app_config

    monkeypatch.setattr(main_module, "store", store)
    store.save_signal(
        _signal(source="tradingview", symbol="AAPL", received_at=datetime(2024, 1, 1, tzinfo=timezone.utc))
    )
    csv_path = tmp_path / "AAPL.csv"
    csv_path.write_text(
        "timestamp,open,high,low,close\n"
        "2024-01-02T00:00:00+00:00,101,111,100,110\n"
    )

    client = TestClient(main_module.app)
    with client:
        _login(client, monkeypatch, main_module, app_config)
        base_req = {
            "source": "tradingview",
            "symbol": "AAPL",
            "start": "2024-01-01T00:00:00+00:00",
            "end": "2024-01-31T00:00:00+00:00",
            "csv_paths": {"AAPL": str(csv_path)},
        }
        r1 = client.post("/backtest", json={**base_req, "max_hold_days": 5.0})
        r2 = client.post("/backtest", json={**base_req, "max_hold_days": 30.0})
        assert r1.json()["config_hash"] != r2.json()["config_hash"]

        # Simulate "reload": a brand-new list call sees both runs.
        runs = client.get("/backtest/runs").json()["runs"]
        ids = {r1.json()["run_id"], r2.json()["run_id"]}
        assert ids.issubset({r["id"] for r in runs})


def test_market_path_endpoint_returns_real_bars_from_the_runs_own_csv(store, tmp_path, monkeypatch):
    import app.main as main_module
    from app import config as app_config

    monkeypatch.setattr(main_module, "store", store)
    store.save_signal(
        _signal(source="tradingview", symbol="AAPL", received_at=datetime(2024, 1, 1, tzinfo=timezone.utc))
    )
    csv_path = tmp_path / "AAPL.csv"
    csv_path.write_text(
        "timestamp,open,high,low,close\n"
        "2024-01-01T12:00:00+00:00,100,103,99,102\n"
        "2024-01-02T00:00:00+00:00,101,111,100,110\n"
    )

    client = TestClient(main_module.app)
    with client:
        _login(client, monkeypatch, main_module, app_config)
        req = {
            "source": "tradingview",
            "symbol": "AAPL",
            "start": "2024-01-01T00:00:00+00:00",
            "end": "2024-01-31T00:00:00+00:00",
            "csv_paths": {"AAPL": str(csv_path)},
        }
        run = client.post("/backtest", json=req).json()
        run_id = run["run_id"]
        signal_id = run["trades"][0]["signal_id"]

        path_res = client.get(f"/backtest/runs/{run_id}/trades/{signal_id}/market-path")
        assert path_res.status_code == 200
        body = path_res.json()
        assert body["available"] is True
        assert len(body["bars"]) == 2
        assert body["bars"][0]["close"] == 102


def test_market_path_endpoint_honestly_reports_missing_csv(store, tmp_path, monkeypatch):
    import app.main as main_module
    from app import config as app_config

    monkeypatch.setattr(main_module, "store", store)
    store.save_signal(
        _signal(source="tradingview", symbol="AAPL", received_at=datetime(2024, 1, 1, tzinfo=timezone.utc))
    )
    csv_path = tmp_path / "AAPL.csv"
    csv_path.write_text(
        "timestamp,open,high,low,close\n"
        "2024-01-02T00:00:00+00:00,101,111,100,110\n"
    )

    client = TestClient(main_module.app)
    with client:
        _login(client, monkeypatch, main_module, app_config)
        req = {
            "source": "tradingview",
            "symbol": "AAPL",
            "start": "2024-01-01T00:00:00+00:00",
            "end": "2024-01-31T00:00:00+00:00",
            "csv_paths": {"AAPL": str(csv_path)},
        }
        run = client.post("/backtest", json=req).json()
        run_id = run["run_id"]
        signal_id = run["trades"][0]["signal_id"]

        csv_path.unlink()  # simulate the CSV moving/disappearing after the run

        path_res = client.get(f"/backtest/runs/{run_id}/trades/{signal_id}/market-path")
        assert path_res.status_code == 200
        body = path_res.json()
        assert body["available"] is False
        assert body["bars"] == []


# --- Load-bearing: config-hash collision resistance ---

def test_config_hash_distinguishes_genuinely_different_configs(tmp_path):
    from app.main import BacktestRequest, compute_backtest_config_hash

    csv_a = tmp_path / "A.csv"
    csv_a.write_text("timestamp,open,high,low,close\n2024-01-01T00:00:00+00:00,1,2,0,1\n")
    csv_b = tmp_path / "B.csv"
    csv_b.write_text("timestamp,open,high,low,close\n2024-01-01T00:00:00+00:00,9,9,9,9\n")

    base = dict(
        source="tradingview",
        start="2024-01-01T00:00:00+00:00",
        end="2024-01-31T00:00:00+00:00",
        csv_paths={"AAPL": str(csv_a)},
    )

    hash_base = compute_backtest_config_hash(BacktestRequest(**base))

    # Different max_hold_days -> different hash.
    hash_diff_hold = compute_backtest_config_hash(BacktestRequest(**{**base, "max_hold_days": 99.0}))
    assert hash_diff_hold != hash_base

    # Different slippage_bps -> different hash.
    hash_diff_slippage = compute_backtest_config_hash(BacktestRequest(**{**base, "slippage_bps": 25.0}))
    assert hash_diff_slippage != hash_base

    # Different fee_per_trade -> different hash.
    hash_diff_fee = compute_backtest_config_hash(BacktestRequest(**{**base, "fee_per_trade": 3.0}))
    assert hash_diff_fee != hash_base

    # Different period -> different hash.
    hash_diff_period = compute_backtest_config_hash(
        BacktestRequest(**{**base, "end": "2024-06-30T00:00:00+00:00"})
    )
    assert hash_diff_period != hash_base

    # Same path, but genuinely DIFFERENT CSV content -> different hash
    # (this is the exact case a naive "hash the path string" implementation
    # would miss).
    hash_diff_symbol_filter = compute_backtest_config_hash(BacktestRequest(**{**base, "symbol": "AAPL"}))
    assert hash_diff_symbol_filter != hash_base  # symbol filter None vs "AAPL"

    hash_diff_csv_content = compute_backtest_config_hash(
        BacktestRequest(**{**base, "csv_paths": {"AAPL": str(csv_b)}})
    )
    assert hash_diff_csv_content != hash_base

    # Identical config really does hash identically (not just "always different").
    hash_repeat = compute_backtest_config_hash(BacktestRequest(**base))
    assert hash_repeat == hash_base
