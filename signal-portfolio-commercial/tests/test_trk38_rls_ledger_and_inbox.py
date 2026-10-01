"""TRK-38: extends tests/test_row_level_security.py's real, non-superuser
`app_role` unfiltered-query RLS proof pattern to two financially
load-bearing models that did not have it yet.

Cross-referencing every model under app/models/ with a `tenant_id`
column against the existing RLS test files
(test_row_level_security.py, test_rollback_recovery_rls.py,
test_require_tenant_scope_dependency.py, test_content_document.py,
test_relay_role_access.py) turned up real coverage for Product,
PortfolioVersion/PortfolioVersionSleeve, PublicationIntent, Sleeve,
CustomerProfile and ContentDocument -- but NOT for `LedgerEntry` (the
append-only four-book accounting journal, app/models/ledger.py -- the
single most financially load-bearing table in this service) or
`InboxEvent` (the integration inbox's own durable event log,
app/models/integration_inbox.py). Both are added here, same shape as
the existing tests: seed two tenants' rows directly via the superuser
`db_session` fixture, then issue a DELIBERATELY UNFILTERED `select(...)`
-- no `tenant_id ==` clause anywhere -- over a real `app_role` session
scoped to one tenant, and prove only that tenant's own rows come back.
This is specifically the scenario a bug would actually create: an
engineer forgets to filter a query, and the proof is that Postgres RLS
itself still saves them, never an application-level filter standing in
for it.
"""
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select

from app.db import set_tenant_scope
from app.models.integration_inbox import InboxEvent
from app.models.ledger import Book, LedgerEntry, ReconciliationState, Side
from app.models.tenancy import Tenant
from signal_platform_contracts import EvidenceClass


def _seed_two_tenants(db_session):
    db_session.add_all(
        [
            Tenant(tenant_id="tenant-a", display_name="A", environment="LOCAL_SIM"),
            Tenant(tenant_id="tenant-b", display_name="B", environment="LOCAL_SIM"),
        ]
    )
    db_session.commit()


def _seed_ledger_entry(db_session, *, tenant_id: str, entry_id: str) -> None:
    db_session.add(
        LedgerEntry(
            entry_id=entry_id,
            tenant_id=tenant_id,
            book=Book.PLATFORM,
            instrument="AAPL",
            side=Side.BUY,
            quantity=Decimal("10"),
            price=Decimal("150.00"),
            currency="USD",
            event_time=datetime.now(timezone.utc),
            source_authority=f"test-{tenant_id}",
            evidence_class=EvidenceClass.SYNTHETIC_FIXTURE,
            reconciliation_state=ReconciliationState.UNRECONCILED,
        )
    )
    db_session.commit()


def _seed_inbox_event(db_session, *, tenant_id: str, event_id: str) -> None:
    db_session.add(
        InboxEvent(
            event_id=event_id,
            tenant_id=tenant_id,
            event_type="EXECUTION_APPLIED",
            source_stream=f"signal-copier:{tenant_id}",
            producer_generation=1,
            export_sequence=0,
            envelope_json="{}",
            payload_hash="deadbeef",
            received_at=datetime.now(timezone.utc),
        )
    )
    db_session.commit()


def test_a_tenant_scoped_session_cannot_see_another_tenants_ledger_entries_via_an_unfiltered_query(
    db_session, tenant_session_factory
):
    """The append-only four-book accounting journal: a real app_role
    session scoped to tenant-a, issuing a bare `select(LedgerEntry)`
    with NO tenant_id filter at all, must still only see tenant-a's own
    entries -- proving isolation here relies on real Postgres RLS, not
    on every caller remembering to add `.where(LedgerEntry.tenant_id ==
    ...)` by hand."""
    _seed_two_tenants(db_session)
    _seed_ledger_entry(db_session, tenant_id="tenant-a", entry_id="entry-a-1")
    _seed_ledger_entry(db_session, tenant_id="tenant-b", entry_id="entry-b-1")

    session = tenant_session_factory()
    try:
        set_tenant_scope(session, "tenant-a")
        rows = session.scalars(select(LedgerEntry)).all()
        assert {row.entry_id for row in rows} == {"entry-a-1"}
        assert {row.tenant_id for row in rows} == {"tenant-a"}
    finally:
        session.close()


def test_a_tenant_scoped_session_cannot_read_another_tenants_ledger_entry_by_primary_key(
    db_session, tenant_session_factory
):
    _seed_two_tenants(db_session)
    _seed_ledger_entry(db_session, tenant_id="tenant-a", entry_id="entry-a-pk")
    _seed_ledger_entry(db_session, tenant_id="tenant-b", entry_id="entry-b-pk")

    session = tenant_session_factory()
    try:
        set_tenant_scope(session, "tenant-a")
        assert session.get(LedgerEntry, "entry-b-pk") is None
        own = session.get(LedgerEntry, "entry-a-pk")
        assert own is not None and own.tenant_id == "tenant-a"
    finally:
        session.close()


def test_no_tenant_scope_set_means_no_ledger_entries_visible_not_all_entries(
    db_session, tenant_session_factory
):
    """Fail-closed: an `app_role` session with NO `set_tenant_scope` call
    at all must see zero ledger rows, never every tenant's rows -- the
    same fail-closed shape test_row_level_security.py's
    `test_no_tenant_scope_set_means_no_rows_visible_not_all_rows` proves
    for Product."""
    _seed_two_tenants(db_session)
    _seed_ledger_entry(db_session, tenant_id="tenant-a", entry_id="entry-a-unscoped")
    _seed_ledger_entry(db_session, tenant_id="tenant-b", entry_id="entry-b-unscoped")

    session = tenant_session_factory()
    try:
        rows = session.scalars(select(LedgerEntry)).all()
        assert rows == []
    finally:
        session.close()


def test_a_tenant_scoped_session_cannot_see_another_tenants_inbox_events_via_an_unfiltered_query(
    db_session, tenant_session_factory
):
    """The integration inbox's own durable event log: same unfiltered-
    query proof as LedgerEntry above, for `InboxEvent`."""
    _seed_two_tenants(db_session)
    _seed_inbox_event(db_session, tenant_id="tenant-a", event_id="evt-a-1")
    _seed_inbox_event(db_session, tenant_id="tenant-b", event_id="evt-b-1")

    session = tenant_session_factory()
    try:
        set_tenant_scope(session, "tenant-a")
        rows = session.scalars(select(InboxEvent)).all()
        assert {row.event_id for row in rows} == {"evt-a-1"}
        assert {row.tenant_id for row in rows} == {"tenant-a"}
    finally:
        session.close()


def test_a_tenant_scoped_session_cannot_read_another_tenants_inbox_event_by_primary_key(
    db_session, tenant_session_factory
):
    _seed_two_tenants(db_session)
    _seed_inbox_event(db_session, tenant_id="tenant-a", event_id="evt-a-pk")
    _seed_inbox_event(db_session, tenant_id="tenant-b", event_id="evt-b-pk")

    session = tenant_session_factory()
    try:
        set_tenant_scope(session, "tenant-a")
        assert session.get(InboxEvent, "evt-b-pk") is None
        own = session.get(InboxEvent, "evt-a-pk")
        assert own is not None and own.tenant_id == "tenant-a"
    finally:
        session.close()


def test_no_tenant_scope_set_means_no_inbox_events_visible_not_all_events(
    db_session, tenant_session_factory
):
    _seed_two_tenants(db_session)
    _seed_inbox_event(db_session, tenant_id="tenant-a", event_id="evt-a-unscoped")
    _seed_inbox_event(db_session, tenant_id="tenant-b", event_id="evt-b-unscoped")

    session = tenant_session_factory()
    try:
        rows = session.scalars(select(InboxEvent)).all()
        assert rows == []
    finally:
        session.close()
