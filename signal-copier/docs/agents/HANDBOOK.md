# Multi-agent working pattern

This repository's own history is the specification here — this documents
the real pattern its commits demonstrate, not an idealized process.

## Isolated worktrees, disjoint files

Concurrent agents each work in their own `git worktree`, checked out from
the shared branch's current tip onto their own branch (see
`docs/process/GIT.md`). Within a wave of work, tasks are scoped so
different agents touch different files as much as possible: a foundation
agent works in shared core modules (`app/db.py`, `app/engine.py`,
`app/capital_allocator.py`, `app/lifecycle/manager.py`), while
feature/screen agents each own their own `app/static/views/trXX.js` plus
whatever backend endpoint feeds it. This is why, for example, the four
TR-0X screen batches (`5bb51f1` TR-01..04, `67fd5bf` TR-05..08, `d68ace9`
TR-09..12, `43decc7` TR-13..16) could proceed as separate, mostly
non-colliding units of work.

Nothing prevents two agents from touching the same shared file in the
same wave, though — `app/db.py` and `app/engine.py` are touched by
nearly every P0 commit. When that happens, the collision is resolved at
rebase time (see below), not avoided by locking.

## A foundation wave lands before dependent feature work

Interdependent, structural changes land as their own wave before work
that builds on them. The clearest recent example is the P0 audit-response
wave: `d363e79` (fail-closed sweep) → `c6e4e7b` (capital allocator gates)
→ `c88bb66` (distinct-quantity accounting, AUD-01) → `a5d5aec` (live
qualification ladder) → `9d22b32` (P0-8 readiness split) →
`ae6a016`/`cdfee8b` (command_ledger, P0-2) → `55976eb` (writer lease,
P0-6). These all change core data-model/engine behavior that later,
independent work (dashboard screens, new broker adapters) reads or
depends on — landing them first means a feature agent building against
`/system/readiness` or `SignalStore` already sees the real shape, not a
moving target.

Earlier in this branch's history the same shape appears at larger scale:
a design-system/foundation commit (`3d8e43b` "Add design-system
foundation: 3-level tokens, shared components, grouped nav") lands before
the wave of per-screen redesign commits (`f91e39f`, `7cbf103`, `a07eb4b`,
`145d4b4`, `28b43be`, …) that then build on those shared tokens and
components rather than each inventing their own.

**Practical rule**: if a task changes a data model, a shared contract, or
introduces something several other in-flight or planned tasks will read,
it is foundation-style work and should be sequenced (or at least
rebase-checked against) before the feature work that depends on it — see
`docs/agents/ROUTING.md`.

## Sibling migrations get discovered and renumbered on every rebase

Because each worktree starts from the same shared-branch base and adds
its own Alembic migration independently, migration-number collisions are
expected, not exceptional. The concrete pattern (documented in full in
`docs/process/GIT.md`): fetch the shared branch, rebase, and if the
`down_revision` this worktree's migration was written against has since
been claimed by a sibling's migration that landed first, renumber this
one to chain after it — update `revision`, `down_revision`, and the
in-file comment describing the chain. This is real, not hypothetical:
`ae6a016` (command_ledger, P0-2) and `55976eb` (writer_lease, P0-6) both
independently added their own `0014_*.py` migration chaining onto the
same `0013_add_route_qualifications_table.py` base; `cdfee8b` is the
very next commit on the branch, renumbering writer_lease's migration to
`0015` once command_ledger's `0014` was found to have landed first.

## Every agent verifies its own work before calling it done

No agent's work is considered finished on the strength of "the code looks
right" or "I wrote a test." Every substantive commit in this history
either states the exact CI commands run, or — for anything protecting an
invariant — the explicit revert/confirm-fails/restore/reconfirm
load-bearing cycle (see `docs/process/DEFINITION_OF_DONE.md` for the
full bar and real quoted examples). This is the agent's own
responsibility before handing work off or pushing, not something deferred
to a later reviewer by default — though a separate verification/
integration pass still independently re-runs the suite after a wave
lands (see `docs/agents/ROLES.md`), specifically because a self-report,
however carefully worded, is not itself evidence.

## Handoffs happen mid-task, and carry real state

This branch's history includes many real forced-handback points
mid-task (an agent's turn ending with work partially committed, or
committed-but-unverified, or verified-but-not-yet-rebased). See
`docs/agents/HANDOFF.md` for exactly what the next agent needs to pick
the work back up correctly rather than re-doing or silently skipping a
step.
