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
