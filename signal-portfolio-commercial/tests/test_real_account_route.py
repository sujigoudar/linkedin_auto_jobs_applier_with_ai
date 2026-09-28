"""INTEGRATION_ACCEPTANCE_CASES.json INT-033 "Shared account cannot gain
a second writer" -- app/services/real_account_route.py's own tests."""
import pytest
from sqlalchemy.exc import IntegrityError

from app.models.real_account_route import ExclusiveOwnershipPlan, RealAccountRoute
from app.services.real_account_route import (
    SecondWriterRejectedError,
    qualify_exclusive_ownership_plan,
    register_real_account_route,
)


def test_a_fresh_route_registration_succeeds(db_session):
    register_real_account_route(
        db_session, tenant_id="tenant-a", broker="alpaca", account_reference="acct-123",
        channel="pamm", external_strategy_id="program-1", writer_identity="worker-a",
    )
    db_session.commit()

    route = db_session.get(RealAccountRoute, ("alpaca", "acct-123"))
    assert route.channel == "pamm"
    assert route.external_strategy_id == "program-1"


def test_the_same_route_re_registering_is_idempotent(db_session):
    register_real_account_route(
        db_session, tenant_id="tenant-a", broker="alpaca", account_reference="acct-123",
        channel="pamm", external_strategy_id="program-1", writer_identity="worker-a",
    )
    db_session.commit()

    register_real_account_route(
        db_session, tenant_id="tenant-a", broker="alpaca", account_reference="acct-123",
        channel="pamm", external_strategy_id="program-1", writer_identity="worker-a",
    )
    db_session.commit()

    count = db_session.query(RealAccountRoute).filter_by(broker="alpaca", account_reference="acct-123").count()
    assert count == 1


def test_a_different_route_for_the_same_real_account_is_rejected(db_session):
    """INT-033's own core scenario: a direct PAMM route and an external
    Collective2 alias resolving to the SAME real brokerage account --
    the alias is never treated as independent capital/authority."""
    register_real_account_route(
        db_session, tenant_id="tenant-a", broker="alpaca", account_reference="acct-123",
        channel="pamm", external_strategy_id="program-1", writer_identity="worker-a",
    )
    db_session.commit()

    with pytest.raises(SecondWriterRejectedError):
        register_real_account_route(
            db_session, tenant_id="tenant-a", broker="alpaca", account_reference="acct-123",
            channel="collective2", external_strategy_id="strat-1", writer_identity="worker-b",
        )

    # The original route is untouched by the rejected attempt.
    route = db_session.get(RealAccountRoute, ("alpaca", "acct-123"))
    assert route.channel == "pamm"
    assert route.external_strategy_id == "program-1"


def test_different_real_accounts_are_independent(db_session):
    register_real_account_route(
        db_session, tenant_id="tenant-a", broker="alpaca", account_reference="acct-123",
        channel="pamm", external_strategy_id="program-1", writer_identity="worker-a",
    )
    register_real_account_route(
        db_session, tenant_id="tenant-a", broker="alpaca", account_reference="acct-456",
        channel="collective2", external_strategy_id="strat-1", writer_identity="worker-b",
    )
    db_session.commit()

    assert db_session.get(RealAccountRoute, ("alpaca", "acct-123")).channel == "pamm"
    assert db_session.get(RealAccountRoute, ("alpaca", "acct-456")).channel == "collective2"


def test_a_qualified_exclusive_ownership_plan_lets_the_named_successor_take_over(db_session):
    """INT-033's own "Conflict is rejected until an explicit exclusive
    ownership plan is qualified" -- once an owner explicitly qualifies a
    NAMED successor route, that exact route (and only that one) may take
    over the real account."""
    register_real_account_route(
        db_session, tenant_id="tenant-a", broker="alpaca", account_reference="acct-123",
        channel="pamm", external_strategy_id="program-1", writer_identity="worker-a",
    )
    db_session.commit()

    qualify_exclusive_ownership_plan(
        db_session, broker="alpaca", account_reference="acct-123", approved_channel="collective2",
        approved_external_strategy_id="strat-1", qualified_by="owner-alice",
    )
    db_session.commit()

    register_real_account_route(
        db_session, tenant_id="tenant-a", broker="alpaca", account_reference="acct-123",
        channel="collective2", external_strategy_id="strat-1", writer_identity="worker-b",
    )
    db_session.commit()

    route = db_session.get(RealAccountRoute, ("alpaca", "acct-123"))
    assert route.channel == "collective2"
    assert route.external_strategy_id == "strat-1"
    assert route.writer_identity == "worker-b"


def test_a_plan_never_approves_a_route_other_than_the_one_it_names(db_session):
    """A qualified plan is scoped to its own named successor -- it must
    never be read as "any second writer is now fine" for this account."""
    register_real_account_route(
        db_session, tenant_id="tenant-a", broker="alpaca", account_reference="acct-123",
        channel="pamm", external_strategy_id="program-1", writer_identity="worker-a",
    )
    qualify_exclusive_ownership_plan(
        db_session, broker="alpaca", account_reference="acct-123", approved_channel="collective2",
        approved_external_strategy_id="strat-1", qualified_by="owner-alice",
    )
    db_session.commit()

    with pytest.raises(SecondWriterRejectedError):
        register_real_account_route(
            db_session, tenant_id="tenant-a", broker="alpaca", account_reference="acct-123",
            channel="etoro", external_strategy_id="strat-9", writer_identity="worker-c",
        )


def test_the_database_itself_rejects_a_concurrent_second_row_for_the_same_real_account(db_session):
    """The real fence: even bypassing register_real_account_route's own
    check-then-act, a second row for the same (broker, account_reference)
    primary key cannot exist."""
    db_session.add(
        RealAccountRoute(
            broker="alpaca", account_reference="acct-123", channel="pamm", external_strategy_id="program-1",
            writer_identity="worker-a", tenant_id="tenant-a",
        )
    )
    db_session.flush()
    db_session.add(
        RealAccountRoute(
            broker="alpaca", account_reference="acct-123", channel="collective2", external_strategy_id="strat-1",
            writer_identity="worker-b", tenant_id="tenant-a",
        )
    )
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()


def test_qualifying_a_plan_is_idempotent_and_replaces_a_prior_named_successor(db_session):
    qualify_exclusive_ownership_plan(
        db_session, broker="alpaca", account_reference="acct-123", approved_channel="collective2",
        approved_external_strategy_id="strat-1", qualified_by="owner-alice",
    )
    db_session.commit()

    qualify_exclusive_ownership_plan(
        db_session, broker="alpaca", account_reference="acct-123", approved_channel="etoro",
        approved_external_strategy_id="strat-9", qualified_by="owner-alice",
    )
    db_session.commit()

    plan = db_session.get(ExclusiveOwnershipPlan, ("alpaca", "acct-123"))
    assert plan.approved_channel == "etoro"
    assert plan.approved_external_strategy_id == "strat-9"
    count = db_session.query(ExclusiveOwnershipPlan).filter_by(broker="alpaca", account_reference="acct-123").count()
    assert count == 1
