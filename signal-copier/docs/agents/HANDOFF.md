# What a handoff needs

This branch's history has many real forced-handback points mid-task — an
agent's turn ends with work in some intermediate state, and the next
agent (or the same agent, next turn) needs to pick it up correctly
without re-doing finished steps or silently skipping unfinished ones. A
handoff that omits any of the following makes that impossible to do
safely.

## Required fields

1. **Current worktree path and branch.** Where the in-progress work
   physically lives, and its branch name — e.g. `/tmp/wt-docs-sc-e` on
   `agent-docs-sc-e`, tracking `origin/claude/signal-copier-redesign`.
   Without this the next turn has to rediscover or re-create the
   worktree, risking a second, divergent copy of the same work.

2. **What's committed vs. uncommitted.** Exactly which changes are in a
   real commit (with its hash) versus still sitting as an uncommitted
   diff in the working tree. Uncommitted work is invisible to anyone
   else's `git fetch` and is lost if the worktree is discarded — call
   this out explicitly rather than assuming "obviously" it'll be
   committed later.

3. **What's been verified vs. not yet.** Which of
   `docs/process/DEFINITION_OF_DONE.md`'s steps have actually been run
   against the current state (ruff? the scoped mypy command? full
   pytest? the load-bearing revert/confirm/restore cycle?) versus which
   are still outstanding. "Tests pass" without saying *which* tests,
   run *when*, against *which* commit, is not usable information — see
   `docs/agents/VERIFICATION.md`.

4. **Any known collision that needs re-checking on the next fetch.**
   Most concretely: an Alembic revision number this task's migration is
   currently using, which may already have been claimed by a sibling
   that lands on the shared branch before this work's next rebase (the
   real, repeated pattern in `docs/process/GIT.md`). Name the exact
   revision number and file, so the next turn doesn't have to rediscover
   the collision from a rebase conflict with no context.

5. **Exact next steps.** Not "finish this up" — the specific remaining
   actions, in order (e.g. "run the full mypy command from
   DEFINITION_OF_DONE.md item 2; if clean, rebase onto
   origin/claude/signal-copier-redesign; re-check alembic/versions/ for
   a new 0016 before renumbering this migration; re-run pytest -q; push").

## Template

```
## Handoff: <task name>

**Worktree**: <path> on branch <branch>, tracking <remote-ref>
**Committed**: <commit hash(es) and one-line summary of what's in them>
**Uncommitted**: <files with pending changes, or "none">

**Verified**:
- [ ] ruff check .
- [ ] mypy (CI-scoped command)
- [ ] pytest -q (full suite)
- [ ] load-bearing verification: <what invariant, and whether the
      revert/confirm-fails/restore/reconfirm cycle was actually done>
- [ ] rebased onto current origin/claude/signal-copier-redesign
- [ ] re-verified post-rebase
- [ ] pushed

**Known collision to re-check**: <e.g. "migration 0016_x.py — re-check
alembic/versions/ on next fetch for a sibling that claimed 0016 first"
or "none currently">

**Next steps** (in order):
1. ...
2. ...
```

## A real filled example, from this branch's own history

```
## Handoff: P0-6 writer lease fencing, post-rebase migration collision

**Worktree**: (agent's own worktree for the P0-6 task) on its own
agent branch, tracking origin/claude/signal-copier-redesign
**Committed**: 55976eb "P0-6: cross-process fencing + manual-only
failover for the writer lease" — writer_lease table, WriterLeaseGuard,
promote_cli, wired into engine.py/lifecycle/manager.py/main.py, as
migration 0014_add_writer_lease_table.py (chaining after
0013_add_route_qualifications_table.py, the head this worktree last saw)
**Uncommitted**: none

**Verified** (as of 55976eb, pre-rebase):
- [x] ruff check .
- [x] mypy (CI-scoped command)
- [x] pytest -q (full suite, including 13 new tests in
      tests/test_writer_lease_fencing.py)
- [x] load-bearing verification: confirmed a second site_id is refused
      while the lease is valid, and that WriterLeaseGuard.require_active()
      fences a stale in-memory token immediately on the next check
- [ ] rebased onto current origin/claude/signal-copier-redesign — NOT
      YET DONE as of this commit
- [ ] re-verified post-rebase
- [ ] pushed

**Known collision to re-check**: migration is currently numbered 0014
(0014_add_writer_lease_table.py). Re-check alembic/versions/ on next
fetch before pushing — a sibling task (command_ledger, P0-2) is landing
around the same time and may claim 0014 first.

**Next steps**:
1. git fetch origin claude/signal-copier-redesign
2. Check alembic/versions/ for the real current head
3. If 0014 is now taken, renumber writer_lease's migration to chain
   after whatever landed, updating revision/down_revision and its
   in-file chain comment
4. Re-run ruff/mypy/pytest against the rebased tree
5. Push
```

This is exactly what commit `cdfee8b` ("Renumber writer_lease migration
to 0015 (0014 taken by command_ledger)") shows actually happened: both
`ae6a016` (command_ledger, P0-2) and `55976eb` (writer_lease, P0-6) added
their own `0014_*.py` migration independently, from the same
`0013_add_route_qualifications_table.py` base; `command_ledger` reached
the shared branch first, so `writer_lease`'s migration was renumbered to
`0015` on the next rebase.
