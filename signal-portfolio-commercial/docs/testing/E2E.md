# Integration / End-to-End Test Patterns

The real integration-level pattern this codebase uses is: run against a
real, disposable Postgres 16 cluster, and switch the actual login role the
session connects as to prove a security boundary holds for that role --
never simulate a role with an application-level `if role == ...` check
alone. See `docs/standards/TESTING.md` for the CI-level mechanics and
`docs/testing/FIXTURES.md` for exactly how the cluster/session fixtures
are built.

## 1. The real cluster (recap)

`tests/conftest.py::postgres_cluster` starts one real `initdb`/`pg_ctl`
Postgres 16 instance per test session, on a free local port, and creates
two real, restricted, non-superuser login roles inside it up front:

```sql
CREATE ROLE app_role LOGIN NOSUPERUSER NOBYPASSRLS;
CREATE ROLE relay_role LOGIN NOSUPERUSER NOBYPASSRLS;
```

`NOBYPASSRLS` is the load-bearing part: Postgres superusers (and any role
with `BYPASSRLS`) ignore row-level security regardless of `FORCE ROW
LEVEL SECURITY`. A test that only ever connects as the `postgres`
superuser used by `db_session` (schema setup) would pass even if an RLS
policy were completely broken -- proving nothing about real access
control.

## 2. Role-switching between `app_role` and `relay_role`

Three connection-scoped fixtures exist, all pointed at the *same* already
-set-up database, differing only in which login role the connection uses:

| Fixture | Role | Used for |
|---|---|---|
| `db_session` | `postgres` (superuser/owner) | schema setup (`create_all`/`drop_all`), applying RLS policies/grants/triggers, and any test that genuinely needs unrestricted access to seed data across tenants |
| `tenant_session_factory` | `app_role` | the ordinary browser-facing application connection -- customer/staff/owner requests |
| `relay_session_factory` | `relay_role` | the restricted relay worker's own ingress connection (`app/api/relay_routes.py`) |

`tenant_session_factory` and `relay_session_factory` both depend on
`db_session` (not just `postgres_cluster`) specifically so the schema,
grants, and RLS policies that fixture just (re)created already exist
before the restricted role connects -- see `docs/testing/FIXTURES.md`.

**Pattern 1 -- `app_role` tenant isolation** (`tests/
test_row_level_security.py`): seed two tenants as the superuser
(`db_session`), then reconnect as `app_role` (`tenant_session_factory`),
call `set_tenant_scope(session, "tenant-a")`, and assert only tenant A's
rows come back; call it again with `"tenant-b"` and assert the visible
set flips entirely; and assert that *no* `set_tenant_scope` call at all
means *zero* rows, never "everything" (fail-closed, not fail-open).

**Pattern 2 -- `relay_role`'s narrower, bespoke grant** (`tests/
test_relay_role_access.py`), the load-bearing property "slice 4
explicitly deferred": a restricted role can look up ANY tenant's
`export_stream_registrations` row by `source_stream` **with no scope set
at all** (needed to discover which tenant a stream belongs to before any
`app.tenant_id` can be set), but can never read or write another tenant's
`inbox_events` or `ledger_entries` once scoped, and has **no write access
at all** to command-authority tables. Its four tests each prove one edge
of that shape:

- `test_relay_role_can_look_up_a_registration_for_any_tenant_without_scope_set`
- `test_relay_role_cannot_insert_an_inbox_event_for_a_tenant_it_has_not_scoped_to`
- `test_relay_role_has_no_write_access_to_any_command_authority_table`
- `test_ingest_export_event_end_to_end_through_relay_role_populates_the_correct_tenant_only`
  -- the real end-to-end version: call the actual service function
  (`ingest_export_event`) through the `relay_role`-scoped session, not
  just raw SQL, and confirm it lands in the correct tenant only.

When adding a new restricted role or a new bespoke RLS policy (see
`docs/standards/CODING.md` §2 for when a table needs one), add tests in
this same shape: prove the role *can* do the one thing it's meant to,
*cannot* do anything scoped to another tenant, and *cannot* write outside
its granted tables -- as three separate, named tests, not one combined
assertion.

## 3. Real end-to-end HTTP tests

For an actual HTTP round-trip (not just a service-function call), tests
build a FastAPI `TestClient` against `app.main.app` and override its DB
dependency to the test's own `db_session`/`relay_session_factory`, e.g.
`tests/test_api.py`, `tests/test_relay_routes.py`, `tests/
test_id01_id02_id03_auth_routes.py` (the full signup / email verification
/ sign-in / password recovery / logout path, through
`app/api/dependencies.py`'s real cookie-authenticated flow), and
`tests/test_dashboard_routes.py`/`tests/test_source_coverage_route.py`/
`tests/test_platform_performance_route.py` for the server-rendered
dashboard routes. These are still run against the real Postgres cluster
via the overridden dependency -- there is no separate "unit-test-only"
in-memory app configuration.

## 4. Cross-service boundaries are mocked, real Postgres is not

A call across process boundaries (this service's own HTTP call to
`signal-copier`'s fit-simulation endpoint) is mocked at the boundary --
`tests/test_fit_simulation_client.py` and `tests/
test_public_fit_simulation_route.py` both state this explicitly ("Every
cross-service HTTP call is mocked at the boundary"). The rule this
codebase follows: mock what you don't own or can't run locally (another
real service's live HTTP endpoint); never mock what you do own and can
run for real (this service's own Postgres, RLS, or ORM layer).
