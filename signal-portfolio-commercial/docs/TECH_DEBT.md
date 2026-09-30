# Tech debt

Real, named debt — things that work but carry an acknowledged cost or
a deliberately deferred proper fix, distinct from `KNOWN_ISSUES.md`'s
externally-blocked gaps.

## Migrator connection uses the Postgres superuser in local/paper mode

`docker-compose.yml`'s own comment: "The Postgres superuser is fine for
this local/paper stack; a real production deployment should use a
dedicated migrator role instead." `deploy/entrypoint-commercial.sh`
runs both `alembic upgrade head` and `ops/bootstrap.py`'s own role/GRANT
DDL under `COMMERCIAL_MIGRATOR_DATABASE_URL`, which the compose file
currently points at the superuser connection. A dedicated migrator
role (`CREATEROLE` + ownership of the target schema, never superuser)
is real, named future work — see `docs/state/tasks.json` (`TASK-10`).

## `04c418cbb547`'s frozen-table-list pattern must be repeated by hand for every future RLS/append-only migration

`app/db.py`'s `_apply_row_level_security`/`_apply_append_only` take an
optional `tables` parameter specifically so a historical migration can
pass a frozen, revision-specific snapshot instead of the live,
ever-growing `_TENANT_SCOPED_TABLES`/`_APPEND_ONLY_TABLES` constants —
this is documented, tested, and correct, but it's also a manual
discipline: every future migration in this family needs the author to
remember to freeze the list explicitly, not just call the generic
function. A missed freeze reproduces the exact `UndefinedTable` failure
`04c418cbb547`'s own docstring records as a real, previously-hit bug.
See `docs/process/REVIEW_CHECKLIST.md`'s "Alembic migration
correctness" section — this is a reviewer-enforced discipline, not a
type-checked one.

## `04c418cbb547` has no `downgrade()` path

Deliberately — see `docs/process/ROLLBACK.md` — but it means the
migration chain is not uniformly reversible. Any future tooling that
assumes every migration downgrades cleanly needs to special-case this
revision (and any future one that follows the same "never silently
weaken a live guarantee" pattern).

## `relay_role`'s access is hand-maintained, not generated

`app/db.py`'s `_apply_relay_role_access` grants exactly three
statement-scoped privileges by hand (`SELECT` on
`export_stream_registrations`, `SELECT, INSERT, UPDATE` on
`inbox_events`, `INSERT` on `ledger_entries`) plus one bespoke
permissive policy. This is intentionally minimal and real-tested
(`tests/test_relay_role_access.py`), but every new table `relay_role`
might legitimately need to touch requires a deliberate, reviewed grant
addition — there is no automatic "relay-safe" table classification. A
reviewer must re-check `docs/process/REVIEW_CHECKLIST.md`'s relay_role
section on every schema change that could plausibly interact with the
relay path.

## `ops/commercial_state.json` / `ops/COMMERCIAL_RESUME.md` are now historical, not current

These files were the authoritative handoff during the original
13-phase build and are extremely detailed, but they were last
substantially updated at the "post-Phase-12 addendum" point — a great
deal of real work has landed since (revocable sessions, evidence
manifest, secret rotation, the full CU-/AD-/PU- screen build-out, the
INT- integration pack). Treat them as valuable historical record (see
`docs/history/ENGINEERING_LOG.md`) and read `docs/state/PROGRESS.md`
for the current snapshot instead of assuming `ops/commercial_state.json`
is up to date.

## No dedicated production migrator/runtime-role provisioning script beyond `ops/bootstrap.py`'s local/paper defaults

`ops/bootstrap.py` reads its role passwords from env vars
(`COMMERCIAL_RUNTIME_ROLE_PASSWORD` etc.) with synthetic local-sim
defaults baked into `docker-compose.yml`. A real production deployment
needs a real secrets-management story for these — not built, since no
production deployment exists yet (see `docs/process/RELEASE.md`).

## Test suite has no automated e2e browser layer of its own

Some sibling-app commits (`5e8d9e4`) mention manual Playwright smoke
testing for specific changes, but this app has no standing,
CI-integrated browser-automation harness — verification relies on
HTTP-level `pytest` coverage plus the load-bearing break/restore
discipline (`docs/agents/VERIFICATION.md`), which is real and rigorous
but does not exercise the rendered HTML/JS the way a browser would.
