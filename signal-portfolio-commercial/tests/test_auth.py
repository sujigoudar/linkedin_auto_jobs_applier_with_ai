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
    assert scope == TenantScope(tenant_id="tenant-a", user_id="user-a", role=MembershipRole.OWNER)


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
    with pytest.raises(InvalidTokenError):
        decode_token(forged)


def test_a_token_missing_tenant_id_is_rejected():
    from app import config

    forged = jwt.encode({"user_id": "user-a", "role": "owner"}, config.LOCAL_JWT_SECRET, algorithm="HS256")
    with pytest.raises(InvalidTokenError):
        decode_token(forged)
