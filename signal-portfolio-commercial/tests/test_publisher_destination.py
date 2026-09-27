"""AD-09 "Publisher channels and strategies" -- app/services/publisher_destination.py's
own tests. Real Postgres, real tenant-scoped session."""
import pytest

from app.services.publisher_destination import (
    InvalidPublisherDestinationError,
    create_publisher_destination,
    list_publisher_destinations,
)


def test_list_publisher_destinations_is_empty_before_any_are_saved(db_session):
    assert list_publisher_destinations(db_session, tenant_id="tenant-a") == []


def test_create_rejects_an_unknown_platform(db_session):
    with pytest.raises(InvalidPublisherDestinationError):
        create_publisher_destination(
            db_session,
            tenant_id="tenant-a",
            platform="not-a-real-platform",
            external_strategy_id="strat-1",
            environment="local_simulation",
            credential_ref=None,
            capability_manifest_id=None,
            publication_mode="api_strategy_publisher",
        )


def test_create_rejects_any_environment_other_than_local_simulation(db_session):
    with pytest.raises(InvalidPublisherDestinationError, match="EXTERNAL_ENVIRONMENT_NOT_AUTHORIZED"):
        create_publisher_destination(
            db_session,
            tenant_id="tenant-a",
            platform="collective2",
            external_strategy_id="strat-1",
            environment="external_test",
            credential_ref=None,
            capability_manifest_id=None,
            publication_mode="api_strategy_publisher",
        )


def test_create_then_reload_persists_the_real_destination(db_session):
    create_publisher_destination(
        db_session,
        tenant_id="tenant-a",
        platform="collective2",
        external_strategy_id="strat-1",
        environment="local_simulation",
        credential_ref="cred-ref-1",
        capability_manifest_id=None,
        publication_mode="api_strategy_publisher",
    )
    db_session.commit()

    destinations = list_publisher_destinations(db_session, tenant_id="tenant-a")
    assert len(destinations) == 1
    assert destinations[0].external_strategy_id == "strat-1"
    assert destinations[0].credential_ref == "cred-ref-1"


def test_a_second_tenant_cannot_claim_the_same_channel_and_strategy(db_session):
    create_publisher_destination(
        db_session,
        tenant_id="tenant-a",
        platform="collective2",
        external_strategy_id="strat-shared",
        environment="local_simulation",
        credential_ref=None,
        capability_manifest_id=None,
        publication_mode="api_strategy_publisher",
    )
    db_session.commit()

    with pytest.raises(InvalidPublisherDestinationError):
        create_publisher_destination(
            db_session,
            tenant_id="tenant-b",
            platform="collective2",
            external_strategy_id="strat-shared",
            environment="local_simulation",
            credential_ref=None,
            capability_manifest_id=None,
            publication_mode="api_strategy_publisher",
        )


def test_the_same_tenant_re_saving_the_same_strategy_is_not_a_conflict(db_session):
    create_publisher_destination(
        db_session,
        tenant_id="tenant-a",
        platform="collective2",
        external_strategy_id="strat-1",
        environment="local_simulation",
        credential_ref=None,
        capability_manifest_id=None,
        publication_mode="api_strategy_publisher",
    )
    db_session.commit()

    create_publisher_destination(
        db_session,
        tenant_id="tenant-a",
        platform="collective2",
        external_strategy_id="strat-1",
        environment="local_simulation",
        credential_ref="new-ref",
        capability_manifest_id=None,
        publication_mode="api_strategy_publisher",
    )
    db_session.commit()

    destinations = list_publisher_destinations(db_session, tenant_id="tenant-a")
    assert len(destinations) == 2
