"""app/relay_worker.py -- tested against a documented request/response
shape with an injected `http_post`, the same way every other adapter in
this build (ccxt, Alpaca, SignalStack) was built and tested before real
credentials/transport existed for it. No live network call."""
from datetime import datetime, timezone

import pytest
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

from app.db import SignalStore
from app.relay_worker import (
    RelayNotConfiguredError,
    classify_parked_reason,
    run_once,
    sign_relay_payload,
)

_SECRET = "test-relay-secret"


@pytest.fixture
def store(tmp_path):
    return SignalStore(tmp_path / "test.db")


def _instrument():
    return InstrumentIdentity(
        instrument_id="AAPL", venue="NASDAQ", market_type="equity", currency="USD",
        multiplier="1", quantity_convention="shares",
    )


def _execution_envelope(*, event_id, export_sequence=0, source_stream="signal-copier:acct1"):
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


class _FakeResponse:
    def __init__(self, results):
        self._results = results

    def raise_for_status(self):
        pass

    def json(self):
        return {"results": self._results}


def test_run_once_refuses_without_an_ingress_url(store):
    with pytest.raises(RelayNotConfiguredError):
        run_once(store, ingress_url="", signing_secret=_SECRET)


def test_run_once_with_nothing_undelivered_makes_no_http_call(store):
    calls = []

    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        return _FakeResponse([])

    result = run_once(store, ingress_url="https://commercial.example/internal/relay/ingest-batch",
                       signing_secret=_SECRET, http_post=fake_post)
    assert calls == []
    assert result.delivered_event_ids == []


def test_run_once_signs_the_batch_and_marks_applied_events_delivered(store):
    envelope = _execution_envelope(event_id="evt-relay-1")
    store.append_export_event(envelope)

    captured = {}

    def fake_post(url, *, content, headers):
        captured["url"] = url
        captured["content"] = content
        captured["headers"] = headers
        return _FakeResponse([{"status": "applied", "event_id": "evt-relay-1"}])

    result = run_once(
        store,
        ingress_url="https://commercial.example/internal/relay/ingest-batch",
        signing_secret=_SECRET,
        http_post=fake_post,
    )

    assert captured["url"] == "https://commercial.example/internal/relay/ingest-batch"
    assert "x-relay-signature" in captured["headers"]
    assert result.delivered_event_ids == ["evt-relay-1"]
    assert store.list_undelivered_export_events() == []


def test_run_once_leaves_an_unregistered_stream_event_undelivered_for_retry(store):
    envelope = _execution_envelope(event_id="evt-relay-2")
    store.append_export_event(envelope)

    def fake_post(url, **kwargs):
        return _FakeResponse([{"status": "unregistered_stream", "detail": "no tenant"}])

    result = run_once(
        store, ingress_url="https://commercial.example/x", signing_secret=_SECRET, http_post=fake_post,
    )

    assert result.delivered_event_ids == []
    assert result.unregistered_stream_event_ids == ["evt-relay-2"]
    # Still undelivered -- the next poll retries it.
    assert len(store.list_undelivered_export_events()) == 1


def test_run_once_leaves_an_integrity_error_event_undelivered_and_reports_it(store):
    envelope = _execution_envelope(event_id="evt-relay-3")
    store.append_export_event(envelope)

    def fake_post(url, **kwargs):
        return _FakeResponse([{"status": "integrity_error", "detail": "hash mismatch"}])

    result = run_once(
        store, ingress_url="https://commercial.example/x", signing_secret=_SECRET, http_post=fake_post,
    )

    assert result.delivered_event_ids == []
    assert result.integrity_error_event_ids == ["evt-relay-3"]
    assert len(store.list_undelivered_export_events()) == 1


@pytest.mark.parametrize(
    "parked_reason,expected",
    [
        ("sequence_gap_awaiting_predecessor", "transient"),
        ("fee_target_not_found:paper|order-1", "transient"),
        ("routing_outcome_target_not_found:evt-1", "transient"),
        ("edit_without_resolvable_target:tenant-a|telegram||src-evt-9", "transient"),
        ("unsupported_schema_version:9.9", "structural"),
        ("unimplemented_event_type:cash_movement", "structural"),
        ("source_event_kind_not_ledger_representable:target_update", "structural"),
        ("manifest_generation_mismatch:manifest-1", "structural"),
        ("manifest_metadata_mismatch:manifest-1", "structural"),
        ("generation_rollback_detected:3", "structural"),
        ("new_generation_requires_bootstrap:5", "structural"),
        ("edit_without_resolvable_target:missing_original_source_event_id", "structural"),
        ("some_future_reason_this_worker_has_never_heard_of", "structural"),
    ],
)
def test_classify_parked_reason_matches_the_documented_taxonomy(parked_reason, expected):
    assert classify_parked_reason(parked_reason) == expected


def test_run_once_leaves_a_transiently_parked_event_undelivered_for_retry(store):
    envelope = _execution_envelope(event_id="evt-relay-parked-1")
    store.append_export_event(envelope)

    def fake_post(url, **kwargs):
        return _FakeResponse(
            [{"status": "parked", "event_id": "evt-relay-parked-1", "parked_reason": "fee_target_not_found:paper|o-1"}]
        )

    result = run_once(
        store, ingress_url="https://commercial.example/x", signing_secret=_SECRET, http_post=fake_post,
    )

    assert result.delivered_event_ids == []
    assert result.transiently_parked_event_ids == ["evt-relay-parked-1"]
    assert result.terminally_parked_event_ids == []
    # Still undelivered -- the next poll retries it, same as any other
    # correlation-wait that may resolve once the correlated event arrives
    # and the commercial side's own cascade applies it.
    assert len(store.list_undelivered_export_events()) == 1
    event = store.get_export_event("evt-relay-parked-1")
    assert event["terminal_park_reason"] is None


def test_run_once_terminally_parks_a_structurally_unresolvable_event_and_stops_resending_it(store):
    envelope = _execution_envelope(event_id="evt-relay-parked-2")
    store.append_export_event(envelope)

    def fake_post(url, **kwargs):
        return _FakeResponse(
            [
                {
                    "status": "parked",
                    "event_id": "evt-relay-parked-2",
                    "parked_reason": "source_event_kind_not_ledger_representable:target_update",
                }
            ]
        )

    result = run_once(
        store, ingress_url="https://commercial.example/x", signing_secret=_SECRET, http_post=fake_post,
    )

    assert result.delivered_event_ids == []
    assert result.transiently_parked_event_ids == []
    assert result.terminally_parked_event_ids == ["evt-relay-parked-2"]

    # Never marked delivered -- it was never applied, and `delivered_at`
    # must never imply that it was.
    event = store.get_export_event("evt-relay-parked-2")
    assert event["delivered_at"] is None
    assert event["terminal_park_reason"] == "source_event_kind_not_ledger_representable:target_update"
    assert event["terminal_parked_at"] is not None

    # Excluded from future polls -- resending it forever would be pure
    # noise (the commercial side's own idempotent dedup already short-
    # circuits any reprocessing, and nothing about the park can resolve
    # without a code change there).
    assert store.list_undelivered_export_events() == []
    assert store.terminally_parked_export_event_count() == 1

    # A second poll with nothing new in the outbox makes no further HTTP
    # call for this event (it's no longer "undelivered").
    calls = []

    def fake_post_second(url, **kwargs):
        calls.append(url)
        return _FakeResponse([])

    run_once(store, ingress_url="https://commercial.example/x", signing_secret=_SECRET, http_post=fake_post_second)
    assert calls == []


def test_the_signature_a_run_produces_verifies_against_the_documented_scheme(store):
    """Cross-checks against the exact same "t=...,v1=..." construction
    signal-portfolio-commercial's own app/services/relay_auth.py verifies
    -- proves the two independently-written implementations agree on the
    wire format without importing each other."""
    import hashlib
    import hmac

    payload = b'{"events": ["x"]}'
    header = sign_relay_payload(payload, _SECRET, timestamp=1_700_000_000)

    parts = dict(item.split("=", 1) for item in header.split(","))
    assert parts["t"] == "1700000000"
    expected = hmac.new(_SECRET.encode(), b"1700000000." + payload, hashlib.sha256).hexdigest()
    assert parts["v1"] == expected
