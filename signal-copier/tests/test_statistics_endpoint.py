"""Phase A5: GET /accounts/{account_id}/statistics and
GET /accounts/correlation -- the queryable endpoints on top of
app/statistics.py's rolling stats / pairwise correlation."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app import config as app_config
from app.db import SignalStore
from app.models import DestinationAccount
from app.statistics import MIN_CORRELATION_SAMPLES


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(app_config, "OWNER_PASSWORD", "test-owner-password")
    monkeypatch.setattr(app_config, "SESSION_SECRET", "test-session-secret")

    store = SignalStore(tmp_path / "test.db")
    monkeypatch.setattr(main_module, "store", store)
    monkeypatch.setattr(main_module.engine, "store", store)
    main_module.routing_config.accounts.clear()
    main_module.routing_config.accounts["acct1"] = DestinationAccount(account_id="acct1", broker="paper")
    main_module.routing_config.accounts["acct2"] = DestinationAccount(account_id="acct2", broker="paper")

    test_client = TestClient(main_module.app)
    login = test_client.post("/auth/login", json={"password": "test-owner-password"})
    assert login.status_code == 200
    return test_client, store


def _seed(store, account_id, values, *, start=None, step=timedelta(hours=1)):
    start = start or datetime(2024, 1, 1, tzinfo=timezone.utc)
    for i, v in enumerate(values):
        store.record_equity_snapshot(
            account_id, captured_at=start + i * step, realized_pnl=v, unrealized_pnl=0.0, cumulative_pnl=v
        )


def test_statistics_unknown_account_404s(client):
    test_client, _ = client
    with test_client:
        response = test_client.get("/accounts/nope/statistics")
    assert response.status_code == 404


def test_statistics_unauthenticated_401s(client):
    test_client, _ = client
    with test_client:
        response = TestClient(main_module.app).get("/accounts/acct1/statistics")
    assert response.status_code == 401


def test_statistics_no_snapshots_yet_is_all_null_not_an_error(client):
    test_client, _ = client
    with test_client:
        response = test_client.get("/accounts/acct1/statistics")
    assert response.status_code == 200
    body = response.json()
    assert body["account_id"] == "acct1"
    assert body["sample_count"] == 0
    assert body["mean_pnl_delta"] is None
    assert body["max_drawdown"] is None


def test_statistics_returns_real_computed_values(client):
    test_client, store = client
    _seed(store, "acct1", [100.0, 90.0, 85.0, 95.0, 80.0, 100.0])

    with test_client:
        response = test_client.get("/accounts/acct1/statistics", params={"window": 10})

    assert response.status_code == 200
    body = response.json()
    assert body["sample_count"] == 6
    assert body["mean_pnl_delta"] == pytest.approx(0.0)
    assert body["max_drawdown"] == pytest.approx(20.0)
    assert body["max_drawdown_duration_seconds"] == pytest.approx(4 * 3600.0)


def test_correlation_unknown_account_404s(client):
    test_client, _ = client
    with test_client:
        response = test_client.get(
            "/accounts/correlation", params={"account_a": "acct1", "account_b": "nope"}
        )
    assert response.status_code == 404


def test_correlation_below_minimum_samples_is_null(client):
    test_client, store = client
    _seed(store, "acct1", [1.0, 2.0, 3.0])
    _seed(store, "acct2", [2.0, 4.0, 6.0])

    with test_client:
        response = test_client.get(
            "/accounts/correlation", params={"account_a": "acct1", "account_b": "acct2"}
        )

    assert response.status_code == 200
    body = response.json()
    assert body["correlation"] is None
    assert body["sample_count"] == 3


def test_correlation_returns_real_computed_value(client):
    test_client, store = client
    values_a = [float(i) for i in range(MIN_CORRELATION_SAMPLES)]
    values_b = [2.0 * v for v in values_a]
    _seed(store, "acct1", values_a)
    _seed(store, "acct2", values_b)

    with test_client:
        response = test_client.get(
            "/accounts/correlation", params={"account_a": "acct1", "account_b": "acct2"}
        )

    assert response.status_code == 200
    body = response.json()
    assert body["sample_count"] == MIN_CORRELATION_SAMPLES
    assert body["correlation"] == pytest.approx(1.0)
