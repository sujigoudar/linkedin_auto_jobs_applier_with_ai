"""An Idempotency-Key header on POST /positions/{account}/{symbol}/close and
POST /accounts/{account}/flatten makes a retried request replay the
original result instead of re-executing the close -- defense in depth on
top of (not instead of) SignalCopierEngine's own per-(account, symbol)
lock (see tests/test_plain_close_race.py), for the case where a client
retries after not seeing the first response (e.g. a timed-out connection).
"""
import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app import config as app_config
from app.brokers.paper import PaperBroker
from app.db import SignalStore
from app.models import DestinationAccount, Side, Signal


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
    test_client.headers["X-CSRF-Token"] = login.json()["csrf_token"]
    return test_client, store


def test_repeated_idempotency_key_replays_the_original_close_result(client):
    test_client, store = client
    with test_client:
        paper: PaperBroker = main_module.brokers["paper"]
        account = main_module.routing_config.accounts["acct1"]
        import asyncio

        asyncio.run(paper.place_order(Signal(source="test", symbol="AAPL", side=Side.BUY), account, 10.0, "AAPL"))
        store.record_fill("acct1", "AAPL", Side.BUY, 10.0)

        first = test_client.post("/positions/acct1/AAPL/close", headers={"Idempotency-Key": "abc-123"})
        assert first.status_code == 200
        assert first.json()["status"] == "filled"
        assert store.get_position("acct1", "AAPL") == 0.0

        # A genuine second close attempt (no key) correctly finds nothing left.
        no_key_retry = test_client.post("/positions/acct1/AAPL/close")
        assert no_key_retry.json()["status"] == "rejected"

        # But retrying with the SAME idempotency key replays the original
        # "filled" result rather than re-resolving against the now-flat position.
        replay = test_client.post("/positions/acct1/AAPL/close", headers={"Idempotency-Key": "abc-123"})
        assert replay.status_code == 200
        assert replay.json() == first.json()


def test_different_idempotency_keys_are_independent(client):
    test_client, store = client
    with test_client:
        response = test_client.post("/positions/acct1/AAPL/close", headers={"Idempotency-Key": "key-1"})
        assert response.json()["status"] == "rejected"
        response2 = test_client.post("/positions/acct1/AAPL/close", headers={"Idempotency-Key": "key-2"})
        assert response2.json()["status"] == "rejected"
        # both cached independently, no cross-talk
        assert store.get_idempotent_response("key-1") is not None
        assert store.get_idempotent_response("key-2") is not None


def test_flatten_idempotency_key_replays_result(client):
    test_client, store = client
    with test_client:
        import asyncio

        paper: PaperBroker = main_module.brokers["paper"]
        account = main_module.routing_config.accounts["acct1"]
        asyncio.run(paper.place_order(Signal(source="test", symbol="AAPL", side=Side.BUY), account, 4.0, "AAPL"))
        store.record_fill("acct1", "AAPL", Side.BUY, 4.0)

        first = test_client.post("/accounts/acct1/flatten", headers={"Idempotency-Key": "flat-1"})
        assert first.status_code == 200
        assert len(first.json()["closed"]) == 1

        replay = test_client.post("/accounts/acct1/flatten", headers={"Idempotency-Key": "flat-1"})
        assert replay.json() == first.json()
