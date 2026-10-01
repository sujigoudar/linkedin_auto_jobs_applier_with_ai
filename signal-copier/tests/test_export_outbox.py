"""Signal Platform Integration Correction Pack's own INTEGRATION_DECISION.md
S4.2/S6: the private export outbox (app/db.py's export_events table +
SignalStore's own append/list/mark-delivered methods).
"""
import sqlite3
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
from app.models import OrderResult, OrderStatus, Signal, Side


@pytest.fixture
def store(tmp_path):
    return SignalStore(tmp_path / "test.db")


def _instrument():
    return InstrumentIdentity(
        instrument_id="AAPL", venue="NASDAQ", market_type="equity", currency="USD",
        multiplier="1", quantity_convention="shares",
    )


def _execution_envelope(*, event_id="evt-1", export_sequence=0, source_stream="signal-copier:acct1"):
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


def test_append_export_event_is_retrievable_undelivered(store):
    envelope = _execution_envelope()
    store.append_export_event(envelope)

    undelivered = store.list_undelivered_export_events()
    assert len(undelivered) == 1
    assert undelivered[0] == envelope


def test_append_export_event_reconstructs_the_exact_envelope_not_a_rebuilt_one(store):
    """S6: "the relay never reconstructs or reinterprets it, only forwards
    these exact bytes" -- proven here by comparing full model equality,
    not just a few fields."""
    envelope = _execution_envelope(event_id="evt-exact")
    store.append_export_event(envelope)

    [reloaded] = store.list_undelivered_export_events()
    assert reloaded.model_dump() == envelope.model_dump()


def test_appending_the_same_event_id_twice_is_a_harmless_no_op(store):
    envelope = _execution_envelope(event_id="evt-dup")
    store.append_export_event(envelope)
    store.append_export_event(envelope)  # e.g. a retried in-process call

    assert len(store.list_undelivered_export_events()) == 1


def test_next_export_sequence_starts_at_zero_for_a_fresh_stream(store):
    assert store.next_export_sequence("signal-copier:acct1") == 0


def test_next_export_sequence_increments_past_the_highest_appended(store):
    store.append_export_event(_execution_envelope(event_id="evt-1", export_sequence=0))
    store.append_export_event(_execution_envelope(event_id="evt-2", export_sequence=1))
    assert store.next_export_sequence("signal-copier:acct1") == 2


def test_next_export_sequence_is_independent_per_stream(store):
    store.append_export_event(
        _execution_envelope(event_id="evt-1", export_sequence=0, source_stream="signal-copier:acct1")
    )
    assert store.next_export_sequence("signal-copier:acct2") == 0


def test_mark_export_events_delivered_removes_them_from_the_undelivered_list(store):
    envelope = _execution_envelope(event_id="evt-1")
    store.append_export_event(envelope)

    store.mark_export_events_delivered([envelope.event_id])
    assert store.list_undelivered_export_events() == []


def test_marking_an_unknown_event_id_delivered_is_not_an_error(store):
    store.mark_export_events_delivered(["never-appended"])  # must not raise


def test_save_order_result_with_export_envelope_commits_both_in_one_transaction(store):
    signal = Signal(source="tradingview", symbol="AAPL", side=Side.BUY)
    store.save_signal(signal)
    envelope = _execution_envelope(event_id="evt-fill-1")

    store.save_order_result(
        OrderResult(account_id="acct1", status=OrderStatus.FILLED, signal_id=signal.id),
        export_envelope=envelope,
    )

    assert len(store.list_recent_orders()) == 1
    assert len(store.list_undelivered_export_events()) == 1


# --- Track 33: list_export_events/get_export_event (the operator-facing
# read surface GET /export-events builds on) ---------------------------


def test_list_export_events_returns_real_rows_newest_first(store):
    store.append_export_event(_execution_envelope(event_id="evt-1", export_sequence=0))
    store.append_export_event(_execution_envelope(event_id="evt-2", export_sequence=1))
    store.append_export_event(_execution_envelope(event_id="evt-3", export_sequence=2))

    listed = store.list_export_events()
    assert [e["event_id"] for e in listed] == ["evt-3", "evt-2", "evt-1"]
    assert listed[0]["source_stream"] == "signal-copier:acct1"
    assert listed[0]["event_type"] == "execution_applied"
    assert listed[0]["delivered_at"] is None
    assert listed[0]["payload"]["broker_order_id"] == "paper-1"


def test_list_export_events_respects_limit(store):
    for i in range(5):
        store.append_export_event(_execution_envelope(event_id=f"evt-{i}", export_sequence=i))
    assert len(store.list_export_events(limit=2)) == 2


def test_list_export_events_filters_by_source_stream_and_delivered(store):
    store.append_export_event(
        _execution_envelope(event_id="evt-acct1", export_sequence=0, source_stream="signal-copier:acct1")
    )
    store.append_export_event(
        _execution_envelope(event_id="evt-acct2", export_sequence=0, source_stream="signal-copier:acct2")
    )
    store.mark_export_events_delivered(["evt-acct1"])

    assert [e["event_id"] for e in store.list_export_events(source_stream="signal-copier:acct2")] == ["evt-acct2"]
    assert [e["event_id"] for e in store.list_export_events(delivered=True)] == ["evt-acct1"]
    assert [e["event_id"] for e in store.list_export_events(delivered=False)] == ["evt-acct2"]
    assert {e["event_id"] for e in store.list_export_events(delivered=None)} == {"evt-acct1", "evt-acct2"}


def test_get_export_event_by_id_and_unknown_id_is_none(store):
    store.append_export_event(_execution_envelope(event_id="evt-1", export_sequence=0))
    found = store.get_export_event("evt-1")
    assert found is not None
    assert found["event_id"] == "evt-1"
    assert found["payload"]["broker_order_id"] == "paper-1"

    assert store.get_export_event("does-not-exist") is None


def test_a_failed_order_insert_leaves_no_orphaned_export_event(store):
    """The load-bearing property S6 actually asks for: if the order half
    of the same-transaction write fails, the outbox half must not survive
    it either -- an orphaned export event for a fill that was never
    really recorded would be worse than no export at all."""
    envelope = _execution_envelope(event_id="evt-orphan-check")

    with pytest.raises(sqlite3.IntegrityError):
        store.save_order_result(
            # signal_id references a signal that was never saved -- DB-01's
            # own foreign-key enforcement rejects the INSERT itself.
            OrderResult(account_id="acct1", status=OrderStatus.FILLED, signal_id="never-saved"),
            export_envelope=envelope,
        )

    assert store.list_undelivered_export_events() == []
    assert store.list_recent_orders() == []
