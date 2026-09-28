"""INTEGRATION_ACCEPTANCE_CASES.json INT-008 "Snapshot and delta overlap" /
INT-009 "Interrupted bootstrap resumes" -- app/services/integration_inbox.py's
own bootstrap-snapshot manifest mechanism. Real Postgres, real tenant-scoped
session, exercised entirely through `ingest_export_event`."""
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from signal_platform_contracts import (
    Environment,
    EventEnvelope,
    EventType,
    EvidenceClass,
    InstrumentIdentity,
    PositionSnapshotEntry,
    PositionSnapshotPayload,
    PrivateAccountIdentity,
    build_subject,
    compute_payload_hash,
)

from app.models.integration_inbox import InboxEvent
from app.models.ledger import Book, LedgerEntry
from app.models.tenancy import Tenant
from app.services.integration_inbox import ingest_export_event, register_export_stream
from tests.test_integration_inbox import _execution_envelope


def _seed_tenant(db_session, tenant_id="tenant-a"):
    if db_session.get(Tenant, tenant_id) is None:
        db_session.add(Tenant(tenant_id=tenant_id, display_name="Tenant", environment="LOCAL_SIM"))
        db_session.commit()


def _instrument(instrument_id="AAPL"):
    return InstrumentIdentity(
        instrument_id=instrument_id, venue="NASDAQ", market_type="equity", currency="USD",
        multiplier="1", quantity_convention="shares",
    )


def _snapshot_envelope(
    *,
    event_id,
    export_sequence,
    manifest_id,
    page_index,
    page_count,
    cutoff_sequence,
    positions,
    source_stream="signal-copier:acct1",
    producer_generation=1,
):
    payload = PositionSnapshotPayload(
        manifest_id=manifest_id, cutoff_sequence=cutoff_sequence, page_index=page_index,
        page_count=page_count, positions=positions,
    )
    payload_dict = payload.model_dump(mode="json")
    now = datetime.now(timezone.utc)
    return EventEnvelope(
        event_type=EventType.POSITION_SNAPSHOT,
        event_id=event_id,
        producer_id="signal-copier-instance-1",
        source_stream=source_stream,
        producer_generation=producer_generation,
        export_sequence=export_sequence,
        subject=build_subject(account=PrivateAccountIdentity(account_id="acct1")),
        event_time=now,
        effective_time=now,
        availability_time=now,
        receipt_time=now,
        environment=Environment.LOCAL_SIM,
        evidence_class=EvidenceClass.SYNTHETIC_FIXTURE,
        payload_hash=compute_payload_hash(payload_dict),
        payload=payload_dict,
    )


def _position(instrument_id="AAPL", side="buy", quantity="10", average_cost="100.00"):
    return PositionSnapshotEntry(
        instrument=_instrument(instrument_id), side=side, quantity=quantity, average_cost=average_cost,
    )


def test_a_single_page_manifest_activates_immediately_and_creates_a_platform_baseline_entry(db_session):
    _seed_tenant(db_session)
    register_export_stream(db_session, tenant_id="tenant-a", source_stream="signal-copier:acct1", environment="LOCAL_SIM")
    db_session.commit()

    envelope = _snapshot_envelope(
        event_id="evt-snap-0", export_sequence=0, manifest_id="manifest-1", page_index=0, page_count=1,
        cutoff_sequence=100, positions=[_position()],
    )
    inbox_event = ingest_export_event(db_session, envelope.model_dump_json())
    db_session.commit()

    assert inbox_event.applied_at is not None
    entries = db_session.scalars(select(LedgerEntry).where(LedgerEntry.tenant_id == "tenant-a")).all()
    assert len(entries) == 1
    assert entries[0].book == Book.PLATFORM
    assert entries[0].instrument == "AAPL"
    assert entries[0].quantity == Decimal("10")
    assert entries[0].price == Decimal("100.00")
    assert entries[0].source_authority == "signal-copier-snapshot-relay:signal-copier-instance-1"


def test_a_multi_page_manifest_never_activates_until_every_page_arrives(db_session):
    """INT-009's own "Partial snapshot labeled complete" is prohibited:
    receiving page 0 of a 2-page manifest must leave it received but
    unapplied, and must NOT create any ledger entry yet."""
    _seed_tenant(db_session)
    register_export_stream(db_session, tenant_id="tenant-a", source_stream="signal-copier:acct1", environment="LOCAL_SIM")
    db_session.commit()

    page_0 = _snapshot_envelope(
        event_id="evt-snap-0", export_sequence=0, manifest_id="manifest-2", page_index=0, page_count=2,
        cutoff_sequence=100, positions=[_position("AAPL")],
    )
    inbox_event = ingest_export_event(db_session, page_0.model_dump_json())
    db_session.commit()

    assert inbox_event.applied_at is None
    assert inbox_event.parked_reason is None  # not an error -- just awaiting the rest of the manifest
    assert db_session.scalars(select(LedgerEntry).where(LedgerEntry.tenant_id == "tenant-a")).all() == []

    page_1 = _snapshot_envelope(
        event_id="evt-snap-1", export_sequence=1, manifest_id="manifest-2", page_index=1, page_count=2,
        cutoff_sequence=100, positions=[_position("MSFT")],
    )
    second_inbox_event = ingest_export_event(db_session, page_1.model_dump_json())
    db_session.commit()

    assert second_inbox_event.applied_at is not None
    db_session.refresh(inbox_event)
    assert inbox_event.applied_at is not None  # page 0 activates retroactively, alongside page 1

    entries = db_session.scalars(select(LedgerEntry).where(LedgerEntry.tenant_id == "tenant-a")).all()
    assert {e.instrument for e in entries} == {"AAPL", "MSFT"}


def test_a_page_claiming_a_different_generation_than_its_siblings_is_rejected(db_session):
    """INT-009's own "Attempt to mix a page from another generation...
    rejected." -- never silently applied or reconciled."""
    _seed_tenant(db_session)
    register_export_stream(db_session, tenant_id="tenant-a", source_stream="signal-copier:acct1", environment="LOCAL_SIM")
    db_session.commit()

    page_0 = _snapshot_envelope(
        event_id="evt-snap-0", export_sequence=0, manifest_id="manifest-gen", page_index=0, page_count=2,
        cutoff_sequence=100, positions=[_position()], producer_generation=1,
    )
    ingest_export_event(db_session, page_0.model_dump_json())
    db_session.commit()

    rogue_page_1 = _snapshot_envelope(
        event_id="evt-snap-1", export_sequence=0, manifest_id="manifest-gen", page_index=1, page_count=2,
        cutoff_sequence=100, positions=[_position("MSFT")], producer_generation=2,
    )
    rogue_inbox_event = ingest_export_event(db_session, rogue_page_1.model_dump_json())
    db_session.commit()

    assert rogue_inbox_event.applied_at is None
    assert rogue_inbox_event.parked_reason == "manifest_generation_mismatch:manifest-gen"
    assert db_session.scalars(select(LedgerEntry).where(LedgerEntry.tenant_id == "tenant-a")).all() == []


def test_a_page_claiming_different_cutoff_or_page_count_than_its_siblings_is_rejected(db_session):
    """Mismatched manifest metadata (cutoff_sequence/page_count
    disagreeing across pages of the SAME manifest_id) is never silently
    reconciled by trusting the latest one."""
    _seed_tenant(db_session)
    register_export_stream(db_session, tenant_id="tenant-a", source_stream="signal-copier:acct1", environment="LOCAL_SIM")
    db_session.commit()

    page_0 = _snapshot_envelope(
        event_id="evt-snap-0", export_sequence=0, manifest_id="manifest-meta", page_index=0, page_count=2,
        cutoff_sequence=100, positions=[_position()],
    )
    ingest_export_event(db_session, page_0.model_dump_json())
    db_session.commit()

    conflicting_page_1 = _snapshot_envelope(
        event_id="evt-snap-1", export_sequence=1, manifest_id="manifest-meta", page_index=1, page_count=2,
        cutoff_sequence=999, positions=[_position("MSFT")],  # disagrees with page 0's own cutoff_sequence=100
    )
    conflicting_inbox_event = ingest_export_event(db_session, conflicting_page_1.model_dump_json())
    db_session.commit()

    assert conflicting_inbox_event.applied_at is None
    assert conflicting_inbox_event.parked_reason == "manifest_metadata_mismatch:manifest-meta"


def test_overlap_events_at_or_below_cutoff_are_applied_without_a_second_ledger_entry(db_session):
    """INT-008's own "Snapshot and delta overlap": deltas 0..100 the
    snapshot's own baseline already covers must still advance
    `applied_at` (they are not lost/parked forever) but must never
    create a SECOND ledger entry for economics the baseline already
    reflects. A delta above the cutoff (101) is a genuinely new fact and
    DOES get its own ledger entry."""
    _seed_tenant(db_session)
    register_export_stream(db_session, tenant_id="tenant-a", source_stream="signal-copier:acct1", environment="LOCAL_SIM")
    db_session.commit()

    # An ordinary delta, sequence 5, arrives BEFORE the snapshot -- parked
    # (out of order: expected sequence is 0, since nothing applied yet).
    overlap_delta = _execution_envelope(event_id="evt-overlap", export_sequence=5, broker_order_id="order-overlap")
    overlap_inbox_event = ingest_export_event(db_session, overlap_delta.model_dump_json())
    db_session.commit()
    assert overlap_inbox_event.applied_at is None  # parked behind the gap at sequence 0

    # The bootstrap snapshot itself lands at sequence 0, cutoff_sequence=100.
    snapshot = _snapshot_envelope(
        event_id="evt-snap-0", export_sequence=0, manifest_id="manifest-overlap", page_index=0, page_count=1,
        cutoff_sequence=100, positions=[_position()],
    )
    ingest_export_event(db_session, snapshot.model_dump_json())
    db_session.commit()

    db_session.refresh(overlap_inbox_event)
    assert overlap_inbox_event.applied_at is not None  # swept as covered-by-snapshot
    assert overlap_inbox_event.ledger_entry_id is None  # no second ledger entry

    entries_after_overlap = db_session.scalars(select(LedgerEntry).where(LedgerEntry.tenant_id == "tenant-a")).all()
    assert len(entries_after_overlap) == 1  # only the snapshot's own baseline entry

    # A delta ABOVE the cutoff is a real new fact and gets its own entry.
    above_cutoff_delta = _execution_envelope(
        event_id="evt-above-cutoff", export_sequence=101, broker_order_id="order-above-cutoff",
    )
    above_cutoff_inbox_event = ingest_export_event(db_session, above_cutoff_delta.model_dump_json())
    db_session.commit()

    assert above_cutoff_inbox_event.applied_at is not None
    assert above_cutoff_inbox_event.ledger_entry_id is not None
    entries_after_above_cutoff = db_session.scalars(
        select(LedgerEntry).where(LedgerEntry.tenant_id == "tenant-a")
    ).all()
    assert len(entries_after_above_cutoff) == 2


def test_snapshot_activation_raises_the_floor_so_next_expected_sequence_never_regresses(db_session):
    """Once a snapshot with cutoff_sequence=100 activates, a LATE-arriving
    delta at sequence 50 (below the floor) must be swept as covered, and
    the stream's own next-expected-sequence must never fall back to 51
    -- INT-008's "immutable manifest cutoff" survives redelivery/timing
    noise."""
    _seed_tenant(db_session)
    register_export_stream(db_session, tenant_id="tenant-a", source_stream="signal-copier:acct1", environment="LOCAL_SIM")
    db_session.commit()

    snapshot = _snapshot_envelope(
        event_id="evt-snap-0", export_sequence=0, manifest_id="manifest-floor", page_index=0, page_count=1,
        cutoff_sequence=100, positions=[_position()],
    )
    ingest_export_event(db_session, snapshot.model_dump_json())
    db_session.commit()

    late_delta = _execution_envelope(event_id="evt-late", export_sequence=50, broker_order_id="order-late")
    late_inbox_event = ingest_export_event(db_session, late_delta.model_dump_json())
    db_session.commit()

    assert late_inbox_event.applied_at is not None
    assert late_inbox_event.ledger_entry_id is None

    entries = db_session.scalars(select(LedgerEntry).where(LedgerEntry.tenant_id == "tenant-a")).all()
    assert len(entries) == 1  # only the snapshot's own baseline entry -- never a second one for sequence 50


def test_resuming_an_interrupted_bootstrap_activates_only_once_the_last_page_lands(db_session):
    """INT-009 "Interrupted bootstrap resumes": pages can arrive across
    separate ingest calls (simulating a relay worker restart between
    them) -- the manifest activates the moment its own last missing page
    is finally delivered, regardless of which page index that happens
    to be."""
    _seed_tenant(db_session)
    register_export_stream(db_session, tenant_id="tenant-a", source_stream="signal-copier:acct1", environment="LOCAL_SIM")
    db_session.commit()

    page_1 = _snapshot_envelope(
        event_id="evt-snap-1", export_sequence=1, manifest_id="manifest-resume", page_index=1, page_count=3,
        cutoff_sequence=200, positions=[_position("MSFT")],
    )
    ingest_export_event(db_session, page_1.model_dump_json())
    db_session.commit()

    page_0 = _snapshot_envelope(
        event_id="evt-snap-0", export_sequence=0, manifest_id="manifest-resume", page_index=0, page_count=3,
        cutoff_sequence=200, positions=[_position("AAPL")],
    )
    ingest_export_event(db_session, page_0.model_dump_json())
    db_session.commit()

    entries_mid_resume = db_session.scalars(select(LedgerEntry).where(LedgerEntry.tenant_id == "tenant-a")).all()
    assert entries_mid_resume == []  # still missing page 2 -- never activates on 2 of 3

    page_2 = _snapshot_envelope(
        event_id="evt-snap-2", export_sequence=2, manifest_id="manifest-resume", page_index=2, page_count=3,
        cutoff_sequence=200, positions=[_position("GOOG")],
    )
    ingest_export_event(db_session, page_2.model_dump_json())
    db_session.commit()

    entries_after_completion = db_session.scalars(select(LedgerEntry).where(LedgerEntry.tenant_id == "tenant-a")).all()
    assert {e.instrument for e in entries_after_completion} == {"AAPL", "MSFT", "GOOG"}

    all_pages = db_session.scalars(
        select(InboxEvent).where(InboxEvent.tenant_id == "tenant-a", InboxEvent.manifest_id == "manifest-resume")
    ).all()
    assert len(all_pages) == 3
    assert all(page.applied_at is not None for page in all_pages)
