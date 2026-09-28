"""app/services/customer_performance_state.py -- the three real,
honestly-computable states of INTEGRATION_DECISION.md S11's own
"Replace blanket UNSUPPORTED with precise states"."""
from decimal import Decimal

from app.models.tenancy import Membership, MembershipRole, Tenant, UserIdentity
from app.services.customer_performance_state import PerformanceState, get_customer_performance_state
from app.services.ledger import append_entry
from app.services.platform_connection import create_platform_connection
from signal_platform_contracts import EvidenceClass
from app.models.ledger import Book, Side
from datetime import datetime, timezone


def _seed_membership(db_session, *, tenant_id="tenant-a", user_id="user-a"):
    if db_session.get(Tenant, tenant_id) is None:
        db_session.add(Tenant(tenant_id=tenant_id, display_name="Tenant", environment="LOCAL_SIM"))
        db_session.flush()
    db_session.add(UserIdentity(user_id=user_id, email=f"{user_id}@example.com"))
    db_session.flush()
    db_session.add(Membership(tenant_id=tenant_id, user_id=user_id, role=MembershipRole.CUSTOMER))
    db_session.commit()


def test_no_connection_at_all_is_not_connected(db_session):
    state = get_customer_performance_state(db_session, tenant_id="tenant-a", connection=None)
    assert state == PerformanceState.NOT_CONNECTED


def test_a_connection_with_no_follower_observations_is_awaiting_observations(db_session):
    _seed_membership(db_session)
    connection = create_platform_connection(
        db_session, tenant_id="tenant-a", user_id="user-a", platform="collective2",
        environment="local_simulation", masked_account_label="C2 ****1234",
    )
    db_session.commit()

    state = get_customer_performance_state(db_session, tenant_id="tenant-a", connection=connection)
    assert state == PerformanceState.AWAITING_OBSERVATIONS


def test_a_real_follower_observation_makes_performance_available(db_session):
    _seed_membership(db_session)
    connection = create_platform_connection(
        db_session, tenant_id="tenant-a", user_id="user-a", platform="collective2",
        environment="local_simulation", masked_account_label="C2 ****1234",
    )
    db_session.commit()

    append_entry(
        db_session, tenant_id="tenant-a", book=Book.FOLLOWER, instrument="AAPL", side=Side.BUY,
        quantity=Decimal("10"), price=Decimal("150"), currency="USD",
        event_time=datetime.now(timezone.utc), source_authority="test",
        evidence_class=EvidenceClass.OBSERVED_FOLLOWER_LIVE, follower_connection_id=connection.connection_id,
    )
    db_session.commit()

    state = get_customer_performance_state(db_session, tenant_id="tenant-a", connection=connection)
    assert state == PerformanceState.AVAILABLE


def test_an_observation_against_a_different_connection_does_not_make_this_one_available(db_session):
    _seed_membership(db_session)
    connection_a = create_platform_connection(
        db_session, tenant_id="tenant-a", user_id="user-a", platform="collective2",
        environment="local_simulation", masked_account_label="C2 A",
    )
    connection_b = create_platform_connection(
        db_session, tenant_id="tenant-a", user_id="user-a", platform="etoro",
        environment="local_simulation", masked_account_label="eToro B",
    )
    db_session.commit()

    append_entry(
        db_session, tenant_id="tenant-a", book=Book.FOLLOWER, instrument="AAPL", side=Side.BUY,
        quantity=Decimal("10"), price=Decimal("150"), currency="USD",
        event_time=datetime.now(timezone.utc), source_authority="test",
        evidence_class=EvidenceClass.OBSERVED_FOLLOWER_LIVE, follower_connection_id=connection_a.connection_id,
    )
    db_session.commit()

    state = get_customer_performance_state(db_session, tenant_id="tenant-a", connection=connection_b)
    assert state == PerformanceState.AWAITING_OBSERVATIONS


def test_an_observation_for_another_tenant_never_leaks_in(db_session):
    _seed_membership(db_session, tenant_id="tenant-a", user_id="user-a")
    _seed_membership(db_session, tenant_id="tenant-b", user_id="user-b")
    connection_a = create_platform_connection(
        db_session, tenant_id="tenant-a", user_id="user-a", platform="collective2",
        environment="local_simulation", masked_account_label="C2 A",
    )
    connection_b = create_platform_connection(
        db_session, tenant_id="tenant-b", user_id="user-b", platform="collective2",
        environment="local_simulation", masked_account_label="C2 B",
    )
    db_session.commit()

    append_entry(
        db_session, tenant_id="tenant-a", book=Book.FOLLOWER, instrument="AAPL", side=Side.BUY,
        quantity=Decimal("10"), price=Decimal("150"), currency="USD",
        event_time=datetime.now(timezone.utc), source_authority="test",
        evidence_class=EvidenceClass.OBSERVED_FOLLOWER_LIVE, follower_connection_id=connection_a.connection_id,
    )
    db_session.commit()

    state = get_customer_performance_state(db_session, tenant_id="tenant-b", connection=connection_b)
    assert state == PerformanceState.AWAITING_OBSERVATIONS
