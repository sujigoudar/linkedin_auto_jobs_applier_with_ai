"""CU-07 "Platform connections" / CU-08 "Connection wizard" --
app/services/platform_connection.py's own tests. Real Postgres, real
tenant-scoped session."""
import pytest

from app.models.tenancy import Membership, MembershipRole, Tenant, UserIdentity
from app.services.platform_connection import (
    InvalidPlatformConnectionError,
    create_platform_connection,
    disconnect_platform_connection,
    get_own_platform_connection,
    list_own_platform_connections,
)


def _seed_membership(db_session, *, tenant_id="tenant-a", user_id="user-a"):
    if db_session.get(Tenant, tenant_id) is None:
        db_session.add(Tenant(tenant_id=tenant_id, display_name="Tenant", environment="LOCAL_SIM"))
        db_session.flush()
    db_session.add(UserIdentity(user_id=user_id, email=f"{user_id}@example.com"))
    db_session.flush()
    db_session.add(Membership(tenant_id=tenant_id, user_id=user_id, role=MembershipRole.CUSTOMER))
    db_session.commit()


def test_list_own_platform_connections_is_empty_before_any_are_created(db_session):
    _seed_membership(db_session)
    assert list_own_platform_connections(db_session, tenant_id="tenant-a", user_id="user-a") == []


def test_create_platform_connection_rejects_an_unreviewed_platform(db_session):
    _seed_membership(db_session)
    with pytest.raises(InvalidPlatformConnectionError, match="REVIEWED_PLATFORM_NOT_FOUND"):
        create_platform_connection(
            db_session,
            tenant_id="tenant-a",
            user_id="user-a",
            platform="not-a-real-broker",
            environment="local_simulation",
            masked_account_label="Test ****1234",
        )


def test_create_platform_connection_rejects_a_non_local_simulation_environment(db_session):
    _seed_membership(db_session)
    with pytest.raises(InvalidPlatformConnectionError, match="EXTERNAL_ENVIRONMENT_NOT_AUTHORIZED"):
        create_platform_connection(
            db_session,
            tenant_id="tenant-a",
            user_id="user-a",
            platform="collective2",
            environment="live",
            masked_account_label="Test ****1234",
        )


def test_create_platform_connection_rejects_an_empty_account_label(db_session):
    _seed_membership(db_session)
    with pytest.raises(InvalidPlatformConnectionError, match="masked_account_label"):
        create_platform_connection(
            db_session,
            tenant_id="tenant-a",
            user_id="user-a",
            platform="collective2",
            environment="local_simulation",
            masked_account_label="",
        )


def test_create_then_reload_persists_the_real_connection(db_session):
    _seed_membership(db_session)
    create_platform_connection(
        db_session,
        tenant_id="tenant-a",
        user_id="user-a",
        platform="collective2",
        environment="local_simulation",
        masked_account_label="Collective2 ****9999",
    )
    db_session.commit()

    connections = list_own_platform_connections(db_session, tenant_id="tenant-a", user_id="user-a")
    assert len(connections) == 1
    assert connections[0].platform == "collective2"
    assert connections[0].state.value == "declared"


def test_get_own_platform_connection_is_none_for_a_cross_tenant_connection(db_session):
    _seed_membership(db_session, tenant_id="tenant-a", user_id="user-a")
    _seed_membership(db_session, tenant_id="tenant-b", user_id="user-b")
    connection = create_platform_connection(
        db_session,
        tenant_id="tenant-a",
        user_id="user-a",
        platform="etoro",
        environment="local_simulation",
        masked_account_label="eToro ****1111",
    )
    db_session.commit()

    assert (
        get_own_platform_connection(db_session, connection.connection_id, tenant_id="tenant-b", user_id="user-b")
        is None
    )


def test_get_own_platform_connection_is_none_for_a_different_users_connection_in_the_same_tenant(db_session):
    _seed_membership(db_session, tenant_id="tenant-a", user_id="user-a")
    _seed_membership(db_session, tenant_id="tenant-a", user_id="user-b")
    connection = create_platform_connection(
        db_session,
        tenant_id="tenant-a",
        user_id="user-a",
        platform="etoro",
        environment="local_simulation",
        masked_account_label="eToro ****1111",
    )
    db_session.commit()

    assert (
        get_own_platform_connection(db_session, connection.connection_id, tenant_id="tenant-a", user_id="user-b")
        is None
    )


def test_disconnect_platform_connection_sets_disconnected_state(db_session):
    _seed_membership(db_session)
    connection = create_platform_connection(
        db_session,
        tenant_id="tenant-a",
        user_id="user-a",
        platform="metaapi_copyfactory",
        environment="local_simulation",
        masked_account_label="CopyFactory ****2222",
    )
    db_session.commit()

    disconnect_platform_connection(db_session, connection)
    db_session.commit()
    assert connection.state.value == "disconnected"
