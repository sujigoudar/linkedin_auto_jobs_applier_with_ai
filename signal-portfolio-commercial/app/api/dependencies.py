"""FastAPI dependencies: a real, per-request DB session and a real,
verified caller identity -- the two things every route in this service
needs and must never fake or skip. Tests override `get_db_session` (to
bind the real disposable-Postgres session tests/conftest.py already set
up) but exercise `get_current_scope` for real, against a real signed
token from app/services/auth.py -- there is no auth bypass path a test
takes that a real caller couldn't also take.
"""
from __future__ import annotations

from collections.abc import Iterator

from fastapi import HTTPException, Request
from sqlalchemy.orm import Session

from app.services.auth import InvalidTokenError, TenantScope, decode_token


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


def get_current_scope(request: Request) -> TenantScope:
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")

    token = auth_header[len("Bearer "):]
    try:
        return decode_token(token)
    except InvalidTokenError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
