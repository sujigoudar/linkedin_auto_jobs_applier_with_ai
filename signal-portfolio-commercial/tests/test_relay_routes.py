"""app/api/relay_routes.py -- real HTTP tests against app/main.py's
FastAPI app, same shape as tests/test_api.py's stripe-webhook coverage.
`get_relay_db_session` is overridden to the test's own
`relay_session_factory` session (bound to the real, restricted
`relay_role` login), so these tests exercise the actual RLS-scoped path
an in-production relay worker would hit, not just the admin connection."""
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
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

from app import config
from app.api.dependencies import get_db_session, get_relay_db_session
from app.main import create_app
from app.models.ledger import LedgerEntry
from app.models.tenancy import Tenant
from app.services.integration_inbox import register_export_stream
from app.services.relay_auth import sign_relay_payload


@pytest.fixture
def client(db_session, relay_session_factory):
    app = create_app()
    relay_session = relay_session_factory()

    def override_get_db_session():
        yield db_session

    def override_get_relay_db_session():
        yield relay_session

    app.dependency_overrides[get_db_session] = override_get_db_session
    app.dependency_overrides[get_relay_db_session] = override_get_relay_db_session
    try:
        yield TestClient(app)
    finally:
        relay_session.close()


def _execution_envelope(*, event_id, source_stream="signal-copier:acct1"):
    instrument = InstrumentIdentity(
        instrument_id="AAPL", venue="NASDAQ", market_type="equity", currency="USD",
        multiplier="1", quantity_convention="shares",
    )
    payload = ExecutionAppliedPayload(
        account=PrivateAccountIdentity(account_id="acct1"),
        instrument=instrument,
        side="buy",
        filled_quantity="10",
        filled_price="150.00",
        fee=None,
        broker="paper",
        broker_order_id="paper-1",
    )
    payload_dict = payload.model_dump(mode="json")
    now = datetime.now(timezone.utc)
    return EventEnvelope(
        event_type=EventType.EXECUTION_APPLIED,
        event_id=event_id,
        producer_id="signal-copier-instance-1",
        source_stream=source_stream,
        export_sequence=0,
        subject=build_subject(account=PrivateAccountIdentity(account_id="acct1"), instrument=instrument),
        event_time=now,
        effective_time=now,
        availability_time=now,
        receipt_time=now,
        environment=Environment.LOCAL_SIM,
        evidence_class=EvidenceClass.INTERNAL_PAPER,
        payload_hash=compute_payload_hash(payload_dict),
        payload=payload_dict,
    )


def _post_batch(client, events, *, secret=None):
    body = f'{{"events": [{", ".join(e for e in events)}]}}'.encode()
    header = sign_relay_payload(body, secret or config.RELAY_SIGNING_SECRET)
    return client.post(
        "/internal/relay/ingest-batch",
        content=body,
        headers={"x-relay-signature": header, "content-type": "application/json"},
    )


def test_ingest_batch_rejects_a_missing_signature(client):
    body = b'{"events": []}'
    response = client.post("/internal/relay/ingest-batch", content=body, headers={"content-type": "application/json"})
    assert response.status_code == 401


def test_ingest_batch_rejects_a_wrong_secret(client):
    import json

    envelope = _execution_envelope(event_id="evt-http-1")
    events = [json.dumps(envelope.model_dump_json())]
    response = _post_batch(client, events, secret="wrong-secret")
    assert response.status_code == 401


def test_ingest_batch_accepts_a_correctly_signed_execution_event(client, db_session):
    import json

    db_session.add(Tenant(tenant_id="tenant-a", display_name="A", environment="LOCAL_SIM"))
    db_session.commit()
    register_export_stream(db_session, tenant_id="tenant-a", source_stream="signal-copier:acct1", environment="LOCAL_SIM")
    db_session.commit()

    envelope = _execution_envelope(event_id="evt-http-2")
    events = [json.dumps(envelope.model_dump_json())]
    response = _post_batch(client, events)

    assert response.status_code == 200
    body = response.json()
    assert body["results"] == [{"status": "applied", "event_id": "evt-http-2"}]

    entries = db_session.scalars(select(LedgerEntry).where(LedgerEntry.tenant_id == "tenant-a")).all()
    assert len(entries) == 1


def test_ingest_batch_reports_an_unregistered_stream_without_500ing(client, db_session):
    import json

    db_session.add(Tenant(tenant_id="tenant-a", display_name="A", environment="LOCAL_SIM"))
    db_session.commit()

    envelope = _execution_envelope(event_id="evt-http-3", source_stream="signal-copier:never-registered")
    events = [json.dumps(envelope.model_dump_json())]
    response = _post_batch(client, events)

    assert response.status_code == 200
    assert response.json()["results"] == [{"status": "unregistered_stream", "detail": response.json()["results"][0]["detail"]}]


def test_ingest_batch_rejects_more_than_the_max_batch_size(client):
    import json

    envelope = _execution_envelope(event_id="evt-http-4")
    events = [json.dumps(envelope.model_dump_json())] * 101
    response = _post_batch(client, events)
    assert response.status_code == 422
