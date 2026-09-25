"""GET /health used to always return {"status": "ok"} regardless of whether
the background PriceMonitor/OrderReconciler loops were actually making
progress -- a fully stuck worker (dead task, every broker call failing)
would report healthy forever. It now reports each worker's own
last-successful-cycle freshness."""
import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app import config as app_config
from app.db import SignalStore


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(app_config, "OWNER_PASSWORD", "")
    monkeypatch.setattr(app_config, "SESSION_SECRET", "")
    store = SignalStore(tmp_path / "test.db")
    monkeypatch.setattr(main_module, "store", store)
    return TestClient(main_module.app)


def test_health_reports_not_ok_before_any_successful_cycle(client, monkeypatch):
    monkeypatch.setattr(main_module.price_monitor, "last_success_at", None)
    monkeypatch.setattr(main_module.reconciler, "last_success_at", None)
    with client:
        response = client.get("/health")
    body = response.json()
    assert body["status"] == "ok"  # process liveness is still fine
    assert body["price_monitor_ok"] is False
    assert body["reconciler_ok"] is False


def test_health_reports_ok_after_a_recent_successful_cycle(client, monkeypatch):
    from datetime import datetime, timezone

    monkeypatch.setattr(main_module.price_monitor, "last_success_at", datetime.now(timezone.utc))
    monkeypatch.setattr(main_module.reconciler, "last_success_at", datetime.now(timezone.utc))
    with client:
        response = client.get("/health")
    body = response.json()
    assert body["price_monitor_ok"] is True
    assert body["reconciler_ok"] is True


def test_health_reports_not_ok_after_a_stale_cycle(client, monkeypatch):
    from datetime import datetime, timedelta, timezone

    stale = datetime.now(timezone.utc) - timedelta(hours=1)
    monkeypatch.setattr(main_module.price_monitor, "last_success_at", stale)
    with client:
        response = client.get("/health")
    assert response.json()["price_monitor_ok"] is False


def test_health_requires_no_authentication(client):
    with client:
        response = client.get("/health")
    assert response.status_code == 200
