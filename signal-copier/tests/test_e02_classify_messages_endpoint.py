"""E02: POST /sources/{source_name}/classify-messages -- read-only batch
message classification, never a live signal."""
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
    return test_client, store


def test_classifies_a_batch_of_messages(client):
    test_client, _ = client
    with test_client:
        response = test_client.post(
            "/sources/telegram_channel_1/classify-messages",
            json={"texts": ["BUY BTCUSDT @ 65000", "DO NOT BUY AAPL 10", "just chatting"]},
        )
    assert response.status_code == 200
    dispositions = response.json()["dispositions"]
    assert [d["outcome"] for d in dispositions] == ["parsed", "ignored", "no_match"]
    assert dispositions[0]["signal"]["symbol"] == "BTCUSDT"
    assert dispositions[1]["signal"] is None


def test_never_creates_a_signal_or_touches_positions(client):
    test_client, store = client
    with test_client:
        test_client.post(
            "/sources/telegram_channel_1/classify-messages", json={"texts": ["BUY BTCUSDT @ 65000"]}
        )
    assert store.list_recent_signals(limit=10) == []
    assert store.list_open_positions() == []


def test_unauthenticated_request_401s(client):
    test_client, _ = client
    with test_client:
        response = TestClient(main_module.app).post(
            "/sources/telegram_channel_1/classify-messages", json={"texts": ["BUY BTCUSDT"]}
        )
    assert response.status_code == 401
