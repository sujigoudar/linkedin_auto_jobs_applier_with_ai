from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.db import SignalStore
from app.models import Signal, Side


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


def test_saved_signal_round_trips_stop_loss_take_profit_and_analyst(store):
    signal = _signal(analyst="alice")
    store.save_signal(signal)

    rows = store.list_signals_in_range(source="tradingview")

    assert len(rows) == 1
    assert rows[0]["stop_loss"] == 95.0
    assert rows[0]["take_profit"] == 110.0
    assert rows[0]["analyst"] == "alice"


def test_list_signals_in_range_filters_by_source_symbol_and_date(store):
    store.save_signal(_signal(source="tradingview", symbol="AAPL", received_at=datetime(2024, 1, 1, tzinfo=timezone.utc)))
    store.save_signal(_signal(source="telegram", symbol="AAPL", received_at=datetime(2024, 1, 2, tzinfo=timezone.utc)))
    store.save_signal(_signal(source="tradingview", symbol="MSFT", received_at=datetime(2024, 1, 3, tzinfo=timezone.utc)))
    store.save_signal(_signal(source="tradingview", symbol="AAPL", received_at=datetime(2024, 6, 1, tzinfo=timezone.utc)))

    rows = store.list_signals_in_range(
        source="tradingview",
        symbol="AAPL",
        start=datetime(2024, 1, 1, tzinfo=timezone.utc),
        end=datetime(2024, 1, 31, tzinfo=timezone.utc),
    )

    assert len(rows) == 1
    assert rows[0]["source"] == "tradingview"
    assert rows[0]["symbol"] == "AAPL"


def test_list_signals_in_range_orders_ascending_by_time(store):
    store.save_signal(_signal(received_at=datetime(2024, 1, 3, tzinfo=timezone.utc)))
    store.save_signal(_signal(received_at=datetime(2024, 1, 1, tzinfo=timezone.utc)))
    store.save_signal(_signal(received_at=datetime(2024, 1, 2, tzinfo=timezone.utc)))

    rows = store.list_signals_in_range(source="tradingview")

    times = [r["received_at"] for r in rows]
    assert times == sorted(times)


def test_backtest_endpoint_runs_a_real_replay_end_to_end(store, tmp_path, monkeypatch):
    import app.main as main_module
    from app import config as app_config

    monkeypatch.setattr(app_config, "OWNER_PASSWORD", "test-owner-password")
    monkeypatch.setattr(app_config, "SESSION_SECRET", "test-session-secret")
    monkeypatch.setattr(main_module, "store", store)
    store.save_signal(
        _signal(source="tradingview", symbol="AAPL", received_at=datetime(2024, 1, 1, tzinfo=timezone.utc))
    )

    csv_path = tmp_path / "AAPL.csv"
    csv_path.write_text(
        "timestamp,open,high,low,close\n"
        "2024-01-02T00:00:00+00:00,101,111,100,110\n"  # target=110 hit
    )

    client = TestClient(main_module.app)
    with client:
        login = client.post("/auth/login", json={"password": "test-owner-password"})
        assert login.status_code == 200
        client.headers["X-CSRF-Token"] = login.json()["csrf_token"]
        response = client.post(
            "/backtest",
            json={
                "source": "tradingview",
                "symbol": "AAPL",
                "start": "2024-01-01T00:00:00+00:00",
                "end": "2024-01-31T00:00:00+00:00",
                "csv_paths": {"AAPL": str(csv_path)},
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["summary"]["wins"] == 1
    assert body["trades"][0]["outcome"] == "win"
    assert body["trades"][0]["pnl"] == pytest.approx((110.0 - 100.0) * 10.0)
    assert "stressed_summary" not in body  # no slippage_bps/fee_per_trade requested


def test_backtest_endpoint_applies_cost_stress_when_requested(store, tmp_path, monkeypatch):
    """E07 (bounded): slippage_bps/fee_per_trade in the request produce a
    stressed_summary alongside the raw one."""
    import app.main as main_module
    from app import config as app_config

    monkeypatch.setattr(app_config, "OWNER_PASSWORD", "test-owner-password")
    monkeypatch.setattr(app_config, "SESSION_SECRET", "test-session-secret")
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
        login = client.post("/auth/login", json={"password": "test-owner-password"})
        client.headers["X-CSRF-Token"] = login.json()["csrf_token"]
        response = client.post(
            "/backtest",
            json={
                "source": "tradingview",
                "symbol": "AAPL",
                "start": "2024-01-01T00:00:00+00:00",
                "end": "2024-01-31T00:00:00+00:00",
                "csv_paths": {"AAPL": str(csv_path)},
                "slippage_bps": 100.0,
                "fee_per_trade": 2.0,
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert "stressed_summary" in body
    assert body["stressed_summary"]["total_pnl"] < body["summary"]["total_pnl"]


def test_fit_simulation_endpoint_runs_a_real_personalized_replay_end_to_end(store, tmp_path, monkeypatch):
    """The Alertsify-style "what would copying this source have done to
    MY account" endpoint -- personalized to a caller-supplied
    account_size/max_per_trade, distinct from /backtest's own
    unscaled replay."""
    import app.main as main_module
    from app import config as app_config

    monkeypatch.setattr(app_config, "OWNER_PASSWORD", "test-owner-password")
    monkeypatch.setattr(app_config, "SESSION_SECRET", "test-session-secret")
    monkeypatch.setattr(main_module, "store", store)
    # A 100-share position at $100 (a $10,000 trade) against a $2,500
    # max_per_trade must be capped to 25 shares in the response.
    store.save_signal(
        _signal(
            source="alerts_guy", symbol="AAPL", quantity=100.0, price=100.0,
            received_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        )
    )

    csv_path = tmp_path / "AAPL.csv"
    csv_path.write_text(
        "timestamp,open,high,low,close\n"
        "2024-01-02T00:00:00+00:00,101,111,100,110\n"  # target=110 hit
    )

    client = TestClient(main_module.app)
    with client:
        login = client.post("/auth/login", json={"password": "test-owner-password"})
        assert login.status_code == 200
        client.headers["X-CSRF-Token"] = login.json()["csrf_token"]
        response = client.post(
            "/providers/alerts_guy/fit-simulation",
            json={
                "source": "alerts_guy",
                "account_size": 25000.0,
                "max_per_trade": 2500.0,
                # Large enough to cover the fixed 2024-01-01 signal date from
                # whenever this test actually runs -- the endpoint (unlike the
                # unit tests in test_fit_simulator.py) has no `now=` override.
                "lookback_days": 5000,
                "csv_paths": {"AAPL": str(csv_path)},
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["summary"]["fit_count"] == 1
    assert body["summary"]["fit_percentage"] == 1.0
    trade = body["trades"][0]
    assert trade["fits"] is True
    assert trade["original_quantity"] == pytest.approx(100.0)
    assert trade["simulated_quantity"] == pytest.approx(25.0)
    # source's own recorded pnl would be (110-100)*100=1000; rescaled by 25/100
    assert trade["pnl"] == pytest.approx(250.0)
    assert body["summary"]["simulated_pnl_at_your_size"] == pytest.approx(250.0)
    # "their record at full size" is preserved alongside the personalized numbers
    assert body["summary"]["full_size_summary"]["total_pnl"] == pytest.approx(1000.0)
    assert len(body["equity_curve"]) == 1


def test_fit_simulation_endpoint_rejects_mismatched_path_and_body_source(store, monkeypatch):
    import app.main as main_module
    from app import config as app_config

    monkeypatch.setattr(app_config, "OWNER_PASSWORD", "test-owner-password")
    monkeypatch.setattr(app_config, "SESSION_SECRET", "test-session-secret")
    monkeypatch.setattr(main_module, "store", store)

    client = TestClient(main_module.app)
    with client:
        login = client.post("/auth/login", json={"password": "test-owner-password"})
        client.headers["X-CSRF-Token"] = login.json()["csrf_token"]
        response = client.post(
            "/providers/alerts_guy/fit-simulation",
            json={
                "source": "someone_else",
                "account_size": 25000.0,
                "max_per_trade": 2500.0,
                "csv_paths": {},
            },
        )

    assert response.status_code == 422
