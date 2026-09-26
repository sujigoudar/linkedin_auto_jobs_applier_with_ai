"""C30: Schemathesis API fuzzing against this app's own OpenAPI schema,
supplementing (never replacing) the hand-written endpoint tests -- it is
far more likely to stumble on an untested query-param/path-param edge
case (empty strings, huge numbers, unicode, missing optional params) than
any fixed set of examples a person writes by hand.

Scope, deliberately bounded rather than fuzzing the whole app:

* Only GET operations that are read-only and side-effect-free are
  included (see SAFE_PATHS below). Nothing here can place, close, or
  flatten a position, create/delete an account, or mutate routing/
  provider config -- fuzzing those would mean generating random
  financial-mutation requests against shared app state, which is a much
  bigger, riskier undertaking than this bounded pass, and firmly out of
  scope for "no live order changes" (this project's own governing
  instruction). A future, separately-reviewed pass could target mutating
  endpoints against a fully isolated per-example store.
* GET /context/fx/{base}/{quote} is excluded even though it's read-only:
  it always makes a real outbound network call (no NotConfigured gate),
  so fuzzing it would mean firing arbitrary generated ticker-like strings
  at a live third-party API from a test suite. The other /context/*
  endpoints are excluded too since only the FX one is unconditionally
  live; the SEC/FRED ones are already covered adequately by their own
  hand-written tests.
* Every included operation is owner-read-protected; the fuzz session
  carries a real session cookie (created directly via
  `app.auth.create_session`, not a network login round-trip) since GET
  requests don't need the separate CSRF header (see `RequireOwner`'s
  docstring in app/auth.py).

What this checks: every generated request against an included operation
gets a response schemathesis's own `not_a_server_error` check accepts
(no unhandled 5xx) and, via `response_schema_conformance`, one that
actually matches this operation's declared OpenAPI response schema --
catching route implementations that drifted from their own declared
contract, not just crashes.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
import schemathesis
from hypothesis import given, settings
from schemathesis.checks import not_a_server_error
from schemathesis.specs.openapi.checks import response_schema_conformance

import app.main as main_module
from app import config as app_config
from app.auth import SESSION_COOKIE_NAME, create_session
from app.db import SignalStore
from app.models import DestinationAccount

SAFE_PATHS = [
    "/health",
    "/metrics",
    "/positions",
    "/accounts/{account_id}/economics",
    "/accounts/{account_id}/execution-quality",
    "/brokers",
    "/providers",
    "/accounts",
    "/routing-rules",
    "/signals",
    "/orders",
]


@pytest.fixture
def fuzz_setup(monkeypatch):
    monkeypatch.setattr(app_config, "OWNER_PASSWORD", "test-owner-password")
    monkeypatch.setattr(app_config, "SESSION_SECRET", "test-session-secret")

    tmpdir = tempfile.TemporaryDirectory()
    store = SignalStore(Path(tmpdir.name) / "test.db")
    monkeypatch.setattr(main_module, "store", store)
    monkeypatch.setattr(main_module.engine, "store", store)
    main_module.routing_config.accounts.clear()
    main_module.routing_config.accounts["acct1"] = DestinationAccount(account_id="acct1", broker="paper")

    session_id, _csrf_token = create_session(store)

    schema = schemathesis.openapi.from_asgi("/openapi.json", main_module.app)
    schema = schema.include(path=SAFE_PATHS, method="GET")

    try:
        yield schema, session_id
    finally:
        tmpdir.cleanup()


def test_safe_read_endpoints_never_5xx_and_match_their_schema(fuzz_setup):
    schema, session_id = fuzz_setup
    cookies = {SESSION_COOKIE_NAME: session_id}

    @settings(max_examples=20, deadline=None)
    @given(case=schema.as_strategy())
    def run(case):
        case.call_and_validate(cookies=cookies, checks=[not_a_server_error, response_schema_conformance])

    run()
