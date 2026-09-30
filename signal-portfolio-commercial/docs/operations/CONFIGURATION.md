# Configuration reference

The complete, exhaustive set of settings `app/config.py` reads, in
declaration order. All are `pydantic_settings.BaseSettings` fields with
`case_sensitive=True, extra="ignore"` and no `env_prefix` -- each is read
from an environment variable exactly matching the field name below.

Secret-shaped fields are cross-referenced to `docs/security/SECRETS.md`,
which covers their provisioning in more depth; this document is the
exhaustive field-by-field reference including the non-secret ones.

## Database

### `COMMERCIAL_DATABASE_URL`
```
str = "postgresql+psycopg://commercial:commercial@localhost:5432/commercial"
```
The real Postgres DSN this process's ordinary (non-relay) connection uses,
in every real environment. **Tests never read this** -- `tests/conftest.py`'s
`postgres_url`-equivalent fixtures spin up and tear down their own
disposable cluster and pass that URL directly, so a missing/placeholder
value here never accidentally points a test at a real database. See
`docs/security/SECRETS.md`.

### `RELAY_DATABASE_URL`
```
str = "postgresql+psycopg://relay_role@localhost:5432/commercial"
```
Postgres DSN for the restricted `relay_role` connection
`app/api/relay_routes.py` uses to ingest signal-copier's export outbox --
deliberately **not** `COMMERCIAL_DATABASE_URL` (that one connects as the
unrestricted admin/app role). See `app/db.py::_apply_relay_role_access`
for exactly what this role can and cannot do, and
`docs/security/ARCHITECTURE.md` section 3.

## Environment identity

### `ENVIRONMENT`
```
str = "LOCAL_SIM"
```
One of `LOCAL_SIM | INTEGRATION_ISOLATED | PLATFORM_DEMO | PRIVATE_SHADOW
| COMMERCIAL_LIVE` -- see `docs/operations/ENVIRONMENTS.md`'s "Environments"
section for the full spec reference. Defaults to the safest, most
restrictive value; `COMMERCIAL_LIVE` is never the default anywhere.

## Authentication and sessions

### `LOCAL_JWT_SECRET`
```
str = "LOCAL_SIM-not-a-real-secret-change-if-ever-deployed"
```
Signing secret for the LOCAL/test JWT issuer (`app/services/auth.py`).
See `docs/security/SECRETS.md`.

### `FORCE_SECURE_COOKIES`
```
bool = False
```
Governs the ID-01/ID-02/ID-03 web session cookie
(`app/api/dependencies.py::SESSION_COOKIE_NAME`). Same reasoning, same
default, as signal-copier's own `app/config.py::FORCE_SECURE_COOKIES`:
`request.url.scheme` alone only ever sees `"http"` behind a
TLS-terminating reverse proxy (the proxy, not this process, terminates
TLS), so a real deployment behind one must set this to `True` explicitly
rather than trusting a spoofable `X-Forwarded-Proto` header by default.

## Billing

### `STRIPE_WEBHOOK_SECRET`
```
str = "whsec_LOCAL_SIM_not_a_real_stripe_secret"
```
The Stripe webhook signing secret `app/services/stripe_webhook.py`
verifies incoming events against. No real Stripe account exists in this
environment (gated on CARD-4, payment processor approval). See
`docs/security/SECRETS.md`.

## Relay ingress

### `RELAY_SIGNING_SECRET`
```
str = "LOCAL_SIM-not-a-real-relay-secret-change-if-ever-deployed"
```
The shared signing secret both sides of the restricted relay (signal-copier's
relay worker and this service's `app/api/relay_routes.py` ingress) use --
per `INTEGRATION_DECISION.md` S4. See `docs/security/ARCHITECTURE.md`
section 4 for the full verification/rotation mechanism and
`docs/security/SECRETS.md` for provisioning.

### `RELAY_SIGNING_SECRET_PREVIOUS`
```
str = ""
```
Optional: the PREVIOUS value of `RELAY_SIGNING_SECRET`, accepted alongside
the CURRENT one by `app/services/relay_auth.py::verify_relay_signature`
during a rotation window. Blank (the default) means no previous secret is
accepted, i.e. rotation is not in progress.

## Public catalog fit-simulator (PU-03)

### `SIGNAL_COPIER_BASE_URL`
```
str = ""
```
Base URL of the signal-copier deployment this service's own public
catalog fit-simulator (PU-03 "Try our fit simulator") calls -- e.g.
`http://localhost:8000` in local/dev. Blank means the feature is not
configured; `app/services/fit_simulation_client.py` refuses to guess a
destination and honestly reports the simulator as unavailable rather than
silently no-op'ing.

### `CATALOG_FIT_SIM_SIGNING_SECRET`
```
str = "LOCAL_SIM-not-a-real-catalog-fit-sim-secret-change-if-ever-deployed"
```
The shared signing secret with signal-copier's own
`CATALOG_FIT_SIM_SIGNING_SECRET` -- signs every
`POST /catalog/providers/{source}/fit-simulation` request this service's
backend makes on behalf of an anonymous public-catalog visitor.
Deliberately a **separate** secret from `RELAY_SIGNING_SECRET` (same
"a stolen credential must not become a different credential" reasoning
`INTEGRATION_DECISION.md` S11 gives for that one vs. the billing-webhook
secret) -- this token authenticates to exactly one signal-copier route and
nothing else.

### `FIT_SIM_CATALOG_CONFIG_JSON`
```
str = "{}"
```
Which signal-copier `source` (and which local CSV price paths, per symbol)
backs each PUBLISHED product's own fit-simulator, keyed by product slug:
```json
{"<slug>": {"source": "<signal-copier source name>",
            "csv_paths": {"<symbol>": "<local CSV path>"}}}
```
Empty (`"{}"`, the default) means **no product has real wiring for this
feature yet** -- `app/services/fit_simulation_client.py` then honestly
reports the simulator as unavailable for every slug, rather than
fabricating a source name or price data that was never really configured.
This is real, disclosed configuration space for an operator to fill in
once real historical price CSVs exist for a published product's
underlying instrument(s), not a claim that any currently do.

## Environment variables read outside `app/config.py`

These are real, load-bearing environment variables the deployment
pipeline reads directly (via `os.environ`), not through
`pydantic_settings` -- listed here for completeness since they are as
essential to a working deployment as anything in `_Settings`:

| Variable | Read by | Purpose |
|---|---|---|
| `COMMERCIAL_RUNTIME_ROLE_PASSWORD` | `ops/bootstrap.py` | Password set on the runtime Postgres role via `ALTER ROLE`. Script refuses to run without it. |
| `RELAY_ROLE_PASSWORD` | `ops/bootstrap.py` | Password set on `relay_role` via `ALTER ROLE`. Script refuses to run without it. |
| `COMMERCIAL_MIGRATOR_DATABASE_URL` | `deploy/entrypoint-commercial.sh` | The elevated (superuser or schema-owning) connection alembic and `ops/bootstrap.py`'s own role/GRANT DDL run as. Entrypoint fails immediately if unset. |
| `BOOTSTRAP_ARGS` | `deploy/entrypoint-commercial.sh` | Shell-word-split and passed through to `ops/bootstrap.py` (e.g. `--account-id ... --source-name ...`). Optional; empty by default. |

## `ops/bootstrap.py`'s own CLI arguments

Not environment configuration, but the real, exhaustive set of runtime
parameters an operator supplies at deployment time (see
`docs/operations/DEPLOYMENT.md` for the full bootstrap behavior):

| Argument | Default | Purpose |
|---|---|---|
| `--tenant-id` | `"tenant-owner"` | The provisioned tenant's ID. |
| `--tenant-name` | `"Owner Workspace"` | The provisioned tenant's display name. |
| `--user-id` | `"owner-1"` | The provisioned owner user's ID. |
| `--email` | `"owner@local-sim.invalid"` | The provisioned owner user's email. |
| `--account-id` (repeatable) | none | Every enabled `account_id` in the paired signal-copier deployment's `accounts.yaml` -- each becomes an `signal-copier:{account_id}` EXECUTION_APPLIED stream registration. At least one of `--account-id`/`--source-name` is required. |
| `--source-name` (repeatable) | none | Every `source` the paired signal-copier deployment's `routing.yaml` names -- each becomes a `signal-copier:source:{source_name}` SOURCE_RECEIPT stream registration. |
| `--runtime-role` | `"commercial"` | Postgres role name to create/grant -- matches `COMMERCIAL_DATABASE_URL`'s own default role name unless overridden. |
| `--token-file` | none | If set, the freshly issued owner token is also written here (mode `0600`), for a caller to read directly rather than scraping container logs (CI log scrapers commonly mask a token that matches their own secret-detection heuristic). |

## Configuration NOT present in this codebase

For completeness, things sometimes expected in a service's configuration
reference that this codebase does not have: there is no rate-limiting
configuration, no feature-flag system, no CDN/asset-URL configuration, and
no separate read-replica DSN -- `COMMERCIAL_DATABASE_URL`/`RELAY_DATABASE_URL`
are the only two database connection strings this process ever opens.
