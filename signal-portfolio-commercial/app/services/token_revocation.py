"""The real service backing `app/models/token_revocation.py`'s two
tables -- see that module's own docstring for the schema/design. Called
from two places: `app/services/auth.py` (`issue_token` records a new
`jti` here; `verify_token` checks the denylist here on every real
request) and the owner/customer-facing "revoke" routes in
`app/api/dashboard_routes.py` (CU-13's "Revoke my API tokens" and
AD-16's per-staff-member "Revoke API tokens").

Every revocation also appends a real AuditEvent
(`app/services/audit_log.py`), the same append-only AD-18 store
`app/services/staff_access.py`'s invite/revoke already writes into --
revoking token access is exactly the kind of command-authority action
that store exists for.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.tenancy import MembershipRole
from app.models.token_revocation import IssuedToken, RevokedToken
from app.services.audit_log import append_audit_event


def record_issued_token(
    session: Session,
    *,
    jti: str,
    tenant_id: str,
    user_id: str,
    role: MembershipRole,
    issued_at: datetime,
    expires_at: datetime,
) -> None:
    """Called by `issue_token` for every real caller that has a DB
    session -- without this, `jti` is minted but never recorded, and
    "log out everywhere" would have nothing to enumerate."""
    session.add(
        IssuedToken(
            jti=jti,
            tenant_id=tenant_id,
            user_id=user_id,
            role=role.value,
            issued_at=issued_at,
            expires_at=expires_at,
        )
    )
    session.flush()


def is_token_revoked(session: Session, *, jti: str) -> bool:
    """A plain primary-key lookup -- deliberately lets any DB exception
    propagate to the caller rather than swallowing it. `app.services.
    auth.verify_token` wraps this call and turns ANY exception (a
    connection error, a timeout, anything) into rejection, never a
    silent skip -- fail CLOSED, never open."""
    return session.get(RevokedToken, jti) is not None


def revoke_token(
    session: Session,
    *,
    jti: str,
    tenant_id: str,
    expires_at: datetime,
    actor_user_id: str,
    target_user_id: str,
) -> None:
    """Revoke a single, already-known `jti`. Idempotent -- revoking an
    already-revoked `jti` is a no-op, not an error (matching this
    codebase's other revoke operations, e.g. `delete_web_session` on an
    already-gone session)."""
    if session.get(RevokedToken, jti) is not None:
        return
    session.add(
        RevokedToken(jti=jti, tenant_id=tenant_id, revoked_at=datetime.now(timezone.utc), expires_at=expires_at)
    )
    session.flush()
    append_audit_event(
        session,
        tenant_id=tenant_id,
        actor_user_id=actor_user_id,
        object_type="api_token",
        object_id=target_user_id,
        action="revoke_token",
    )


def revoke_all_tokens_for_user(session: Session, *, tenant_id: str, user_id: str, acting_user_id: str) -> int:
    """"Log out everywhere" for Bearer-token JWTs: denylists every
    currently-unexpired `jti` this module has ever recorded issuing for
    `user_id` within `tenant_id`. Returns the count actually newly
    revoked. A token issued without a session (never recorded in
    `issued_tokens`) cannot be enumerated here -- it can still be
    revoked individually via `revoke_token` once its own `jti` is
    known."""
    now = datetime.now(timezone.utc)
    issued = session.scalars(
        select(IssuedToken).where(
            IssuedToken.tenant_id == tenant_id,
            IssuedToken.user_id == user_id,
            IssuedToken.expires_at > now,
        )
    ).all()
    revoked_count = 0
    for row in issued:
        if session.get(RevokedToken, row.jti) is None:
            session.add(RevokedToken(jti=row.jti, tenant_id=tenant_id, revoked_at=now, expires_at=row.expires_at))
            revoked_count += 1
    session.flush()
    append_audit_event(
        session,
        tenant_id=tenant_id,
        actor_user_id=acting_user_id,
        object_type="api_token",
        object_id=user_id,
        action="revoke_all_tokens",
    )
    return revoked_count
