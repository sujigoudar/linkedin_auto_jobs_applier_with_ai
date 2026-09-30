# Deployment

The real, working deployment story for signal-portfolio-commercial, traced
to `Dockerfile`, `deploy/entrypoint-commercial.sh`, `ops/bootstrap.py`, the
Alembic migration chain, and `tests/conftest.py`'s equivalent (test-time)
provisioning pattern.

## Topology

Per `spec/docs/02_architecture_and_tenancy.md`: this is a separate
`commercial_api` process (this repository) from the existing private
execution application (`signal-copier`), sharing no database. This
service's own Postgres database is entirely separate from signal-copier's
SQLite execution store -- `app/db.py`'s own docstring is explicit that
"this process never opens that file and that process never opens this
one." The only channel between them is the relay ingress (HMAC-signed
export events, see `docs/security/ARCHITECTURE.md` section 4 and
`docs/integrations/CATALOG.md`).

## Container build

`Dockerfile`:

1. `FROM python:3.11-slim`.
2. Copies the sibling `signal_platform_contracts` package in first (an
   editable dependency, `-e ../signal_platform_contracts` in
   `requirements.txt`) so the subsequent `pip install -r requirements.txt`
   can resolve it.
3. Installs dependencies, then copies the application.
4. Runs as a **non-root** user (`groupadd --system commercial`,
   `useradd --system ... --shell /usr/sbin/nologin commercial`) -- a
   container escape or dependency RCE gets no root inside the container
   for free, matching signal-copier's own Dockerfile precedent.
5. `PYTHONPATH=/app` is set explicitly because `ops/bootstrap.py` is
   invoked as a script (`python3 ops/bootstrap.py`) and needs `/app` on
   `sys.path` to resolve its own `from app import config` import.
6. `EXPOSE 8001`.
7. `ENTRYPOINT ["/app/deploy/entrypoint-commercial.sh"]`,
   `CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001"]`.

## Startup sequence (`deploy/entrypoint-commercial.sh`)

The entrypoint runs three real steps in order, each of which can fail the
container's own startup loudly rather than leaving `uvicorn` running
against a half-migrated schema:

1. **`alembic upgrade head`**, connected via
   `COMMERCIAL_MIGRATOR_DATABASE_URL` (required, `:?` -- the script fails
   immediately if unset). This is deliberately a *separate, more
   privileged* connection string than the ordinary
   `COMMERCIAL_DATABASE_URL` runtime role, because migrations (and the
   bootstrap step that follows) need `GRANT`/`CREATE ROLE`/`ALTER DEFAULT
   PRIVILEGES` rights the restricted runtime role must never itself have.
2. **`python3 ops/bootstrap.py ${BOOTSTRAP_ARGS:-}`**, also using the
   migrator connection. See "Bootstrap" below for exactly what this does.
3. **`exec "$@"`** -- finally starts the real server process (`uvicorn`).

## RLS role provisioning as part of deployment

Row-level security and its two login roles are provisioned by two
different mechanisms depending on whether the target is a real deployment
or a test run -- both call the *same* underlying functions in `app/db.py`,
so the two paths are verified to produce equivalent RLS state.

### Real deployment (migration + bootstrap)

- **`relay_role`** is created by Alembic migration
  `3f7a19c02b8e_add_relay_role_access.py`, which calls
  `app/db.py::_apply_relay_role_access()` directly inside the migration's
  own transaction. This creates the role (`LOGIN NOSUPERUSER
  NOBYPASSRLS`), grants it its narrow table access, and installs its one
  bespoke `relay_stream_lookup` policy -- see
  `docs/security/ARCHITECTURE.md` section 3 for exactly what it can touch.
- **The runtime role** (default name `commercial`, matching
  `COMMERCIAL_DATABASE_URL`'s own default) is created by
  `ops/bootstrap.py::_bootstrap_runtime_role()` -- **not** by any
  migration. This was a genuine, previously undiscovered gap the
  bootstrap script's own docstring documents finding and fixing: nothing
  in the migration chain or app code ever created this role, so a real
  fresh deployment's own app connection failed outright (`psql -U
  commercial` returned `FATAL: role "commercial" does not exist`,
  reproduced for real against a freshly migrated Postgres database before
  this script existed).
- `_bootstrap_runtime_role()` grants `SELECT, INSERT, UPDATE, DELETE` on
  every table that exists right now, **and** sets `ALTER DEFAULT
  PRIVILEGES` so a table a future migration adds is automatically covered
  without re-running this step for that alone. It never grants `BYPASSRLS`
  or superuser -- doing so would silently defeat every RLS policy the
  migrations set up regardless of `FORCE ROW LEVEL SECURITY`.
- **Passwords**: neither `commercial` nor `relay_role` has ever had a
  password set anywhere in the migration chain -- a second genuine gap the
  bootstrap script's docstring documents finding
  (`fe_sendauth: no password supplied` against a real `postgres:16`
  container, since passwordless roles only work against Postgres's
  `trust` auth method, not the password auth the official Postgres Docker
  image and any real managed Postgres use by default).
  `ops/bootstrap.py::_set_role_password()` sets both, reading the values
  from `COMMERCIAL_RUNTIME_ROLE_PASSWORD`/`RELAY_ROLE_PASSWORD`
  environment variables only (never a CLI argument -- see
  `docs/security/SECRETS.md`); the script refuses to run with either
  unset.
- **RLS policies themselves** (`ENABLE`/`FORCE ROW LEVEL SECURITY` plus the
  `tenant_isolation`/`product_visibility`/`content_document_visibility`
  policies) are applied earlier in the migration chain, by whichever
  migration first introduced each covered table -- `app/db.py`'s own
  `_apply_row_level_security`/`_apply_product_visibility_policy`/
  `_apply_content_document_visibility_policy` functions are called from
  inside those migrations' own `upgrade()`. A migration that ran before a
  later table existed passes its **own frozen snapshot** of table names,
  never the live, ever-growing module constant -- `app/db.py`'s own
  comments document catching this for real: a fresh `alembic upgrade
  head` run failed with `UndefinedTable` once `release_reviews` was added
  and a caller still defaulted to the current tuple.
- **Tenant + owner + export-stream registration**: `ops/bootstrap.py` also
  provisions one real `Tenant` and one real owner `Membership` (idempotent
  -- checks `session.get(Tenant, tenant_id)`/`session.get(UserIdentity,
  user_id)` first), registers every export stream the paired signal-copier
  deployment will use (`--account-id`/`--source-name`, both repeatable --
  **both** are required at least once each, since signal-copier exports on
  two independent stream namespaces: `signal-copier:<account_id>` for
  EXECUTION_APPLIED and `signal-copier:source:<source_name>` for
  SOURCE_RECEIPT; registering only the account stream leaves every
  SOURCE_RECEIPT permanently parked as `unregistered_stream`), and prints
  (and optionally writes to a `chmod 0600` file via `--token-file`) a
  freshly issued 24-hour owner JWT via `app.services.auth.issue_token`.
  This exists because there is no self-service login page in this build
  yet -- an operator mints a session out of band and hands it to whoever
  needs owner access.
- The whole script is **idempotent** -- safe to re-run against an
  already-bootstrapped database, which is exactly what happens on every
  container restart.

### Test-time (equivalent, not identical, mechanism)

`tests/conftest.py`'s `db_session` fixture does not use migrations or
`ops/bootstrap.py` -- it calls `Base.metadata.create_all(engine)` directly
against a disposable local Postgres cluster (see
`docs/operations/ENVIRONMENTS.md`), then calls the *same* `app/db.py`
functions a real migration/bootstrap would:
`enable_row_level_security()`, `enable_product_visibility_policy()`,
`enable_content_document_visibility_policy()`, `enable_relay_role_access()`,
`enforce_append_only()`. `app_role`/`relay_role` are created once per test
*session* by the `postgres_cluster` fixture (via `CREATE ROLE ... LOGIN
NOSUPERUSER NOBYPASSRLS`), matching the real deployment's own role shape
exactly. This means the RLS behavior exercised in CI is genuinely the same
mechanism a real deployment runs, not a mocked approximation of it.

## Process/network isolation

`spec/docs/13_operations_deployment_and_cost.md`'s deployment section
states the real operational requirements this build's process/network
design is meant to satisfy (not all independently verified in code for
this document, but stated here as the standing requirement):

- Deploy commercial API and private execution under distinct
  hostnames/process identities, with private DB and metrics.
- Customer auth redirects, billing ingress, and platform callbacks get
  their own allowlisted origins and raw-body validation. No wildcard
  CORS.
- The commercial Postgres database must not share transaction authority
  with signal-copier's SQLite store through dual writes -- the outbox/relay
  bridge is the only channel, and it preserves provenance (see
  `docs/operations/DR.md`).

## What a real production rollout still needs beyond this code

Per `spec/docs/13_operations_deployment_and_cost.md`: a real
`COMMERCIAL_LIVE` deployment needs real Stripe price IDs, processor
acceptance, source grants, platform-provider acceptance, permitted
customer geography rules, versioned Terms/Privacy/risk-disclosure content,
a support/incident owner, and a real secrets-manager integration --
none of which this codebase provides itself; they are deployment-time
decisions layered on top of the mechanisms documented here. See
`docs/operations/ENVIRONMENTS.md` for how `ENVIRONMENT` gates which of
these are even reachable.
