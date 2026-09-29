# Troubleshooting

Real symptom → cause → fix entries, drawn from actual debugging work on
this codebase. Each entry names the module involved so you can go read the
real fix, not just this summary.

## `FencedOutError` under heavy test-suite load

**Symptom**: `app.writer_lease.FencedOutError` raised unexpectedly during
test runs (or, in principle, under heavy concurrent load in a single
process) even though only one logical writer should be active — the error
message shows a token mismatch (`token=N superseded by N+1`) against the
*same* site/holder, not a genuinely different site taking over.

**Cause**: a thread-safety race in `WriterLeaseGuard.acquire()`.
`app/main.py`'s `lifespan()` runs on anyio's thread-based portal, and this
codebase's test suite opens many overlapping `TestClient` context managers,
each re-entering `lifespan()` (and thus `acquire()`) on its own worker
thread against the one process-wide `app.main` singleton guard. Without
synchronization, two threads could both observe `_token is None` (or both
observe the same stale `_token`), both perform a *real* acquisition/renewal
against the store (bumping the database's fencing token twice), and then
race to write `self._token` — whichever write landed last won, and it
could be the stale lower value, leaving the process fenced out by its own
second acquisition on the very next `require_active()` call — not by any
genuine second writer.

**Fix (already applied)**: `WriterLeaseGuard` now holds a
`threading.Lock()` (not `asyncio.Lock`, which only excludes coroutines on
one event loop, not OS threads) around the check-then-act read of `_token`
plus the real acquisition/renewal call and the subsequent write of
`_token`. See `app/writer_lease.py`'s `WriterLeaseGuard.__init__` comment
for the full reasoning.

**If you see this symptom pattern again**: check whether something is
re-entering `lifespan()`/constructing a new guard concurrently against a
shared store — the fix assumes exactly one `WriterLeaseGuard` instance per
process being driven from possibly-multiple threads, not multiple guard
instances racing each other (that would be a genuinely different bug).

## `no such table: writer_lease` (or any other table) during tests

**Symptom**: a test fails with `sqlite3.OperationalError: no such table:
writer_lease` (or another table this codebase's own migrations create),
even though the migration that creates it is present and passes on its
own.

**Cause, in the overwhelming majority of cases: NOT a real migration bug.**
It's stray concurrent `pytest` processes sharing one SQLite file — a
previous test run's process didn't fully exit, or a second `pytest`
invocation started against the same `DATABASE_PATH` while an earlier one
was still tearing down, and one process's fresh/reset database file is
being read by another process expecting the older or newer schema.

**Fix / diagnostic procedure — always do this before trusting the failure
as a real bug**:

1. `ps aux | grep pytest` (or equivalent) — check for stray concurrent test
   processes and kill them.
2. Clear `__pycache__` directories before re-running:
   `find . -name __pycache__ -exec rm -rf {} +` (stale bytecode from a
   renumbered/edited migration file is a related, separate cause — see the
   next entry).
3. Re-run the single failing test in isolation
   (`pytest tests/test_whatever.py::test_name`) to confirm it passes
   outside the full suite — if it does, the failure was almost certainly
   test-isolation/concurrency noise, not a real schema bug.

Only treat this as a real migration bug if the failure reproduces
consistently in isolation, on a clean database, with no concurrent test
processes running.

## Alembic "multiple heads" after a migration renumber

**Symptom**: `alembic upgrade head` (or `alembic history`) reports multiple
heads / a branching revision graph, even though the migration files on disk
show a clean linear sequence (e.g. `0011_...py` → `0012_...py` →
`0013_...py` → `0014_...py` → `0015_...py`).

**Cause**: stale `.pyc`/`__pycache__` files from a *previous* revision
number for a migration that was later renumbered. Alembic discovers
migrations by importing every file in `alembic/versions/`; a leftover
compiled bytecode file for an old filename/revision id (e.g. from before
`0014_add_command_ledger_table.py` and `0015_add_writer_lease_table.py`
were assigned their final numbers, when `writer_lease`'s migration briefly
collided with `command_ledger`'s taken `0014` slot) can be picked up
alongside the current, correctly-numbered file, producing two heads that
both claim to follow the same parent revision.

**Fix**: clear `__pycache__` under `alembic/versions/` (and anywhere else
Python may have cached the old module) before re-running Alembic:

```bash
find alembic/versions -name __pycache__ -exec rm -rf {} +
find alembic/versions -name "*.pyc" -delete
```

Then re-check `alembic history` / `alembic heads` — a genuine multi-head
situation (two real migrations both claiming the same `down_revision`)
looks the same superficially, so confirm by inspecting the actual `.py`
files in `alembic/versions/` for a real branch before assuming this is
just stale bytecode.

## General diagnostic habit this session's own work reinforced

When a failure doesn't match the code you're reading, check for stale
compiled artifacts and stray concurrent processes *before* assuming the
code itself is wrong — both of the last two entries above look, at first
glance, like real schema/migration bugs and are not.
