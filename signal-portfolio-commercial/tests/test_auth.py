"""app/services/auth.py's local JWT issuer/decoder -- pure function
tests, no database needed. Real deployment identity comes from Supabase
Auth; this issuer exists only so local code/tests have a real signed
token to exercise tenant-scope enforcement against."""
import jwt
import pytest

from app.models.tenancy import MembershipRole
from app.services.auth import InvalidTokenError, TenantScope, decode_token, issue_token


def test_a_freshly_issued_token_decodes_to_its_own_claims():
    token = issue_token("tenant-a", "user-a", MembershipRole.OWNER)
    scope = decode_token(token)
    assert scope.tenant_id == "tenant-a"
    assert scope.user_id == "user-a"
    assert scope.role == MembershipRole.OWNER
    assert scope.jti  # a real, non-empty jti claim on every issued token


def test_two_tokens_issued_with_identical_claims_get_different_jtis():
    token_a = issue_token("tenant-a", "user-a", MembershipRole.OWNER)
    token_b = issue_token("tenant-a", "user-a", MembershipRole.OWNER)
    assert decode_token(token_a).jti != decode_token(token_b).jti


def test_a_token_missing_its_jti_claim_is_rejected():
    from app import config

    forged = jwt.encode(
        {"tenant_id": "tenant-a", "user_id": "user-a", "role": "owner"},
        config.LOCAL_JWT_SECRET,
        algorithm="HS256",
    )
    with pytest.raises(InvalidTokenError):
        decode_token(forged)


def test_a_tampered_signature_is_rejected():
    token = issue_token("tenant-a", "user-a", MembershipRole.OWNER)
    tampered = token[:-1] + ("A" if token[-1] != "A" else "B")
    with pytest.raises(InvalidTokenError):
        decode_token(tampered)


def test_an_expired_token_is_rejected():
    token = issue_token("tenant-a", "user-a", MembershipRole.OWNER, ttl_seconds=-1)
    with pytest.raises(InvalidTokenError):
        decode_token(token)


def test_an_unrecognized_role_claim_is_rejected():
    from app import config

    forged = jwt.encode(
        {"tenant_id": "tenant-a", "user_id": "user-a", "role": "super_admin_backdoor"},
        config.LOCAL_JWT_SECRET,
        algorithm="HS256",
    )
    with pytest.raises(InvalidTokenError) as exc_info:
        decode_token(forged)
    assert "unrecognized or missing role claim" in str(exc_info.value)


def test_a_token_missing_tenant_id_is_rejected():
    from app import config

    forged = jwt.encode({"user_id": "user-a", "role": "owner"}, config.LOCAL_JWT_SECRET, algorithm="HS256")
    with pytest.raises(InvalidTokenError):
        decode_token(forged)


def test_a_token_missing_tenant_id_but_carrying_a_real_user_id_and_jti_is_still_rejected():
    """Isolates the `not tenant_id or not user_id` guard from the
    downstream `not jti` guard: this forged token has a real `jti`, so
    only a genuine `or` (not an `and`) between the two missing-claim
    checks can catch a token that is missing ONLY `tenant_id`. A mutant
    that widens this to `and` would wrongly decode this into a scope
    with `tenant_id=None` -- a real cross-tenant-authority widening
    bug, not a cosmetic one."""
    from app import config

    forged = jwt.encode(
        {"user_id": "user-a", "role": "owner", "jti": "forged-jti-a"},
        config.LOCAL_JWT_SECRET,
        algorithm="HS256",
    )
    with pytest.raises(InvalidTokenError) as exc_info:
        decode_token(forged)
    assert "missing tenant_id or user_id" in str(exc_info.value)


def test_a_token_missing_user_id_but_carrying_a_real_tenant_id_and_jti_is_still_rejected():
    """The symmetric case to the tenant_id one above -- missing ONLY
    `user_id`, with a real `tenant_id` and `jti` present, must also be
    rejected by the same `or` guard."""
    from app import config

    forged = jwt.encode(
        {"tenant_id": "tenant-a", "role": "owner", "jti": "forged-jti-b"},
        config.LOCAL_JWT_SECRET,
        algorithm="HS256",
    )
    with pytest.raises(InvalidTokenError) as exc_info:
        decode_token(forged)
    assert "missing tenant_id or user_id" in str(exc_info.value)


def test_a_token_missing_its_jti_claim_is_rejected_with_its_own_message():
    """Strengthens the existing
    `test_a_token_missing_its_jti_claim_is_rejected` above with the
    exact diagnostic text, so a cosmetic string-literal mutation of
    this specific message can't hide behind the generic exception
    type already asserted there."""
    from app import config

    forged = jwt.encode(
        {"tenant_id": "tenant-a", "user_id": "user-a", "role": "owner"},
        config.LOCAL_JWT_SECRET,
        algorithm="HS256",
    )
    with pytest.raises(InvalidTokenError) as exc_info:
        decode_token(forged)
    assert str(exc_info.value) == "token is missing its jti claim"


def test_issue_token_defaults_to_exactly_one_hour_ttl():
    """`issue_token`'s own `ttl_seconds: int = 3600` default is a real,
    documented contract (a one-hour session), not an arbitrary number --
    pin it exactly so an off-by-one in the default can't silently widen
    (a longer-lived token) or narrow (sessions that expire early) it."""
    from app import config

    token = issue_token("tenant-a", "user-a", MembershipRole.OWNER)
    claims = jwt.decode(token, config.LOCAL_JWT_SECRET, algorithms=["HS256"])
    assert claims["exp"] - claims["iat"] == 3600


def test_tenant_scope_jti_defaults_to_none_when_omitted():
    """`TenantScope`'s own docstring says `jti` is `None` ONLY for a
    cookie/web-session-derived scope (`app/api/dependencies.py`'s other
    branch, which constructs `TenantScope` without a `jti=` at all) --
    pin that the dataclass's own default really is `None`, not some
    other falsy placeholder, since nothing else in this module exercises
    that default."""
    scope = TenantScope(tenant_id="tenant-a", user_id="user-a", role=MembershipRole.OWNER)
    assert scope.jti is None
