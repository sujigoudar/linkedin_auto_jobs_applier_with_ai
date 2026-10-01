"""ID-01/ID-02/ID-03 (dashboard_spec/screens/) -- a real, working local
sign-in/sign-up/verify/recover system for THIS deployment.

This is deliberately NOT a Supabase Auth integration -- app/services/
auth.py's own module docstring already says a real production
deployment's customer/operator identity comes from Supabase Auth, an
external deployment task this codebase never becomes. What was
genuinely missing before this slice (confirmed: no password field on
`UserIdentity`, no session-cookie mechanism, no `/auth` route of any
kind anywhere in this app) was a real, usable way for anyone --
operator or customer -- to actually log in through a browser at all;
`ops/bootstrap.py` minting a token out of band was the only path. This
module is that missing piece, built the same way signal-copier's own
app/auth.py already solved the identical problem for its own single-
owner case: `pwdlib` (argon2id) password hashing, a server-side
revocable session (here, the `web_sessions` table) carried as an
httponly cookie, plus a separate CSRF token returned once in the
login/verify response body.

## Session audit -- a real writer into AD-18's audit store

`create_web_session`/`delete_web_session` each append a real AuditEvent
(app/services/audit_log.py, object_type="session", action "login"/
"logout") -- the same append-only store AD-16's own invite/revoke
already writes into. This resolves AD-16's own "Session audit is NOT
implemented" gap and AD-11's own "no ... audit-log model exists" gap
for a customer's own login/logout history; both were previously
documented as unsupported before this store existed.

## Email delivery is NOT wired -- disclosed, not hidden

`create_account`/`request_password_reset` generate a real, single-use,
expiring `AuthToken` and return it directly to the caller -- there is
no SMTP/SendGrid/any email-provider integration in this codebase (the
same disclosed gap this project already documents for SMS/WhatsApp
providers elsewhere: the token/message is real, actual DELIVERY needs
a real provider account). A real deployment's route handler is
expected to email that token's own verify/reset link; until an email
provider is wired in, the token is only usable by whoever the caller
hands it to directly (e.g. shown once on the confirmation page in a
LOCAL_SIM/dev deployment -- see app/api/dashboard_routes.py's own
ID-01/ID-02/ID-03 route docstrings for exactly how it's surfaced).
"""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

from pwdlib import PasswordHash
from pwdlib.exceptions import UnknownHashError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import set_tenant_scope
from app.models.local_auth import AuthToken, AuthTokenType, WebSession
from app.models.tenancy import Membership, MembershipRole, Tenant, UserIdentity
from app.services.audit_log import append_audit_event

_password_hasher = PasswordHash.recommended()

#: A verification/reset link is only useful for this long -- short enough
#: that a leaked-but-unused token (e.g. in a server access log) is a
#: narrow window, long enough a real emailed link survives a normal
#: inbox-check delay.
_TOKEN_TTL = timedelta(hours=24)
_SESSION_TTL = timedelta(days=7)


class AccountAlreadyExistsError(Exception):
    pass


class InvalidCredentialsError(Exception):
    pass


class InvalidTokenError(Exception):
    pass


def _reject_nul_bytes(*values: str, on_reject: type[Exception]) -> None:
    """Postgres `text`/`varchar` columns cannot store a NUL (0x00) byte --
    `UserIdentity.email == value` with one embedded raises a raw
    `psycopg.DataError` straight out of the driver, below every ORM/app
    error class, turning an ordinary bad-credentials/bad-signup case into
    an unhandled 500 (found by Track 39's own adversarial-form-body
    fuzzing: `email=%00&password=...` against `/auth/signin`). A NUL byte
    can never appear in a real stored email/password, so rejecting it
    here with the SAME error each caller already raises for an ordinary
    lookup miss/duplicate is indistinguishable from that case -- no new
    enumeration signal, no new caller-visible error type."""
    for value in values:
        if "\x00" in value:
            raise on_reject()


def create_account(session: Session, *, email: str, password: str, tenant_display_name: str) -> tuple[UserIdentity, AuthToken]:
    """ID-01's own F-IDENTITY "Create account" step. Creates a real
    `UserIdentity` + a new `Tenant` + a CUSTOMER-role `Membership` in
    one transaction -- self-service signup is the CUSTOMER path (every
    CU-0X screen's own `require_permission` check, e.g.
    "view_own_customer_overview", is scoped to `Role.CUSTOMER`
    specifically, per app/services/permissions.py -- OWNER is the
    platform-operator role, provisioned only out of band via
    ops/bootstrap.py, never through this self-service form). Refuses an
    email that's already registered (No email enumeration -- ID-01's
    own acceptance text -- is honored by the caller raising the SAME
    generic error message it would for any other signup failure, never
    "that email is taken").

    `memberships` is one of ADR-0001's `_TENANT_SCOPED_TABLES` (FORCE ROW
    LEVEL SECURITY, `tenant_isolation` policy) -- against the real,
    RLS-enforced `commercial`/`app_role` connection this route actually
    runs on in production (never the test suite's superuser `db_session`
    fixture, which silently bypasses RLS and would never have caught
    this), inserting the new Membership row with no `app.tenant_id`
    session scope set fails the policy's WITH CHECK outright
    ("new row violates row-level security policy for table
    'memberships'") -- self-service signup was completely broken. The
    new tenant's own id is known immediately after `flush()`, so
    `set_tenant_scope` here is exactly ADR-0001's own documented
    pattern, not a new exception to it."""
    _reject_nul_bytes(email, password, on_reject=AccountAlreadyExistsError)
    existing = session.scalar(select(UserIdentity).where(UserIdentity.email == email))
    if existing is not None:
        raise AccountAlreadyExistsError(email)

    user = UserIdentity(email=email, password_hash=_password_hasher.hash(password))
    tenant = Tenant(display_name=tenant_display_name, environment="LOCAL_SIM")
    session.add_all([user, tenant])
    session.flush()
    set_tenant_scope(session, tenant.tenant_id)
    session.add(Membership(tenant_id=tenant.tenant_id, user_id=user.user_id, role=MembershipRole.CUSTOMER))

    token = _issue_token(session, user_id=user.user_id, token_type=AuthTokenType.EMAIL_VERIFICATION)
    session.commit()
    return user, token


def verify_email(session: Session, *, token: str) -> UserIdentity:
    """ID-02's own real verification consumption -- single-use: a
    second attempt with the same token raises InvalidTokenError, never
    silently re-confirms."""
    user = _consume_token(session, token=token, expected_type=AuthTokenType.EMAIL_VERIFICATION)
    user.email_verified_at = datetime.now(timezone.utc)
    session.commit()
    return user


def authenticate(session: Session, *, email: str, password: str) -> UserIdentity:
    """ID-01's own F-IDENTITY "Sign in" step. Raises the SAME
    InvalidCredentialsError whether the email doesn't exist or the
    password is wrong -- no email enumeration via a different error for
    each case."""
    _reject_nul_bytes(email, password, on_reject=InvalidCredentialsError)
    user = session.scalar(select(UserIdentity).where(UserIdentity.email == email))
    if user is None or user.password_hash is None:
        raise InvalidCredentialsError()
    try:
        if not _password_hasher.verify(password, user.password_hash):
            raise InvalidCredentialsError()
    except UnknownHashError as exc:
        raise InvalidCredentialsError() from exc
    return user


def request_password_reset(session: Session, *, email: str) -> AuthToken | None:
    """ID-03's own real reset-token issuance. Returns None (not an
    error) for an unknown email -- the caller must show the SAME
    "if that email has an account, a reset link was sent" message
    either way; returning None vs a token is exactly the signal it
    needs to do that without enumerating accounts."""
    if "\x00" in email:
        return None
    user = session.scalar(select(UserIdentity).where(UserIdentity.email == email))
    if user is None:
        return None
    token = _issue_token(session, user_id=user.user_id, token_type=AuthTokenType.PASSWORD_RESET)
    session.commit()
    return token


def reset_password(session: Session, *, token: str, new_password: str) -> UserIdentity:
    """ID-03's own real reset consumption -- single-use, same as
    verify_email."""
    user = _consume_token(session, token=token, expected_type=AuthTokenType.PASSWORD_RESET)
    user.password_hash = _password_hasher.hash(new_password)
    session.commit()
    return user


def create_web_session(session: Session, *, user_id: str, tenant_id: str, role: MembershipRole) -> tuple[str, str]:
    """Returns (session_id, csrf_token) -- session_id goes in the
    cookie, csrf_token is returned once in the response body. Same
    two-value contract as signal-copier's own app/auth.py
    `create_session`. Also appends a real "login" AuditEvent
    (object_type="session", object_id=user_id) -- see this module's own
    docstring's "Session audit" section."""
    session_id = secrets.token_urlsafe(32)
    csrf_token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    session.add(
        WebSession(
            session_id=session_id,
            user_id=user_id,
            tenant_id=tenant_id,
            role=role.value,
            csrf_token=csrf_token,
            created_at=now,
            expires_at=now + _SESSION_TTL,
        )
    )
    append_audit_event(
        session,
        tenant_id=tenant_id,
        actor_user_id=user_id,
        object_type="session",
        object_id=user_id,
        action="login",
    )
    session.commit()
    return session_id, csrf_token


def get_web_session(session: Session, *, session_id: str | None) -> WebSession | None:
    """Returns None (never raises) for a missing/expired/unknown
    session_id -- the caller (app/api/dependencies.py) is expected to
    turn that into a 401, not a 500."""
    if not session_id:
        return None
    row = session.get(WebSession, session_id)
    if row is None:
        return None
    expires_at = row.expires_at if row.expires_at.tzinfo else row.expires_at.replace(tzinfo=timezone.utc)
    if expires_at < datetime.now(timezone.utc):
        session.delete(row)
        session.commit()
        return None
    return row


def delete_web_session(session: Session, *, session_id: str) -> None:
    """Also appends a real "logout" AuditEvent for the session being
    revoked -- see this module's own docstring's "Session audit"
    section. A session_id that doesn't exist (already expired/revoked)
    writes nothing, same as the delete itself being a no-op."""
    row = session.get(WebSession, session_id)
    if row is not None:
        append_audit_event(
            session,
            tenant_id=row.tenant_id,
            actor_user_id=row.user_id,
            object_type="session",
            object_id=row.user_id,
            action="logout",
        )
        session.delete(row)
        session.commit()


def _issue_token(session: Session, *, user_id: str, token_type: AuthTokenType) -> AuthToken:
    now = datetime.now(timezone.utc)
    token = AuthToken(
        token=secrets.token_urlsafe(32),
        user_id=user_id,
        token_type=token_type,
        created_at=now,
        expires_at=now + _TOKEN_TTL,
    )
    session.add(token)
    session.flush()
    return token


def _consume_token(session: Session, *, token: str, expected_type: AuthTokenType) -> UserIdentity:
    row = session.get(AuthToken, token)
    if row is None or row.token_type != expected_type or row.consumed_at is not None:
        raise InvalidTokenError()
    expires_at = row.expires_at if row.expires_at.tzinfo else row.expires_at.replace(tzinfo=timezone.utc)
    if expires_at < datetime.now(timezone.utc):
        raise InvalidTokenError()
    row.consumed_at = datetime.now(timezone.utc)
    user = session.get(UserIdentity, row.user_id)
    if user is None:
        raise InvalidTokenError()
    return user
