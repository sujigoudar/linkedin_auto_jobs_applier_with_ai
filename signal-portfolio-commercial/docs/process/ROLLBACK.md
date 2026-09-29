# Rollback

There is no production deployment to roll back (see `RELEASE.md`) —
this is a git-based and migration-aware posture for a development
branch, not a live-incident runbook.

## Code: git-based rollback

Because every unit of work lands as its own commit off a worktree (see
`GIT.md`) with a rebase-only, linear history, reverting a bad change on
the shared branch is ordinary git:

```
git fetch origin claude/signal-copier-redesign
git revert <bad-commit-sha>
git push origin HEAD:claude/signal-copier-redesign
```

Prefer `git revert` over rewriting shared history — multiple worktrees
may already be based on the commit being undone, and a force-push that
discards it would silently drop their work. If a change hasn't been
pushed anywhere else yet, an interactive rebase to drop it is fine
inside your own worktree only.

## Migrations: check honestly, per revision, before assuming `alembic downgrade` works

Alembic migrations in this app are **real forward/backward revisions**
for the overwhelming majority of the chain — not forward-only
placeholders. Every table-adding or column-adding migration in
`alembic/versions/` (37 revisions as of this writing) has a genuine
`downgrade()` that undoes exactly what `upgrade()` did: e.g.
`a1b2c3d4e5f6_local_auth_password_and_sessions.py`'s `downgrade()`
drops `web_sessions`, drops the `auth_tokens` index and table, and
drops `user_identities.email_verified_at` — a real, working inverse,
not a `pass`.

**One deliberate, documented exception:**
`04c418cbb547_row_level_security_and_append_only_.py` — the migration
that turns on row-level security and append-only enforcement — has a
`downgrade()` that raises `NotImplementedError` on purpose:

```python
def downgrade() -> None:
    raise NotImplementedError(
        "downgrading row-level security / append-only enforcement is never a safe automatic "
        "operation -- it would silently weaken a live database's isolation guarantees"
    )
```

This is the honest answer, not a gap: automatically turning off
tenant isolation or append-only protection as a side effect of an
`alembic downgrade` command is exactly the kind of "silent
regression" this codebase's own conventions refuse to allow (see
`DEFINITION_OF_DONE.md`). If a schema genuinely needs to roll back
past this revision, that is a deliberate, manual, reviewed operation
against the live database — not something a rollback script should do
unattended.

## Practical rollback procedure

1. **Identify the target revision.** `alembic history` (or read
   `alembic/versions/` and follow the `down_revision` chain) to find
   the revision id to roll back to.
2. **Check whether every migration between HEAD and the target has a
   real `downgrade()`.** As of this writing, only `04c418cbb547` does
   not. If the target is *before* that revision, `alembic downgrade`
   cannot get there automatically — this needs a manual, reviewed plan
   (confirm the target genuinely needs RLS/append-only turned off, not
   just an earlier schema shape) rather than a scripted step.
3. **Never run `alembic downgrade` against a database holding real
   data without a fresh backup first** — a downgrade that drops a
   column or table is exactly as destructive as it sounds, and this
   codebase's append-only tables (`ledger_entries`, `portfolio_versions`,
   `portfolio_version_sleeves`, `audit_events`) exist specifically
   because losing that history is unacceptable.
4. **Re-verify after rolling back**: run the app's test suite and the
   CI-equivalent migration check (see `DEFINITION_OF_DONE.md`) against
   the rolled-back schema before resuming work on top of it — a
   downgrade chain is exercised far less often than the upgrade chain
   and is more likely to hide its own bugs.

## What this app does not have

No blue/green deployment, no canary, no automated production rollback
trigger, no versioned release artifact to roll back *to* (see
`RELEASE.md`) — because there is no production deployment yet. This
document will need real operational procedures once one exists; until
then, git-based rollback of the branch and migration-aware rollback of
the schema are the whole story, honestly.
