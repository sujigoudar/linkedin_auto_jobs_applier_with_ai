"""A controlled, local JWT issuer for LOCAL_SIM/tests only (docs/02:
"Local tests use disposable PostgreSQL and a controlled JWT issuer").

A real deployment's customer/operator identity comes from Supabase Auth
-- an external deployment task, not something this module ever becomes.
This exists so application code and tests have a real, cryptographically
verifiable token (not a hand-built dict) to exercise tenant-scope
enforcement against, and so a decoded token's claims are exactly what
`app/db.py`'s row-level-security policies and
`app/services/permissions.py`'s role checks key off of.

## Revocation

Every token now carries a real `jti` claim -- a fresh `uuid.uuid4()` per
token, never a hash of the payload (two tokens with identical claims
minted a second apart must never collide on the same `jti`). `decode_token`
stays a pure, DB-free signature/claims check (used as-is by
`tests/test_auth.py`'s own pure-function tests). The real, DB-backed
denylist check lives in `verify_token` below, and is the function every
real request-verification call site (`app/api/dependencies.py`'s
`get_current_scope`) must use instead of calling `decode_token` directly
-- see `verify_token`'s own docstring for the fail-closed contract.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import jwt
from sqlalchemy.orm import Session

from app import config
from app.models.tenancy import MembershipRole
from app.services.token_revocation import is_token_revoked, record_issued_token

_ALGORITHM = "HS256"


@dataclass(frozen=True)
class TenantScope:
    tenant_id: str
    user_id: str
    role: MembershipRole
    #: The verified token's own `jti` claim. `None` only for a
    #: cookie/web-session-derived scope (`app/api/dependencies.py`'s
    #: other branch) -- those aren't JWTs and have no `jti` to carry.
    jti: str | None = None


class InvalidTokenError(Exception):
    pass


def issue_token(
    tenant_id: str,
    user_id: str,
    role: MembershipRole,
    *,
    ttl_seconds: int = 3600,
    session: Session | None = None,
) -> str:
    """Mints a fresh `jti` for every token, always. When `session` is
    given (every real route/service call site that has a DB session
    available), the new `jti` is also recorded via `record_issued_token`
    so it's later enumerable for "log out everywhere"
    (`revoke_all_tokens_for_user`). Callers with no session available
    (e.g. `ops/bootstrap.py`'s one-off provisioning script, or this
    module's own pure-function tests) still get a fully valid,
    individually-revocable token -- it just isn't enumerable until it's
    used at least once against a real DB-backed caller."""
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(seconds=ttl_seconds)
    jti = str(uuid.uuid4())
    claims = {
        "tenant_id": tenant_id,
        "user_id": user_id,
        "role": role.value,
        "jti": jti,
        "iat": now,
        "exp": expires_at,
    }
    token = jwt.encode(claims, config.LOCAL_JWT_SECRET, algorithm=_ALGORITHM)
    if session is not None:
        record_issued_token(
            session, jti=jti, tenant_id=tenant_id, user_id=user_id, role=role, issued_at=now, expires_at=expires_at
        )
    return token


def decode_token(token: str) -> TenantScope:
    """Fail closed on anything malformed, expired, or carrying an
    unrecognized role -- never returns a partially-trusted scope. Pure
    and DB-free: this does NOT check the revocation denylist -- real
    request verification must go through `verify_token` instead, which
    wraps this and adds that check."""
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

    jti = claims.get("jti")
    if not jti:
        raise InvalidTokenError("token is missing its jti claim")

    return TenantScope(tenant_id=tenant_id, user_id=user_id, role=role, jti=jti)


def verify_token(token: str, session: Session) -> TenantScope:
    """The real verification path every Bearer-token request must use
    (`app/api/dependencies.py`'s `get_current_scope`). Decodes+validates
    the token exactly as `decode_token` does, THEN checks the DB-backed
    denylist for its `jti`.

    Fail CLOSED, never open: any exception raised while checking the
    denylist (a DB error, a timeout, anything) is caught here and turned
    into `InvalidTokenError` -- it is never treated as "not revoked" by
    default and never silently skipped. A broken denylist check denies
    the request; it can never make an otherwise-revoked token pass."""
    scope = decode_token(token)
    try:
        revoked = is_token_revoked(session, jti=scope.jti)  # type: ignore[arg-type]
    except Exception as exc:
        raise InvalidTokenError(f"could not verify token has not been revoked: {exc}") from exc
    if revoked:
        raise InvalidTokenError("token has been revoked")
    return scope
