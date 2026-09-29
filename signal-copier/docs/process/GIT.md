# Git workflow

This describes the convention this repository's own history actually
follows on `claude/signal-copier-redesign`, not an idealized one.

## Shared integration branch

All work lands on one shared branch — currently `claude/signal-copier-redesign`
off `main`. There is no long-lived per-feature branch that survives past a
single unit of work: an agent creates a branch or worktree, does the work,
rebases onto the current tip of the shared branch, and pushes straight to
it. The shared branch itself is the integration point multiple agents
converge on, sometimes within the same session window (see
`ae6a016` "Add command_ledger" and `55976eb` "P0-6: cross-process fencing"
landing four minutes apart, then `cdfee8b` resolving the migration
collision between them).

## Worktrees per unit of work

Concurrent agents do not share a working directory. Each checks out the
shared branch's current tip into its own `git worktree`, on its own
branch name, works there in isolation, and only interacts with the other
agents' work at rebase/push time:

```
git worktree add <path> origin/claude/signal-copier-redesign -b <agent-branch>
```

This is why collisions are discovered at push/rebase time rather than
mid-edit: two worktrees can independently add migration `0014` without
ever seeing each other's file until one of them fetches and rebases.

## Commit trailers

Every commit on this branch carries a two-line trailer identifying the
agent and the session that produced it:

```
Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_<id>
```

This is present on effectively every substantive commit in the log (see
`git log --format='%H %s%n%b'`) and is what makes it possible to trace a
given change back to the exact agent session that produced it, including
across a handoff.

## Rebase, never merge, never force-push over someone else's work

Integrating with the shared branch is always a `git fetch` +
`git rebase` onto the fetched tip, followed by an ordinary `git push`.
There are no merge commits in this history. A rebase that turns out to
conflict with another agent's already-pushed change is resolved by
editing in place (see the migration-renumbering pattern below) and
re-pushing — never by force-pushing over the other agent's commits, and
never by falling back to a merge commit to avoid resolving the conflict.

If a push is rejected because the remote has moved, the correct response
is: fetch, rebase onto the new tip, resolve, verify again (see
`docs/process/DEFINITION_OF_DONE.md`), push. Never `--force` a shared
branch to make a stale local branch "win."

## Alembic revision collisions get renumbered, not merged blindly

Because migrations are added by concurrent agents working from the same
base revision, two worktrees frequently pick the same next revision
number independently. The convention is: whichever migration reaches the
shared branch second is renumbered to chain onto the one that landed
first, not left to create a branching migration history.

Concrete example from this branch: both `ae6a016` ("Add command_ledger",
P0-2) and `55976eb` ("P0-6: cross-process fencing") independently added
their own `alembic/versions/0014_*.py` migration, each chaining onto
`0013_add_route_qualifications_table.py` — the real head each worktree
last saw before starting. `ae6a016`'s `0014_add_command_ledger_table.py`
reached the shared branch first. Commit `cdfee8b` ("Renumber
writer_lease migration to 0015 (0014 taken by command_ledger)") is the
next agent's rebase-time fix: `0014_add_writer_lease_table.py` was
renamed to `0015_add_writer_lease_table.py`, its `Revision ID`/`Revises`
docstring fields updated, and its own in-file comment updated to
describe the real chain (`0011` capital_reservations → `0012` AUD-01 →
`0013` route_qualifications → `0014` command_ledger → `0015`
writer_lease).

The rule this demonstrates: **the revision number is claimed by whichever
migration is first to actually land on the shared branch tip; every
later-landing sibling renumbers itself to chain after it**, and updates
its own explanatory comments to describe the real chain rather than the
chain it was originally written against. This is checked on every
rebase, not just once — see `docs/agents/HANDBOOK.md` and
`docs/agents/HANDOFF.md` for the corresponding "known collision to
re-check" handoff practice.
