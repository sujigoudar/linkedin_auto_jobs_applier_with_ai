"""Owner session authentication.

This is a single-owner app (one operator's own accounts/routing/positions),
not a multi-tenant product, so there is no user database. Two mutually
exclusive ways to configure the one owner credential: `config.OWNER_PASSWORD`
(legacy, plain) is compared with a constant-time check (`hmac.compare_digest`),
the same trust level every other secret in this project already gets from an
env var; `config.OWNER_PASSWORD_HASH` (C05, preferred) is an argon2id hash
(via `pwdlib`) so the configured value itself isn't a directly usable
credential even if it leaks (a log dump, a leaked `.env`, a config export) --
see app/config.py's docstring for how to generate one. `POST /auth/login`
exchanges the password for a server-side session (`SignalStore.sessions`, so
it's revocable and survives a restart) carried as an httponly,
samesite=strict cookie, plus a separate CSRF token returned once in the
login response body.

## Why a session cookie AND a separate CSRF token

A cookie alone isn't enough: the browser attaches cookies to *any* request to
this origin, including ones a malicious page tricks it into making (classic
CSRF). The CSRF token is returned only in the login response JSON, never in a
cookie, so only JavaScript that actually read the login response can put it
in the `X-CSRF-Token` header -- a cross-site form has no way to read it. Every
mutating request (anything but GET) must carry a valid session cookie AND the
matching `X-CSRF-Token` header; either missing or wrong is a 401/403.

## Fail-closed, not fail-open

If `OWNER_PASSWORD` or `SESSION_SECRET` isn't set, this module refuses to
authenticate anyone -- `require_owner` raises 503 for every request rather
than silently treating "auth not configured" as "no auth needed". A
misconfigured deployment is loud and broken, not quietly public.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import Cookie, Header, HTTPException, Request
from pwdlib import PasswordHash
from pwdlib.exceptions import UnknownHashError

from app import config
from app.db import SignalStore

SESSION_COOKIE_NAME = "scr_session"

logger = logging.getLogger(__name__)

#: C05: argon2id via pwdlib, for operators who set OWNER_PASSWORD_HASH
#: instead of the legacy plaintext OWNER_PASSWORD (see app/config.py's
#: docstring for the generation command).
_password_hasher = PasswordHash.recommended()


def _misconfigured_both_credentials_set() -> bool:
    return bool(config.OWNER_PASSWORD) and bool(config.OWNER_PASSWORD_HASH)


def auth_configured() -> bool:
    if _misconfigured_both_credentials_set():
        # Ambiguous which one is authoritative -- fail closed rather than
        # silently pick one (see verify_password's identical check).
        return False
    has_credential = bool(config.OWNER_PASSWORD) or bool(config.OWNER_PASSWORD_HASH)
    return has_credential and bool(config.SESSION_SECRET)


def _active_credential_value() -> str:
    """Whichever of OWNER_PASSWORD/OWNER_PASSWORD_HASH is actually
    configured -- used only to derive the credential epoch fingerprint
    (see _credential_epoch), never compared against user input directly."""
    return config.OWNER_PASSWORD_HASH or config.OWNER_PASSWORD


def _credential_epoch() -> str:
    """A short fingerprint of the CURRENT credential+SESSION_SECRET pair.
    Every session is stamped with the epoch active when it was created; a
    session is only honored while its stamp still matches the current one
    (see `_session_or_none`). SEC-04: SESSION_SECRET used to be checked
    only as a configured/not-configured boolean flag -- changing either
    secret had no effect on sessions already issued, since nothing about a
    session was ever derived from either value. Rotating EITHER secret (or
    switching between OWNER_PASSWORD and OWNER_PASSWORD_HASH) now revokes
    every existing session automatically, with no separate sign-out-all
    step required (though `delete_all_sessions` remains available for
    revoking without rotating anything)."""
    fingerprint = f"{_active_credential_value()}:{config.SESSION_SECRET}".encode()
    return hashlib.sha256(fingerprint).hexdigest()[:16]


def verify_password(password: str) -> bool:
    if not auth_configured():
        return False
    if config.OWNER_PASSWORD_HASH:
        try:
            return _password_hasher.verify(password, config.OWNER_PASSWORD_HASH)
        except UnknownHashError:
            # A malformed/corrupted OWNER_PASSWORD_HASH must fail closed,
            # not raise a 500 that could leak into an error response.
            logger.error("OWNER_PASSWORD_HASH is set but is not a hash pwdlib recognizes -- rejecting all logins")
            return False
    # hmac.compare_digest raises TypeError for a non-ASCII `str` (it only
    # accepts ASCII-only str or arbitrary bytes) -- a non-ASCII password
    # used to reach this as a raw 500 (SEC-02). Comparing as UTF-8 bytes
    # is unicode-safe and still constant-time.
    return hmac.compare_digest(password.encode("utf-8"), config.OWNER_PASSWORD.encode("utf-8"))


def create_session(store: SignalStore) -> tuple[str, str]:
    """Returns (session_id, csrf_token). The session_id is what goes in the
    cookie; the csrf_token is returned once, in the login response body."""
    session_id = secrets.token_urlsafe(32)
    csrf_token = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=config.SESSION_TTL_SECONDS)
    store.create_session(session_id, csrf_token, expires_at, credential_epoch=_credential_epoch())
    return session_id, csrf_token


def _session_or_none(store: SignalStore, session_id: str | None) -> dict | None:
    if not session_id:
        return None
    row = store.get_session(session_id)
    if row is None:
        return None
    if row.get("credential_epoch") != _credential_epoch():
        # OWNER_PASSWORD or SESSION_SECRET changed since this session was
        # issued -- treat it as revoked, not merely stale.
        store.delete_session(session_id)
        return None
    expires_at = datetime.fromisoformat(row["expires_at"])
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at < datetime.now(timezone.utc):
        store.delete_session(session_id)
        return None
    return row


class RequireOwner:
    """A FastAPI dependency: `Depends(RequireOwner(lambda: store))`. Raises
    503 if auth isn't configured, 401 if there's no valid session, 403 if
    the session is valid but the request is a mutation missing/mismatching
    its CSRF token. GET requests only need the session (read access doesn't
    need CSRF protection -- there's nothing for a forged GET to change).

    Takes a zero-arg getter rather than a `SignalStore` directly so it keeps
    resolving whatever `store` currently is at call time -- app/main.py's
    module-level `store` is reassigned by tests (`monkeypatch.setattr`), and
    a `RequireOwner` built once at route-decoration time would otherwise
    keep talking to the original store forever."""

    def __init__(self, get_store, *, require_csrf: bool = True):
        self._get_store = get_store
        self.require_csrf = require_csrf

    def __call__(
        self,
        request: Request,
        scr_session: str | None = Cookie(default=None),
        x_csrf_token: str | None = Header(default=None),
    ) -> dict:
        if not auth_configured():
            raise HTTPException(
                status_code=503,
                detail="owner authentication is not configured (set OWNER_PASSWORD or OWNER_PASSWORD_HASH, "
                "not both, plus SESSION_SECRET)",
            )
        session = _session_or_none(self._get_store(), scr_session)
        if session is None:
            raise HTTPException(status_code=401, detail="not authenticated")
        if self.require_csrf and request.method not in ("GET", "HEAD", "OPTIONS"):
            if not x_csrf_token or not hmac.compare_digest(x_csrf_token, session["csrf_token"]):
                raise HTTPException(status_code=403, detail="missing or invalid CSRF token")
        return session
