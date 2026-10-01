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

from app.db import set_tenant_scope
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


def require_tenant_scope(
    scope: TenantScope = Depends(get_current_scope),
    session: Session = Depends(get_db_session),
) -> TenantScope:
    """Centralizes `app/db.py`'s `set_tenant_scope()` as a dependency
    instead of an imperative call repeated at the top of ~85 individual
    route handlers in `app/api/dashboard_routes.py` -- the prior pattern
    worked (a forgotten call fails closed under RLS, never leaks a row
    out of tenant) but was structurally fragile: nothing caught a new
    route that forgot the call except that same fail-closed behavior in
    production. A route declares `scope: TenantScope = Depends(require_tenant_scope)`
    in place of `Depends(get_current_scope)` and gets both the verified
    scope AND a session already `set_tenant_scope`-d to it, with no
    other change to its body.

    FastAPI caches a dependency's result per request, keyed by the
    callable -- `Depends(get_db_session)` here and a route's own
    `session: Session = Depends(get_db_session)` resolve to the exact
    same `Session` instance within one request, so this really does run
    before the handler body touches that session, not on a second one.

    Deliberately NOT applied everywhere `get_current_scope` is used --
    left as explicit, documented exceptions rather than forced through
    this one shape:

    * `rights_register_page`, `deployment_status_page` and
      `platform_connection_wizard_page` (app/api/dashboard_routes.py)
      need the verified caller identity/role for a permission check but
      never run a tenant-scoped query (`RightsGrant` carries no
      `tenant_id`; the other two read no tenant-scoped table at all) --
      they keep `Depends(get_current_scope)` directly.
    * A handler that hits `session.rollback()` mid-request (e.g. to
      re-render a form after a validation error) still needs its own,
      explicit, POST-rollback `set_tenant_scope(session, scope.tenant_id)`
      call for the query that follows -- `set_tenant_scope` uses
      `set_config(..., is_local=true)`, which `rollback()` (like
      `commit()`) clears, and this dependency only ever runs once, at
      the start of request handling, before any rollback exists to undo
      it. ~8 such sites remain in dashboard_routes.py, each with a
      comment pointing back here.
    * `sign_in_submit` and `verify_email_page` (app/api/dashboard_routes.py)
      run BEFORE any `TenantScope` exists -- discovering the caller's
      tenant membership is the whole point of that code path (ADR-0009)
      -- so they call `set_current_user_scope` (a distinct, narrower,
      SELECT-only bootstrap policy) and then `set_tenant_scope` by hand
      once the tenant is known, never through this dependency.
    * `app/api/relay_routes.py` sets no tenant scope at all -- it runs
      on the separate, restricted `relay_role` connection
      (`get_relay_db_session`), which can only ever see one tenant's
      rows to begin with (see that module's own docstring).
    """
    set_tenant_scope(session, scope.tenant_id)
    return scope
