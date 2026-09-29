# Development lifecycle

The real sequence a change goes through in this repository, reconstructed
from the pattern its own commits follow (see e.g. `ae6a016`, `c6e4e7b`,
`d363e79`, `9d22b32` for commit messages that narrate exactly this
sequence).

## 1. Understand the real current behavior first

Before writing a fix or a feature, read what the code actually does
today — not what a docstring elsewhere claims it does. This repo's own
module docstrings are written to be read this way (e.g.
`app/capital_allocator.py`'s docstring spells out precisely which gaps
are real and which are already closed; `app/engine.py`'s docstring
describes the exact optimistic-accounting behavior `c88bb66` later
replaced). Read the existing tests for the area too — they're the
executable statement of current behavior, and a change that doesn't
touch them but should is a sign the current behavior wasn't understood.

Concrete example: `c88bb66` (AUD-01) opens by quoting `engine.py`'s own
module docstring describing the exact optimistic-PENDING-accounting
behavior being replaced, rather than asserting the bug from scratch.

## 2. Implement

Make the real change. Keep it scoped to the files the task actually
needs — this matters for the multi-agent model in
`docs/agents/HANDBOOK.md`, where concurrent agents work on disjoint files
specifically so their worktrees don't collide on anything but shared
files like `app/db.py`, `app/engine.py`, and the Alembic revision chain.

## 3. Add or extend tests, including one that is genuinely load-bearing

A new behavior gets a new test. A bug fix gets a test that would have
caught the bug — which means it must be provably capable of failing (see
step 4 and `docs/process/DEFINITION_OF_DONE.md` item 4). It's normal for
this to mean extending an existing suite (e.g. `c88bb66` adds
`tests/test_aud01_distinct_quantity_model.py` *and* corrects two existing
suites that had been asserting the old, wrong behavior by name).

## 4. Verify

```
ruff check .
python -m mypy <the CI file list> --follow-imports=silent
pytest -q
```

(exact commands in `docs/process/DEFINITION_OF_DONE.md`), plus the
load-bearing revert/confirm-fails/restore/reconfirm cycle for whatever
invariant the change is actually protecting. This is not optional
polish — several commits on this branch exist purely to fix something
this step caught (`eaa5c80`, `857ae01`, `00665ce`, `146935e`,
`c59cd97`).

## 5. Rebase onto the latest shared branch

```
git fetch origin claude/signal-copier-redesign
git rebase origin/claude/signal-copier-redesign
```

Resolve any conflicts, and specifically re-check for an Alembic revision
collision against whatever new migrations landed since this work started
(`docs/process/GIT.md` has the real example: `cdfee8b`).

## 6. Verify again, post-rebase

Re-run everything in step 4 against the rebased tree. A green run before
the rebase does not guarantee a green run after it — the rebase can land
on top of a sibling's schema change, renamed helper, or renumbered
migration that this change now needs to account for.

## 7. Push

```
git push origin HEAD:claude/signal-copier-redesign
```

Once pushed, the change is visible to every other agent's next fetch —
including its Alembic revision number, which becomes the thing the next
colliding sibling renumbers itself against.
