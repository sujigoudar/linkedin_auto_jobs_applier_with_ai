"""SEC-02/03/04/05: malformed login bodies must never 500, repeated wrong
passwords from one client must be throttled, and rotating either owner
secret must revoke every session already issued."""
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
    main_module._login_failures.clear()
    return TestClient(main_module.app)


@pytest.mark.parametrize(
    "body,content_type",
    [
        ("null", "application/json"),
        ("42", "application/json"),
        ('{"password": 12345}', "application/json"),
        ('{"password": ["not", "a", "string"]}', "application/json"),
        ("not even json", "application/json"),
    ],
)
def test_malformed_login_bodies_never_500(client, body, content_type):
    with client:
        response = client.post("/auth/login", content=body, headers={"content-type": content_type})
    assert response.status_code in (400, 401, 422)


def test_non_ascii_password_is_handled_safely(client):
    with client:
        response = client.post("/auth/login", json={"password": "pässwörd-💀"})
    assert response.status_code == 401  # rejected on its merits, not a 500


def test_login_is_throttled_after_repeated_failures(client):
    with client:
        for _ in range(5):
            response = client.post("/auth/login", json={"password": "wrong"})
            assert response.status_code == 401
        throttled = client.post("/auth/login", json={"password": "wrong"})
    assert throttled.status_code == 429

    # The real owner is not permanently locked out -- a correct password
    # is still rejected only by the throttle, not forever.
    with client:
        still_throttled = client.post("/auth/login", json={"password": "test-owner-password"})
    assert still_throttled.status_code == 429


def test_successful_login_clears_the_failure_count(client):
    with client:
        for _ in range(4):
            client.post("/auth/login", json={"password": "wrong"})
        ok = client.post("/auth/login", json={"password": "test-owner-password"})
        assert ok.status_code == 200
        # 4 more failures right after a success should NOT immediately trip
        # the 5-failure threshold, since the successful login reset it.
        for _ in range(4):
            response = client.post("/auth/login", json={"password": "wrong"})
            assert response.status_code == 401


def test_rotating_owner_password_revokes_existing_sessions(client, monkeypatch):
    with client:
        login = client.post("/auth/login", json={"password": "test-owner-password"})
    assert login.status_code == 200

    monkeypatch.setattr(app_config, "OWNER_PASSWORD", "a-brand-new-password")

    with client:
        response = client.get("/positions")
    assert response.status_code == 401


def test_rotating_session_secret_revokes_existing_sessions(client, monkeypatch):
    with client:
        login = client.post("/auth/login", json={"password": "test-owner-password"})
    assert login.status_code == 200

    monkeypatch.setattr(app_config, "SESSION_SECRET", "a-brand-new-session-secret")

    with client:
        response = client.get("/positions")
    assert response.status_code == 401


def test_unchanged_credentials_keep_the_session_valid(client):
    with client:
        login = client.post("/auth/login", json={"password": "test-owner-password"})
        response = client.get("/positions")
    assert login.status_code == 200
    assert response.status_code == 200
