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

    #: Optional: the PREVIOUS value of RELAY_SIGNING_SECRET, accepted
    #: alongside the CURRENT one above by
    #: app/services/relay_auth.py's own `verify_relay_signature` -- see
    #: that module's own docstring for the two-step zero-downtime
    #: rotation procedure this enables. Blank (the default) means no
    #: previous secret is accepted, i.e. rotation is not in progress.
    RELAY_SIGNING_SECRET_PREVIOUS: str = ""

    #: app/api/dependencies.py's ID-01/ID-02/ID-03 web session cookie
    #: (`SESSION_COOKIE_NAME`). Same reasoning, same default, as
    #: signal-copier's own app/config.py's own `FORCE_SECURE_COOKIES`:
    #: `request.url.scheme` alone sees only "http" behind a TLS-
    #: terminating reverse proxy (the proxy, not this process, terminates
    #: TLS), so a real deployment behind one must set this explicitly
    #: rather than this process trusting a spoofable X-Forwarded-Proto
    #: header by default.
    FORCE_SECURE_COOKIES: bool = False

    #: Base URL of the signal-copier deployment this service's own public
    #: catalog fit-simulator (PU-03's "Try our fit simulator") calls --
    #: e.g. "http://localhost:8000" in local/dev. Blank means the feature
    #: is not configured; app/services/fit_simulation_client.py refuses to
    #: guess a destination and honestly reports the simulator as
    #: unavailable rather than silently no-op'ing.
    SIGNAL_COPIER_BASE_URL: str = ""

    #: The shared signing secret with signal-copier's own
    #: CATALOG_FIT_SIM_SIGNING_SECRET (app/services/catalog_fit_sim_auth.py
    #: there) -- signs every `POST /catalog/providers/{source}/fit-
    #: simulation` request this service's own backend makes on behalf of
    #: an anonymous public-catalog visitor. Deliberately a SEPARATE secret
    #: from RELAY_SIGNING_SECRET above (same "a stolen credential must not
    #: become a different credential" reasoning INTEGRATION_DECISION.md
    #: S11 already gives for that one vs the billing-webhook secret): this
    #: token authenticates to exactly one signal-copier route and nothing
    #: else. A placeholder for local/test use only; a real deployment
    #: sets this via a secrets manager, kept in sync with signal-copier's
    #: own identically-named setting.
    CATALOG_FIT_SIM_SIGNING_SECRET: str = "LOCAL_SIM-not-a-real-catalog-fit-sim-secret-change-if-ever-deployed"

    #: Which signal-copier `source` (and which local CSV price paths, per
    #: symbol) backs each PUBLISHED product's own fit-simulator, keyed by
    #: product slug -- a JSON object:
    #: '{"<slug>": {"source": "<signal-copier source name>",
    #:              "csv_paths": {"<symbol>": "<local CSV path>"}}}'.
    #: Empty ("{}", the default) means NO product has real wiring for
    #: this feature yet -- app/services/fit_simulation_client.py then
    #: honestly reports the simulator as unavailable for every slug,
    #: rather than fabricating a source name or price data that was never
    #: really configured. This is real, disclosed configuration space for
    #: an operator to fill in once real historical price CSVs exist for a
    #: published product's underlying instrument(s), not a claim that any
    #: currently do.
    FIT_SIM_CATALOG_CONFIG_JSON: str = "{}"

    #: Track 11 -- whether the background health-sampler task (app/main.py's
    #: `_health_sampler_loop`) runs at all. A single, explicit process-
    #: wide gate; tests set this False and record samples directly via
    #: app.services.service_health.record_health_sample instead of
    #: relying on a live timer.
    HEALTH_SAMPLER_ENABLED: bool = True

    #: Seconds between background health-sampler passes. A same-process/
    #: cross-service SAMPLING cadence, not a correctness requirement -- a
    #: slower interval just means a wider gap before AD-22's uptime
    #: window has enough samples.
    HEALTH_SAMPLER_INTERVAL_SECONDS: float = 60.0


_settings = _Settings()

#: The exact repo-committed placeholder default for every secret this
#: build ships with a fixed, public value for. Compared by EXACT value
#: (never a substring/marker heuristic, which would be fragile against
#: a real secret that happens to contain a similar-looking word) against
#: the value actually loaded at startup -- see `placeholder_secrets_in_use`
#: below. Keep this mapping's values in lockstep with the `_Settings`
#: field defaults above; a mismatch here would make the COMMERCIAL_LIVE
#: startup guard either miss a real placeholder or false-positive on a
#: deliberately-chosen real secret that happens to equal an old default.
_PLACEHOLDER_SECRET_DEFAULTS: dict[str, str] = {
    "LOCAL_JWT_SECRET": "LOCAL_SIM-not-a-real-secret-change-if-ever-deployed",
    "RELAY_SIGNING_SECRET": "LOCAL_SIM-not-a-real-relay-secret-change-if-ever-deployed",
    "CATALOG_FIT_SIM_SIGNING_SECRET": "LOCAL_SIM-not-a-real-catalog-fit-sim-secret-change-if-ever-deployed",
    "STRIPE_WEBHOOK_SECRET": "whsec_LOCAL_SIM_not_a_real_stripe_secret",
}

#: The literal value of `ENVIRONMENT` that means "this process is
#: handling real customer money/data" -- see spec/docs/02's
#: "Environments" section and this module's own `ENVIRONMENT` docstring.
COMMERCIAL_LIVE_ENVIRONMENT = "COMMERCIAL_LIVE"


def placeholder_secrets_in_use() -> list[str]:
    """Names (in `_PLACEHOLDER_SECRET_DEFAULTS`'s own key order) of every
    secret setting still equal to its repo-committed placeholder default,
    as actually loaded into this process's environment/settings -- not a
    hardcoded assumption about what `app.config` exports. Used by
    app/main.py's startup guard to refuse to boot a COMMERCIAL_LIVE
    process with any of these still unrotated; also usable directly by
    tests/an operator without needing to boot the app at all."""
    current = {
        "LOCAL_JWT_SECRET": _settings.LOCAL_JWT_SECRET,
        "RELAY_SIGNING_SECRET": _settings.RELAY_SIGNING_SECRET,
        "CATALOG_FIT_SIM_SIGNING_SECRET": _settings.CATALOG_FIT_SIM_SIGNING_SECRET,
        "STRIPE_WEBHOOK_SECRET": _settings.STRIPE_WEBHOOK_SECRET,
    }
    return [name for name, placeholder in _PLACEHOLDER_SECRET_DEFAULTS.items() if current[name] == placeholder]


COMMERCIAL_DATABASE_URL = _settings.COMMERCIAL_DATABASE_URL
ENVIRONMENT = _settings.ENVIRONMENT
LOCAL_JWT_SECRET = _settings.LOCAL_JWT_SECRET
STRIPE_WEBHOOK_SECRET = _settings.STRIPE_WEBHOOK_SECRET
RELAY_DATABASE_URL = _settings.RELAY_DATABASE_URL
RELAY_SIGNING_SECRET = _settings.RELAY_SIGNING_SECRET
RELAY_SIGNING_SECRET_PREVIOUS = _settings.RELAY_SIGNING_SECRET_PREVIOUS
FORCE_SECURE_COOKIES = _settings.FORCE_SECURE_COOKIES

SIGNAL_COPIER_BASE_URL = _settings.SIGNAL_COPIER_BASE_URL
CATALOG_FIT_SIM_SIGNING_SECRET = _settings.CATALOG_FIT_SIM_SIGNING_SECRET
FIT_SIM_CATALOG_CONFIG_JSON = _settings.FIT_SIM_CATALOG_CONFIG_JSON
HEALTH_SAMPLER_ENABLED = _settings.HEALTH_SAMPLER_ENABLED
HEALTH_SAMPLER_INTERVAL_SECONDS = _settings.HEALTH_SAMPLER_INTERVAL_SECONDS
