"""app/services/integration_inbox.py's own ordering/gap-detection
logic -- INTEGRATION_ACCEPTANCE_CASES.json INT-006 "Ordering and gap
detection": delivering export_sequence 42 before 41 must not apply 42
early; delivering 41 afterward must apply both, in order, without 42
needing to be redelivered."""
from datetime import datetime, timezone

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

from app.models.ledger import LedgerEntry
from app.models.tenancy import Tenant
from app.services.integration_inbox import ingest_export_event, register_export_stream


def _seed_tenant(db_session, tenant_id="tenant-a"):
    if db_session.get(Tenant, tenant_id) is None:
        db_session.add(Tenant(tenant_id=tenant_id, display_name="Tenant", environment="LOCAL_SIM"))
        db_session.commit()


def _instrument():
    return InstrumentIdentity(
        instrument_id="AAPL", venue="NASDAQ", market_type="equity", currency="USD",
        multiplier="1", quantity_convention="shares",
    )


def _envelope(*, event_id, export_sequence, source_stream="signal-copier:acct1"):
    payload = ExecutionAppliedPayload(
        account=PrivateAccountIdentity(account_id="acct1"), instrument=_instrument(), side="buy",
        filled_quantity="1", filled_price="100.00", broker="paper", broker_order_id=f"paper-{event_id}",
    )
    payload_dict = payload.model_dump(mode="json")
    now = datetime.now(timezone.utc)
    return EventEnvelope(
        event_type=EventType.EXECUTION_APPLIED, event_id=event_id, producer_id="signal-copier-instance-1",
        source_stream=source_stream, export_sequence=export_sequence,
        subject=build_subject(account=PrivateAccountIdentity(account_id="acct1"), instrument=_instrument()),
        event_time=now, effective_time=now, availability_time=now, receipt_time=now,
        environment=Environment.LOCAL_SIM, evidence_class=EvidenceClass.INTERNAL_PAPER,
        payload_hash=compute_payload_hash(payload_dict), payload=payload_dict,
    )


def _setup(db_session, source_stream="signal-copier:acct1"):
    _seed_tenant(db_session)
    register_export_stream(db_session, tenant_id="tenant-a", source_stream=source_stream, environment="LOCAL_SIM")
    db_session.commit()


def test_sequence_0_applies_immediately_with_nothing_before_it(db_session):
    _setup(db_session)
    envelope = _envelope(event_id="evt-0", export_sequence=0)
    inbox_event = ingest_export_event(db_session, envelope.model_dump_json())
    db_session.commit()
    assert inbox_event.applied_at is not None


def test_an_out_of_order_arrival_is_received_but_not_applied(db_session):
    _setup(db_session)
    # Sequence 1 arrives before sequence 0 exists at all -- a genuine gap.
    envelope_1 = _envelope(event_id="evt-1", export_sequence=1)
    inbox_event_1 = ingest_export_event(db_session, envelope_1.model_dump_json())
    db_session.commit()

    assert inbox_event_1.applied_at is None
    assert inbox_event_1.ledger_entry_id is None
    ledger_entries = db_session.scalars(select(LedgerEntry).where(LedgerEntry.tenant_id == "tenant-a")).all()
    assert ledger_entries == []


def test_delivering_the_missing_predecessor_cascades_through_the_parked_successor(db_session):
    """The exact INT-006 scenario: deliver 1 (as export_sequence 1) before
    0, confirm 1 stays parked, then deliver 0 and confirm BOTH apply --
    0 immediately, then 1 cascades without being redelivered."""
    _setup(db_session)
    envelope_1 = _envelope(event_id="evt-1", export_sequence=1)
    inbox_event_1 = ingest_export_event(db_session, envelope_1.model_dump_json())
    db_session.commit()
    assert inbox_event_1.applied_at is None

    envelope_0 = _envelope(event_id="evt-0", export_sequence=0)
    inbox_event_0 = ingest_export_event(db_session, envelope_0.model_dump_json())
    db_session.commit()

    assert inbox_event_0.applied_at is not None

    refreshed_1 = db_session.get(type(inbox_event_1), inbox_event_1.event_id)
    assert refreshed_1 is not None
    assert refreshed_1.applied_at is not None
    assert refreshed_1.ledger_entry_id is not None

    ledger_entries = db_session.scalars(
        select(LedgerEntry).where(LedgerEntry.tenant_id == "tenant-a").order_by(LedgerEntry.event_time)
    ).all()
    assert len(ledger_entries) == 2


def test_redelivering_the_already_cascaded_event_is_still_a_harmless_no_op(db_session):
    _setup(db_session)
    envelope_1 = _envelope(event_id="evt-1", export_sequence=1)
    ingest_export_event(db_session, envelope_1.model_dump_json())
    db_session.commit()
    envelope_0 = _envelope(event_id="evt-0", export_sequence=0)
    ingest_export_event(db_session, envelope_0.model_dump_json())
    db_session.commit()

    # Redeliver 1 (already cascaded and applied) -- must not double-apply.
    result = ingest_export_event(db_session, envelope_1.model_dump_json())
    db_session.commit()
    assert result.event_id == "evt-1"

    ledger_entries = db_session.scalars(select(LedgerEntry).where(LedgerEntry.tenant_id == "tenant-a")).all()
    assert len(ledger_entries) == 2


def test_independent_streams_never_leak_gap_state_into_each_other(db_session):
    _setup(db_session, source_stream="signal-copier:acct1")
    _seed_tenant(db_session)
    register_export_stream(db_session, tenant_id="tenant-a", source_stream="signal-copier:acct2", environment="LOCAL_SIM")
    db_session.commit()

    # acct2's own sequence 0 must apply even though acct1 has a parked gap.
    ingest_export_event(db_session, _envelope(event_id="evt-acct1-1", export_sequence=1, source_stream="signal-copier:acct1").model_dump_json())
    db_session.commit()
    inbox_event_acct2 = ingest_export_event(
        db_session, _envelope(event_id="evt-acct2-0", export_sequence=0, source_stream="signal-copier:acct2").model_dump_json()
    )
    db_session.commit()

    assert inbox_event_acct2.applied_at is not None
