"""app/services/publisher_writer_claim.py -- CP-045 "Single publishing
mode": exactly one publication authority per (channel, external_strategy_id)."""
import pytest
from sqlalchemy.exc import IntegrityError

from app.models.publisher_writer_claim import PublisherWriterClaim
from app.services.publisher_writer_claim import (
    WriterAlreadyClaimedError,
    claim_writer,
    release_writer,
)


def test_a_fresh_claim_succeeds(db_session):
    claim_writer(db_session, channel="collective2", external_strategy_id="strat-1", writer_identity="worker-a")
    db_session.commit()

    claim = db_session.get(PublisherWriterClaim, ("collective2", "strat-1"))
    assert claim.writer_identity == "worker-a"


def test_the_same_writer_reclaiming_is_idempotent(db_session):
    claim_writer(db_session, channel="collective2", external_strategy_id="strat-1", writer_identity="worker-a")
    db_session.commit()

    claim_writer(db_session, channel="collective2", external_strategy_id="strat-1", writer_identity="worker-a")
    db_session.commit()

    count = db_session.query(PublisherWriterClaim).filter_by(external_strategy_id="strat-1").count()
    assert count == 1


def test_a_different_writer_is_rejected(db_session):
    claim_writer(db_session, channel="collective2", external_strategy_id="strat-1", writer_identity="worker-a")
    db_session.commit()

    with pytest.raises(WriterAlreadyClaimedError):
        claim_writer(db_session, channel="collective2", external_strategy_id="strat-1", writer_identity="worker-b")


def test_the_same_strategy_on_a_different_channel_is_independent(db_session):
    claim_writer(db_session, channel="collective2", external_strategy_id="strat-1", writer_identity="worker-a")
    claim_writer(db_session, channel="etoro", external_strategy_id="strat-1", writer_identity="worker-b")
    db_session.commit()

    assert db_session.get(PublisherWriterClaim, ("collective2", "strat-1")).writer_identity == "worker-a"
    assert db_session.get(PublisherWriterClaim, ("etoro", "strat-1")).writer_identity == "worker-b"


def test_the_holder_can_release_its_own_claim(db_session):
    claim_writer(db_session, channel="collective2", external_strategy_id="strat-1", writer_identity="worker-a")
    db_session.commit()

    release_writer(db_session, channel="collective2", external_strategy_id="strat-1", writer_identity="worker-a")
    db_session.commit()

    assert db_session.get(PublisherWriterClaim, ("collective2", "strat-1")) is None


def test_a_non_holder_cannot_release_someone_elses_claim(db_session):
    claim_writer(db_session, channel="collective2", external_strategy_id="strat-1", writer_identity="worker-a")
    db_session.commit()

    with pytest.raises(WriterAlreadyClaimedError):
        release_writer(db_session, channel="collective2", external_strategy_id="strat-1", writer_identity="worker-b")


def test_the_database_itself_rejects_a_concurrent_second_row_for_the_same_key(db_session):
    """The real fence: even bypassing claim_writer's own check-then-act,
    a second row for the same (channel, external_strategy_id) primary
    key cannot exist."""
    db_session.add(PublisherWriterClaim(channel="collective2", external_strategy_id="strat-1", writer_identity="a"))
    db_session.flush()
    db_session.add(PublisherWriterClaim(channel="collective2", external_strategy_id="strat-1", writer_identity="b"))
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()
