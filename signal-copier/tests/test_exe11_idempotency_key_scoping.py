"""EXE-11: the idempotency response cache for POST /positions/.../close and
POST /accounts/.../flatten was keyed ONLY by the caller-supplied key, with
no record of what action/target it was originally used for. Reusing the
same key for a genuinely different action (a close, then a flatten; or a
close of a different account/symbol) replayed the FIRST action's cached
response as if it belonged to the second request, instead of refusing the
reuse.

Reproduces the audit's exact case
(test_http_audit::test_idempotency_key_cannot_replay_other_action).
"""
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
    main_module.routing_config.accounts.clear()
    main_module.routing_config.rules.clear()

    test_client = TestClient(main_module.app)
    login = test_client.post("/auth/login", json={"password": "test-owner-password"})
    assert login.status_code == 200
    test_client.headers["X-CSRF-Token"] = login.json()["csrf_token"]

    test_client.post("/accounts", json={"account_id": "acct", "broker": "paper"})
    return test_client


def test_audits_exact_case_reusing_key_across_close_and_flatten_conflicts(client):
    with client:
        client.post("/positions/acct/AAPL/close", headers={"Idempotency-Key": "same"})
        response = client.post("/accounts/acct/flatten", headers={"Idempotency-Key": "same"})
    assert response.status_code == 409


def test_reusing_key_for_a_different_symbols_close_also_conflicts(client):
    with client:
        client.post("/positions/acct/AAPL/close", headers={"Idempotency-Key": "same"})
        response = client.post("/positions/acct/MSFT/close", headers={"Idempotency-Key": "same"})
    assert response.status_code == 409


def test_reusing_key_for_the_same_close_still_replays(client):
    with client:
        first = client.post("/positions/acct/AAPL/close", headers={"Idempotency-Key": "same"})
        second = client.post("/positions/acct/AAPL/close", headers={"Idempotency-Key": "same"})
    assert second.status_code == 200
    assert second.json() == first.json()


def test_reusing_key_for_the_same_flatten_still_replays(client):
    with client:
        first = client.post("/accounts/acct/flatten", headers={"Idempotency-Key": "same"})
        second = client.post("/accounts/acct/flatten", headers={"Idempotency-Key": "same"})
    assert second.status_code == 200
    assert second.json() == first.json()


def test_different_keys_for_different_actions_both_process_normally(client):
    with client:
        first = client.post("/positions/acct/AAPL/close", headers={"Idempotency-Key": "key-a"})
        second = client.post("/accounts/acct/flatten", headers={"Idempotency-Key": "key-b"})
    assert first.status_code == 200
    assert second.status_code == 200
