"""Commercial-service configuration.

Deliberately separate from `signal-copier/app/config.py` -- this is a
different process with a different database (Postgres, not the owner's
SQLite execution store) and different secrets. Nothing here can read or
override the owner's execution-service configuration.
"""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class _Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="", case_sensitive=True, extra="ignore")

    #: A real Postgres DSN in every real environment. Tests never read this
    #: -- tests/conftest.py's `postgres_url` fixture spins up and tears down
    #: its own disposable cluster and passes that URL directly, so a
    #: missing/placeholder value here never accidentally points a test at
    #: a real database.
    COMMERCIAL_DATABASE_URL: str = "postgresql+psycopg://commercial:commercial@localhost:5432/commercial"

    #: LOCAL_SIM | INTEGRATION_ISOLATED | PLATFORM_DEMO | PRIVATE_SHADOW |
    #: COMMERCIAL_LIVE -- see spec/docs/02_architecture_and_tenancy.md's
    #: "Environments" section. Defaults to the safest, most restrictive
    #: value; COMMERCIAL_LIVE is never the default anywhere.
    ENVIRONMENT: str = "LOCAL_SIM"

    #: Signing secret for the LOCAL/test JWT issuer (app/services/auth.py).
    #: docs/02: "Local tests use disposable PostgreSQL and a controlled JWT
    #: issuer" -- a real deployment's customer identity comes from Supabase
    #: Auth (an external deployment task), never from this issuer, which
    #: exists only so local code and tests have a real, verifiable token to
    #: exercise tenant-scope enforcement against.
    LOCAL_JWT_SECRET: str = "LOCAL_SIM-not-a-real-secret-change-if-ever-deployed"

    #: The Stripe webhook signing secret app/services/stripe_webhook.py
    #: verifies incoming events against. No real Stripe account exists in
    #: this environment (CARD-4 not yet obtained) -- this default is a
    #: placeholder for local/test use only, never a real `whsec_...`
    #: value, and must be replaced with the real one issued by Stripe's
    #: dashboard before this endpoint is ever pointed at a real account.
    STRIPE_WEBHOOK_SECRET: str = "whsec_LOCAL_SIM_not_a_real_stripe_secret"

    #: Postgres DSN for the restricted `relay_role` connection
    #: app/api/relay_routes.py uses to ingest signal-copier's own export
    #: outbox -- deliberately NOT `COMMERCIAL_DATABASE_URL` (that one
    #: connects as the unrestricted admin/app role). See
    #: app/db.py's `_apply_relay_role_access` for exactly what this role
    #: can and cannot do.
    RELAY_DATABASE_URL: str = "postgresql+psycopg://relay_role@localhost:5432/commercial"

    #: The shared signing secret both sides of the restricted relay
    #: (signal-copier's own relay worker and this service's
    #: app/api/relay_routes.py ingress) already have -- per
    #: INTEGRATION_DECISION.md S4: "Use ... an audience-bound
    #: signed-service-token implementation, with expiry ... and replay
    #: protection." This is a placeholder for local/test use only; a real
    #: deployment issues and stores this via a secrets manager, never a
    #: repo default, and rotates it independently of any customer- or
    #: broker-facing credential.
    RELAY_SIGNING_SECRET: str = "LOCAL_SIM-not-a-real-relay-secret-change-if-ever-deployed"

    #: app/api/dependencies.py's ID-01/ID-02/ID-03 web session cookie
    #: (`SESSION_COOKIE_NAME`). Same reasoning, same default, as
    #: signal-copier's own app/config.py's own `FORCE_SECURE_COOKIES`:
    #: `request.url.scheme` alone sees only "http" behind a TLS-
    #: terminating reverse proxy (the proxy, not this process, terminates
    #: TLS), so a real deployment behind one must set this explicitly
    #: rather than this process trusting a spoofable X-Forwarded-Proto
    #: header by default.
    FORCE_SECURE_COOKIES: bool = False


_settings = _Settings()

COMMERCIAL_DATABASE_URL = _settings.COMMERCIAL_DATABASE_URL
ENVIRONMENT = _settings.ENVIRONMENT
LOCAL_JWT_SECRET = _settings.LOCAL_JWT_SECRET
STRIPE_WEBHOOK_SECRET = _settings.STRIPE_WEBHOOK_SECRET
RELAY_DATABASE_URL = _settings.RELAY_DATABASE_URL
RELAY_SIGNING_SECRET = _settings.RELAY_SIGNING_SECRET
FORCE_SECURE_COOKIES = _settings.FORCE_SECURE_COOKIES
