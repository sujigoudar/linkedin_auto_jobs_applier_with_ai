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
from app.services.integration_inbox import SequenceSlotAlreadyConsumedError, ingest_export_event, register_export_stream


def _seed_tenant(db_session, tenant_id="tenant-a"):
    if db_session.get(Tenant, tenant_id) is None:
        db_session.add(Tenant(tenant_id=tenant_id, display_name="Tenant", environment="LOCAL_SIM"))
        db_session.commit()


def _instrument():
    return InstrumentIdentity(
        instrument_id="AAPL", venue="NASDAQ", market_type="equity", currency="USD",
        multiplier="1", quantity_convention="shares",
    )


def _envelope(*, event_id, export_sequence, source_stream="signal-copier:acct1", producer_generation=1):
    payload = ExecutionAppliedPayload(
        account=PrivateAccountIdentity(account_id="acct1"), instrument=_instrument(), side="buy",
        filled_quantity="1", filled_price="100.00", broker="paper", broker_order_id=f"paper-{event_id}",
    )
    payload_dict = payload.model_dump(mode="json")
    now = datetime.now(timezone.utc)
    return EventEnvelope(
        event_type=EventType.EXECUTION_APPLIED, event_id=event_id, producer_id="signal-copier-instance-1",
        source_stream=source_stream, export_sequence=export_sequence, producer_generation=producer_generation,
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


def test_an_older_generation_arriving_after_a_newer_one_is_established_is_parked_as_a_rollback(db_session):
    """INTEGRATION_ACCEPTANCE_CASES.json INT-010 "Producer restored to
    older database": once generation 2 is established (applied), an
    event claiming generation 1 again must never be silently applied
    as new financial history -- "Reset sequence accepted as new
    financial history" is the case's own prohibited outcome."""
    _setup(db_session)
    gen2 = _envelope(event_id="evt-gen2-0", export_sequence=0, producer_generation=2)
    inbox_event_gen2 = ingest_export_event(db_session, gen2.model_dump_json())
    db_session.commit()
    assert inbox_event_gen2.applied_at is not None

    gen1_again = _envelope(event_id="evt-gen1-again-0", export_sequence=0, producer_generation=1)
    inbox_event_gen1 = ingest_export_event(db_session, gen1_again.model_dump_json())
    db_session.commit()

    assert inbox_event_gen1.applied_at is None
    assert inbox_event_gen1.ledger_entry_id is None
    assert inbox_event_gen1.parked_reason == "generation_rollback_detected:1"


def test_a_newer_generation_than_established_is_parked_pending_reconciled_bootstrap(db_session):
    """A generation bump LOOKS like a legitimate re-bootstrap, but this
    build has no real snapshot/manifest reconciliation (INT-008/INT-009,
    explicitly out of scope) -- it is never silently trusted and started
    fresh; it parks until an explicit bootstrap resolves it."""
    _setup(db_session)
    gen1 = _envelope(event_id="evt-gen1-0", export_sequence=0, producer_generation=1)
    inbox_event_gen1 = ingest_export_event(db_session, gen1.model_dump_json())
    db_session.commit()
    assert inbox_event_gen1.applied_at is not None

    gen2 = _envelope(event_id="evt-gen2-0", export_sequence=0, producer_generation=2)
    inbox_event_gen2 = ingest_export_event(db_session, gen2.model_dump_json())
    db_session.commit()

    assert inbox_event_gen2.applied_at is None
    assert inbox_event_gen2.ledger_entry_id is None
    assert inbox_event_gen2.parked_reason == "new_generation_requires_bootstrap:2"


def test_the_very_first_event_on_a_stream_establishes_whatever_generation_it_carries(db_session):
    """No generation is "established" yet on a brand-new stream -- the
    first event's own generation (even if it's not 1) becomes the
    baseline, not rejected as a mismatch against nothing."""
    _setup(db_session)
    envelope = _envelope(event_id="evt-gen5-0", export_sequence=0, producer_generation=5)
    inbox_event = ingest_export_event(db_session, envelope.model_dump_json())
    db_session.commit()

    assert inbox_event.applied_at is not None
    assert inbox_event.parked_reason is None


def test_reusing_a_sequence_within_the_same_generation_under_a_fresh_event_id_is_rejected(db_session):
    """A producer that reuses export_sequence 0 within the SAME
    generation, under a brand-new event_id, must never have that second
    event silently applied as if it were new financial history -- the
    unique (source_stream, producer_generation, export_sequence)
    constraint is the real fence; this is a clean, dedicated error, not
    a raw DB integrity violation leaking through."""
    _setup(db_session)
    first = _envelope(event_id="evt-first", export_sequence=0, producer_generation=1)
    ingest_export_event(db_session, first.model_dump_json())
    db_session.commit()

    second = _envelope(event_id="evt-second-different-id", export_sequence=0, producer_generation=1)
    try:
        ingest_export_event(db_session, second.model_dump_json())
        raised = False
    except SequenceSlotAlreadyConsumedError:
        raised = True
        db_session.rollback()
    assert raised

    ledger_entries = db_session.scalars(select(LedgerEntry).where(LedgerEntry.tenant_id == "tenant-a")).all()
    assert len(ledger_entries) == 1  # only the first event's own entry -- never a second


def test_two_generations_each_get_their_own_independent_sequence_numbering(db_session):
    """A legitimate new generation restarting export_sequence at 0 must
    never collide with (or be treated as behind) the previous
    generation's own sequence 0 -- they are different numbering spaces
    entirely, per signal_platform_contracts.EventEnvelope's own
    producer_generation docstring."""
    _setup(db_session)
    gen1_0 = _envelope(event_id="evt-gen1-0", export_sequence=0, producer_generation=1)
    ingest_export_event(db_session, gen1_0.model_dump_json())
    db_session.commit()

    # gen1 sequence 0 applied and established; a gen2 sequence 0 parks
    # as "requires reconciled bootstrap" (a different, honest outcome
    # from a rejection) -- proven above. This test's own point is that
    # the (source_stream, producer_generation, export_sequence) row for
    # gen2/0 can be durably STORED at all, alongside gen1/0, without a
    # uniqueness collision.
    gen2_0 = _envelope(event_id="evt-gen2-0", export_sequence=0, producer_generation=2)
    inbox_event_gen2 = ingest_export_event(db_session, gen2_0.model_dump_json())
    db_session.commit()

    assert inbox_event_gen2.export_sequence == 0
    assert inbox_event_gen2.producer_generation == 2
