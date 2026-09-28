"""Signal Platform Integration Correction Pack's own INTEGRATION_DECISION.md
S4.4/S6 -- app/services/integration_inbox.py's own tests. Real Postgres,
real tenant-scoped session."""
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select
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

from app.models.ledger import Book, LedgerEntry
from app.models.tenancy import Tenant
from app.services.integration_inbox import (
    EventIntegrityError,
    StreamAlreadyRegisteredToAnotherTenantError,
    UnregisteredStreamError,
    ingest_export_event,
    register_export_stream,
)


def _seed_tenant(db_session, tenant_id="tenant-a"):
    if db_session.get(Tenant, tenant_id) is None:
        db_session.add(Tenant(tenant_id=tenant_id, display_name="Tenant", environment="LOCAL_SIM"))
        db_session.commit()


def _instrument():
    return InstrumentIdentity(
        instrument_id="AAPL", venue="NASDAQ", market_type="equity", currency="USD",
        multiplier="1", quantity_convention="shares",
    )


def _execution_envelope(
    *, event_id="evt-1", export_sequence=0, source_stream="signal-copier:acct1", fee=None, filled_price="150.00"
):
    payload = ExecutionAppliedPayload(
        account=PrivateAccountIdentity(account_id="acct1"),
        instrument=_instrument(),
        side="buy",
        filled_quantity="10",
        filled_price=filled_price,
        fee=fee,
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


def _source_receipt_envelope(*, event_id="evt-src-1", export_sequence=0, source_stream="signal-copier:acct1"):
    payload = SourceReceiptPayload(
        source=SourceIdentity(source_provider_id="telegram", parser_version="v3", source_event_id="src-evt-1"),
        instrument=_instrument(),
        side="buy",
        quantity="10",
        price="150.00",
    )
    payload_dict = payload.model_dump(mode="json")
    now = datetime.now(timezone.utc)
    return EventEnvelope(
        event_type=EventType.SOURCE_RECEIPT,
        event_id=event_id,
        producer_id="signal-copier-instance-1",
        source_stream=source_stream,
        export_sequence=export_sequence,
        subject=build_subject(source=SourceIdentity(
            source_provider_id="telegram", parser_version="v3", source_event_id="src-evt-1",
        )),
        event_time=now,
        effective_time=now,
        availability_time=now,
        receipt_time=now,
        environment=Environment.LOCAL_SIM,
        evidence_class=EvidenceClass.SYNTHETIC_FIXTURE,
        payload_hash=compute_payload_hash(payload_dict),
        payload=payload_dict,
    )


def test_register_export_stream_persists_the_registration(db_session):
    _seed_tenant(db_session)
    registration = register_export_stream(
        db_session, tenant_id="tenant-a", source_stream="signal-copier:acct1", environment="LOCAL_SIM",
    )
    db_session.commit()
    assert registration.tenant_id == "tenant-a"


def test_registering_the_same_stream_twice_for_the_same_tenant_is_idempotent(db_session):
    _seed_tenant(db_session)
    first = register_export_stream(
        db_session, tenant_id="tenant-a", source_stream="signal-copier:acct1", environment="LOCAL_SIM",
    )
    db_session.commit()
    second = register_export_stream(
        db_session, tenant_id="tenant-a", source_stream="signal-copier:acct1", environment="LOCAL_SIM",
    )
    assert first.registration_id == second.registration_id


def test_registering_the_same_stream_for_a_different_tenant_is_refused(db_session):
    _seed_tenant(db_session, "tenant-a")
    _seed_tenant(db_session, "tenant-b")
    register_export_stream(db_session, tenant_id="tenant-a", source_stream="signal-copier:acct1", environment="LOCAL_SIM")
    db_session.commit()

    with pytest.raises(StreamAlreadyRegisteredToAnotherTenantError):
        register_export_stream(db_session, tenant_id="tenant-b", source_stream="signal-copier:acct1", environment="LOCAL_SIM")


def test_ingest_execution_applied_event_is_refused_for_an_unregistered_stream(db_session):
    _seed_tenant(db_session)
    envelope = _execution_envelope()
    with pytest.raises(UnregisteredStreamError):
        ingest_export_event(db_session, envelope.model_dump_json())


def test_ingest_execution_applied_event_creates_a_real_platform_ledger_entry(db_session):
    _seed_tenant(db_session)
    register_export_stream(db_session, tenant_id="tenant-a", source_stream="signal-copier:acct1", environment="LOCAL_SIM")
    db_session.commit()

    envelope = _execution_envelope(fee="1.50")
    inbox_event = ingest_export_event(db_session, envelope.model_dump_json())
    db_session.commit()

    assert inbox_event.tenant_id == "tenant-a"
    assert inbox_event.applied_at is not None
    assert inbox_event.ledger_entry_id is not None

    entry = db_session.get(LedgerEntry, inbox_event.ledger_entry_id)
    assert entry is not None
    assert entry.tenant_id == "tenant-a"
    assert entry.book == Book.PLATFORM
    assert entry.instrument == "AAPL"
    assert entry.quantity == Decimal("10")
    assert entry.price == Decimal("150.00")
    assert entry.fee == Decimal("1.50")
    assert entry.evidence_class == EvidenceClass.INTERNAL_PAPER


def test_ingest_execution_applied_event_with_unknown_fee_leaves_ledger_fee_none(db_session):
    _seed_tenant(db_session)
    register_export_stream(db_session, tenant_id="tenant-a", source_stream="signal-copier:acct1", environment="LOCAL_SIM")
    db_session.commit()

    envelope = _execution_envelope()  # fee=None
    inbox_event = ingest_export_event(db_session, envelope.model_dump_json())
    db_session.commit()

    entry = db_session.get(LedgerEntry, inbox_event.ledger_entry_id)
    assert entry.fee is None


def test_ingest_source_receipt_event_creates_no_ledger_entry(db_session):
    _seed_tenant(db_session)
    register_export_stream(db_session, tenant_id="tenant-a", source_stream="signal-copier:acct1", environment="LOCAL_SIM")
    db_session.commit()

    envelope = _source_receipt_envelope()
    inbox_event = ingest_export_event(db_session, envelope.model_dump_json())
    db_session.commit()

    assert inbox_event.ledger_entry_id is None
    assert inbox_event.applied_at is not None


def test_reingesting_the_identical_event_is_a_harmless_no_op(db_session):
    _seed_tenant(db_session)
    register_export_stream(db_session, tenant_id="tenant-a", source_stream="signal-copier:acct1", environment="LOCAL_SIM")
    db_session.commit()

    envelope = _execution_envelope(event_id="evt-dup")
    first = ingest_export_event(db_session, envelope.model_dump_json())
    db_session.commit()
    second = ingest_export_event(db_session, envelope.model_dump_json())
    db_session.commit()

    assert first.event_id == second.event_id
    assert first.ledger_entry_id == second.ledger_entry_id
    ledger_entries = db_session.scalars(select(LedgerEntry).where(LedgerEntry.tenant_id == "tenant-a")).all()
    assert len(ledger_entries) == 1


def test_reingesting_the_same_event_id_with_a_different_payload_raises(db_session):
    """S6: "The same identity with a different hash is an integrity
    incident, not last-write-wins.\""""
    _seed_tenant(db_session)
    register_export_stream(db_session, tenant_id="tenant-a", source_stream="signal-copier:acct1", environment="LOCAL_SIM")
    db_session.commit()

    original = _execution_envelope(event_id="evt-tamper", filled_price="150.00")
    ingest_export_event(db_session, original.model_dump_json())
    db_session.commit()

    tampered = _execution_envelope(event_id="evt-tamper", filled_price="999.00")
    with pytest.raises(EventIntegrityError):
        ingest_export_event(db_session, tampered.model_dump_json())


def test_two_tenants_streams_never_leak_ledger_entries_across_tenants(db_session):
    _seed_tenant(db_session, "tenant-a")
    _seed_tenant(db_session, "tenant-b")
    register_export_stream(db_session, tenant_id="tenant-a", source_stream="signal-copier:acct-a", environment="LOCAL_SIM")
    register_export_stream(db_session, tenant_id="tenant-b", source_stream="signal-copier:acct-b", environment="LOCAL_SIM")
    db_session.commit()

    envelope_a = _execution_envelope(event_id="evt-a", source_stream="signal-copier:acct-a")
    ingest_export_event(db_session, envelope_a.model_dump_json())
    db_session.commit()

    tenant_b_entries = db_session.scalars(select(LedgerEntry).where(LedgerEntry.tenant_id == "tenant-b")).all()
    assert tenant_b_entries == []
