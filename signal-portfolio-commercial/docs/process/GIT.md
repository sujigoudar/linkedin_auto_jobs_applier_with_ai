# Git process

This is the real convention this repo's own history demonstrates for
`signal-portfolio-commercial` — the same convention used across the
monorepo (see the sibling `signal-copier` app), not a separate one
invented for this document.

## One worktree per unit of work

Every commit in this app's history carries a `Claude-Session:` trailer
pointing at a distinct `claude.ai/code/session_...` URL (see
`git log --format='%B' -- signal-portfolio-commercial | grep Claude-Session`).
Each session works in its own `git worktree`, checked out from the
shared integration branch (currently `claude/signal-copier-redesign`),
on its own branch. This is why the history reads as a long, mostly
linear sequence of focused commits rather than a tangle of merge
commits: concurrent sessions never share a working tree, so they never
step on each other's uncommitted changes, and each one's diff is
reviewable in isolation before it lands back on the shared branch.

Concretely, for a new unit of work:

```
git worktree add /tmp/wt-<short-task-name> origin/claude/signal-copier-redesign -b agent-<short-task-name>
cd /tmp/wt-<short-task-name>/signal-portfolio-commercial
```

Work only inside that worktree's `signal-portfolio-commercial/`
directory (or the specific sibling paths a task explicitly needs, e.g.
`signal_platform_contracts/` for the cross-service contract package).
Never edit files under another app's directory unless the task is
explicitly cross-cutting (e.g. `9ddc681` touched both
`signal-copier/app/services/catalog_fit_sim_auth.py` and
`signal-portfolio-commercial/app/services/relay_auth.py` in one commit
because dual-secret rotation is one real feature spanning both
processes).

## Commit trailers

Every commit ends with:

```
Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_<id>
```

This is not decorative — it is how a later session (human or agent)
traces a change back to the exact conversation that produced it,
including the verification steps described in the commit body (see
`DEFINITION_OF_DONE.md`). Every commit body in this app's history also
documents *why*, not just *what*: what gap it closes, what it
deliberately did not build, and how it was verified. Keep writing
commit messages that would let a cold reader (another agent, a future
session) understand the change without re-reading the diff.

## Rebase, never merge

This branch's history is linear — there are no merge commits under
`signal-portfolio-commercial/`. Before pushing, always:

```
git fetch origin claude/signal-copier-redesign
git rebase origin/claude/signal-copier-redesign
```

resolving conflicts in place, then

```
git push origin HEAD:claude/signal-copier-redesign
```

A `--force` push to the shared branch is a last resort and only after
confirming no other session's unpushed work would be discarded — with
many concurrent worktrees rebasing onto the same branch, an ordinary
(non-force) push that is rejected almost always means "rebase again,"
not "force."

## Alembic revision collisions are a real, demonstrated risk

Concurrent sessions each generating an Alembic revision off what they
believe is the current head is exactly the kind of collision multiple
worktrees invite, and this repo has hit it for real: the sibling
`signal-copier` app's own history has a commit
(`cdfee8b Renumber writer_lease migration to 0015 (0014 taken by
command_ledger)`) where two sessions, working concurrently in separate
worktrees, both generated a migration against the same prior head —
one session's `command_ledger` migration claimed revision `0014`
first, and the losing session's `writer_lease` migration had to be
renumbered to `0015` and its `down_revision` repointed at
`command_ledger`'s new revision id before it could land.
`signal-portfolio-commercial`'s own migrations use Alembic's default
random hex revision ids (see `alembic/versions/`), which makes the
literal filename collision `signal-copier`'s numbered-prefix scheme hit
less likely, but the underlying race is identical: two sessions
generating a new revision from the same `down_revision` will produce
two independent revisions both claiming the same parent, i.e. two
branch heads instead of one linear chain.

When rebasing a branch that adds a new Alembic revision and the
upstream branch has *also* added one since your branch's own
`down_revision`:

1. Check `alembic heads` (or `alembic history`) after the rebase — more
   than one head means a real collision landed, not just a text
   conflict.
2. Re-point your revision's `down_revision` at the new real head (the
   revision id that is now first in migration order), not the id your
   revision was originally generated against.
3. Re-run `alembic upgrade head` against a fresh disposable Postgres
   cluster (see `DEFINITION_OF_DONE.md`) to confirm the chain actually
   applies in the new order — a `down_revision` edit that "looks right"
   textually can still reference the wrong parent's schema state.
4. Never leave two heads merged only implicitly by file order — Alembic
   requires one, and only one, `down_revision` chain per branch of the
   schema (a real multi-head merge revision if the two migrations are
   both still needed and independent, or a renumber, matching
   `cdfee8b`'s own fix, if one supersedes the other's ordering).

See `REVIEW_CHECKLIST.md` for the equivalent check from a reviewer's
seat.
