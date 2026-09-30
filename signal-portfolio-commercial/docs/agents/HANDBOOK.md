# Agent handbook — signal-portfolio-commercial

How multiple Claude Code sessions actually work on this app, evidenced
by its own git history rather than prescribed in the abstract. Start
here; `ROLES.md`, `ROUTING.md`, `HANDOFF.md`, `TOOLS.md`, and
`VERIFICATION.md` go deeper on each piece.

## The working pattern, in one paragraph

Each unit of work happens in its own `git worktree`, checked out onto
its own branch off the shared integration branch. A session reads the
real current state (the code, the tests, `ops/commercial_state.json`,
the acceptance-status document at the repo root), does the smallest
real, honestly-scoped slice of work that closes a genuine gap, proves
it with load-bearing verification (break it on purpose, watch the
right test fail, restore, reconfirm), documents what it did and did
not build in the commit message, and rebases onto the shared branch
before pushing. Nothing is marked done without a cited, currently
passing test. Nothing is faked to look finished — a missing real
dependency (broker sandbox, payment processor, licensed data) is
disclosed as `PARTIAL`/`BLOCKED`/`NOT_RUN`, never silently simulated.

## Why worktrees, not shared branches per session

100 commits deep into this app's own history, there are no merge
commits and no evidence of two sessions editing the same file inside a
single shared working tree — every commit has its own
`Claude-Session:` trailer, and the sibling app's real revision-collision
incident (`cdfee8b`, see `GIT.md`) shows exactly what happens when two
sessions' independent work (each correct in isolation) needs to be
reconciled after the fact: a renumbered migration, not a merge
conflict inside someone's live edit. Isolation at the worktree level is
what keeps that reconciliation mechanical (rebase, fix the
`down_revision`, re-verify) instead of a race between two sessions'
uncommitted files.

## Real evidence bar, not vibes

This codebase's own definition of "real" is concrete and checkable:
a real disposable Postgres cluster (never SQLite/mocked), a real
migration chain verified end-to-end (a separate CI step from the test
suite, see `DEFINITION_OF_DONE.md`), and a real load-bearing
verification loop documented in the commit body. `docs/12_validation_report.md`
and `INTEGRATION_ACCEPTANCE_STATUS.md` (repo root) apply this same
discipline at the requirement/acceptance-case level: every `PASS` cites
the actual test; every `PARTIAL`/`BLOCKED`/`NOT_RUN` says exactly
what's missing and why, never silently marked done. Agents working
this app inherit that discipline for every change, not just the
big audits.

## A real forced-handback pattern from this app's own history

Sessions do not always finish what they started, and that's expected —
the codebase's own convention is to leave the next session a precise,
honest account of where things stand rather than pretend a slice is
finished. Two concrete examples:

- `64d596d`'s own commit message: "Fixes a peak-tracking regression
  left mid-verification by the prior session (the running peak was
  being overwritten with every point instead of only on a new high)."
  The prior session's work was real progress, left in a broken
  intermediate state; the next session found it, understood why the
  existing load-bearing test alone hadn't caught it (its own true peak
  sits immediately before its own true trough), and added a new
  regression test that specifically catches that bug class before
  fixing it.
- `ops/COMMERCIAL_RESUME.md` and `ops/commercial_state.json` are
  themselves a real, maintained cross-session handoff artifact — every
  phase's status, what's wired versus not, and an explicit "Where this
  build actually stands (read this first if picking up cold)" section
  written specifically so a session starting fresh does not need to
  re-derive the state of the world from the diff history.

See `HANDOFF.md` for the current equivalent of that pattern.

## Read next

- `ROLES.md` — what a session is actually responsible for on this app.
- `ROUTING.md` — how work gets scoped to `signal-portfolio-commercial`
  versus its siblings in the monorepo.
- `HANDOFF.md` — how to leave the next session a real, honest starting
  point.
- `TOOLS.md` — the real, local verification tools this app's own CI
  and tests rely on.
- `VERIFICATION.md` — what "proven," not just "passing," means here.
