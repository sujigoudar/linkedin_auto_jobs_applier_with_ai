"""E05: GET /accounts/{account_id}/execution-quality."""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app import config as app_config
from app.db import SignalStore
from app.models import DestinationAccount, OrderResult, OrderStatus, Side, Signal


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(app_config, "OWNER_PASSWORD", "test-owner-password")
    monkeypatch.setattr(app_config, "SESSION_SECRET", "test-session-secret")

    store = SignalStore(tmp_path / "test.db")
    monkeypatch.setattr(main_module, "store", store)
    monkeypatch.setattr(main_module.engine, "store", store)
    main_module.routing_config.accounts.clear()
    main_module.routing_config.accounts["acct1"] = DestinationAccount(account_id="acct1", broker="paper")

    test_client = TestClient(main_module.app)
    login = test_client.post("/auth/login", json={"password": "test-owner-password"})
    assert login.status_code == 200
    return test_client, store


def test_unknown_account_404s(client):
    test_client, _ = client
    with test_client:
        response = test_client.get("/accounts/nope/execution-quality")
    assert response.status_code == 404


def test_unauthenticated_request_401s(client):
    test_client, _ = client
    with test_client:
        response = TestClient(main_module.app).get("/accounts/acct1/execution-quality")
    assert response.status_code == 401


def test_returns_latency_from_actual_fills(client):
    test_client, store = client
    t0 = datetime(2024, 1, 1, tzinfo=timezone.utc)
    signal = Signal(source="test", symbol="AAPL", side=Side.BUY, received_at=t0)
    store.save_signal(signal)
    store.save_order_result(
        OrderResult(
            account_id="acct1",
            status=OrderStatus.FILLED,
            signal_id=signal.id,
            filled_quantity=10.0,
            filled_price=100.0,
            executed_at=t0 + timedelta(seconds=3),
        ),
        broker="paper",
        symbol="AAPL",
        side=Side.BUY,
    )

    with test_client:
        response = test_client.get("/accounts/acct1/execution-quality")

    assert response.status_code == 200
    body = response.json()
    assert body["per_symbol"]["AAPL"]["mean_seconds"] == 3.0
    assert body["per_symbol"]["AAPL"]["sample_count"] == 1
    assert body["unmatched_order_count"] == 0


def test_no_orders_returns_empty_report(client):
    test_client, _ = client
    with test_client:
        response = test_client.get("/accounts/acct1/execution-quality")
    assert response.status_code == 200
    body = response.json()
    assert body["per_symbol"] == {}
    assert body["unmatched_order_count"] == 0
