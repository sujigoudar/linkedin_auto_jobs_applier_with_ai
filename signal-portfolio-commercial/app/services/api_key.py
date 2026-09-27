"""CU-16 "API delivery, keys and exports" -- the real generate/list/
revoke service backing F-API-ACCESS. See dashboard_spec/screens/CU-16.md
for the full screen contract this implements a bounded slice of.

`generate_scoped_key` is the only place the raw secret ever exists --
callers must show it to the customer in that one response and never
persist, log, or return it again ("Generated key shown once only" is a
contract this module enforces by construction: only a SHA-256 hash is
ever written to the database).

`_VALID_SCOPES` is a fixed, safe allowlist -- "No trading/admin scope"
(CU-16's own field help text) means an unknown or overly powerful scope
string is always refused, never silently narrowed or accepted.
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.api_key import ApiKey

_VALID_SCOPES: frozenset[str] = frozenset({"alerts_read", "reports_read", "delivery_receive"})
_MAX_LABEL_LENGTH = 80


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _hash_secret(raw_secret: str) -> str:
    return hashlib.sha256(raw_secret.encode("utf-8")).hexdigest()


class InvalidApiKeyRequestError(Exception):
    pass


class ApiKeyNotFoundError(Exception):
    pass


def list_api_keys(session: Session, *, tenant_id: str, user_id: str) -> list[ApiKey]:
    return list(
        session.scalars(
            select(ApiKey)
            .where(ApiKey.tenant_id == tenant_id, ApiKey.user_id == user_id)
            .order_by(ApiKey.created_at.desc())
        ).all()
    )


def generate_scoped_key(
    session: Session,
    *,
    tenant_id: str,
    user_id: str,
    label: str,
    scopes: list[str],
    expires_at: datetime,
) -> tuple[ApiKey, str]:
    if not label or not (1 <= len(label) <= _MAX_LABEL_LENGTH):
        raise InvalidApiKeyRequestError(f"label must be 1..{_MAX_LABEL_LENGTH} characters")
    if not scopes:
        raise InvalidApiKeyRequestError("at least one scope is required")
    unknown = sorted(set(scopes) - _VALID_SCOPES)
    if unknown:
        raise InvalidApiKeyRequestError(f"unknown or unauthorized scope(s): {unknown} -- no trading/admin scope exists")
    if expires_at <= _now():
        raise InvalidApiKeyRequestError("expires_at must be in the future -- no never-expire default")

    raw_secret = secrets.token_urlsafe(32)
    key = ApiKey(
        tenant_id=tenant_id,
        user_id=user_id,
        label=label,
        scopes=list(scopes),
        key_hash=_hash_secret(raw_secret),
        expires_at=expires_at,
    )
    session.add(key)
    session.flush()
    return key, raw_secret


def revoke_api_key(session: Session, *, tenant_id: str, user_id: str, key_id: str) -> ApiKey:
    """Idempotent: revoking an already-revoked key is a no-op, not an
    error (CU-16's own "Revocation idempotent" acceptance text) -- only
    a key that never existed for this customer raises."""
    key = session.get(ApiKey, key_id)
    if key is None or key.tenant_id != tenant_id or key.user_id != user_id:
        raise ApiKeyNotFoundError(f"no API key {key_id!r} for this account")
    if key.revoked_at is None:
        key.revoked_at = _now()
        session.flush()
    return key
