# Secrets reference

Every secret-shaped field this service reads, sourced from
`app/config.py`'s `_Settings` class. Every one of these ships with a
`LOCAL_SIM`-labelled placeholder default that is explicitly documented in
the source as never safe for a real deployment -- this table repeats that
distinction field by field so it can't be missed by skimming the code.

All settings are read via `pydantic_settings.BaseSettings` with
`case_sensitive=True`, i.e. from environment variables matching the field
name exactly (`COMMERCIAL_DATABASE_URL`, not `commercial_database_url`).

| Field | Purpose | Placeholder default | Real provisioning |
|---|---|---|---|
| `COMMERCIAL_DATABASE_URL` | The admin/app Postgres DSN this service's ordinary (non-relay) connection uses. | `postgresql+psycopg://commercial:commercial@localhost:5432/commercial` | A real deployment sets this to the runtime role `ops/bootstrap.py` provisions (default role name `commercial`), with a real password set via `COMMERCIAL_RUNTIME_ROLE_PASSWORD` (see below) -- never the placeholder credential. |
| `RELAY_DATABASE_URL` | The Postgres DSN for the restricted `relay_role` connection `app/api/relay_routes.py` uses to ingest signal-copier's export outbox -- deliberately **not** `COMMERCIAL_DATABASE_URL`. | `postgresql+psycopg://relay_role@localhost:5432/commercial` | Points at the same database, authenticating as `relay_role` with the password set via `RELAY_ROLE_PASSWORD` (see below). Never the unrestricted admin role. |
| `LOCAL_JWT_SECRET` | HS256 signing secret for the JWT issuer in `app/services/auth.py`. | `LOCAL_SIM-not-a-real-secret-change-if-ever-deployed` | Real deployment's customer/operator identity is expected to come from Supabase Auth (see `docs/security/THREAT_MODEL.md` gap 2); this secret exists so local code and tests have a real, verifiable token to exercise tenant-scope enforcement against. If this issuer is ever used outside LOCAL_SIM, this value must be a real, high-entropy secret from a secrets manager -- never the repo default. |
| `RELAY_SIGNING_SECRET` | CURRENT HMAC secret the relay ingress verifies incoming signal-copier events against (`app/services/relay_auth.py`). | `LOCAL_SIM-not-a-real-relay-secret-change-if-ever-deployed` | Issued and rotated through a secrets manager; kept in sync with signal-copier's own matching setting. See the rotation procedure in `docs/security/ARCHITECTURE.md` section 4. |
| `RELAY_SIGNING_SECRET_PREVIOUS` | Optional PREVIOUS value accepted alongside CURRENT during a rotation window. | `""` (blank -- no previous secret accepted, i.e. rotation not in progress) | Set only during an active rotation; unset again once every signal-copier deployment has confirmed signing with the new `RELAY_SIGNING_SECRET`. |
| `STRIPE_WEBHOOK_SECRET` | Signing secret `app/services/stripe_webhook.py` verifies incoming Stripe events against. | `whsec_LOCAL_SIM_not_a_real_stripe_secret` | No real Stripe account exists in this environment (gated on CARD-4, payment processor approval, per `spec/docs/13_operations_deployment_and_cost.md`). Must be replaced with the real `whsec_...` value issued by Stripe's dashboard before this endpoint is ever pointed at a real account. |
| `CATALOG_FIT_SIM_SIGNING_SECRET` | Shared signing secret with signal-copier's own `CATALOG_FIT_SIM_SIGNING_SECRET` -- signs every fit-simulation request this service's backend makes on behalf of an anonymous public-catalog visitor. Deliberately separate from `RELAY_SIGNING_SECRET` (same "a stolen credential must not become a different credential" reasoning). | `LOCAL_SIM-not-a-real-catalog-fit-sim-secret-change-if-ever-deployed` | A real deployment sets this via a secrets manager, kept in sync with signal-copier's identically-named setting. |
| `COMMERCIAL_RUNTIME_ROLE_PASSWORD` (env var, not a `_Settings` field) | Password `ops/bootstrap.py` sets on the runtime Postgres role (default name `commercial`) via `ALTER ROLE ... PASSWORD`. | Not set -- the bootstrap script refuses to run without it. | Must be set as a real environment variable, never a CLI argument (a password on a process's own argv is visible to every other process on the host via `/proc`). |
| `RELAY_ROLE_PASSWORD` (env var, not a `_Settings` field) | Password `ops/bootstrap.py` sets on `relay_role`. | Not set -- the bootstrap script refuses to run without it. | Same handling as `COMMERCIAL_RUNTIME_ROLE_PASSWORD`: environment variable only. |
| `COMMERCIAL_MIGRATOR_DATABASE_URL` (env var, used by `deploy/entrypoint-commercial.sh`, not a `_Settings` field) | A separate, **more privileged** connection string than `COMMERCIAL_DATABASE_URL` -- alembic and `ops/bootstrap.py`'s own role/GRANT DDL need rights the restricted runtime role (created by that very bootstrap step) must never have itself. | Not set by default; the entrypoint script requires it (`:?COMMERCIAL_MIGRATOR_DATABASE_URL must be set`). | `docker-compose.yml`'s local/paper `commercial` service points this at the Postgres superuser connection. A real production deployment should instead use a dedicated migrator role with `CREATEROLE` and ownership of the target schema -- never the database's own superuser, per the entrypoint script's own comments. |

## Non-secret but security-relevant configuration

These are not secrets but govern secret-adjacent behavior; listed here
because they're easy to overlook when auditing secret handling:

- `FORCE_SECURE_COOKIES: bool = False` -- whether the session cookie is
  marked `Secure`. Defaults to `False` because `request.url.scheme` alone
  cannot see through a TLS-terminating reverse proxy. A real deployment
  behind one must set this to `True` explicitly.
- `ENVIRONMENT: str = "LOCAL_SIM"` -- selects the deployment identity (see
  `docs/operations/ENVIRONMENTS.md`). Defaults to the safest, most
  restrictive value; `COMMERCIAL_LIVE` is never the default anywhere in
  this codebase.

## What is explicitly *not* a secret field here

`SIGNAL_COPIER_BASE_URL` and `FIT_SIM_CATALOG_CONFIG_JSON` are plain
configuration, not secrets (a base URL and a JSON mapping of product slugs
to source/CSV-path config) -- listed for completeness in
`docs/operations/CONFIGURATION.md`, not here.

## Handling discipline already enforced in code

- Every placeholder secret's own docstring in `app/config.py` states, in
  the field's own comment, that it is a LOCAL_SIM/test-only value and must
  never be the value used in a real deployment. This document is a
  transcription of that discipline, not a new one.
- `ops/bootstrap.py` never accepts a role password as a CLI argument --
  only from environment variables -- specifically to avoid it being
  visible to other processes on the host via `/proc/<pid>/cmdline`.
- The raw secret behind an issued API key (`app/services/api_key.py`)
  exists only for the single response that creates it; only its SHA-256
  hash (`key_hash`) is ever persisted.
- Verification/password-reset tokens (`app/services/local_auth.py`) are
  stored as the token value itself (not hashed) in `AuthToken`, since they
  are already single-use and time-limited (`_TOKEN_TTL = 24h`) -- this is
  a narrower, more constrained secret than a long-lived session or API key
  and is treated accordingly in the schema.

## Where a real deployment stores these

Per `spec/docs/13_operations_deployment_and_cost.md`: "Secrets only in
approved deployment channels, never JSON catalogs, browser assets, logs or
examples." This codebase does not itself integrate a specific secrets
manager (AWS Secrets Manager, Vault, etc.) -- all of the above are read
from process environment variables by `pydantic_settings`, and it is the
deployment platform's responsibility to inject them from a real secrets
store rather than a checked-in `.env` file. No `.env.production` or
equivalent committed secrets file exists in this repository, and none
should ever be added.
