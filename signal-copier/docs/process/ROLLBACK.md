# Rollback

There is no automated rollback/canary mechanism in this project (see
`docs/process/RELEASE.md` — there is no release pipeline for one to plug
into). Rollback here means two distinct things depending on what broke:
rolling back *code*, and — a much more sensitive case this codebase
treats separately and deliberately manually — changing *which process is
the writer*.

## Rolling back a bad code change

Plain git-based rollback:

1. Identify the bad commit on `claude/signal-copier-redesign`.
2. `git revert <bad-commit>` (a real revert commit, not a force-push
   rewrite of shared history — see `docs/process/GIT.md`: this branch
   never force-pushes over already-pushed work).
3. Run the full verification in `docs/process/DEFINITION_OF_DONE.md`
   against the reverted tree.
4. Push the revert, rebuild the Docker image
   (`docs/process/RELEASE.md`) from the new tip, and redeploy.

If the bad commit included an Alembic migration, reverting the code
without a corresponding down-migration leaves the schema ahead of the
code. This codebase's migrations are additive-only by convention (see
`app/db.py`'s comment on why schema changes are now Alembic revisions
rather than appended tuples) specifically so that an additive column or
table left behind by a reverted commit is inert, not actively harmful —
check that the specific migration being rolled back from is genuinely
additive before assuming a plain code revert is sufficient.

## Rolling back a bad *writer-role* change — the manual, fenced model

This is the case `docs/FAILOVER.md` exists to explain in full, and it
should be read before ever touching writer-role state. The short version,
as it relates to rollback specifically:

- **There is no automatic failover or automatic rollback of the writer
  role, anywhere, on purpose.** A standby never promotes itself, and
  nothing in this codebase will silently "roll back" to a previous
  writer either.
- The only way a different site ever becomes the writer — including
  rolling back to a previous known-good site after a bad promotion — is
  a human running `python -m app.promote_cli promote`, which requires
  three explicit confirmation flags and independently verifies (via
  `app/writer_lease.py`'s fencing-token protocol) that the lease it
  would be taking over is genuinely expired, refusing with
  `LeaseStillValidError` otherwise.
- That command is only the automatic *check* layer. The actual proof
  that the previous writer is safe to move away from is
  `deploy/RUNBOOK.md`'s human-executed checklist — confirming, at the
  infrastructure or brokerage level, that the process being rolled back
  from genuinely cannot reach the broker any more (a stop/terminate
  confirmation, or a revoked/rotated API credential). Skipping that and
  promoting purely because the lease "looks expired" is exactly the
  failure mode the manual procedure and `promote_cli`'s
  `--confirm-old-writer-fenced` flag exist to prevent.
- The moment a new fencing token is issued, every process still holding
  the old token is fenced out on its very next command-execution check
  (`WriterLeaseGuard.require_active()`) — so a rollback of the writer
  role takes effect immediately for every other process in the fleet,
  without needing them to notice anything went stale on their own.

In short: rolling back code is ordinary git revert-and-redeploy.
Rolling back *who is allowed to trade* is deliberately not something
this system will ever do for you automatically — it is a human-verified,
two-layer procedure, and `docs/FAILOVER.md` plus `deploy/RUNBOOK.md` are
the actual source of truth for running it.
