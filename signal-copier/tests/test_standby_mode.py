"""STANDBY_MODE is the independent release guard a promotion runbook depends
on (see deploy/RUNBOOK.md): a standby host must refuse every financial
command even if its own route-level auth were somehow misconfigured, and
must never start signal ingestion or the background reconciliation/price
polling loops that would let it act on broker state."""
import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app import config as app_config
from app.db import SignalStore


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(app_config, "OWNER_PASSWORD", "")
    monkeypatch.setattr(app_config, "SESSION_SECRET", "")
    store = SignalStore(tmp_path / "test.db")
    monkeypatch.setattr(main_module, "store", store)
    return store


def test_reads_still_work_in_standby_mode(store, monkeypatch):
    monkeypatch.setattr(app_config, "STANDBY_MODE", True)
    client = TestClient(main_module.app)
    with client:
        response = client.get("/health")
    assert response.status_code == 200


def test_writes_are_refused_in_standby_mode_regardless_of_route(store, monkeypatch):
    monkeypatch.setattr(app_config, "STANDBY_MODE", True)
    client = TestClient(main_module.app)
    with client:
        response = client.post("/webhook/tradingview", json={"symbol": "AAPL", "side": "buy"})
    assert response.status_code == 503
    assert "read-only" in response.json()["detail"]


def test_writes_work_normally_when_not_in_standby_mode(store, monkeypatch):
    monkeypatch.setattr(app_config, "STANDBY_MODE", False)
    client = TestClient(main_module.app)
    with client:
        response = client.post("/webhook/tradingview", json={"symbol": "AAPL", "side": "buy"})
    # No shared secret configured in this fixture -- the route itself fails
    # closed (503), but crucially NOT via the standby gate: this proves the
    # gate is off, not that the request otherwise succeeded.
    assert response.status_code == 503
    assert "read-only" not in response.json()["detail"]


def test_owner_can_still_log_in_on_a_standby(store, monkeypatch):
    """DEP-08: a blanket non-GET refusal also blocked /auth/login itself,
    leaving no way for the owner to even inspect a standby's read-only
    data. Login/logout are session-only -- never a financial effect --
    so they're the one narrow exception to the read-only gate."""
    monkeypatch.setattr(app_config, "STANDBY_MODE", True)
    monkeypatch.setattr(app_config, "OWNER_PASSWORD", "test-owner-password")
    monkeypatch.setattr(app_config, "SESSION_SECRET", "test-session-secret")
    client = TestClient(main_module.app)
    with client:
        response = client.post("/auth/login", json={"password": "test-owner-password"})
    assert response.status_code == 200
    # A real financial command must still be refused even with a fresh session.
    with client:
        client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
        blocked = client.post("/webhook/tradingview", json={"symbol": "AAPL", "side": "buy"})
    assert blocked.status_code == 503


def test_security_headers_present_on_every_response(store, monkeypatch):
    monkeypatch.setattr(app_config, "STANDBY_MODE", False)
    client = TestClient(main_module.app)
    with client:
        response = client.get("/health")
    assert "default-src 'self'" in response.headers["content-security-policy"]
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"


@pytest.mark.asyncio
async def test_lifespan_skips_background_loops_in_standby_mode(store, monkeypatch):
    monkeypatch.setattr(app_config, "STANDBY_MODE", True)
    started = {"webhook": False, "reconciler": False, "price_monitor": False}

    async def fake_start_webhook():
        started["webhook"] = True

    async def fake_start_reconciler():
        started["reconciler"] = True

    async def fake_start_price_monitor():
        started["price_monitor"] = True

    monkeypatch.setattr(main_module.webhook_source, "start", fake_start_webhook)
    monkeypatch.setattr(main_module.reconciler, "start", fake_start_reconciler)
    monkeypatch.setattr(main_module.price_monitor, "start", fake_start_price_monitor)

    async with main_module.lifespan(main_module.app):
        pass

    assert started == {"webhook": False, "reconciler": False, "price_monitor": False}
