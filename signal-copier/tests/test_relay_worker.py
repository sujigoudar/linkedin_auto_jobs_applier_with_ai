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
