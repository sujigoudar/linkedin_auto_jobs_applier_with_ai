"""Owner session authentication.

This is a single-owner app (one operator's own accounts/routing/positions),
not a multi-tenant product, so there is no user database -- `config.OWNER_PASSWORD`
is compared with a constant-time check (`hmac.compare_digest`), the same trust
level every other secret in this project already gets from an env var (see
README's Security notes). `POST /auth/login` exchanges that password for a
server-side session (`SignalStore.sessions`, so it's revocable and survives a
restart) carried as an httponly, samesite=strict cookie, plus a separate CSRF
token returned once in the login response body.

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

import hmac
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import Cookie, Header, HTTPException, Request

from app import config
from app.db import SignalStore

SESSION_COOKIE_NAME = "scr_session"


def auth_configured() -> bool:
    return bool(config.OWNER_PASSWORD) and bool(config.SESSION_SECRET)


def verify_password(password: str) -> bool:
    if not auth_configured():
        return False
    return hmac.compare_digest(password, config.OWNER_PASSWORD)


def create_session(store: SignalStore) -> tuple[str, str]:
    """Returns (session_id, csrf_token). The session_id is what goes in the
    cookie; the csrf_token is returned once, in the login response body."""
    session_id = secrets.token_urlsafe(32)
    csrf_token = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=config.SESSION_TTL_SECONDS)
    store.create_session(session_id, csrf_token, expires_at)
    return session_id, csrf_token


def _session_or_none(store: SignalStore, session_id: str | None) -> dict | None:
    if not session_id:
        return None
    row = store.get_session(session_id)
    if row is None:
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
                detail="owner authentication is not configured (set OWNER_PASSWORD and SESSION_SECRET)",
            )
        session = _session_or_none(self._get_store(), scr_session)
        if session is None:
            raise HTTPException(status_code=401, detail="not authenticated")
        if self.require_csrf and request.method not in ("GET", "HEAD", "OPTIONS"):
            if not x_csrf_token or not hmac.compare_digest(x_csrf_token, session["csrf_token"]):
                raise HTTPException(status_code=403, detail="missing or invalid CSRF token")
        return session
