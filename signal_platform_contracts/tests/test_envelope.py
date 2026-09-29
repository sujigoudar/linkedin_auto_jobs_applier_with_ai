"""EventEnvelope's own tests."""
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from signal_platform_contracts import (
    Environment,
    EventEnvelope,
    EventType,
    EvidenceClass,
    ExecutionAppliedPayload,
    InstrumentIdentity,
    PrivateAccountIdentity,
    SourceIdentity,
    SourceReceiptPayload,
    build_subject,
    compute_payload_hash,
)
from signal_platform_contracts.envelope import IMPLEMENTED_EVENT_TYPES


def _now():
    return datetime.now(timezone.utc)


def _source_identity():
    return SourceIdentity(
        source_provider_id="telegram", analyst_id="momentum_mike", parser_version="v3",
        source_event_id="src-evt-1",
    )


def _instrument_identity():
    return InstrumentIdentity(
        instrument_id="AAPL", venue="NASDAQ", market_type="equity", currency="USD",
        multiplier="1", quantity_convention="shares",
    )


def _valid_envelope(**overrides):
    payload = SourceReceiptPayload(
        source=_source_identity(), instrument=_instrument_identity(), side="buy",
        quantity="10", price="150.00",
    )
    payload_dict = payload.model_dump(mode="json")
    fields = {
        "event_type": EventType.SOURCE_RECEIPT,
        "event_id": "evt-1",
        "producer_id": "signal-copier-instance-1",
        "source_stream": "signal-copier:acct1",
        "producer_generation": 1,
        "export_sequence": 0,
        "subject": build_subject(source=_source_identity()),
        "event_time": _now(),
        "effective_time": _now(),
        "availability_time": _now(),
        "receipt_time": _now(),
        "environment": Environment.LOCAL_SIM,
        "evidence_class": EvidenceClass.SYNTHETIC_FIXTURE,
        "payload_hash": compute_payload_hash(payload_dict),
        "payload": payload_dict,
    }
    fields.update(overrides)
    return EventEnvelope(**fields)


def test_a_valid_envelope_round_trips_through_json():
    envelope = _valid_envelope()
    restored = EventEnvelope.model_validate_json(envelope.model_dump_json())
    assert restored == envelope


def test_event_id_must_not_be_blank():
    with pytest.raises(ValidationError, match="must not be blank"):
        _valid_envelope(event_id="")


def test_naive_datetime_is_rejected():
    naive = datetime(2026, 1, 1)  # noqa: DTZ001 -- deliberately naive, this is what's being rejected
    with pytest.raises(ValidationError, match="timezone-aware"):
        _valid_envelope(event_time=naive)


def test_subject_must_not_be_empty():
    with pytest.raises(ValidationError, match="subject must not be empty"):
        _valid_envelope(subject={})


def test_only_five_event_types_are_actually_implemented_in_this_slice():
    """Documents the honest scope boundary this package's own docstring
    claims -- if this test needs updating, a real payload model was added
    for a new EventType and the docstring/IMPLEMENTED_EVENT_TYPES set
    should be updated in the same change, not drift silently."""
    assert IMPLEMENTED_EVENT_TYPES == {
        EventType.SOURCE_RECEIPT,
        EventType.EXECUTION_APPLIED,
        EventType.FEE,
        EventType.POSITION_SNAPSHOT,
        EventType.ROUTING_ADMISSION_OUTCOME,
    }


def test_a_redelivery_with_the_same_event_id_and_payload_has_the_same_hash():
    """The idempotency contract this package hands to a future outbox/
    inbox: two envelopes built from the SAME real event must hash
    identically, so a receiver's unique-identity check can recognize a
    resend as harmless."""
    first = _valid_envelope()
    second = _valid_envelope()
    assert first.event_id == second.event_id
    assert first.payload_hash == second.payload_hash


def test_a_tampered_redelivery_with_a_different_payload_has_a_different_hash():
    """The other half of that contract: the SAME event_id with a
    DIFFERENT payload must hash differently, so a receiver can tell the
    two apart as the integrity incident INTEGRATION_DECISION.md S6
    describes, never silently overwrite the original."""
    original_payload = SourceReceiptPayload(
        source=_source_identity(), instrument=_instrument_identity(), side="buy", quantity="10", price="150.00",
    ).model_dump(mode="json")
    tampered_payload = SourceReceiptPayload(
        source=_source_identity(), instrument=_instrument_identity(), side="buy", quantity="999", price="150.00",
    ).model_dump(mode="json")
    assert compute_payload_hash(original_payload) != compute_payload_hash(tampered_payload)


def test_execution_applied_payload_treats_missing_fee_as_unknown_not_zero():
    payload = ExecutionAppliedPayload(
        account=PrivateAccountIdentity(account_id="acct1"),
        instrument=_instrument_identity(),
        side="buy",
        filled_quantity="10",
        filled_price="150.00",
        broker="paper",
        broker_order_id="paper-1",
    )
    assert payload.fee is None
