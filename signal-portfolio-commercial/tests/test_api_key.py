"""CU-16 "API delivery, keys and exports" -- app/services/api_key.py's
own tests. Real Postgres, real tenant-scoped session."""
from datetime import datetime, timedelta, timezone

import pytest

from app.models.tenancy import Membership, MembershipRole, Tenant, UserIdentity
from app.services.api_key import (
    ApiKeyNotFoundError,
    InvalidApiKeyRequestError,
    generate_scoped_key,
    list_api_keys,
    revoke_api_key,
)


def _seed_membership(db_session, *, tenant_id="tenant-a", user_id="user-a"):
    db_session.add(Tenant(tenant_id=tenant_id, display_name="Tenant", environment="LOCAL_SIM"))
    db_session.flush()
    db_session.add(UserIdentity(user_id=user_id, email=f"{user_id}@example.com"))
    db_session.flush()
    db_session.add(Membership(tenant_id=tenant_id, user_id=user_id, role=MembershipRole.CUSTOMER))
    db_session.commit()


def _future(days=30):
    return datetime.now(timezone.utc) + timedelta(days=days)


def test_list_api_keys_is_empty_before_any_are_generated(db_session):
    _seed_membership(db_session)
    assert list_api_keys(db_session, tenant_id="tenant-a", user_id="user-a") == []


def test_generate_scoped_key_rejects_a_trading_scope(db_session):
    _seed_membership(db_session)
    with pytest.raises(InvalidApiKeyRequestError):
        generate_scoped_key(
            db_session,
            tenant_id="tenant-a",
            user_id="user-a",
            label="My key",
            scopes=["trading_write"],
            expires_at=_future(),
        )


def test_generate_scoped_key_rejects_a_never_expiring_key(db_session):
    _seed_membership(db_session)
    with pytest.raises(InvalidApiKeyRequestError):
        generate_scoped_key(
            db_session,
            tenant_id="tenant-a",
            user_id="user-a",
            label="My key",
            scopes=["alerts_read"],
            expires_at=datetime.now(timezone.utc) - timedelta(days=1),
        )


def test_generate_scoped_key_rejects_an_empty_label(db_session):
    _seed_membership(db_session)
    with pytest.raises(InvalidApiKeyRequestError):
        generate_scoped_key(
            db_session, tenant_id="tenant-a", user_id="user-a", label="", scopes=["alerts_read"], expires_at=_future()
        )


def test_generate_then_reload_persists_only_a_hash_never_the_raw_secret(db_session):
    _seed_membership(db_session)
    key, raw_secret = generate_scoped_key(
        db_session,
        tenant_id="tenant-a",
        user_id="user-a",
        label="My key",
        scopes=["alerts_read", "reports_read"],
        expires_at=_future(),
    )
    db_session.commit()

    reloaded = list_api_keys(db_session, tenant_id="tenant-a", user_id="user-a")
    assert len(reloaded) == 1
    assert reloaded[0].label == "My key"
    assert reloaded[0].key_hash != raw_secret
    assert raw_secret not in reloaded[0].key_hash


def test_revoke_api_key_is_idempotent(db_session):
    _seed_membership(db_session)
    key, _ = generate_scoped_key(
        db_session, tenant_id="tenant-a", user_id="user-a", label="My key", scopes=["alerts_read"], expires_at=_future()
    )
    db_session.commit()

    first = revoke_api_key(db_session, tenant_id="tenant-a", user_id="user-a", key_id=key.key_id)
    db_session.commit()
    second = revoke_api_key(db_session, tenant_id="tenant-a", user_id="user-a", key_id=key.key_id)
    db_session.commit()

    assert first.revoked_at == second.revoked_at


def test_revoke_api_key_requires_your_own_key(db_session):
    _seed_membership(db_session, tenant_id="tenant-a", user_id="user-a")
    _seed_membership(db_session, tenant_id="tenant-b", user_id="user-b")
    key, _ = generate_scoped_key(
        db_session, tenant_id="tenant-a", user_id="user-a", label="My key", scopes=["alerts_read"], expires_at=_future()
    )
    db_session.commit()

    with pytest.raises(ApiKeyNotFoundError):
        revoke_api_key(db_session, tenant_id="tenant-b", user_id="user-b", key_id=key.key_id)
