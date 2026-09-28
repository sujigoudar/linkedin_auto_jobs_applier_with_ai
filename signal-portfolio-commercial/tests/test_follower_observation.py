"""app/services/follower_observation.py -- S12 step 6's real half:
normalized-observation ingestion into Book.FOLLOWER, idempotent by
external_observation_id."""
from datetime import datetime, timezone
from decimal import Decimal

from app.models.ledger import Book, EvidenceClass, LedgerEntry, Side
from app.models.tenancy import Membership, MembershipRole, Tenant, UserIdentity
from app.services.follower_observation import ObservedFill, ingest_observed_fill
from app.services.platform_connection import create_platform_connection

_T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _seed_membership(db_session, *, tenant_id="tenant-a", user_id="user-a"):
    if db_session.get(Tenant, tenant_id) is None:
        db_session.add(Tenant(tenant_id=tenant_id, display_name="Tenant", environment="LOCAL_SIM"))
        db_session.flush()
    db_session.add(UserIdentity(user_id=user_id, email=f"{user_id}@example.com"))
    db_session.flush()
    db_session.add(Membership(tenant_id=tenant_id, user_id=user_id, role=MembershipRole.CUSTOMER))
    db_session.commit()


def _connection(db_session, *, tenant_id="tenant-a", user_id="user-a", platform="collective2"):
    return create_platform_connection(
        db_session, tenant_id=tenant_id, user_id=user_id, platform=platform,
        environment="local_simulation", masked_account_label=f"{platform} ****1234",
    )


def _fill(*, instrument="AAPL", side=Side.BUY, quantity="10", price="150.00", external_observation_id="obs-1", fee=None):
    return ObservedFill(
        instrument=instrument, side=side, quantity=Decimal(quantity), price=Decimal(price), currency="USD",
        executed_at=_T0, external_observation_id=external_observation_id, fee=None if fee is None else Decimal(fee),
    )


def test_a_real_observation_creates_a_follower_book_entry(db_session):
    _seed_membership(db_session)
    connection = _connection(db_session)
    db_session.commit()

    entry = ingest_observed_fill(
        db_session, tenant_id="tenant-a", connection=connection, fill=_fill(),
        evidence_class=EvidenceClass.OBSERVED_FOLLOWER_LIVE, source_authority="test:collective2",
    )
    db_session.commit()

    assert entry.book == Book.FOLLOWER
    assert entry.follower_connection_id == connection.connection_id
    assert entry.instrument == "AAPL"
    assert entry.quantity == Decimal("10")
    assert entry.external_observation_id == "obs-1"
    assert entry.evidence_class == EvidenceClass.OBSERVED_FOLLOWER_LIVE


def test_reingesting_the_identical_observation_is_a_harmless_no_op(db_session):
    _seed_membership(db_session)
    connection = _connection(db_session)
    db_session.commit()

    first = ingest_observed_fill(
        db_session, tenant_id="tenant-a", connection=connection, fill=_fill(external_observation_id="obs-dup"),
        evidence_class=EvidenceClass.OBSERVED_FOLLOWER_LIVE, source_authority="test:collective2",
    )
    db_session.commit()
    second = ingest_observed_fill(
        db_session, tenant_id="tenant-a", connection=connection, fill=_fill(external_observation_id="obs-dup"),
        evidence_class=EvidenceClass.OBSERVED_FOLLOWER_LIVE, source_authority="test:collective2",
    )
    db_session.commit()

    assert first.entry_id == second.entry_id
    entries = db_session.query(LedgerEntry).filter(
        LedgerEntry.tenant_id == "tenant-a", LedgerEntry.external_observation_id == "obs-dup",
    ).all()
    assert len(entries) == 1


def test_the_same_external_id_on_two_different_connections_creates_two_entries(db_session):
    """external_observation_id is only unique WITHIN a connection --
    two different customers' own connections could each report an id
    "1" from their own respective platform accounts; these must never
    collide."""
    _seed_membership(db_session)
    connection_a = _connection(db_session, platform="collective2")
    connection_b = _connection(db_session, platform="etoro")
    db_session.commit()

    ingest_observed_fill(
        db_session, tenant_id="tenant-a", connection=connection_a, fill=_fill(external_observation_id="1"),
        evidence_class=EvidenceClass.OBSERVED_FOLLOWER_LIVE, source_authority="test:collective2",
    )
    db_session.commit()
    ingest_observed_fill(
        db_session, tenant_id="tenant-a", connection=connection_b, fill=_fill(external_observation_id="1"),
        evidence_class=EvidenceClass.OBSERVED_FOLLOWER_LIVE, source_authority="test:etoro",
    )
    db_session.commit()

    entries = db_session.query(LedgerEntry).filter(
        LedgerEntry.tenant_id == "tenant-a", LedgerEntry.external_observation_id == "1",
    ).all()
    assert len(entries) == 2


def test_two_distinct_observations_both_land(db_session):
    _seed_membership(db_session)
    connection = _connection(db_session)
    db_session.commit()

    ingest_observed_fill(
        db_session, tenant_id="tenant-a", connection=connection,
        fill=_fill(external_observation_id="obs-1", side=Side.BUY, quantity="10", price="100"),
        evidence_class=EvidenceClass.OBSERVED_FOLLOWER_LIVE, source_authority="test:collective2",
    )
    db_session.commit()
    ingest_observed_fill(
        db_session, tenant_id="tenant-a", connection=connection,
        fill=_fill(external_observation_id="obs-2", side=Side.SELL, quantity="10", price="110"),
        evidence_class=EvidenceClass.OBSERVED_FOLLOWER_LIVE, source_authority="test:collective2",
    )
    db_session.commit()

    entries = db_session.query(LedgerEntry).filter(
        LedgerEntry.tenant_id == "tenant-a", LedgerEntry.follower_connection_id == connection.connection_id,
    ).all()
    assert len(entries) == 2


def test_a_different_tenants_observation_never_leaks_in(db_session):
    _seed_membership(db_session, tenant_id="tenant-a", user_id="user-a")
    _seed_membership(db_session, tenant_id="tenant-b", user_id="user-b")
    connection_a = _connection(db_session, tenant_id="tenant-a", user_id="user-a")
    db_session.commit()

    ingest_observed_fill(
        db_session, tenant_id="tenant-a", connection=connection_a, fill=_fill(),
        evidence_class=EvidenceClass.OBSERVED_FOLLOWER_LIVE, source_authority="test:collective2",
    )
    db_session.commit()

    entries_b = db_session.query(LedgerEntry).filter(LedgerEntry.tenant_id == "tenant-b").all()
    assert entries_b == []


def test_the_customer_performance_state_becomes_available_after_a_real_observation(db_session):
    """End-to-end proof that this module's own output is exactly what
    app/services/customer_performance_state.py's own query looks for."""
    from app.services.customer_performance_state import PerformanceState, get_customer_performance_state

    _seed_membership(db_session)
    connection = _connection(db_session)
    db_session.commit()

    state_before = get_customer_performance_state(db_session, tenant_id="tenant-a", connection=connection)
    assert state_before == PerformanceState.AWAITING_OBSERVATIONS

    ingest_observed_fill(
        db_session, tenant_id="tenant-a", connection=connection, fill=_fill(),
        evidence_class=EvidenceClass.OBSERVED_FOLLOWER_LIVE, source_authority="test:collective2",
    )
    db_session.commit()

    state_after = get_customer_performance_state(db_session, tenant_id="tenant-a", connection=connection)
    assert state_after == PerformanceState.AVAILABLE
