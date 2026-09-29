# Environments

The real environment identities this service supports, per
`app/config.py`'s `ENVIRONMENT` setting and
`spec/docs/02_architecture_and_tenancy.md`'s "Environments" section.

## The five defined environments

```python
# app/config.py
ENVIRONMENT: str = "LOCAL_SIM"
```

The field's own comment enumerates the full, closed set:
`LOCAL_SIM | INTEGRATION_ISOLATED | PLATFORM_DEMO | PRIVATE_SHADOW |
COMMERCIAL_LIVE`.

`spec/docs/02_architecture_and_tenancy.md` states these are **disjoint
deployment identities** -- database records, secrets, domains, storage, and
event buses all carry environment, with no promotion by flipping a
restored row. A backup restored into one environment does not become
another environment by editing a field.

| Environment | Real meaning in this codebase |
|---|---|
| `LOCAL_SIM` | The default everywhere -- "the safest, most restrictive value" (`app/config.py`'s own comment: "COMMERCIAL_LIVE is never the default anywhere"). This is what `ops/bootstrap.py::_provision_tenant_and_owner` stamps onto a freshly-created `Tenant` when run with default config, and what `local_auth.py::create_account` stamps onto every self-service-signup tenant (`Tenant(display_name=tenant_display_name, environment="LOCAL_SIM")`). |
| `INTEGRATION_ISOLATED` | Named in the environment enum and in the tenancy spec; used for cross-service integration testing against the real relay/ingest path without touching live customer or platform state. |
| `PLATFORM_DEMO` | Named in the environment enum; customer and platform demos must not subscribe to a live strategy (`spec/docs/02_architecture_and_tenancy.md`). |
| `PRIVATE_SHADOW` | Named in the environment enum; a non-live environment for owner-side shadow verification against the paired signal-copier deployment. |
| `COMMERCIAL_LIVE` | The real, customer-facing production environment. Never the default; reaching it requires explicit configuration at every layer (database, secrets, domains) named in `docs/operations/DEPLOYMENT.md`'s "what a real production rollout still needs" section. |

## Test environment: disposable, not one of the five

`tests/conftest.py` describes its own approach directly: "Local tests use
disposable PostgreSQL and a controlled JWT issuer" (quoting
`spec/docs/02_architecture_and_tenancy.md` verbatim in its module
docstring). This is not itself one of the five named `ENVIRONMENT` values
-- it is the mechanism by which `LOCAL_SIM`-shaped behavior is verified in
CI without any persistent state:

- One real Postgres **cluster** per test *session* (`postgres_cluster`
  fixture, `scope="session"` -- expensive to start, so shared across the
  whole run), initialized with `initdb --auth=trust` and started on a free
  local port.
- Tables **dropped and recreated per test function** (`db_session`
  fixture) for isolation, so one test's rows never leak into another
  test's assertions.
- Tests **skip** (not fail, not fall back to SQLite or a mock) when the
  `postgresql-16` server binaries aren't present
  (`_pg_available()` checks for `/usr/lib/postgresql/16/bin/{initdb,pg_ctl}`)
  -- this codebase's own Postgres-specific column types (e.g.
  `RightsGrant.uses`' `ARRAY(String)`) would behave differently, or simply
  not work, against anything else, so a silent fallback would test the
  wrong database engine entirely.
- Runs as the `postgres` system user via `su postgres -c` when the test
  runner is root (most sandboxes, including CI, run as root; Postgres
  refuses to run its own server/init tools as root directly).
- Uses `--username=postgres` on `initdb` specifically so the cluster's
  superuser role name is pinned regardless of which OS user actually
  invokes `initdb` (it would otherwise default to the invoking OS user's
  own name).

## Environment-gated behavior in the code

- `AD-09` publisher-destination creation (`app/services/publisher_destination.py`)
  accepts **only** `PublisherEnvironment.LOCAL_SIMULATION` --
  `external_test`/`demo`/`live` are all a real, named
  `EXTERNAL_ENVIRONMENT_NOT_AUTHORIZED` blocker, never silently accepted,
  because "Collective2 tests are not assumed sandbox" (AD-09's own
  acceptance text) and this environment has no real Collective2/eToro/
  CopyFactory credentials or sandbox access at all.
- `etoro_adapter.py::build_trade_request` structurally refuses any
  `account_mode` other than `"demo"` -- there is no fallback branch that
  would ever select a real endpoint, so a caller cannot accidentally
  "fail over" into one even if demo transport is unavailable.

## Practical implication for anyone deploying this service

Setting `ENVIRONMENT=COMMERCIAL_LIVE` alone does **not** unlock live
publisher/broker/payment behavior -- the modules above have their own
independent, code-level refusals (demo-only transport, LOCAL_SIMULATION-only
publisher creation, placeholder Stripe/relay secrets) that must each be
separately and deliberately satisfied with real credentials before any
live financial effect is possible. `ENVIRONMENT` is a labelling/isolation
mechanism (which database, which secrets, which domain), not a single
master switch that turns on live trading.
