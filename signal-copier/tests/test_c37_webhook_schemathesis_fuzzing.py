"""Track 40: a separate, narrowly-bounded Schemathesis pass specifically
for `POST /webhook/{source_name}` -- the one route
tests/test_c30_schemathesis_api_fuzzing.py's own module docstring
explicitly excludes from its generic, schema-wide GET-only pass (it can
place a real paper-broker order; see that file's own Track 40 note for
why this is split out rather than folded in).

Why this route is worth its own fuzz pass despite that exclusion: it is
the single most adversarial-input-exposed endpoint in this whole
service -- the only one that accepts untrusted, unauthenticated-by-
session, arbitrary-shaped JSON directly from the public internet (a
TradingView alert, or literally any `curl`). `tests/test_webhook_
source.py` and `tests/test_sig01_duplicate_submission_protection.py`
already hand-write a good set of malformed-body cases; this adds the
`hypothesis`-driven property a hand-written test suite structurally
can't: hundreds of generated header/body/path-param combinations this
project's own authors never thought to write by hand.

Scope: a REAL correct `X-Webhook-Secret` is attached to every generated
case (so generated bodies actually reach `webhook_source.parse`, not
just the auth gate) -- but ONLY `not_a_server_error` is checked, not
`response_schema_conformance`. Unlike the read-only GETs in C30, most
generated bodies here are missing a `symbol`/`side` or carry an invalid
one, so a 400 is the overwhelmingly common, CORRECT response -- and
this route's OpenAPI schema (app/main.py's own `-> dict` return
annotation) carries no declared error-response schema to conform
against in the first place, only a success shape. The property that
actually matters for an ingress route like this one is unconditional:
genuinely never crash with a 500, for ANY generated body/header/path
combination, authenticated or not."""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
import schemathesis
from hypothesis import HealthCheck, given, settings
from schemathesis.checks import not_a_server_error

import app.main as main_module
from app import config as app_config
from app.db import SignalStore
from app.models import DestinationAccount

WEBHOOK_SECRET = "test-webhook-secret-for-c37-fuzzing"


@pytest.fixture
def fuzz_setup(monkeypatch):
    monkeypatch.setattr(app_config, "OWNER_PASSWORD", "test-owner-password")
    monkeypatch.setattr(app_config, "SESSION_SECRET", "test-session-secret")
    monkeypatch.setattr(app_config, "WEBHOOK_SHARED_SECRET", WEBHOOK_SECRET)

    tmpdir = tempfile.TemporaryDirectory()
    store = SignalStore(Path(tmpdir.name) / "test.db")
    monkeypatch.setattr(main_module, "store", store)
    monkeypatch.setattr(main_module.engine, "store", store)
    main_module.routing_config.accounts.clear()
    main_module.routing_config.rules.clear()
    main_module.routing_config.accounts["acct1"] = DestinationAccount(account_id="acct1", broker="paper")

    schema = schemathesis.openapi.from_asgi("/openapi.json", main_module.app)
    schema = schema.include(path=["/webhook/{source_name}"], method="POST")

    try:
        yield schema
    finally:
        tmpdir.cleanup()


def test_webhook_ingress_never_5xx_on_arbitrary_generated_bodies(fuzz_setup):
    schema = fuzz_setup
    headers = {"X-Webhook-Secret": WEBHOOK_SECRET}

    @settings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.filter_too_much])
    @given(case=schema.as_strategy())
    def run(case):
        case.call_and_validate(headers=headers, checks=[not_a_server_error])

    run()


def test_webhook_ingress_never_5xx_without_the_correct_secret_either(fuzz_setup):
    """The same generated bodies, but with whatever header Schemathesis
    itself generates for `X-Webhook-Secret` (never the real one) --
    proving the auth-gate's own constant-time comparison
    (`hmac.compare_digest`, app/main.py) never itself raises on
    adversarial header values (wrong type coercion, empty string,
    extreme length) before `not_a_server_error` could catch it."""
    schema = fuzz_setup

    @settings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.filter_too_much])
    @given(case=schema.as_strategy())
    def run(case):
        case.call_and_validate(checks=[not_a_server_error])

    run()
