"""IssuedToken/RevokedToken -- the DB-backed denylist that makes a
Bearer-token JWT (app/services/auth.py's `issue_token`/`decode_token`)
revocable before its natural expiry.

The audit that prompted this table pair: cookie-based web sessions
(app/services/local_auth.py's `web_sessions`) were always revocable --
`delete_web_session` just deletes the row -- but a Bearer-token JWT was
cryptographically self-contained and stayed valid until it expired, with
no way for an owner/customer to kill one early (e.g. after a suspected
compromise, or "log out everywhere"). This is that missing mechanism.

`revoked_tokens` is keyed by the JWT's own `jti` claim (a real UUID per
token, generated fresh in `issue_token` -- never a hash of the payload,
which could collide across two genuinely different tokens carrying
identical claims). `expires_at` is copied from the token's own `exp`
claim at revoke time so a future pruning job can delete rows for tokens
that would have expired naturally anyway, without re-decoding anything
-- that job is not built in this slice, only the schema to support it.

`issued_tokens` is the companion table that makes "log out everywhere"
possible at all: a JWT is stateless by design, so without recording each
`jti` at issuance there would be no way to enumerate a principal's
currently-active tokens to revoke them. `app.services.auth.issue_token`
writes one row here whenever it is called with a `session` (every real
route/service call site that has one; a bare `issue_token(...)` with no
session, as ops/bootstrap.py's own one-off provisioning script and this
module's own pure-function tests already used before this change, still
works and simply isn't enumerable for "log out everywhere" -- it can
still be revoked individually once its own `jti` is known).

Neither table is tenant-scoped via `app/db.py`'s row-level-security
policy, same reasoning `user_identities`/`web_sessions`/`auth_tokens`
already document: a token is looked up by its own `jti` BEFORE any
tenant scope is otherwise established -- `tenant_id` is stored on both
tables for audit/reporting only, not as an RLS gate.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class IssuedToken(Base):
    """One row per JWT ever issued through `issue_token(..., session=...)`
    -- the record that lets "revoke all of this user's tokens" enumerate
    what's currently outstanding without needing to have kept every raw
    token around."""

    __tablename__ = "issued_tokens"

    jti: Mapped[str] = mapped_column(String, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    role: Mapped[str] = mapped_column(String, nullable=False)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)


class RevokedToken(Base):
    """The actual denylist -- a row here means the JWT carrying this
    `jti` must be rejected regardless of what its own signature/`exp`
    claim says. Presence, not any status column, is the signal: a
    missing row means "not revoked", checked with a straight primary-key
    lookup so the hot path (every authenticated request) stays cheap."""

    __tablename__ = "revoked_tokens"

    jti: Mapped[str] = mapped_column(String, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    revoked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    #: Copied from the token's own `exp` claim at revoke time (not
    #: computed later) -- a future pruning job can delete rows whose
    #: `expires_at` has passed (the underlying token would already be
    #: rejected as expired on its own) without decoding anything.
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
