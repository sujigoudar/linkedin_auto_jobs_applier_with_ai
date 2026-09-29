# Acceptance / Done-Bar

Source of truth: `pyproject.toml` (`[tool.ruff]`, `[tool.mypy]`), `pytest.ini`,
`tests/conftest.py`, `alembic/env.py`.

This is the real, current done-bar for a change to this codebase — not an
aspirational CI description.

## Lint

```bash
ruff check .
```

`pyproject.toml` pins `select = ["F", "B"]` explicitly (pyflakes + bugbear — real-bug
detectors: unused imports/variables, unreachable code, mutable default args) rather
than ruff's own shifting defaults, "matching signal-copier/pyproject.toml's own
documented choice" — deliberately not the opinionated style families (E/W, I, C4,
UP) that would produce a repo-wide reformatting diff unrelated to catching actual
mistakes. `dashboard_spec/` is excluded (a vendored design-package delivery, not
this application's own code). `B008` (FastAPI's `Depends()`-as-default-argument
idiom) is ignored — not a real mutable-default-argument bug, just the framework's
DI pattern.

## Type checking

```bash
mypy .
```

`python_version = "3.11"`, `ignore_missing_imports = true`.

## Tests

```bash
pytest
```

Must run against a **real, disposable PostgreSQL cluster** — never SQLite, never a
mock. `tests/conftest.py`'s own docstring is explicit about why: this schema uses
Postgres-specific types (e.g. `RightsGrant.uses`' `ARRAY(String)` column) that
"would behave differently, or simply not work, against anything else," and the
requirements this codebase actually cares about verifying — `FORCE ROW LEVEL
SECURITY`, the append-only triggers, `relay_role`'s restricted grants — have no
SQLite equivalent at all. One cluster is started per test session (expensive);
tables are dropped and recreated per test function for isolation (cheap, and avoids
one test's rows leaking into another's assertions). If the `postgresql-16` server
binaries or a `psycopg` driver are genuinely unavailable, tests using the
`postgres_engine` fixture **skip** rather than silently running against SQLite or a
mock — a skip is honest; a silent SQLite fallback would not actually verify the
requirement.

## Migrations

```bash
alembic upgrade head
```

verified end-to-end against a real disposable Postgres cluster, from a fresh,
empty database, following the exact linear chain documented in
`docs/database/MIGRATIONS.md` — not merely "the tests pass," since RLS, `FORCE ROW
LEVEL SECURITY`, the append-only trigger function, and `relay_role`'s grants are all
real DDL applied by specific migrations (`04c418cbb547`, `3f7a19c02b8e`) that must
themselves succeed against a real server, in order, from nothing. A migration that
imports `app.db`'s live, current-state constants instead of a frozen historical
snapshot (see `docs/database/MIGRATIONS.md`'s convention section) can pass in
isolation yet still fail a genuine fresh `upgrade head` replay once a later table has
been added to those live constants — this exact failure mode has occurred for real
(`UndefinedTable: relation "release_reviews" does not exist`) and is why this check
is part of the done-bar, not merely "migrations exist."

## Full done-bar (a change is not complete until all four pass)

1. `ruff check .` clean
2. `mypy .` clean
3. `pytest` clean against a real disposable Postgres cluster (not SQLite, not
   mocked, not skipped when the tooling is actually available)
4. `alembic upgrade head` succeeds end-to-end against a real disposable Postgres
   cluster, from a fresh database, through the entire migration chain

A change that passes tests against SQLite, or that skips the Postgres-only RLS/
append-only/relay-role coverage without a genuine tooling-unavailable reason, has
not met this project's own done-bar even if every other check is green.
