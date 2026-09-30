# Review checklist

App-specific items, on top of the generic "does it pass CI" bar in
`DEFINITION_OF_DONE.md`. Every item below is grounded in a real
mechanism (or a real historical bug) in this codebase, not a generic
best practice.

## Row-level security / tenant isolation

- [ ] Does every new table that carries a `tenant_id` column get added
      to `app/db.py`'s `_TENANT_SCOPED_TABLES` (or, if it needs a
      bespoke visibility rule — public/anonymous read of PUBLISHED
      rows, like `products` or `content_documents` — its own explicit
      policy function, never silently left with no RLS at all)?
- [ ] Is the RLS policy applied with **both** `ENABLE ROW LEVEL
      SECURITY` and `FORCE ROW LEVEL SECURITY`? `ENABLE` alone is
      bypassed by the table owner — in this single-role-per-database
      development setup that is exactly the role the app usually
      connects as, so `ENABLE` alone would make the policy a no-op in
      practice, not a real boundary.
- [ ] Does the policy fail **closed** — no `app.tenant_id` session
      setting means zero visible rows, never "everything"? (`app/db.py`'s
      `_apply_row_level_security` policy is `tenant_id =
      current_setting('app.tenant_id', true)`, which evaluates to no
      match at all when unset — check a new bespoke policy preserves
      this rather than defaulting open.)
- [ ] Is there a real test exercising this under the actual restricted,
      non-superuser `app_role`/`relay_role` login (not the Postgres
      superuser, which bypasses `FORCE ROW LEVEL SECURITY` regardless)?
      A test asserting RLS behavior while connected as superuser proves
      nothing.

## `relay_role` — zero write access to command-authority tables

`relay_role` exists for exactly one purpose: the restricted ingress
worker that turns `signal-copier`'s own signal into an `InboxEvent`,
per the Integration Correction Pack's own S6/S8 decision
(`app/db.py`'s `_apply_relay_role_access` docstring). It must never be
able to write anything that constitutes trading/customer authority.

- [ ] Does the change grant `relay_role` any *new* privilege? If so, is
      it scoped to exactly the table and exactly the statement type
      needed (see the existing grants: `SELECT` on
      `export_stream_registrations`, `SELECT, INSERT, UPDATE` on
      `inbox_events`, `INSERT` on `ledger_entries` — never `UPDATE` or
      `DELETE` on `ledger_entries`, which is append-only regardless)?
- [ ] Does `relay_role` still have **zero** write access to
      `copy_mandates` (the one table recording customer copy-trading
      authority, CU-09) and every other command-authority table? This
      is not just a code-review question — it is a real, currently
      passing test:
      `tests/test_relay_role_access.py::test_relay_role_has_no_write_access_to_any_command_authority_table`,
      which asserts a real `ProgrammingError` (a genuine Postgres
      permission denial) when the restricted `relay_role` login tries
      to insert a `CopyMandate` row. If a change adds a new
      command-authority table, add the equivalent negative test for it
      — don't just trust that no grant was written.
- [ ] Does `app/api/relay_routes.py` still hold: "this route ingests
      observations only — it has no code path that submits, cancels or
      modifies a broker order"? A docstring claim is not the proof;
      the Postgres GRANT is. Re-check both stay true together.

## Alembic migration correctness after rebase

- [ ] After rebasing onto the latest shared branch, does `alembic
      heads` show exactly one head under this app's migration chain?
      More than one means a real revision collision landed (see
      `GIT.md`'s "Alembic revision collisions" section for the real
      `cdfee8b` incident in the sibling app) and needs a
      `down_revision` fix, not just a text-conflict resolution.
- [ ] Does the new revision's `down_revision` point at the actual
      current head after rebase, not the head it was generated
      against before rebase?
- [ ] Has `alembic upgrade head` actually been re-run against a fresh
      disposable Postgres cluster *after* the rebase, not just before
      it? A migration chain that applied cleanly pre-rebase can still
      be broken post-rebase if the chain order changed.
- [ ] If the migration touches RLS or append-only enforcement, does it
      pass a **frozen, revision-specific snapshot** of the affected
      table names (see `04c418cbb547`'s own
      `_TABLES_AT_THIS_REVISION`/`_APPEND_ONLY_TABLES_AT_THIS_REVISION`
      constants) rather than the live, ever-growing
      `app.db._TENANT_SCOPED_TABLES`/`_APPEND_ONLY_TABLES` constants?
      Defaulting to the live constant is exactly the bug that produced
      a real `UndefinedTable` failure during this app's own
      development — a migration replaying from an earlier point in
      history must never reference a table that doesn't exist yet at
      that point.
- [ ] Does a migration that would weaken an existing safety guarantee
      (RLS, append-only) have a `downgrade()` that actually raises
      rather than silently undoing the guarantee? See
      `04c418cbb547`'s own `downgrade()` — "downgrading row-level
      security / append-only enforcement is never a safe automatic
      operation." A migration that *adds* a normal column/table should
      still have a real, working `downgrade()` (see
      `ROLLBACK.md` for which migrations do and don't).

## Append-only / ledger integrity

- [ ] If the change touches `ledger_entries`, `portfolio_versions`,
      `portfolio_version_sleeves`, or `audit_events`, does it correct
      mistakes only by inserting a new row (e.g.
      `append_correction`/`correction_of`), never by editing or
      deleting the original? The Postgres trigger
      (`forbid_ledger_mutation`) will reject a direct `UPDATE`/`DELETE`
      even for the table owner, but the application-level code should
      never attempt one in the first place.

## Load-bearing verification present

- [ ] Does the commit message (or PR description) actually describe
      breaking the new guarantee on purpose and observing the specific
      new test fail, then restoring? See `DEFINITION_OF_DONE.md` for
      what this looks like in practice. A PR whose only evidence is
      "tests pass" has not demonstrated the test is testing anything.

## Honesty discipline

- [ ] Does the change claim something is "done" that actually still
      needs a real external dependency (broker sandbox, payment
      processor, live vendor credentials) this environment doesn't
      have? Check against `docs/PENDING_DECISIONS.md` and
      `docs/KNOWN_ISSUES.md` — if the dependency is genuinely missing,
      the change should say so explicitly (PARTIAL/BLOCKED, in this
      codebase's own vocabulary — see `INTEGRATION_ACCEPTANCE_STATUS.md`
      at the repo root), not silently simulate or fabricate it.
