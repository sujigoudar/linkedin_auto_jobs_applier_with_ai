"""Track 40 (fault injection extension): genuinely adversarial payloads
against `POST /internal/relay/ingest-batch` (app/api/relay_routes.py) --
the real signal-copier-relay-facing ingress tests/test_relay_routes.py
already covers the well-formed-but-wrong-business-state cases (missing
signature, wrong secret, unregistered stream, oversized batch, reused
sequence slot). This file adds the adversarial-input angle: wrong
content-type, truncated JSON, deeply nested/oversized payloads, unicode/
encoding edge cases, duplicate idempotency keys with different bodies,
out-of-order sequences, and injection-style strings in text fields.

Every case asserts the same thing (this track's own stated contract):
no 500, no unhandled exception, no partial/corrupt ledger state --
always either a clean 4xx rejection or correct processing."""
from __future__ import annotations

import json
import sys
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
from app.models.integration_inbox import InboxEvent
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


def _instrument(instrument_id="AAPL"):
    return InstrumentIdentity(
        instrument_id=instrument_id, venue="NASDAQ", market_type="equity", currency="USD",
        multiplier="1", quantity_convention="shares",
    )


def _execution_envelope(
    *, event_id, source_stream="signal-copier:acct1", export_sequence=0, broker_order_id="paper-1",
    instrument_id="AAPL", filled_quantity="10",
):
    instrument = _instrument(instrument_id)
    payload = ExecutionAppliedPayload(
        account=PrivateAccountIdentity(account_id="acct1"),
        instrument=instrument,
        side="buy",
        filled_quantity=filled_quantity,
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


def _signed_post(client, body: bytes, *, secret=None, content_type="application/json"):
    header = sign_relay_payload(body, secret or config.RELAY_SIGNING_SECRET)
    return client.post(
        "/internal/relay/ingest-batch",
        content=body,
        headers={"x-relay-signature": header, "content-type": content_type},
    )


def _seed_tenant_and_stream(db_session, *, source_stream="signal-copier:acct1"):
    if db_session.get(Tenant, "tenant-a") is None:
        db_session.add(Tenant(tenant_id="tenant-a", display_name="A", environment="LOCAL_SIM"))
        db_session.commit()
    register_export_stream(db_session, tenant_id="tenant-a", source_stream=source_stream, environment="LOCAL_SIM")
    db_session.commit()


def test_wrong_content_type_header_is_still_handled_safely(client, db_session):
    """The signature is computed over the raw bytes, independent of the
    Content-Type header -- a sender that (wrongly) declares `text/plain`
    for a real JSON body must not 500; it is processed exactly the same
    as a correctly labeled request."""
    _seed_tenant_and_stream(db_session)
    envelope = _execution_envelope(event_id="evt-ct-1")
    body = json.dumps({"events": [envelope.model_dump_json()]}).encode()

    response = _signed_post(client, body, content_type="text/plain")
    assert response.status_code == 200
    assert response.json()["results"] == [{"status": "applied", "event_id": "evt-ct-1"}]


def test_truncated_json_body_is_rejected_cleanly_not_500(client):
    body = b'{"events": ["{\\"event_id\\": \\"evt-trunc'  # cut off mid-string, invalid JSON
    response = _signed_post(client, body)
    assert response.status_code == 422
    assert response.status_code < 500


def test_wrong_content_type_and_truncated_body_together_still_422s(client):
    body = b"not json at all \x00\x01\xff"
    response = _signed_post(client, body, content_type="application/octet-stream")
    assert response.status_code == 422


def test_deeply_nested_payload_field_is_rejected_cleanly_not_500(client, db_session):
    """`EventEnvelope.payload` is a loosely-typed dict at the envelope
    layer (the per-event-type Pydantic model only validates it once
    `_apply_projection` dispatches on `event_type`) -- a batch entry
    whose own outer JSON is syntactically valid but absurdly deeply
    nested must still be handled without a 500 (a stack-overflow-style
    crash in the JSON/pydantic parser, or in `model_validate_json`,
    would be exactly the unsafe outcome this test guards against).

    FOUND AND FIXED by this exact test (see app/api/relay_routes.py's
    own new `except ValidationError` block, and CHANGELOG.md): this
    reproducibly 500'd before the fix -- `EventEnvelope.model_validate_
    json` raises a real `pydantic.ValidationError` once pydantic-core's
    own internal recursion guard trips on input this deep
    ("Invalid JSON: recursion limit exceeded"), and NOTHING in
    `ingest_export_event`/`app/api/relay_routes.py` caught that
    exception type -- it propagated straight out of the route as an
    unhandled 500, which would have aborted the ENTIRE batch request,
    including every other already-committed, perfectly valid event
    processed earlier in the same loop."""
    _seed_tenant_and_stream(db_session)
    nested: object = "bottom"
    for _ in range(2000):
        nested = {"n": nested}
    envelope = _execution_envelope(event_id="evt-nested-1")
    envelope_dict = json.loads(envelope.model_dump_json())
    envelope_dict["payload"]["deeply_nested_extra_field"] = nested

    # Building (and signing) this adversarial body at all needs a raised
    # recursion limit on THIS test's own encoding step -- real client
    # code generating 2000-deep JSON is exactly as capable of raising its
    # own limit, so this isn't an artificial advantage; it only ensures
    # the request this test sends is genuinely well-formed-but-deep, not
    # itself truncated by this process's own default limit. The SERVER
    # side (the real subject under test, reached via `_signed_post`
    # below) runs with its own untouched, default recursion limit.
    old_limit = sys.getrecursionlimit()
    sys.setrecursionlimit(10_000)
    try:
        event_string = json.dumps(envelope_dict)
        body = json.dumps({"events": [event_string]}).encode()
    finally:
        sys.setrecursionlimit(old_limit)

    response = _signed_post(client, body)
    # The route-level request itself is always a clean 200 (per-event
    # results, never a 500) -- `_IngestBatchRequest.model_validate_json`
    # over the OUTER body never even sees the deep nesting (that's one
    # level further in, inside each individual event string).
    assert response.status_code == 200
    result = response.json()["results"][0]
    assert result["status"] == "malformed_envelope"


def test_oversized_single_event_string_is_rejected_cleanly_not_500(client, db_session):
    """No per-event-string byte-size cap exists in `_IngestBatchRequest`
    (only the 100-event batch LENGTH is bounded) -- a single ~2MB event
    string is adversarial in a different dimension than the batch-size
    cap: this proves it is still handled safely (parsed, validated, and
    either applied or cleanly rejected) rather than crashing the
    request.

    This specific case (an extra, unrecognized field inside the
    per-event-type payload) is ALSO an instance of the same bug class
    `test_deeply_nested_payload_field_is_rejected_cleanly_not_500`
    found and fixed -- `ExecutionAppliedPayload.model_validate` forbids
    extra fields by default, and that `pydantic.ValidationError` is
    exactly what app/api/relay_routes.py's new `except ValidationError`
    block now catches. Kept as its own separate test (rather than
    folded into the one above) since it exercises the OTHER of the two
    places that validation happens (`_apply_projection`'s per-event-type
    model, not `EventEnvelope.model_validate_json` itself) and runs
    AFTER `ingest_export_event` has already flushed (not yet committed)
    an `InboxEvent` insert for this event_id -- proving the rollback in
    that except block also cleanly discards that uncommitted flush."""
    _seed_tenant_and_stream(db_session)
    envelope = _execution_envelope(event_id="evt-huge-1")
    envelope_dict = json.loads(envelope.model_dump_json())
    envelope_dict["payload"]["_huge_padding"] = "x" * (2 * 1024 * 1024)
    body = json.dumps({"events": [json.dumps(envelope_dict)]}).encode()

    response = _signed_post(client, body)
    assert response.status_code == 200
    result = response.json()["results"][0]
    assert result["status"] == "malformed_envelope"

    from app.models.integration_inbox import InboxEvent

    assert db_session.get(InboxEvent, "evt-huge-1") is None  # the uncommitted flush was cleanly rolled back, not left dangling


@pytest.mark.parametrize(
    "instrument_id",
    [
        "\U0001f4c8\U0001f680",  # emoji
        "héllo-wörld-日本語",  # multi-script unicode
        "'; DROP TABLE ledger_entries; --",  # injection-style string
        "<script>alert(1)</script>",  # injection-style string
    ],
    ids=["emoji", "multi-script-unicode", "sql-injection-string", "xss-string"],
)
def test_unicode_and_injection_style_instrument_ids_are_safely_stored_as_data(client, db_session, instrument_id):
    """Every one of these must be treated as an opaque string value, never
    executed/interpolated: a real ledger entry is written with the
    EXACT string given (proving it went through a parameterized write,
    not string-built SQL), and the request never 500s."""
    _seed_tenant_and_stream(db_session)
    envelope = _execution_envelope(event_id=f"evt-unicode-{hash(instrument_id)}", instrument_id=instrument_id)
    body = json.dumps({"events": [envelope.model_dump_json()]}).encode()

    response = _signed_post(client, body)
    assert response.status_code == 200
    result = response.json()["results"][0]
    assert result["status"] == "applied"

    entry = db_session.scalars(
        select(LedgerEntry).where(LedgerEntry.tenant_id == "tenant-a")
    ).first()
    assert entry is not None
    assert entry.instrument == instrument_id  # stored verbatim, never executed/interpolated


def test_embedded_nul_byte_instrument_id_is_rejected_cleanly_not_500(client, db_session):
    """A real byte Python's `str`/pydantic happily accepts but Postgres's
    `text`/`varchar` columns reject outright (`psycopg.DataError:
    PostgreSQL text fields cannot contain NUL (0x00) bytes`).

    FOUND AND FIXED by this exact test (see app/api/relay_routes.py's
    new `except DataError` block, and CHANGELOG.md): this reproducibly
    500'd before the fix, the exact same unhandled-exception shape as
    the deeply-nested-payload/extra-field cases above -- `append_entry`'s
    own INSERT raised `sqlalchemy.exc.DataError`, uncaught anywhere
    between it and the route, aborting the whole batch request."""
    _seed_tenant_and_stream(db_session)
    instrument_id = "AAPL\u0000NUL"
    envelope = _execution_envelope(event_id="evt-nul-1", instrument_id=instrument_id)
    body = json.dumps({"events": [envelope.model_dump_json()]}).encode()

    response = _signed_post(client, body)
    assert response.status_code == 200
    result = response.json()["results"][0]
    assert result["status"] == "malformed_envelope"

    entries = db_session.scalars(select(LedgerEntry).where(LedgerEntry.tenant_id == "tenant-a")).all()
    assert entries == []  # the failed insert was cleanly rolled back, not left partially written


def test_duplicate_event_id_with_a_different_body_is_a_clean_integrity_error_not_500(client, db_session):
    """EventIntegrityError (app/services/integration_inbox.py) is already
    unit-tested at the service layer (tests/test_integration_inbox.py)
    but never through the real HTTP route -- this closes that gap: a
    redelivery of the SAME event_id carrying genuinely different bytes
    (not the harmless identical-redelivery case) must surface as a
    clean per-event `integrity_error` result, never a 500, and must
    never silently overwrite the original ledger entry."""
    _seed_tenant_and_stream(db_session)
    original = _execution_envelope(event_id="evt-dup-1", broker_order_id="paper-dup-original")
    response = _signed_post(client, json.dumps({"events": [original.model_dump_json()]}).encode())
    assert response.json()["results"] == [{"status": "applied", "event_id": "evt-dup-1"}]

    tampered = _execution_envelope(event_id="evt-dup-1", broker_order_id="paper-dup-TAMPERED")
    response = _signed_post(client, json.dumps({"events": [tampered.model_dump_json()]}).encode())
    assert response.status_code == 200
    result = response.json()["results"][0]
    assert result["status"] == "integrity_error"

    entries = db_session.scalars(select(LedgerEntry).where(LedgerEntry.tenant_id == "tenant-a")).all()
    assert len(entries) == 1  # the original -- the tampered redelivery never wrote a second entry

    inbox_event = db_session.get(InboxEvent, "evt-dup-1")
    assert inbox_event is not None
    assert "paper-dup-original" in inbox_event.envelope_json
    assert "paper-dup-TAMPERED" not in inbox_event.envelope_json


def test_out_of_order_sequence_in_one_batch_parks_then_resolves_without_500(client, db_session):
    """Sequence 2 delivered before sequence 0/1 (a genuine gap, not the
    anomalously-low case `_next_expected_sequence`'s own docstring
    covers) -- sequence 2 must be received and durably stored but left
    UNAPPLIED (no ledger entry) until its missing predecessors arrive,
    and the whole request must stay a clean 200 with per-event results,
    never a 500 and never a double-counted/ledger entry for the parked
    event.

    KNOWN GAP this test documents rather than silently fixing (see
    docs/KNOWN_ISSUES.md): app/api/relay_routes.py's own per-event
    result dict hardcodes `"status": "applied"` for every event that
    reaches that line without raising one of the three named exceptions
    -- it never actually checks `inbox_event.applied_at`. A genuinely
    PARKED event (this one) is reported back to the caller as
    `"applied"` even though NO ledger projection happened for it. Left
    unfixed here because signal-copier's own app/relay_worker.py
    consumes this exact field (`if status == "applied": delivered.
    append(...)`) and marks the export event permanently delivered
    (never retried) on it -- correcting the label without first
    designing what the relay worker should DO with a genuinely distinct
    "parked" status (retry forever? never retry, since redelivery alone
    can't unstick e.g. an unsupported-schema-version park?) is a
    cross-service wire-contract change, not a same-file bugfix, so it
    is flagged for a separate, reviewed track instead of guessed at here."""
    _seed_tenant_and_stream(db_session)
    seq2 = _execution_envelope(event_id="evt-ooo-2", export_sequence=2, broker_order_id="paper-ooo-2")

    body = json.dumps({"events": [seq2.model_dump_json()]}).encode()
    response = _signed_post(client, body)
    assert response.status_code == 200
    results = response.json()["results"]
    # This assertion documents the known gap above, it is not an
    # endorsement of it: the route reports "applied" even though the
    # event below is, provably, NOT applied.
    assert results[0]["status"] == "applied"

    inbox_event = db_session.get(InboxEvent, "evt-ooo-2")
    assert inbox_event is not None
    assert inbox_event.applied_at is None  # genuinely parked -- the "applied" label above is inaccurate for this row

    # The real, load-bearing safety property: no ledger entry exists for
    # a genuinely unapplied event, regardless of what the HTTP label says.
    entries = db_session.scalars(select(LedgerEntry).where(LedgerEntry.tenant_id == "tenant-a")).all()
    assert entries == []

    # Delivering the missing predecessors now correctly cascades and
    # applies sequence 2 for real.
    seq0 = _execution_envelope(event_id="evt-ooo-0", export_sequence=0, broker_order_id="paper-ooo-0")
    seq1 = _execution_envelope(event_id="evt-ooo-1", export_sequence=1, broker_order_id="paper-ooo-1")
    body = json.dumps({"events": [seq0.model_dump_json(), seq1.model_dump_json()]}).encode()
    response = _signed_post(client, body)
    assert response.status_code == 200

    db_session.refresh(inbox_event)
    assert inbox_event.applied_at is not None
    entries = db_session.scalars(select(LedgerEntry).where(LedgerEntry.tenant_id == "tenant-a")).all()
    assert len(entries) == 3
