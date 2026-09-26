"""E06: GET /accounts/{account_id}/economics -- the authoritative
account-economics endpoint on top of app/economics.py."""
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
        response = test_client.get("/accounts/nope/economics")
    assert response.status_code == 404


def test_unauthenticated_request_401s(client):
    test_client, _ = client
    with test_client:
        response = TestClient(main_module.app).get("/accounts/acct1/economics")
    assert response.status_code == 401


def test_returns_realized_pnl_from_actual_fills(client):
    test_client, store = client
    signal1 = Signal(source="test", symbol="AAPL", side=Side.BUY)
    signal2 = Signal(source="test", symbol="AAPL", side=Side.SELL)
    store.save_signal(signal1)
    store.save_signal(signal2)
    store.save_order_result(
        OrderResult(account_id="acct1", status=OrderStatus.FILLED, signal_id=signal1.id, filled_quantity=10.0, filled_price=100.0),
        broker="paper",
        symbol="AAPL",
        side=Side.BUY,
    )
    store.save_order_result(
        OrderResult(account_id="acct1", status=OrderStatus.FILLED, signal_id=signal2.id, filled_quantity=10.0, filled_price=110.0),
        broker="paper",
        symbol="AAPL",
        side=Side.SELL,
    )

    with test_client:
        response = test_client.get("/accounts/acct1/economics")

    assert response.status_code == 200
    body = response.json()
    assert body["realized_pnl"] == pytest.approx(100.0)
    assert body["completed_trade_win_rate"] == 1.0
    assert body["per_symbol"]["AAPL"]["closing_fills"] == 1


def test_no_fills_returns_zero_not_an_error(client):
    test_client, _ = client
    with test_client:
        response = test_client.get("/accounts/acct1/economics")
    assert response.status_code == 200
    assert response.json()["realized_pnl"] == 0.0
