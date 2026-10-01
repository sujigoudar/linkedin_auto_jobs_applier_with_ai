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
    if db_session.get(Tenant, tenant_id) is None:
        db_session.add(Tenant(tenant_id=tenant_id, display_name="Tenant", environment="LOCAL_SIM"))
        db_session.flush()
    if db_session.get(UserIdentity, user_id) is None:
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
    with pytest.raises(InvalidApiKeyRequestError) as exc_info:
        generate_scoped_key(
            db_session,
            tenant_id="tenant-a",
            user_id="user-a",
            label="My key",
            scopes=["trading_write"],
            expires_at=_future(),
        )
    # startswith, not `in` -- a cosmetic XX-prefix/suffix-padding
    # mutation of this whole message still contains this substring in
    # the middle, so only anchoring at the start can catch it.
    assert str(exc_info.value).startswith("unknown or unauthorized scope(s):")


def test_generate_scoped_key_accepts_every_named_valid_scope(db_session):
    """Each of `_VALID_SCOPES`'s three named scopes is a real,
    deliberate allow-list entry (CU-16's "No trading/admin scope" is
    enforced by this allowlist being a closed set) -- only
    `alerts_read`/`reports_read` were ever actually exercised by an
    existing test, so a mutation removing/renaming `delivery_receive`
    from the set went unnoticed. Generating a key with each scope,
    one at a time, closes that gap for all three at once."""
    _seed_membership(db_session)
    for scope in ("alerts_read", "reports_read", "delivery_receive"):
        key, _ = generate_scoped_key(
            db_session,
            tenant_id="tenant-a",
            user_id="user-a",
            label=f"key-{scope}",
            scopes=[scope],
            expires_at=_future(),
        )
        db_session.commit()
        assert key.scopes == [scope]


def test_generate_scoped_key_rejects_a_never_expiring_key(db_session):
    _seed_membership(db_session)
    with pytest.raises(InvalidApiKeyRequestError) as exc_info:
        generate_scoped_key(
            db_session,
            tenant_id="tenant-a",
            user_id="user-a",
            label="My key",
            scopes=["alerts_read"],
            expires_at=datetime.now(timezone.utc) - timedelta(days=1),
        )
    assert str(exc_info.value) == "expires_at must be in the future -- no never-expire default"


def test_generate_scoped_key_rejects_an_empty_scopes_list(db_session):
    """`scopes=[]` is the other, distinct validation branch from "an
    unknown scope was given" -- nothing exercised it at all before
    this, so its own cosmetic message mutation and the branch itself
    were both untested."""
    _seed_membership(db_session)
    with pytest.raises(InvalidApiKeyRequestError) as exc_info:
        generate_scoped_key(
            db_session, tenant_id="tenant-a", user_id="user-a", label="My key", scopes=[], expires_at=_future()
        )
    assert str(exc_info.value) == "at least one scope is required"


def test_generate_scoped_key_rejects_an_empty_label(db_session):
    _seed_membership(db_session)
    with pytest.raises(InvalidApiKeyRequestError) as exc_info:
        generate_scoped_key(
            db_session, tenant_id="tenant-a", user_id="user-a", label="", scopes=["alerts_read"], expires_at=_future()
        )
    assert str(exc_info.value) == "label must be 1..80 characters"


def test_generate_scoped_key_accepts_a_single_character_label(db_session):
    """Pins the label length lower boundary (1 character) as VALID --
    the only existing test at this end used an empty (0-char) label,
    so an off-by-one on either the `1 <=` comparison or a `not label`
    shortcut would silently start rejecting legitimate 1-character
    labels without any test noticing."""
    _seed_membership(db_session)
    key, _ = generate_scoped_key(
        db_session, tenant_id="tenant-a", user_id="user-a", label="x", scopes=["alerts_read"], expires_at=_future()
    )
    db_session.commit()
    assert key.label == "x"


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


def test_generate_scoped_key_produces_a_secret_of_the_expected_length(db_session):
    """`secrets.token_urlsafe(32)` (256 bits of real entropy) is a
    deliberate, security-relevant constant -- nothing pinned its exact
    output length, so a mutation of the byte count (more OR fewer
    bytes) would go unnoticed. `token_urlsafe(32)` always produces a
    43-character base64url string (no padding)."""
    _seed_membership(db_session)
    _, raw_secret = generate_scoped_key(
        db_session, tenant_id="tenant-a", user_id="user-a", label="My key", scopes=["alerts_read"], expires_at=_future()
    )
    assert len(raw_secret) == 43


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


def test_revoke_api_key_genuinely_sets_revoked_at_on_the_first_call(db_session):
    """The real, load-bearing proof behind the idempotency test above:
    a mutant that inverts `revoke_api_key`'s `if key.revoked_at is
    None:` guard (to `is not None`) or that replaces `key.revoked_at =
    _now()` with `= None` would make a FRESH, never-revoked key's
    `revoked_at` stay `None` forever -- revoke_api_key would silently
    do nothing on the very first call. The idempotency test above
    can't catch either mutant on its own: both leave `revoked_at` as
    `None` on BOTH calls, so `first.revoked_at == second.revoked_at`
    (`None == None`) still passes. This is exactly the "a revoked key
    still treated as valid" risk this track was told to prioritize."""
    _seed_membership(db_session)
    key, raw_secret = generate_scoped_key(
        db_session, tenant_id="tenant-a", user_id="user-a", label="My key", scopes=["alerts_read"], expires_at=_future()
    )
    db_session.commit()
    assert key.revoked_at is None

    revoked = revoke_api_key(db_session, tenant_id="tenant-a", user_id="user-a", key_id=key.key_id)
    db_session.commit()

    assert revoked.revoked_at is not None


def test_revoke_api_key_requires_your_own_key(db_session):
    _seed_membership(db_session, tenant_id="tenant-a", user_id="user-a")
    _seed_membership(db_session, tenant_id="tenant-b", user_id="user-b")
    key, _ = generate_scoped_key(
        db_session, tenant_id="tenant-a", user_id="user-a", label="My key", scopes=["alerts_read"], expires_at=_future()
    )
    db_session.commit()

    with pytest.raises(ApiKeyNotFoundError) as exc_info:
        revoke_api_key(db_session, tenant_id="tenant-b", user_id="user-b", key_id=key.key_id)
    assert str(exc_info.value) == f"no API key {key.key_id!r} for this account"


def test_revoke_api_key_requires_your_own_key_even_within_the_same_tenant(db_session):
    """Isolates the ownership guard's `tenant_id`/`user_id` checks from
    each other: a mutant that weakens `revoke_api_key`'s `or` chain
    (`key is None or key.tenant_id != tenant_id or key.user_id !=
    user_id`) into an `and` between the tenant/user mismatch checks
    would wrongly let a DIFFERENT user who happens to share the SAME
    tenant (e.g. another staff member or customer) revoke someone
    else's key -- `test_revoke_api_key_requires_your_own_key` above
    mismatches both tenant_id AND user_id at once, so it can't catch
    that specific widening on its own."""
    _seed_membership(db_session, tenant_id="tenant-a", user_id="user-a")
    _seed_membership(db_session, tenant_id="tenant-a", user_id="user-c")
    key, _ = generate_scoped_key(
        db_session, tenant_id="tenant-a", user_id="user-a", label="My key", scopes=["alerts_read"], expires_at=_future()
    )
    db_session.commit()

    with pytest.raises(ApiKeyNotFoundError):
        revoke_api_key(db_session, tenant_id="tenant-a", user_id="user-c", key_id=key.key_id)


def test_revoke_api_key_requires_your_own_tenant_even_with_the_same_user_id(db_session):
    """The other half of the ownership-guard isolation: a mutant that
    weakens the chain's FIRST `or` into an `and` (`(key is None and
    key.tenant_id != tenant_id) or key.user_id != user_id`) would wrongly
    let a DIFFERENT tenant revoke a key as long as the SAME `user_id`
    value is presented -- a real scenario when one person holds
    memberships in more than one tenant. Neither of the two isolation
    tests above catches this: both already mismatch `user_id`, which
    alone satisfies that mutated condition's second `or` term and
    raises for the wrong reason."""
    _seed_membership(db_session, tenant_id="tenant-a", user_id="user-a")
    _seed_membership(db_session, tenant_id="tenant-b", user_id="user-a")  # same user, second tenant membership
    key, _ = generate_scoped_key(
        db_session, tenant_id="tenant-a", user_id="user-a", label="My key", scopes=["alerts_read"], expires_at=_future()
    )
    db_session.commit()

    with pytest.raises(ApiKeyNotFoundError):
        revoke_api_key(db_session, tenant_id="tenant-b", user_id="user-a", key_id=key.key_id)


def test_generate_scoped_key_accepts_a_label_at_the_max_length_boundary(db_session):
    """Pins the `_MAX_LABEL_LENGTH = 80` upper boundary at both ends:
    exactly 80 characters must be accepted (not just rejected above
    it), closing off an off-by-one widen (accepting 81+) or narrow
    (rejecting exactly 80) mutation of either the constant or the `<=`
    comparison."""
    _seed_membership(db_session)
    label_80 = "x" * 80
    key, _ = generate_scoped_key(
        db_session, tenant_id="tenant-a", user_id="user-a", label=label_80, scopes=["alerts_read"], expires_at=_future()
    )
    db_session.commit()
    assert key.label == label_80

    with pytest.raises(InvalidApiKeyRequestError):
        generate_scoped_key(
            db_session,
            tenant_id="tenant-a",
            user_id="user-a",
            label="x" * 81,
            scopes=["alerts_read"],
            expires_at=_future(),
        )
