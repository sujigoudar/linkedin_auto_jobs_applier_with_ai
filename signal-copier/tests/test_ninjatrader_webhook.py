"""POST /ninjatrader/webhook (app/main.py) -- the shared-secret-gated
ingress for ninjascript/SignalCopierAutoJournal.cs's fill reports."""
import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app import config as app_config
from app.db import SignalStore

SECRET = "test-ninjatrader-secret"


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(app_config, "OWNER_PASSWORD", "")
    monkeypatch.setattr(app_config, "SESSION_SECRET", "")
    store = SignalStore(tmp_path / "test.db")
    monkeypatch.setattr(main_module, "store", store)
    # app/main.py's module-level `engine` was already constructed against
    # the ORIGINAL store object at import time -- replacing the module's
    # own `store` name above doesn't change what `engine.store` points
    # to, so a test that checks persisted state via `main_module.store`
    # (list_recent_signals below) needs this too, or it'll see an empty
    # store while the signal was actually recorded in the old one.
    monkeypatch.setattr(main_module.engine, "store", store)
    return TestClient(main_module.app)


def _payload(**overrides):
    payload = {
        "ticker": "ES 12-24",
        "action": "entry",
        "direction": "Long",
        "price": 4500.25,
        "qty": 2,
        "asset_class": "Futures",
        "order_id": "abc123",
        "time": "2026-01-15T12:00:00.000Z",
    }
    payload.update(overrides)
    return payload


def test_fails_closed_when_unconfigured(client):
    with client:
        response = client.post(
            "/ninjatrader/webhook", json=_payload(), headers={"X-NinjaTrader-Secret": "whatever"}
        )
    assert response.status_code == 503
    assert "NINJATRADER_WEBHOOK_SECRET" in response.json()["detail"]


def test_rejects_missing_secret(client, monkeypatch):
    monkeypatch.setattr(app_config, "NINJATRADER_WEBHOOK_SECRET", SECRET)
    with client:
        response = client.post("/ninjatrader/webhook", json=_payload())
    assert response.status_code == 401


def test_rejects_wrong_secret(client, monkeypatch):
    monkeypatch.setattr(app_config, "NINJATRADER_WEBHOOK_SECRET", SECRET)
    with client:
        response = client.post(
            "/ninjatrader/webhook", json=_payload(), headers={"X-NinjaTrader-Secret": "wrong"}
        )
    assert response.status_code == 401


def test_accepts_a_correctly_authenticated_entry_fill(client, monkeypatch):
    monkeypatch.setattr(app_config, "NINJATRADER_WEBHOOK_SECRET", SECRET)
    with client:
        response = client.post(
            "/ninjatrader/webhook", json=_payload(), headers={"X-NinjaTrader-Secret": SECRET}
        )
    assert response.status_code == 200
    body = response.json()
    assert "orders" in body


def test_malformed_payload_is_rejected_with_400(client, monkeypatch):
    monkeypatch.setattr(app_config, "NINJATRADER_WEBHOOK_SECRET", SECRET)
    with client:
        response = client.post(
            "/ninjatrader/webhook",
            json=_payload(action="modify"),
            headers={"X-NinjaTrader-Secret": SECRET},
        )
    assert response.status_code == 400


def test_non_json_body_is_rejected_with_400(client, monkeypatch):
    monkeypatch.setattr(app_config, "NINJATRADER_WEBHOOK_SECRET", SECRET)
    with client:
        response = client.post(
            "/ninjatrader/webhook",
            content=b"not json",
            headers={"X-NinjaTrader-Secret": SECRET, "Content-Type": "application/json"},
        )
    assert response.status_code == 400


def test_a_redelivered_order_id_and_action_pair_is_not_processed_twice(client, monkeypatch):
    monkeypatch.setattr(app_config, "NINJATRADER_WEBHOOK_SECRET", SECRET)
    payload = _payload(order_id="dup-1")
    with client:
        first = client.post("/ninjatrader/webhook", json=payload, headers={"X-NinjaTrader-Secret": SECRET})
        second = client.post("/ninjatrader/webhook", json=payload, headers={"X-NinjaTrader-Secret": SECRET})
    assert first.status_code == 200 and second.status_code == 200
    assert first.json() == second.json()
    # The underlying signal must only have been processed once -- confirmed
    # by there being exactly one recorded signal for this order_id/action.
    assert len(main_module.store.list_recent_signals(limit=50)) == 1


def test_a_reversal_entry_and_exit_share_order_id_but_are_not_deduped_against_each_other(client, monkeypatch):
    """A reversal reports an 'exit' and an 'entry' under the SAME
    order_id -- the idempotency key must include `action` or these two
    genuinely different legs would collide."""
    monkeypatch.setattr(app_config, "NINJATRADER_WEBHOOK_SECRET", SECRET)
    exit_payload = _payload(order_id="rev-1", action="exit", direction="Long")
    entry_payload = _payload(order_id="rev-1", action="entry", direction="Short")
    with client:
        exit_response = client.post(
            "/ninjatrader/webhook", json=exit_payload, headers={"X-NinjaTrader-Secret": SECRET}
        )
        entry_response = client.post(
            "/ninjatrader/webhook", json=entry_payload, headers={"X-NinjaTrader-Secret": SECRET}
        )
    assert exit_response.status_code == 200
    assert entry_response.status_code == 200
    assert len(main_module.store.list_recent_signals(limit=50)) == 2
