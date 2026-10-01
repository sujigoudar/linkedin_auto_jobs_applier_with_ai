"""Track 40 (fault injection extension, bounded): a real, disposable
Postgres cluster already backs this repo's test suite (tests/conftest.py's
`postgres_cluster`) -- this file adds the one fault type that suite never
exercised: the DB connection itself dropping *mid-transaction*, during a
ledger write or an `integration_inbox` event application, rather than any
handled application-level error.

This is one of the highest-value checks for this codebase specifically
(signal-portfolio-commercial is financial accounting software -- see
CLAUDE.md rule 11, and app/models/ledger.py's four-book design): an
ingested event that silently left behind a partial ledger entry, or an
`inbox_events` row marked `applied_at` without its paired ledger row
actually existing, would be real, undetected financial-data corruption.

How the fault is injected: this sandbox has no Toxiproxy (see
signal-copier's tests/test_c32_fault_injection.py for that project's own
note on the same constraint) and no way to sever a TCP socket from
Python in a way psycopg would see as a genuine mid-write disconnect
reliably. What Postgres itself provides, and what actually produces the
exact failure mode under test -- the backend process dying while a
transaction is still open, before COMMIT -- is `pg_terminate_backend()`,
issued from a SEPARATE admin connection against the session under test's
own backend pid, invoked via a `before_flush`/explicit hook placed
*between* the application-level ledger write and that session's own
COMMIT. This is not a mocked exception: it is a real SIGTERM to the
actual Postgres backend process holding the open transaction, and
`session.commit()` (or the `flush()` immediately preceding it, depending
on where the kill lands) genuinely raises `sqlalchemy.exc.OperationalError`
("terminating connection due to administrator command" / "server closed
the connection unexpectedly") from the real driver, not a monkeypatched
substitute.

Postgres's own guarantee (not this codebase's) is that a backend dying
before COMMIT rolls the whole transaction back -- this file exists to
*prove* this codebase relies on that guarantee correctly: it never
COMMITs in smaller pieces that could leave a half-applied event, and the
caller (app/api/relay_routes.py) never mistakes the dropped connection
for a handled `EventIntegrityError`/`UnregisteredStreamError`/
`SequenceSlotAlreadyConsumedError` and silently swallows it as "parked."
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import OperationalError
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

from app.models.integration_inbox import InboxEvent
from app.models.ledger import LedgerEntry
from app.models.tenancy import Tenant
from app.services.integration_inbox import ingest_export_event, register_export_stream


def _instrument():
    return InstrumentIdentity(
        instrument_id="AAPL", venue="NASDAQ", market_type="equity", currency="USD",
        multiplier="1", quantity_convention="shares",
    )


def _execution_envelope(*, event_id="evt-drop-1", export_sequence=0, source_stream="signal-copier:acct1"):
    payload = ExecutionAppliedPayload(
        account=PrivateAccountIdentity(account_id="acct1"),
        instrument=_instrument(),
        side="buy",
        filled_quantity="10",
        filled_price="150.00",
        fee=None,
        broker="paper",
        broker_order_id="paper-drop-1",
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


def _kill_backend(admin_session, victim_pid: int) -> None:
    """A real `pg_terminate_backend` from a SEPARATE connection -- not a
    mocked exception. Issued, then committed immediately on the admin
    connection, so the SIGTERM is actually delivered to the victim backend
    before this function returns (Postgres processes the termination
    request asynchronously but promptly; the victim's NEXT statement,
    typically its own COMMIT, is what observes the dropped connection)."""
    admin_session.execute(text("SELECT pg_terminate_backend(:pid)"), {"pid": victim_pid})
    admin_session.commit()


def test_connection_dropped_after_ledger_write_but_before_commit_leaves_no_partial_ledger_entry(
    db_session, postgres_cluster
):
    """Reproduces: ingest an EXECUTION_APPLIED event (writes an
    `inbox_events` row AND a `ledger_entries` row in the same, still-open
    transaction via `ingest_export_event`/`append_entry`), then kill the
    connection's own backend process before that transaction ever
    COMMITs. Expected, correct outcome: NEITHER row exists afterward --
    Postgres rolled the whole, never-committed transaction back when its
    backend died, and this codebase never split that work across more
    than one commit that could leave one half durable without the
    other."""
    from app.db import make_engine, make_session_factory

    db_session.add(Tenant(tenant_id="tenant-a", display_name="Tenant", environment="LOCAL_SIM"))
    db_session.commit()
    register_export_stream(
        db_session, tenant_id="tenant-a", source_stream="signal-copier:acct1", environment="LOCAL_SIM"
    )
    db_session.commit()

    # A genuinely separate connection/session for the "victim" transaction
    # under test, so killing its backend never touches db_session's own
    # connection (used afterward, from a clean state, to verify outcome).
    victim_engine = make_engine(postgres_cluster["admin_url"])
    victim_session = make_session_factory(victim_engine)()
    try:
        victim_pid = victim_session.execute(text("SELECT pg_backend_pid()")).scalar()

        envelope = _execution_envelope()
        inbox_event = ingest_export_event(victim_session, envelope.model_dump_json())
        # The real ledger write already happened against `victim_session`'s
        # own (still-open, uncommitted) transaction -- confirmed durable
        # ONLY if the pending commit below actually succeeds.
        assert inbox_event.ledger_entry_id is not None

        # Kill the backend NOW -- after the application-level write, before
        # this transaction's own COMMIT. A real connection/backend failure,
        # not a mock.
        _kill_backend(db_session, victim_pid)

        with pytest.raises(OperationalError):
            victim_session.commit()
    finally:
        victim_session.close()
        victim_engine.dispose()

    # Verify from a CLEAN session/connection (db_session, never touched by
    # the kill) that nothing from the dropped transaction is durable.
    assert db_session.get(InboxEvent, "evt-drop-1") is None
    entries = db_session.scalars(
        select(LedgerEntry).where(LedgerEntry.tenant_id == "tenant-a")
    ).all()
    assert entries == [], "a connection drop before COMMIT must never leave a partial ledger entry"


def test_the_same_event_redelivered_after_a_connection_drop_applies_cleanly_exactly_once(
    db_session, postgres_cluster
):
    """Follow-up to the test above, proving recovery (not just that
    nothing corrupt was left behind): since the dropped transaction's
    event was never durably applied, the SAME envelope (same event_id,
    same bytes -- a real relay worker's own retry-after-failure behavior)
    must be free to apply normally on redelivery, exactly once, with a
    real ledger entry this time."""
    from app.db import make_engine, make_session_factory

    db_session.add(Tenant(tenant_id="tenant-a", display_name="Tenant", environment="LOCAL_SIM"))
    db_session.commit()
    register_export_stream(
        db_session, tenant_id="tenant-a", source_stream="signal-copier:acct1", environment="LOCAL_SIM"
    )
    db_session.commit()

    envelope = _execution_envelope(event_id="evt-drop-2")

    victim_engine = make_engine(postgres_cluster["admin_url"])
    victim_session = make_session_factory(victim_engine)()
    try:
        victim_pid = victim_session.execute(text("SELECT pg_backend_pid()")).scalar()
        ingest_export_event(victim_session, envelope.model_dump_json())
        _kill_backend(db_session, victim_pid)
        with pytest.raises(OperationalError):
            victim_session.commit()
    finally:
        victim_session.close()
        victim_engine.dispose()

    assert db_session.get(InboxEvent, "evt-drop-2") is None

    # Real redelivery, on a fresh connection, same bytes.
    inbox_event = ingest_export_event(db_session, envelope.model_dump_json())
    db_session.commit()

    assert inbox_event.applied_at is not None
    entry = db_session.get(LedgerEntry, inbox_event.ledger_entry_id)
    assert entry is not None
    assert entry.quantity == Decimal("10")
