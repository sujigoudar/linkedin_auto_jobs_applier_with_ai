"""app/services/publication.py -- idempotent enqueue and state-transition
enforcement for PublicationIntent (spec/docs/05, spec/contracts/PublicationIntent.schema.json)."""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.exc import IntegrityError

from app.models.publication import (
    Environment,
    PublicationAction,
    PublicationIntent,
    PublicationSide,
    PublicationState,
    QuantityBasis,
)
from app.services.publication import (
    IdempotencyConflictError,
    InvalidPublicationTransitionError,
    enqueue_intent,
    transition,
)


def _intent(**overrides) -> PublicationIntent:
    now = datetime.now(timezone.utc)
    defaults = dict(
        tenant_id="tenant-a",
        environment=Environment.LOCAL_SIM,
        portfolio_version_id="pv-1",
        episode_id="ep-1",
        revision=1,
        action=PublicationAction.OPEN,
        side=PublicationSide.BUY,
        channel="collective2",
        external_strategy_id="strategy-1",
        instrument_id="AAPL",
        quantity="10",
        quantity_basis=QuantityBasis.UNITS,
        price_basis="market",
        policy_hash="policy-hash-1",
        audience_snapshot_hash="audience-hash-1",
        source_revision_ids=["src-rev-1"],
        rights_grant_ids=["grant-1"],
        body_hash="body-hash-1",
        idempotency_key="idem-1",
        valid_from=now,
        expires_at=now + timedelta(hours=1),
    )
    defaults.update(overrides)
    return PublicationIntent(**defaults)


def test_enqueuing_a_new_intent_persists_it(db_session):
    intent = enqueue_intent(db_session, _intent())
    db_session.commit()

    fetched = db_session.get(PublicationIntent, intent.intent_id)
    assert fetched is not None
    assert fetched.state == PublicationState.DRAFT


def test_same_key_same_body_returns_the_existing_row_not_a_duplicate(db_session):
    first = enqueue_intent(db_session, _intent())
    db_session.commit()

    second = enqueue_intent(db_session, _intent(idempotency_key="idem-1", body_hash="body-hash-1"))

    assert second.intent_id == first.intent_id
    count = db_session.query(PublicationIntent).filter_by(idempotency_key="idem-1").count()
    assert count == 1


def test_same_key_different_body_raises_conflict_not_silently_picks_one(db_session):
    enqueue_intent(db_session, _intent())
    db_session.commit()

    with pytest.raises(IdempotencyConflictError):
        enqueue_intent(db_session, _intent(idempotency_key="idem-1", body_hash="a-different-body-hash"))


def test_the_natural_key_is_unique_at_the_database_level(db_session):
    enqueue_intent(db_session, _intent())
    db_session.commit()

    # Same (channel, external_strategy_id, portfolio_version_id, action,
    # revision) tuple, but a different idempotency_key/body_hash --
    # application-level dedup wouldn't catch this, so the DB constraint
    # must.
    db_session.add(_intent(idempotency_key="idem-2", body_hash="a-different-body-hash"))
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()


def test_valid_lifecycle_transitions_succeed(db_session):
    intent = enqueue_intent(db_session, _intent())
    db_session.commit()

    transition(db_session, intent, PublicationState.ELIGIBLE)
    transition(db_session, intent, PublicationState.QUEUED)
    transition(db_session, intent, PublicationState.SENDING)
    transition(db_session, intent, PublicationState.ACKNOWLEDGED)
    assert intent.state == PublicationState.ACKNOWLEDGED


def test_cannot_skip_from_draft_straight_to_sending(db_session):
    intent = enqueue_intent(db_session, _intent())
    db_session.commit()

    with pytest.raises(InvalidPublicationTransitionError):
        transition(db_session, intent, PublicationState.SENDING)


def test_unknown_cannot_be_blindly_retried_into_acknowledged(db_session):
    """UNKNOWN ("possibly accepted") must go through RECONCILING -- it can
    never jump straight to ACKNOWLEDGED or REJECTED, which is exactly the
    "blindly retried" shortcut docs/05 forbids."""
    intent = enqueue_intent(db_session, _intent())
    db_session.commit()
    transition(db_session, intent, PublicationState.ELIGIBLE)
    transition(db_session, intent, PublicationState.QUEUED)
    transition(db_session, intent, PublicationState.SENDING)
    transition(db_session, intent, PublicationState.UNKNOWN)

    with pytest.raises(InvalidPublicationTransitionError):
        transition(db_session, intent, PublicationState.ACKNOWLEDGED)

    # The only legal way forward from UNKNOWN:
    transition(db_session, intent, PublicationState.RECONCILING)
    assert intent.state == PublicationState.RECONCILING


def test_terminal_and_superseded_are_absorbing_states(db_session):
    intent = enqueue_intent(db_session, _intent())
    db_session.commit()
    transition(db_session, intent, PublicationState.ELIGIBLE)
    transition(db_session, intent, PublicationState.QUEUED)
    transition(db_session, intent, PublicationState.SENDING)
    transition(db_session, intent, PublicationState.ACKNOWLEDGED)
    transition(db_session, intent, PublicationState.TERMINAL)

    for candidate in PublicationState:
        with pytest.raises(InvalidPublicationTransitionError):
            transition(db_session, intent, candidate)
