"""PU-A4: GET /positions/{account_id}/{symbol}/stop-events -- the query
surface on top of SignalStore.stop_target_events for a later Phase B10
charting pass."""
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app import config as app_config
from app.db import SignalStore
from app.models import DestinationAccount


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
        response = test_client.get("/positions/nope/AAPL/stop-events")
    assert response.status_code == 404


def test_unauthenticated_request_401s(client):
    test_client, _ = client
    with test_client:
        response = TestClient(main_module.app).get("/positions/acct1/AAPL/stop-events")
    assert response.status_code == 401


def test_no_events_yet_returns_an_empty_list_not_an_error(client):
    test_client, _ = client
    with test_client:
        response = test_client.get("/positions/acct1/AAPL/stop-events")
    assert response.status_code == 200
    body = response.json()
    assert body["account_id"] == "acct1"
    assert body["symbol"] == "AAPL"
    assert body["events"] == []


def test_returns_the_real_persisted_events_oldest_first(client):
    test_client, store = client
    t0 = datetime.now(timezone.utc)
    store.record_stop_target_event(
        "acct1", "AAPL", event_type="stop_placed", at=t0, price=48.50, previous_price=None, source="signal"
    )
    store.record_stop_target_event(
        "acct1", "AAPL", event_type="stop_tightened", at=t0, price=49.00, previous_price=48.50, source="signal"
    )
    # A different symbol's event must not leak into this position's list.
    store.record_stop_target_event(
        "acct1", "MSFT", event_type="stop_placed", at=t0, price=300.0, previous_price=None, source="signal"
    )

    with test_client:
        response = test_client.get("/positions/acct1/AAPL/stop-events")

    assert response.status_code == 200
    events = response.json()["events"]
    assert len(events) == 2
    assert events[0]["event_type"] == "stop_placed"
    assert events[0]["previous_price"] is None
    assert events[1]["event_type"] == "stop_tightened"
    assert events[1]["previous_price"] == 48.50
    assert events[1]["price"] == 49.00
