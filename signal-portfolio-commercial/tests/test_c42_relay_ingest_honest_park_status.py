"""Track 42: `POST /internal/relay/ingest-batch` (app/api/relay_routes.py)
now reports a real, distinct `"parked"` status -- with the specific
`parked_reason` -- for any event that was received and durably stored
but NOT applied, instead of the `"applied"` label every such event got
before this track (see docs/KNOWN_ISSUES.md's own, now-closed, entry
and tests/test_c40_relay_ingest_adversarial_payloads.py's
`test_out_of_order_sequence_in_one_batch_parks_then_resolves_without_500`,
which this track also updated).

Covers the three distinct ways an event can be reported back:
`"applied"` (a real ledger projection happened), `"parked"` with a
NAMED reason (the processing logic itself decided not to apply it --
here, a `SourceEventKind.EDIT` with no resolvable target (Track 35's
own `PARKED_REASON_EDIT_WITHOUT_RESOLVABLE_TARGET`; `TARGET_UPDATE`/
`STOP_UPDATE` are no longer park cases at all as of Track 41 -- see
their own real-representation coverage in test_integration_inbox.py),
and an `EventType` with no implemented payload at all), and `"parked"`
with the UNNAMED sequence-gap reason (covered by Track 40's own test,
not duplicated here)."""
from __future__ import annotations

import json
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
    SourceEventKind,
    SourceEventPayload,
    SourceIdentity,
    build_subject,
    compute_payload_hash,
)

from app import config
from app.api.dependencies import get_db_session, get_relay_db_session
from app.main import create_app
from app.models.integration_inbox import InboxEvent
from app.models.tenancy import Tenant
from app.services.integration_inbox import (
    PARKED_REASON_EDIT_WITHOUT_RESOLVABLE_TARGET,
    PARKED_REASON_UNIMPLEMENTED_EVENT_TYPE,
    register_export_stream,
)
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


def _instrument(instrument_id="AAPL"):
    return InstrumentIdentity(
        instrument_id=instrument_id, venue="NASDAQ", market_type="equity", currency="USD",
        multiplier="1", quantity_convention="shares",
    )


def _execution_envelope(*, event_id, source_stream="signal-copier:acct1", export_sequence=0, broker_order_id="paper-1"):
    instrument = _instrument()
    payload = ExecutionAppliedPayload(
        account=PrivateAccountIdentity(account_id="acct1"),
        instrument=instrument,
        side="buy",
        filled_quantity="10",
        filled_price="150.00",
        fee=None,
        broker="paper",
        broker_order_id=broker_order_id,
    )
    payload_dict = payload.model_dump(mode="json")
    now = datetime.now(timezone.utc)
    return EventEnvelope(
        event_type=EventType.EXECUTION_APPLIED,
        event_id=event_id,
        producer_id="signal-copier-instance-1",
        source_stream=source_stream,
        export_sequence=export_sequence,
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


def _source_event_envelope(
    *, event_id, kind, source_stream="signal-copier:acct1", export_sequence=0, source_event_id="src-evt-1",
):
    source_identity = SourceIdentity(
        source_provider_id="telegram", source_channel_id=None, analyst_id=None,
        parser_version="v3", source_event_id=source_event_id,
    )
    now = datetime.now(timezone.utc)
    payload = SourceEventPayload(
        kind=SourceEventKind(kind),
        source=source_identity,
        provider_timestamp=now,
        local_receipt_timestamp=now,
        instrument=_instrument(),
        signal=None,
    )
    payload_dict = payload.model_dump(mode="json")
    return EventEnvelope(
        event_type=EventType.SOURCE_EVENT,
        event_id=event_id,
        producer_id="signal-copier-instance-1",
        source_stream=source_stream,
        export_sequence=export_sequence,
        subject=build_subject(source=source_identity),
        event_time=now,
        effective_time=now,
        availability_time=now,
        receipt_time=now,
        environment=Environment.LOCAL_SIM,
        evidence_class=EvidenceClass.SYNTHETIC_FIXTURE,
        payload_hash=compute_payload_hash(payload_dict),
        payload=payload_dict,
    )


def _unimplemented_type_envelope(*, event_id, source_stream="signal-copier:acct1", export_sequence=0):
    """`EventType.CASH_MOVEMENT` has no `_apply_projection` branch at all
    -- the generic `PARKED_REASON_UNIMPLEMENTED_EVENT_TYPE` fallback."""
    now = datetime.now(timezone.utc)
    payload_dict = {"note": "no implemented payload model for this event_type yet"}
    return EventEnvelope(
        event_type=EventType.CASH_MOVEMENT,
        event_id=event_id,
        producer_id="signal-copier-instance-1",
        source_stream=source_stream,
        export_sequence=export_sequence,
        subject=build_subject(account=PrivateAccountIdentity(account_id="acct1")),
        event_time=now,
        effective_time=now,
        availability_time=now,
        receipt_time=now,
        environment=Environment.LOCAL_SIM,
        evidence_class=EvidenceClass.INTERNAL_PAPER,
        payload_hash=compute_payload_hash(payload_dict),
        payload=payload_dict,
    )


def _signed_post(client, body: bytes):
    header = sign_relay_payload(body, config.RELAY_SIGNING_SECRET)
    return client.post(
        "/internal/relay/ingest-batch",
        content=body,
        headers={"x-relay-signature": header, "content-type": "application/json"},
    )


def _seed_tenant_and_stream(db_session, *, source_stream="signal-copier:acct1"):
    if db_session.get(Tenant, "tenant-a") is None:
        db_session.add(Tenant(tenant_id="tenant-a", display_name="A", environment="LOCAL_SIM"))
        db_session.commit()
    register_export_stream(db_session, tenant_id="tenant-a", source_stream=source_stream, environment="LOCAL_SIM")
    db_session.commit()


def test_genuinely_applied_event_still_reports_applied(client, db_session):
    _seed_tenant_and_stream(db_session)
    envelope = _execution_envelope(event_id="evt-c42-applied-1")
    response = _signed_post(client, json.dumps({"events": [envelope.model_dump_json()]}).encode())
    assert response.status_code == 200
    assert response.json()["results"] == [{"status": "applied", "event_id": "evt-c42-applied-1"}]

    inbox_event = db_session.get(InboxEvent, "evt-c42-applied-1")
    assert inbox_event.applied_at is not None


def test_unresolvable_edit_source_event_reports_parked_with_real_reason(client, db_session):
    """A `SourceEventKind.EDIT` whose `source.original_source_event_id`
    is unset -- this build can't even attempt to correlate it to the
    receipt it's meant to revise, so it genuinely never applies (Track
    35/41's own `PARKED_REASON_EDIT_WITHOUT_RESOLVABLE_TARGET`). Before
    Track 42, this reported `"applied"` even though `applied_at` stayed
    `None` -- a fabricated status for an honestly-parked event."""
    _seed_tenant_and_stream(db_session)
    envelope = _source_event_envelope(event_id="evt-c42-edit-no-target-1", kind="edit")
    response = _signed_post(client, json.dumps({"events": [envelope.model_dump_json()]}).encode())
    assert response.status_code == 200
    assert response.json()["results"] == [
        {
            "status": "parked",
            "event_id": "evt-c42-edit-no-target-1",
            "parked_reason": f"{PARKED_REASON_EDIT_WITHOUT_RESOLVABLE_TARGET}:missing_original_source_event_id",
        }
    ]

    inbox_event = db_session.get(InboxEvent, "evt-c42-edit-no-target-1")
    assert inbox_event is not None
    assert inbox_event.applied_at is None


def test_unimplemented_event_type_reports_parked_with_real_reason(client, db_session):
    """G-C-26 (WP-38): unknown non-economic event types are marked as applied
    (with applied_at set) to advance the cursor without permanently stalling
    the stream. The parked_reason is still recorded for tracking and future
    support."""
    _seed_tenant_and_stream(db_session)
    envelope = _unimplemented_type_envelope(event_id="evt-c42-unimplemented-1")
    response = _signed_post(client, json.dumps({"events": [envelope.model_dump_json()]}).encode())
    assert response.status_code == 200
    assert response.json()["results"] == [
        {
            "status": "applied",
            "event_id": "evt-c42-unimplemented-1",
        }
    ]

    inbox_event = db_session.get(InboxEvent, "evt-c42-unimplemented-1")
    assert inbox_event is not None
    assert inbox_event.applied_at is not None
    assert inbox_event.parked_reason == f"{PARKED_REASON_UNIMPLEMENTED_EVENT_TYPE}:cash_movement"
