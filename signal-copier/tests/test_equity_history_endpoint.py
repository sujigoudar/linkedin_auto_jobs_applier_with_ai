"""PU-A3: GET /accounts/{account_id}/equity-history -- the queryable
persisted equity/P&L snapshot series endpoint on top of
app/equity_history.py."""
from datetime import datetime, timedelta, timezone

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
        response = test_client.get("/accounts/nope/equity-history")
    assert response.status_code == 404


def test_unauthenticated_request_401s(client):
    test_client, _ = client
    with test_client:
        response = TestClient(main_module.app).get("/accounts/acct1/equity-history")
    assert response.status_code == 401


def test_no_snapshots_yet_returns_an_empty_series_not_an_error(client):
    test_client, _ = client
    with test_client:
        response = test_client.get("/accounts/acct1/equity-history")
    assert response.status_code == 200
    body = response.json()
    assert body["account_id"] == "acct1"
    assert body["snapshots"] == []


def test_returns_the_real_persisted_series_oldest_first(client):
    test_client, store = client
    t0 = datetime.now(timezone.utc)
    store.record_equity_snapshot(
        "acct1", captured_at=t0, realized_pnl=10.0, unrealized_pnl=5.0, cumulative_pnl=15.0
    )
    store.record_equity_snapshot(
        "acct1",
        captured_at=t0 + timedelta(minutes=5),
        realized_pnl=20.0,
        unrealized_pnl=-2.0,
        cumulative_pnl=18.0,
        unpriced_open_symbols=["MSFT"],
    )

    with test_client:
        response = test_client.get("/accounts/acct1/equity-history")

    assert response.status_code == 200
    snapshots = response.json()["snapshots"]
    assert len(snapshots) == 2
    assert snapshots[0]["realized_pnl"] == 10.0
    assert snapshots[1]["cumulative_pnl"] == 18.0
    assert snapshots[1]["unpriced_open_symbols"] == ["MSFT"]


def test_since_and_until_query_params_bound_the_series(client):
    test_client, store = client
    t0 = datetime.now(timezone.utc)
    for i, pnl in enumerate([1.0, 2.0, 3.0]):
        store.record_equity_snapshot(
            "acct1",
            captured_at=t0 + timedelta(hours=i),
            realized_pnl=pnl,
            unrealized_pnl=0.0,
            cumulative_pnl=pnl,
        )

    since = (t0 + timedelta(minutes=30)).isoformat()
    until = (t0 + timedelta(hours=1, minutes=30)).isoformat()
    with test_client:
        response = test_client.get(
            "/accounts/acct1/equity-history", params={"since": since, "until": until}
        )

    assert response.status_code == 200
    snapshots = response.json()["snapshots"]
    assert [s["realized_pnl"] for s in snapshots] == [2.0]


def test_never_calls_itself_equity_in_the_response_note(client):
    """Honest-labeling regression guard: this account has no configured
    starting-balance baseline, so the endpoint's own note must say this is
    a cumulative P&L series, not claim an equity figure."""
    test_client, _ = client
    with test_client:
        response = test_client.get("/accounts/acct1/equity-history")
    note = response.json()["note"].lower()
    assert "cumulative" in note
    assert "not a broker-confirmed" in note or "not a broker-confirmed absolute equity" in note
