"""Track 40: the first Schemathesis API-fuzzing pass for this repo,
following the exact pattern signal-copier's own tests/test_c30_
schemathesis_api_fuzzing.py already established (same library, same
`not_a_server_error` check, same bounded-scope reasoning) rather than
inventing a new approach for this service.

Scope, deliberately bounded to this service's highest-exposure surfaces
per this track's own brief:

* `POST /internal/relay/ingest-batch` -- the ONE route signal-copier's
  relay worker calls, and the most adversarial-input-exposed route in
  this whole service (it ingests untrusted-shape JSON from a different
  process over the network, see app/api/relay_routes.py's own
  docstring). Schemathesis has no way to forge a valid
  `X-Relay-Signature` for a randomly generated body, so every generated
  case is expected to fail signature verification -- the property under
  test is narrower than signal-copier's own GET-route pass: a malformed/
  adversarial body and headers combination must ALWAYS get a clean 401/
  422, never a 500, regardless of what garbage reaches the signature
  check or (on the rare case a signature string happens to look
  superficially well-formed) the JSON body parser beneath it.
* `POST /api/v1/billing/webhook/stripe` -- same reasoning, same
  unforgeable-signature property, for the other real external-ingress
  route this service exposes.
* `GET /health`, `GET /system/readiness`, `GET /api/v1/me` -- read-only,
  side-effect-free, real response-schema-conformance fuzzing (the
  FULL signal-copier-style check: no 5xx AND the response matches this
  operation's own declared OpenAPI schema), carrying a real signed
  Bearer token so the owner-gated routes are actually exercised past
  their auth check, not just fuzzed into a 401 every time.
* `POST /auth/signin`, `POST /auth/signup` (ID-01/ID-02/ID-03) -- the
  public, unauthenticated account-creation/sign-in surface, the other
  highest-exposure routes per this track's brief. Only `not_a_server_
  error` is checked (not response-schema-conformance): both routes
  return a mix of `RedirectResponse`/`TemplateResponse` HTML with no
  declared Pydantic response model for FastAPI to generate a schema
  from, so "matches its own schema" isn't a meaningful check here the
  way it is for the three JSON GET routes above. Each test function
  gets its own freshly created tenant-free Postgres schema (`db_session`
  is function-scoped per tests/conftest.py), so even a `hypothesis`
  example that successfully creates an account is just adding harmless
  rows to this one disposable test database, never touching anything
  shared.

Nothing here targets a route that would place a real financial order
or otherwise mutate trading state -- this service has none (see
app/main.py's own module docstring: "Deliberately NOT here: any route
that would admit new financial exposure... those all sit behind CARD-3/
CARD-4"), so that exclusion signal-copier's own C30 file documents at
length doesn't even arise for this service yet.
"""
from __future__ import annotations

import schemathesis
from hypothesis import HealthCheck, given, settings
from schemathesis.checks import not_a_server_error
from schemathesis.specs.openapi.checks import response_schema_conformance

from app.api.dependencies import get_db_session
from app.main import create_app
from app.models.tenancy import MembershipRole
from app.services.auth import issue_token

INGRESS_PATHS = ["/internal/relay/ingest-batch", "/api/v1/billing/webhook/stripe"]
SAFE_READ_PATHS = ["/health", "/system/readiness", "/api/v1/me"]
PUBLIC_AUTH_PATHS = ["/auth/signin", "/auth/signup"]


def _app(db_session):
    app = create_app()

    def override_get_db_session():
        yield db_session

    app.dependency_overrides[get_db_session] = override_get_db_session
    return app


def _bearer_headers() -> dict:
    token = issue_token("tenant-a", "user-a", MembershipRole.OWNER)
    return {"Authorization": f"Bearer {token}"}


def test_external_ingress_routes_never_5xx_on_adversarial_bodies_without_a_valid_signature(db_session):
    """Neither ingress route can be fuzzed into a real application
    behavior (no example carries a valid signature), but a malformed
    body/headers combination reaching the signature-verification or
    JSON-parsing code ahead of it must always be a clean 401/422, never
    an unhandled 500."""
    app = _app(db_session)
    schema = schemathesis.openapi.from_asgi("/openapi.json", app)
    schema = schema.include(path=INGRESS_PATHS, method="POST")

    @settings(max_examples=20, deadline=None, suppress_health_check=[HealthCheck.filter_too_much])
    @given(case=schema.as_strategy())
    def run(case):
        case.call_and_validate(checks=[not_a_server_error])

    run()


def test_safe_read_endpoints_never_5xx_and_match_their_schema(db_session):
    app = _app(db_session)
    headers = _bearer_headers()
    schema = schemathesis.openapi.from_asgi("/openapi.json", app)
    schema = schema.include(path=SAFE_READ_PATHS, method="GET")

    @settings(max_examples=20, deadline=None, suppress_health_check=[HealthCheck.filter_too_much])
    @given(case=schema.as_strategy())
    def run(case):
        case.call_and_validate(headers=headers, checks=[not_a_server_error, response_schema_conformance])

    run()


def test_public_signin_signup_never_5xx_on_adversarial_form_bodies(db_session):
    app = _app(db_session)
    schema = schemathesis.openapi.from_asgi("/openapi.json", app)
    schema = schema.include(path=PUBLIC_AUTH_PATHS, method="POST")

    @settings(max_examples=20, deadline=None, suppress_health_check=[HealthCheck.filter_too_much])
    @given(case=schema.as_strategy())
    def run(case):
        case.call_and_validate(checks=[not_a_server_error])
        db_session.rollback()  # a generated example that got partway through a real write must never leak into the next one

    run()
