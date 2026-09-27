"""A controlled, local JWT issuer for LOCAL_SIM/tests only (docs/02:
"Local tests use disposable PostgreSQL and a controlled JWT issuer").

A real deployment's customer/operator identity comes from Supabase Auth
-- an external deployment task, not something this module ever becomes.
This exists so application code and tests have a real, cryptographically
verifiable token (not a hand-built dict) to exercise tenant-scope
enforcement against, and so a decoded token's claims are exactly what
`app/db.py`'s row-level-security policies and
`app/services/permissions.py`'s role checks key off of.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import jwt

from app import config
from app.models.tenancy import MembershipRole

_ALGORITHM = "HS256"


@dataclass(frozen=True)
class TenantScope:
    tenant_id: str
    user_id: str
    role: MembershipRole


class InvalidTokenError(Exception):
    pass


def issue_token(tenant_id: str, user_id: str, role: MembershipRole, *, ttl_seconds: int = 3600) -> str:
    now = datetime.now(timezone.utc)
    claims = {
        "tenant_id": tenant_id,
        "user_id": user_id,
        "role": role.value,
        "iat": now,
        "exp": now + timedelta(seconds=ttl_seconds),
    }
    return jwt.encode(claims, config.LOCAL_JWT_SECRET, algorithm=_ALGORITHM)


def decode_token(token: str) -> TenantScope:
    """Fail closed on anything malformed, expired, or carrying an
    unrecognized role -- never returns a partially-trusted scope."""
    try:
        claims = jwt.decode(token, config.LOCAL_JWT_SECRET, algorithms=[_ALGORITHM])
    except jwt.PyJWTError as exc:
        raise InvalidTokenError(str(exc)) from exc

    try:
        role = MembershipRole(claims["role"])
    except (KeyError, ValueError) as exc:
        raise InvalidTokenError(f"unrecognized or missing role claim: {exc}") from exc

    tenant_id = claims.get("tenant_id")
    user_id = claims.get("user_id")
    if not tenant_id or not user_id:
        raise InvalidTokenError("token is missing tenant_id or user_id claims")

    return TenantScope(tenant_id=tenant_id, user_id=user_id, role=role)
