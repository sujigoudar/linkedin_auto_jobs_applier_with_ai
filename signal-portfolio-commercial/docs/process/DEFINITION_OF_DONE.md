# Definition of done

The real bar this app's CI (`.github/workflows/signal-portfolio-commercial-ci.yml`)
and its own commit history apply, not an aspirational one.

## 1. `ruff check .` clean

Scoped by `pyproject.toml`'s `[tool.ruff]` to `select = ["F", "B"]`
(pyflakes + bugbear — real-bug detectors: unused imports/variables,
unreachable code, mutable default arguments) with `dashboard_spec/`
excluded (a vendored design-package delivery, not this app's own code)
and `B008` ignored (FastAPI's `Depends()`-as-default-argument DI
idiom, not a real mutable-default bug). This selection is deliberate,
not ruff's default — `pyproject.toml`'s own comment records that CI
once resolved a newer ruff whose broader default ruleset flagged 66
pre-existing style choices as errors, none of them real bugs, which is
why the rule set is pinned explicitly rather than left to whatever
ruff happens to be installed.

## 2. `mypy app --ignore-missing-imports` clean

Scoped to `app/` (the CI step), not the whole package — `tests/`,
`alembic/versions/`, and vendored/spec directories are not part of this
gate.

## 3. `pytest -q` green against a real disposable Postgres cluster

`tests/conftest.py` starts and tears down its own real, local
`postgresql-16` server per test session (not SQLite, not a mock
session, not a shared long-lived database) — see `app/db.py`'s own
comment: "This service is Postgres-only... never add a SQLite fallback
or a mock session for these tests." That fixture also creates the
restricted, non-superuser `relay_role`/`app_role` login roles the RLS
and relay-boundary tests run against for real, since a superuser
connection bypasses `FORCE ROW LEVEL SECURITY` and would make those
tests pass trivially without proving anything.

`tests/conftest.py` builds this per-test schema with
`Base.metadata.create_all` plus the same `app/db.py` helper functions
(`enable_row_level_security`, `enforce_append_only`,
`enable_relay_role_access`, the visibility-policy functions) the real
Alembic migrations also call — never a hand-duplicated copy of that
SQL — so passing tests and a real migrated database are provably
running the same schema-enforcement logic, not two implementations
that can silently drift apart.

## 4. `alembic upgrade head` verified end-to-end on a fresh database — a separate CI step

CI has a dedicated step for exactly this
(`Verify Alembic migrations apply cleanly`), deliberately **separate**
from the test suite's own schema setup:

```yaml
- name: Verify Alembic migrations apply cleanly
  run: |
    set -e
    export PATH="/usr/lib/postgresql/16/bin:$PATH"
    PGDATA=$(mktemp -d)
    PORT=55433
    initdb --auth=trust -D "$PGDATA"
    pg_ctl -D "$PGDATA" -o "-p $PORT -k $PGDATA -c listen_addresses=127.0.0.1" -l "$PGDATA/server.log" -w start
    createdb -h 127.0.0.1 -p $PORT -U "$(whoami)" commercial_migration_check
    ALEMBIC_DATABASE_URL="postgresql+psycopg://$(whoami)@127.0.0.1:$PORT/commercial_migration_check" python -m alembic upgrade head
    pg_ctl -D "$PGDATA" -m fast stop
    rm -rf "$PGDATA"
```

**Why this is a separate step, in the workflow's own words:**
`tests/conftest.py` never runs Alembic — it builds each test's schema
directly via `Base.metadata.create_all`. That means a green test suite
proves the *models and RLS/append-only helpers* are correct, but proves
nothing about whether the *migration chain itself* — every
`upgrade()` in order, from `f1cbfcc4819c` (initial schema) through the
current head — actually applies cleanly to a brand-new database the
way a real deployment's entrypoint (`deploy/entrypoint-commercial.sh`)
will run it. Those are genuinely different failure modes: a model can
be correct while a migration that builds towards it is broken (wrong
`down_revision`, a DDL statement valid only against a *later* schema
snapshot, a reused revision id). This is not a hypothetical concern —
the row-level-security migration's own docstring
(`04c418cbb547_row_level_security_and_append_only_.py`) records a real
failure this exact check caught: a fresh `alembic upgrade head` run
failed with `UndefinedTable: relation "release_reviews" does not
exist` when that migration's `upgrade()` was (incorrectly) defaulting
to the live, ever-growing `app.db._TENANT_SCOPED_TABLES` constant
instead of a frozen snapshot of the tables that actually existed at
that point in migration history. The fix was pinning
`_TABLES_AT_THIS_REVISION`/`_APPEND_ONLY_TABLES_AT_THIS_REVISION`
tuples inside the migration file itself. Without this dedicated CI
step, that class of bug surfaces only at real deployment time, against
a real database, which is exactly what `deploy/entrypoint-commercial.sh`
is trying to prevent by running `alembic upgrade head` before `uvicorn`
ever starts.

## 5. Genuine, load-bearing verification

Not "the new test passes" — this codebase's own convention, visible in
essentially every commit body, is to **break the change on purpose and
confirm the specific new test(s) fail for the predicted reason**, then
restore and reconfirm green. Examples straight from this app's history:

- `64d596d` (CU-06 drawdown/win-rate): "Hand-verified: broke the fix
  back to 'peak = previous point', confirmed only the new test failed
  with the exact predicted wrong numbers (100/2min instead of
  150/4min), restored, reconfirmed all 7 tests green."
- `3b8cf40` (INT-033 real-account single-writer enforcement): "Load-bearing
  verified by temporarily disabling the rejection branch and confirming
  exactly the two tests exercising it fail, then restoring."
- `72efeae` (revocable JWT sessions): "Manually confirmed these tests
  fail when `verify_token` is changed to skip the denylist check, and
  pass again once restored."
- `f752904` (AD-18 evidence manifest): a load-bearing tamper-detection
  test showing the content hash changes when a bundled row is altered.

A test that has never been observed to fail for the right reason is
not verified — it might be asserting something the code can't actually
violate. "Done" means this loop was actually run this session, not
merely that the final diff looks plausible.

## Full bar, restated

A change to `signal-portfolio-commercial` is done when:

1. `ruff check .` is clean.
2. `mypy app --ignore-missing-imports` is clean.
3. `pytest -q` is green against a real disposable Postgres cluster (never SQLite, never mocked).
4. `alembic upgrade head` has been verified end-to-end against a fresh database — either by re-running the CI-equivalent local check, or by trusting the dedicated CI step, when a migration was touched.
5. The specific new assertion(s) have been demonstrated load-bearing: broken on purpose, observed to fail, restored, reconfirmed green.
6. The commit message documents what was built, what was deliberately left out (per `ASSUMPTIONS.md`/`KNOWN_ISSUES.md`'s own honesty convention), and how it was verified — so a cold reader never has to re-derive that from the diff.
