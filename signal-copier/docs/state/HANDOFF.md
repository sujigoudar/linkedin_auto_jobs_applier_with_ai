# Handoff (state snapshot)

For the general handoff practice and required fields, see
`docs/agents/HANDOFF.md`. This file is the current, standing snapshot —
overwrite it as the real current handoff whenever a task ends mid-stream,
rather than letting it drift out of date.

## Template

```
## Handoff: <task name>
Date: <date>

**Worktree**: <path> on branch <branch>, tracking <remote-ref>
**Committed**: <commit hash(es) and one-line summary>
**Uncommitted**: <files, or "none">

**Verified**:
- [ ] ruff check .
- [ ] mypy (CI-scoped command, docs/process/DEFINITION_OF_DONE.md #2)
- [ ] pytest -q (full suite)
- [ ] load-bearing verification: <invariant, and whether the revert/
      confirm-fails/restore/reconfirm cycle was actually done>
- [ ] rebased onto current origin/claude/signal-copier-redesign
- [ ] re-verified post-rebase
- [ ] pushed

**Known collision to re-check**: <e.g. a specific alembic revision
number, or "none currently">

**Next steps** (in order):
1. ...
```

## Current real handoff (as of this documentation pass)

```
## Handoff: docs/process, docs/agents, docs/state, and repo-level docs
Date: 2026-09-29

**Worktree**: /tmp/wt-docs-sc-e/signal-copier on branch agent-docs-sc-e,
tracking origin/claude/signal-copier-redesign (checked out from HEAD
cdfee8b1dc4d5daea58e178ce679aff37adecba8)
**Committed**: pending -- this documentation task's own commit, adding
docs/process/*, docs/agents/*, docs/state/*, docs/ROADMAP.md,
docs/FEATURES.md, docs/KNOWN_ISSUES.md, docs/TECH_DEBT.md,
docs/ASSUMPTIONS.md, CHANGELOG.md, docs/history/ENGINEERING_LOG.md
**Uncommitted**: none once committed as instructed

**Verified**:
- [ ] ruff check . -- N/A, documentation-only change (no Python files
      touched)
- [ ] mypy -- N/A, documentation-only change
- [ ] pytest -q -- N/A, documentation-only change; NOT independently
      re-run against current HEAD as part of this pass (see
      docs/state/state.json's last_verified note) -- a future task
      picking real code work back up should run it fresh, not assume
      this documentation pass implies a green suite
- [x] load-bearing verification: N/A (no code/invariant changed)
- [ ] rebased onto current origin/claude/signal-copier-redesign --
      done immediately before pushing (git fetch + git rebase)
- [ ] re-verified post-rebase -- re-read the rebased files for
      conflicts; no code to re-run
- [ ] pushed -- see the pushed commit hash this task reports back

**Known collision to re-check**: none expected -- this task owns only
documentation paths (docs/process/, docs/agents/, docs/state/,
docs/ROADMAP.md, docs/FEATURES.md, docs/KNOWN_ISSUES.md,
docs/TECH_DEBT.md, docs/ASSUMPTIONS.md, CHANGELOG.md, docs/history/)
that sibling documentation-layer agents working the same redesign are
expected to avoid; a genuine collision here would mean two
documentation agents wrote to the same path, not an Alembic-style
numbering race.

**Next steps**:
1. Whoever picks up real code work on this branch next should treat
   docs/state/PROGRESS.md, tasks.json, and BLOCKERS.md as current only
   as of commit cdfee8b -- re-read git log for anything that has landed
   since, and update these files rather than trusting them stale.
2. The trading_authority-wiring-to-P0-6 gap in docs/state/tasks.json is
   real, currently open, and actionable -- a good next foundation-style
   task.
3. Independently re-run ruff/mypy/pytest against current HEAD before
   relying on this snapshot's "last known-verified" characterization for
   anything beyond documentation.
```
