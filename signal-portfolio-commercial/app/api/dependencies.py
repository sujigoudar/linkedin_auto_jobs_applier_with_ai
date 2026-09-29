"""FastAPI dependencies: a real, per-request DB session and a real,
verified caller identity -- the two things every route in this service
needs and must never fake or skip. Tests override `get_db_session` (to
bind the real disposable-Postgres session tests/conftest.py already set
up) but exercise `get_current_scope` for real, against a real signed
token from app/services/auth.py -- there is no auth bypass path a test
takes that a real caller couldn't also take.

`get_current_scope` accepts EITHER a Bearer token (the original,
still-used-by-relay/API-caller path) OR the ID-01/ID-02/ID-03 web
session cookie (app/services/local_auth.py) -- a browser navigating
this app's own HTML pages has no natural way to attach a bearer header
to every GET, so the cookie path is what those pages actually use. A
cookie-authenticated MUTATION additionally requires a matching CSRF
token (same two-part design, same reason, as signal-copier's own
app/auth.py `RequireOwner`) -- a Bearer-token caller needs no such
check (nothing auto-attaches a custom header the way a cookie
auto-attaches, so Bearer auth was never CSRF-vulnerable in the first
place).

The token is accepted from EITHER an `X-CSRF-Token` header (a caller
using `fetch`/XHR) OR a `csrf_token` form field (every real
`<form method="post">` in app/templates/*.html -- this deployment has
no JavaScript layer, so a plain HTML form submit can only ever carry
the token as a field, never a custom header). Checking form data here
requires this dependency itself to be `async` (`await request.form()`);
Starlette caches the parsed body, so this does not re-read the request
out from under a route's own `Form(...)` parameters below it. Prior to
this, `create_web_session`'s own `csrf_token` return value was
generated but never actually delivered anywhere a browser could read it
back (not a cookie, not a template context, not a hidden field) --
every real cookie-authenticated form POST in this app was rejected with
403 "missing or invalid CSRF token", a total, untested (every existing
POST test used Bearer auth, which this check exempts) break of the
browser-facing write path. See `_set_csrf_cookie` in
app/api/dashboard_routes.py for the other half of the fix (the cookie
that carries the token to the browser) and `_base.html`'s own injection
script (the one place that reads it back into every form, so no
per-template change was needed).
"""
from __future__ import annotations

import hmac
from collections.abc import Iterator

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.models.tenancy import MembershipRole
from app.services.auth import InvalidTokenError, TenantScope, verify_token
from app.services.local_auth import get_web_session

SESSION_COOKIE_NAME = "cp_session"


def get_db_session(request: Request) -> Iterator[Session]:
    session = request.app.state.session_factory()
    try:
        yield session
    finally:
        session.close()


def get_relay_db_session(request: Request) -> Iterator[Session]:
    """A session bound to the restricted `relay_role` connection
    (app/db.py's `_apply_relay_role_access`), never the admin/app
    connection `get_db_session` uses -- app/api/relay_routes.py is the
    only route that depends on this."""
    session = request.app.state.relay_session_factory()
    try:
        yield session
    finally:
        session.close()


async def get_current_scope(request: Request, db: Session = Depends(get_db_session)) -> TenantScope:
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[len("Bearer "):]
        try:
            return verify_token(token, db)
        except InvalidTokenError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc

    session_id = request.cookies.get(SESSION_COOKIE_NAME)
    web_session = get_web_session(db, session_id=session_id)
    if web_session is None:
        raise HTTPException(status_code=401, detail="missing bearer token or session cookie")

    if request.method not in ("GET", "HEAD", "OPTIONS"):
        csrf_token = request.headers.get("X-CSRF-Token")
        if not csrf_token:
            content_type = request.headers.get("Content-Type", "")
            if "form" in content_type:
                form = await request.form()
                form_token = form.get("csrf_token")
                csrf_token = form_token if isinstance(form_token, str) else None
        # Constant-time comparison -- a plain `!=` here would leak, via
        # response-timing, how many leading bytes of a guessed
        # csrf_token match the real one, the same class of bug
        # `relay_auth.py`/`stripe_webhook.py` already avoid with
        # `hmac.compare_digest` for their own secrets.
        if not csrf_token or not hmac.compare_digest(csrf_token, web_session.csrf_token):
            raise HTTPException(status_code=403, detail="missing or invalid CSRF token")

    try:
        role = MembershipRole(web_session.role)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="session has an unrecognized role") from exc
    return TenantScope(tenant_id=web_session.tenant_id, user_id=web_session.user_id, role=role)
