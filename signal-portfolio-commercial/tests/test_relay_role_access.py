"""app/db.py's `_apply_relay_role_access` -- exercised as a real
Postgres row-level-security policy under a genuine non-superuser
`relay_role` login (tests/conftest.py's `relay_session_factory`), the
same "test DB row policies independently" discipline
tests/test_row_level_security.py already applies to `app_role`.

This is the load-bearing property slice 4 explicitly deferred: a
restricted role can look up ANY tenant's `export_stream_registrations`
row by `source_stream` (needed to discover a tenant before any scope
exists), but can never read or write another tenant's `inbox_events`
or `ledger_entries` once scoped."""
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.exc import ProgrammingError
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

from app.db import set_tenant_scope
from app.models.integration_inbox import ExportStreamRegistration, InboxEvent
from app.models.ledger import LedgerEntry
from app.models.tenancy import Tenant
from app.services.integration_inbox import ingest_export_event, register_export_stream


def _seed_tenant(db_session, tenant_id):
    db_session.add(Tenant(tenant_id=tenant_id, display_name=tenant_id, environment="LOCAL_SIM"))
    db_session.commit()


def _execution_envelope(*, event_id, source_stream):
    instrument = InstrumentIdentity(
        instrument_id="AAPL", venue="NASDAQ", market_type="equity", currency="USD",
        multiplier="1", quantity_convention="shares",
    )
    payload = ExecutionAppliedPayload(
        account=PrivateAccountIdentity(account_id="acct1"),
        instrument=instrument,
        side="buy",
        filled_quantity="10",
        filled_price="150.00",
        fee=None,
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
        export_sequence=0,
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


def test_relay_role_can_look_up_a_registration_for_any_tenant_without_scope_set(db_session, relay_session_factory):
    _seed_tenant(db_session, "tenant-a")
    _seed_tenant(db_session, "tenant-b")
    register_export_stream(db_session, tenant_id="tenant-a", source_stream="signal-copier:a", environment="LOCAL_SIM")
    register_export_stream(db_session, tenant_id="tenant-b", source_stream="signal-copier:b", environment="LOCAL_SIM")
    db_session.commit()

    relay_session = relay_session_factory()
    try:
        # No set_tenant_scope call at all -- this is the exact lookup
        # ingest_export_event needs before it knows any tenant.
        rows = relay_session.scalars(select(ExportStreamRegistration)).all()
        assert {row.source_stream for row in rows} == {"signal-copier:a", "signal-copier:b"}
    finally:
        relay_session.rollback()
        relay_session.close()


def test_relay_role_cannot_insert_an_inbox_event_for_a_tenant_it_has_not_scoped_to(db_session, relay_session_factory):
    _seed_tenant(db_session, "tenant-a")
    _seed_tenant(db_session, "tenant-b")
    db_session.commit()

    relay_session = relay_session_factory()
    try:
        set_tenant_scope(relay_session, "tenant-a")
        relay_session.add(
            InboxEvent(
                event_id="evt-cross-tenant",
                tenant_id="tenant-b",
                event_type="EXECUTION_APPLIED",
                source_stream="signal-copier:b",
                export_sequence=0,
                envelope_json="{}",
                payload_hash="deadbeef",
            )
        )
        with pytest.raises(ProgrammingError):
            relay_session.flush()
    finally:
        relay_session.rollback()
        relay_session.close()


def test_ingest_export_event_end_to_end_through_relay_role_populates_the_correct_tenant_only(
    db_session, relay_session_factory
):
    _seed_tenant(db_session, "tenant-a")
    _seed_tenant(db_session, "tenant-b")
    register_export_stream(db_session, tenant_id="tenant-a", source_stream="signal-copier:acct1", environment="LOCAL_SIM")
    db_session.commit()

    relay_session = relay_session_factory()
    try:
        envelope = _execution_envelope(event_id="evt-relay-1", source_stream="signal-copier:acct1")
        inbox_event = ingest_export_event(relay_session, envelope.model_dump_json())
        # Read before commit -- set_tenant_scope's is_local=true setting
        # resets at commit, and a post-commit attribute access would
        # trigger a refresh SELECT with no scope set (see
        # app/api/relay_routes.py's own comment on this exact trap).
        tenant_id, ledger_entry_id = inbox_event.tenant_id, inbox_event.ledger_entry_id
        relay_session.commit()

        assert tenant_id == "tenant-a"
        assert ledger_entry_id is not None
    finally:
        relay_session.close()

    # Verify from the admin session -- the row really landed under
    # tenant-a, and nothing leaked into tenant-b.
    entries_a = db_session.scalars(select(LedgerEntry).where(LedgerEntry.tenant_id == "tenant-a")).all()
    entries_b = db_session.scalars(select(LedgerEntry).where(LedgerEntry.tenant_id == "tenant-b")).all()
    assert len(entries_a) == 1
    assert entries_a[0].quantity == Decimal("10")
    assert entries_b == []
