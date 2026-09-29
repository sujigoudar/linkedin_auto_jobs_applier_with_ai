# Testing Standards

The real convention, cross-checked against `tests/conftest.py`,
`.github/workflows/signal-portfolio-commercial-ci.yml`, and recent commit
history. See also `docs/testing/FIXTURES.md`, `docs/testing/TEST_MATRIX.md`,
`docs/testing/E2E.md`, and `docs/testing/REGRESSIONS.md` for the deeper
dives this file points to.

## 1. Tests run against a real, disposable Postgres 16 -- never SQLite, never a mock

`tests/conftest.py`'s own module docstring states the mechanism and why:

> "A real, disposable local PostgreSQL cluster for tests -- per
> spec/docs/02_architecture_and_tenancy.md: 'Local tests use disposable
> PostgreSQL and a controlled JWT issuer.' One cluster per test SESSION
> (expensive to start), with tables dropped and recreated per test
> function for isolation (cheap, and avoids one test's rows leaking into
> another's assertions)."

and explains explicitly why SQLite/mocks are never substituted:

> "if genuinely unavailable, tests using `postgres_engine` skip rather
> than silently running against SQLite or a mock (this package's own data
> types, e.g. `RightsGrant.uses`' `ARRAY(String)` column, are
> Postgres-specific and would behave differently, or simply not work,
> against anything else)."

See `docs/testing/FIXTURES.md` for exactly how the cluster is started,
scoped, and torn down.

## 2. The house standard: load-bearing verification, not "tests that merely pass"

A test in this codebase is expected to actually *catch the bug it claims
to catch* -- demonstrated, not assumed. The clearest recent example is
the CU-06 max-drawdown fix (commit `64d596d`, "CU-06: real max drawdown +
win rate from real FIFO-lot equity/episodes"):

> "Fixes a peak-tracking regression left mid-verification by the prior
> session (the running peak was being overwritten with every point
> instead of only on a new high) and adds a load-bearing regression test
> (`test_max_drawdown_uses_the_true_running_peak_not_just_the_previous_
> point`) that specifically catches that bug class -- the original
> load-bearing test alone did not, because its own true peak sits
> immediately before its own true trough. **Hand-verified: broke the fix
> back to 'peak = previous point', confirmed only the new test failed
> with the exact predicted wrong numbers (100/2min instead of 150/4min),
> restored, reconfirmed all 7 tests green.**"

That is the bar: when you add a regression test for a real bug, you do
not just add an assertion and move on -- you (at least once, during
development) revert the fix and confirm the *specific* new test fails
with the exact wrong numbers you predicted, then restore the fix and
confirm green. A test that would still pass against the broken code is
not load-bearing and does not satisfy this convention (this is exactly
what happened to the *original* CU-06 test before the new one was added
-- its own true peak happened to sit right before its own true trough, so
overwriting the peak on every point didn't change its answer).

The same discipline shows up in `app/services/evidence_manifest.py`'s own
tamper-detection test,
`tests/test_evidence_manifest.py::test_manifest_hash_detects_a_single_
tampered_row`: it does not just assert a hash function returns *some*
non-null value, it mutates one already-hashed row's real field
(`tampered_rows[0]["action"] = "invite_staff_member:owner"`) and asserts
the recomputed hash actually differs -- proving the hash is sensitive to
real tampering, not merely present.

When adding a bug-fix commit:

1. Write the regression test first (or immediately alongside the fix).
2. Confirm it fails against the pre-fix code, with the exact wrong value
   you expect (not just "it fails").
3. Apply the fix, confirm it passes, and confirm the rest of the suite
   for that module is still green.
4. Name the test after the specific bug class it guards against (see
   `docs/standards/NAMING.md`), so its purpose survives even if the
   surrounding code changes shape later.

## 3. Exact CI pipeline (`.github/workflows/signal-portfolio-commercial-ci.yml`)

Every commit runs, in this order, from the `signal-portfolio-commercial/`
working directory:

1. **Install PostgreSQL 16 server binaries** (`apt-get install -y
   postgresql-16`) -- required because `tests/conftest.py` expects real
   server binaries at `/usr/lib/postgresql/16/bin` to match the
   development environment, not "whatever Postgres happens to be
   preinstalled on the runner image."
2. **`pip install -r requirements.txt`**
3. **Lint: `ruff check .`** -- scoped by `pyproject.toml`'s `[tool.ruff]`
   to `select = ["F", "B"]` only (pyflakes real-bug detectors and
   flake8-bugbear), deliberately *not* the opinionated style families
   (E/W, I, C4, UP) that would produce a repo-wide reformatting diff
   unrelated to catching real mistakes -- this mirrors
   `signal-copier/pyproject.toml`'s own documented choice, and exists
   because CI once resolved a newer ruff whose broader default ruleset
   flagged 66 pre-existing style choices as errors, none of them real
   bugs. `B008` (mutable-default-argument) is ignored because FastAPI's
   own `Depends(...)`-as-default-argument idiom would otherwise be
   flagged on every route parameter.
4. **Type check: `mypy app --ignore-missing-imports`**
5. **Run tests: `pytest -q`** -- this is the suite that uses `tests/
   conftest.py`'s own disposable-per-session Postgres cluster and
   `Base.metadata.create_all`/`drop_all` schema, **never Alembic** (see
   `docs/testing/FIXTURES.md`).
6. **Verify Alembic migrations apply cleanly** -- a *separate* step, on a
   *separate* disposable cluster (`initdb`/`pg_ctl` on port `55433`,
   torn down at the end), running `python -m alembic upgrade head` for
   real. The workflow file's own comment explains exactly why this is
   separate from step 5:

   > "Real migration verification, separate from the test suite's own
   > per-test disposable schema (`tests/conftest.py` never runs Alembic
   > -- it uses `Base.metadata.create_all` directly): starts one more
   > disposable cluster and runs the actual `alembic upgrade head` a real
   > deployment would run, so a broken migration (e.g. the
   > transaction-visibility bug the RLS/append-only revision hit during
   > development) fails CI instead of surfacing only at deploy time."

   In other words: the pytest suite proves the *application* is correct
   against a schema built the fast way; this step proves the *actual
   migration path a deployment will run* is correct too -- these are
   deliberately not the same test, because a schema bug (e.g. a migration
   referencing a table that doesn't exist yet at that point in history,
   as happened once with `release_reviews` -- see `docs/standards/
   CODING.md`) can pass `create_all` while still breaking `alembic
   upgrade head`.

A change is not "done" until all six steps are green locally, in that
order, exactly matching the workflow file -- do not rely on `pytest -q`
alone as a stand-in for "CI passes."

## 4. Tests run as root -- and that matters

`tests/conftest.py::_run_as_postgres` documents a real environment detail:
"Postgres refuses to run its own server/init tools as root (most
sandboxes, including this one, run tests as root) -- `su postgres -c`
drops to the system `postgres` user for exactly those commands." If you
add new fixture-level Postgres administration (new roles, new databases),
route it through `_run_as_postgres`, not a bare `subprocess.run`, so it
keeps working in both root sandboxes and a normal CI user.
