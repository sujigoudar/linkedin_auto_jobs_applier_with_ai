"""C23/E10: GET /metrics -- owner-session protected (never public, unlike
/health, since these numbers are operational detail)."""
import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app import config as app_config
from app.db import SignalStore


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(app_config, "OWNER_PASSWORD", "test-owner-password")
    monkeypatch.setattr(app_config, "SESSION_SECRET", "test-session-secret")
    store = SignalStore(tmp_path / "test.db")
    monkeypatch.setattr(main_module, "store", store)
    monkeypatch.setattr(main_module.engine, "store", store)

    test_client = TestClient(main_module.app)
    login = test_client.post("/auth/login", json={"password": "test-owner-password"})
    assert login.status_code == 200
    return test_client


def test_unauthenticated_request_401s(client):
    with TestClient(main_module.app) as anon_client:  # auth IS configured (client fixture set it), just no session
        response = anon_client.get("/metrics")
    assert response.status_code == 401


def test_authenticated_request_returns_prometheus_text(client):
    with client:
        response = client.get("/metrics")
    assert response.status_code == 200
    assert "text/plain" in response.headers["content-type"]
    assert "signal_copier_open_positions" in response.text
