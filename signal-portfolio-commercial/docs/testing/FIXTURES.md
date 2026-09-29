# Fixture Conventions

All real fixtures live in `tests/conftest.py`. This file explains exactly
what each one does and why, so a new test file reuses them correctly
instead of reinventing a fixture that already exists.

## 1. `postgres_cluster` (session-scoped): one real disposable cluster

```python
@pytest.fixture(scope="session")
def postgres_cluster(tmp_path_factory):
    ...
```

- **Scope: `session`** -- starting a real Postgres cluster (`initdb` +
  `pg_ctl start`) is expensive, so it happens exactly once for the whole
  test run, not once per test.
- **Skips, never fakes, when unavailable**: `_pg_available()` checks for
  real server binaries at `/usr/lib/postgresql/16/bin`; if they're
  missing, every test that depends on this fixture (directly or
  transitively) is *skipped* via `pytest.skip(...)`, never silently
  redirected to SQLite or a mock. The conftest module docstring states
  why: "this package's own data types, e.g. `RightsGrant.uses`'
  `ARRAY(String)` column, are Postgres-specific and would behave
  differently, or simply not work, against anything else."
- **Runs Postgres tooling as the `postgres` OS user, not root**, via
  `_run_as_postgres`, because "Postgres refuses to run its own
  server/init tools as root (most sandboxes, including this one, run
  tests as root)." A non-root runner (e.g. an ordinary CI user) just runs
  the commands directly.
- **`--username=postgres` is pinned explicitly on `initdb`** so the
  cluster's superuser role is always named `postgres` regardless of which
  OS user actually ran `initdb` -- every connection string below
  hardcodes `postgres` as the admin user.
- **Yields three real connection URLs**, not three real sessions:

  ```python
  yield {
      "admin_url": f"postgresql+psycopg://postgres@127.0.0.1:{port}/commercial",
      "app_role_url": f"postgresql+psycopg://app_role@127.0.0.1:{port}/commercial",
      "relay_role_url": f"postgresql+psycopg://relay_role@127.0.0.1:{port}/commercial",
  }
  ```

  Downstream fixtures turn these into engines/sessions as needed.
- **Teardown always runs** (`finally`): `pg_ctl ... stop` then
  `shutil.rmtree(data_dir, ignore_errors=True)`, even if a test in the
  session failed.
- **Every model module is imported at the top of `conftest.py`** (even
  ones a given test file never references), specifically so
  `Base.metadata` is fully populated before `create_all` runs -- otherwise
  running a single test file in isolation (`pytest tests/test_ledger.py`)
  would only create the tables *that file's own imports* happened to
  register, silently dropping tables other tests in the same session
  depend on existing. When adding a new `app/models/<x>.py` module, add
  its `import app.models.<x>  # noqa: F401` line here too.

## 2. `db_session` (function-scoped): the real, important nuance

```python
@pytest.fixture
def db_session(postgres_cluster):
    engine = make_engine(postgres_cluster["admin_url"])
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(text("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO app_role"))
    enable_row_level_security(engine)
    enable_product_visibility_policy(engine)
    enable_content_document_visibility_policy(engine)
    enable_relay_role_access(engine)
    enforce_append_only(engine)
    ...
```

**The schema here is built via SQLAlchemy's `Base.metadata.drop_all` /
`create_all` -- deliberately, and exclusively, never via Alembic.** This
is a real and important distinction from how a real deployment sets up
its schema, and it is intentional, not an oversight:

- **The test suite's own schema path** (`create_all`/`drop_all`) is fast:
  drop and recreate every table fresh, every single test *function*
  (cheap enough to do per-test, "and avoids one test's rows leaking into
  another's assertions" -- `conftest.py`'s own docstring), then
  reapply RLS policies, visibility policies, relay-role grants, and the
  append-only trigger from scratch on the freshly created tables.
- **The real deployment schema path** is Alembic (`alembic upgrade
  head`), and it is verified *separately*, in CI, against its own
  *different* disposable cluster (see `docs/standards/TESTING.md` §3 and
  `.github/workflows/signal-portfolio-commercial-ci.yml`'s own comment:
  "tests/conftest.py never runs Alembic -- it uses
  Base.metadata.create_all directly").
- **Why both exist, and why neither one alone would be enough**: `create_
  all` proves the *application code* (models, RLS helper functions,
  service layer) is correct against a schema that always matches the
  current `Base.metadata` exactly -- it is fast because it skips replaying
  migration history. `alembic upgrade head` proves the actual *migration
  path* a real deployment runs is correct -- including ordering and
  historical-migration correctness that `create_all` cannot catch, since
  `create_all` has no concept of migration history at all. A bug like the
  one `app/db.py`'s own docstring recalls -- "a fresh `alembic upgrade
  head` run failed with `UndefinedTable` once `release_reviews` was added
  here and this function still defaulted every caller to the current
  tuple" -- is exactly the class of bug `create_all` can never surface,
  because `create_all` only ever builds the *current* schema in one shot.
- **Never "fix" a slow test suite by pointing `db_session` at Alembic
  instead** -- that would both slow down every test (replaying migration
  history per test function) and remove the fast, code-correctness-only
  signal this fixture is specifically for. If you need to prove a
  *migration* is correct, that belongs in `tests/test_alembic_migrations.py`
  (script-shape sanity) or the CI workflow's separate live-cluster step,
  not in `db_session`.

After building the schema, `db_session` also grants `app_role` its
baseline `SELECT, INSERT, UPDATE, DELETE` on every table, then applies --
in this order -- `enable_row_level_security` (the generic tenant-isolation
policy), the two bespoke visibility policies (`products`,
`content_documents`), `enable_relay_role_access` (creates `relay_role` if
missing, plus its own bespoke lookup policy), and `enforce_append_only`
(the append-only trigger). Any new table-level policy/grant helper added
to `app/db.py` should be wired into this fixture in the same place, so
every test gets it automatically rather than each test file re-applying
it by hand.

## 3. `tenant_session_factory` / `relay_session_factory` (function-scoped)

Both depend on **both** `postgres_cluster` *and* `db_session` -- not just
`postgres_cluster` -- specifically because they need the schema, grants,
and RLS policies `db_session` just (re)created to already exist before a
restricted role tries to use them. They hand back a `sessionmaker`
(a factory), not an open session, so a test can open (and close) more
than one connection under that role if it needs to. See `docs/testing/
E2E.md` for how these are actually used to prove role-scoped access
control.

## 4. Adding a new fixture

- A fixture that needs the database at all takes `db_session` (or one of
  the role-scoped factories) as a dependency, never reimplements its own
  engine/session setup.
- A fixture that administers the Postgres *cluster itself* (new roles,
  new databases) goes through `_run_as_postgres`, for the same root/
  non-root portability reason `postgres_cluster` does.
- Keep fixtures function-scoped by default (matching `db_session`'s own
  per-test isolation); only use `scope="session"` for something as
  genuinely expensive to set up as the cluster itself.
