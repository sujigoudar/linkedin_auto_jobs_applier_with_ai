"""Track 33: GET /export-events, GET /export-events/{event_id} -- the
first live HTTP read surface over the `export_events` outbox (app/db.py's
`list_export_events`/`get_export_event`). Before this, inspecting the
outbox's contents required a direct DB connection; see app/main.py's
`list_export_events` route docstring."""
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from signal_platform_contracts import (
    Environment,
    EventEnvelope,
    EventType,
    EvidenceClass,
    ExecutionAppliedPayload,
    InstrumentIdentity,
    PrivateAccountIdentity,
    build_subject,
    compute_payload_hash,
)

import app.main as main_module
from app import config as app_config
from app.db import SignalStore


def _instrument():
    return InstrumentIdentity(
        instrument_id="AAPL", venue="NASDAQ", market_type="equity", currency="USD",
        multiplier="1", quantity_convention="shares",
    )


def _envelope(*, event_id, export_sequence, source_stream="signal-copier:acct1"):
    payload = ExecutionAppliedPayload(
        account=PrivateAccountIdentity(account_id="acct1"),
        instrument=_instrument(),
        side="buy",
        filled_quantity="10",
        filled_price="150.00",
        broker="paper",
        broker_order_id="paper-1",
    )
    payload_dict = payload.model_dump(mode="json")
    now = datetime.now(timezone.utc)
    return EventEnvelope(
        event_type=EventType.EXECUTION_APPLIED,
        event_id=event_id,
        producer_id="signal-copier-test",
        source_stream=source_stream,
        export_sequence=export_sequence,
        subject=build_subject(account=PrivateAccountIdentity(account_id="acct1"), instrument=_instrument()),
        event_time=now,
        effective_time=now,
        availability_time=now,
        receipt_time=now,
        environment=Environment.LOCAL_SIM,
        evidence_class=EvidenceClass.INTERNAL_PAPER,
        payload_hash=compute_payload_hash(payload_dict),
        payload=payload_dict,
    )


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(app_config, "OWNER_PASSWORD", "test-owner-password")
    monkeypatch.setattr(app_config, "SESSION_SECRET", "test-session-secret")

    store = SignalStore(tmp_path / "test.db")
    monkeypatch.setattr(main_module, "store", store)

    test_client = TestClient(main_module.app)
    login = test_client.post("/auth/login", json={"password": "test-owner-password"})
    assert login.status_code == 200
    return test_client, store


def test_unauthenticated_request_401s(client):
    test_client, _ = client
    with test_client:
        response = TestClient(main_module.app).get("/export-events")
    assert response.status_code == 401


def test_no_events_yet_returns_an_empty_list_not_an_error(client):
    test_client, _ = client
    with test_client:
        response = test_client.get("/export-events")
    assert response.status_code == 200
    assert response.json() == {"export_events": []}


def test_returns_real_rows_after_a_real_fill(client):
    test_client, store = client
    store.append_export_event(_envelope(event_id="evt-1", export_sequence=0))
    store.append_export_event(_envelope(event_id="evt-2", export_sequence=1))

    with test_client:
        response = test_client.get("/export-events")
    assert response.status_code == 200
    body = response.json()
    assert [e["event_id"] for e in body["export_events"]] == ["evt-2", "evt-1"]  # newest first
    assert body["export_events"][0]["payload"]["broker_order_id"] == "paper-1"
    assert body["export_events"][0]["delivered_at"] is None


def test_pagination_limit_is_respected(client):
    test_client, store = client
    for i in range(5):
        store.append_export_event(_envelope(event_id=f"evt-{i}", export_sequence=i))

    with test_client:
        response = test_client.get("/export-events", params={"limit": 2})
    assert response.status_code == 200
    assert len(response.json()["export_events"]) == 2


def test_filters_by_source_stream_and_delivered(client):
    test_client, store = client
    store.append_export_event(_envelope(event_id="evt-acct1", export_sequence=0, source_stream="signal-copier:acct1"))
    store.append_export_event(_envelope(event_id="evt-acct2", export_sequence=0, source_stream="signal-copier:acct2"))
    store.mark_export_events_delivered(["evt-acct1"])

    with test_client:
        by_stream = test_client.get("/export-events", params={"source_stream": "signal-copier:acct2"})
        delivered_only = test_client.get("/export-events", params={"delivered": True})
        undelivered_only = test_client.get("/export-events", params={"delivered": False})

    assert [e["event_id"] for e in by_stream.json()["export_events"]] == ["evt-acct2"]
    assert [e["event_id"] for e in delivered_only.json()["export_events"]] == ["evt-acct1"]
    assert [e["event_id"] for e in undelivered_only.json()["export_events"]] == ["evt-acct2"]


def test_get_by_id_returns_404_for_unknown_event(client):
    test_client, _ = client
    with test_client:
        response = test_client.get("/export-events/does-not-exist")
    assert response.status_code == 404


def test_get_by_id_returns_the_real_event(client):
    test_client, store = client
    store.append_export_event(_envelope(event_id="evt-1", export_sequence=0))

    with test_client:
        response = test_client.get("/export-events/evt-1")
    assert response.status_code == 200
    body = response.json()
    assert body["event_id"] == "evt-1"
    assert body["payload"]["broker_order_id"] == "paper-1"


def test_get_by_id_unauthenticated_request_401s(client):
    test_client, store = client
    store.append_export_event(_envelope(event_id="evt-1", export_sequence=0))
    with test_client:
        response = TestClient(main_module.app).get("/export-events/evt-1")
    assert response.status_code == 401
