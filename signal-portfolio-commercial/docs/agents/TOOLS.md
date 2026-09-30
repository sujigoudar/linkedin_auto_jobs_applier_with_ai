# Tools

The real, local tooling this app's own CI and test suite rely on —
nothing hypothetical.

## Lint / type check

- **`ruff check .`** — scoped by `pyproject.toml` to `select = ["F",
  "B"]` (pyflakes + bugbear), `dashboard_spec/` excluded, `B008`
  ignored (FastAPI `Depends()` DI idiom). See
  `docs/process/DEFINITION_OF_DONE.md` for why this selection is
  pinned rather than left to ruff's defaults.
- **`mypy app --ignore-missing-imports`** — scoped to `app/` only.

## Tests

- **`pytest -q`** (or `pytest tests/ -v` for verbose per-test output,
  per `ops/COMMERCIAL_RESUME.md`'s own documented command). Real,
  currently around 800+ test functions across `tests/`.
- **`tests/conftest.py`** is itself a tool worth understanding, not
  just running: it starts and tears down a real disposable
  `postgresql-16` cluster per test session (`postgres_cluster`
  fixture), builds the schema via `Base.metadata.create_all` plus the
  same RLS/append-only/relay-role helper functions the real migrations
  call, and exposes a `relay_session_factory` fixture bound to the
  genuine restricted, non-superuser `relay_role` login — the exact
  mechanism `tests/test_relay_role_access.py` uses to prove
  `relay_role` really cannot write to command-authority tables (a
  Postgres permission denial, not an application-level assertion).
  Requires real `postgresql-16` server binaries on the runner (CI
  installs them explicitly, since the version must match what
  `conftest.py` expects at `/usr/lib/postgresql/16/bin`).

## Migrations

- **`alembic`** (`alembic.ini`, `alembic/versions/`) — the real
  migration tool. `alembic upgrade head` is CI's own dedicated
  migration-verification step (see `docs/process/DEFINITION_OF_DONE.md`),
  run against a fresh disposable cluster distinct from the test
  suite's own. `alembic revision --autogenerate -m "..."` is the
  starting point for a new migration, but autogenerate does not know
  about RLS policies, append-only triggers, or `relay_role` grants —
  those are always hand-written afterward (see e.g.
  `3f7a19c02b8e_add_relay_role_access.py`).
- **`ALEMBIC_DATABASE_URL`** env var — the connection Alembic itself
  uses (distinct from `COMMERCIAL_DATABASE_URL`/
  `COMMERCIAL_MIGRATOR_DATABASE_URL`, the app's own runtime env vars —
  check `alembic/env.py` for exactly how these map before assuming
  which one a given command needs).

## Containers

- **`docker compose up --build`** (repo root `docker-compose.yml`) —
  the real, runnable local stack: Postgres + both apps + the in-process
  relay, entirely in `LOCAL_SIM`/paper mode with synthetic secrets. See
  `docs/process/RELEASE.md`.
- **`signal-portfolio-commercial/Dockerfile`** /
  **`deploy/entrypoint-commercial.sh`** — the real image build and
  startup ordering (migrate, then bootstrap, then serve).

## Bootstrap / ops scripts

- **`ops/bootstrap.py`** — real, idempotent Postgres role/tenant/export-
  stream provisioning, invoked by the entrypoint script and directly
  runnable for local setup. Reads `COMMERCIAL_RUNTIME_ROLE_PASSWORD`
  and related env vars directly (never a CLI argument — see its own
  module docstring).

## What this app does NOT have

No SQLite test fallback (deliberately refused, see
`docs/process/DEVELOPMENT.md`), no mocked-database test mode, no
separate linter/formatter beyond ruff's two selected rule families, no
load/performance-testing tool in active use, no e2e browser-automation
harness of its own (the sibling `signal-copier` app's history mentions
manual Playwright smoke tests for some changes, e.g. `5e8d9e4`, but
this is ad hoc, not a standing tool in this app's own CI).
