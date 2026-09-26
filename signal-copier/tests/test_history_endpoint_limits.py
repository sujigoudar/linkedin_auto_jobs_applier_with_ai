"""DB-02: limit=-1 used to return every matching row (SQLite treats a
negative LIMIT as "no limit"), and the Query() bound only capped the upper
end (le=500/1000) without a lower bound -- a negative limit passed
validation and reached the SQL query unbounded."""
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
    test_client = TestClient(main_module.app)
    login = test_client.post("/auth/login", json={"password": "test-owner-password"})
    assert login.status_code == 200
    return test_client


@pytest.mark.parametrize("path", ["/signals?limit=-1", "/orders?limit=-1"])
def test_negative_limit_is_rejected(client, path):
    with client:
        response = client.get(path)
    assert response.status_code == 422


def test_zero_limit_is_rejected(client):
    with client:
        response = client.get("/signals?limit=0")
    assert response.status_code == 422
