# Routing a task to the right kind of work

Before starting, classify the task. This decides which role
(`docs/agents/ROLES.md`) applies, how much migration-collision awareness
is needed (`docs/process/GIT.md`), and what "done" verification looks
like (`docs/process/DEFINITION_OF_DONE.md`).

## Data-model / schema change → foundation-style work

Signals: the task touches `app/db.py`'s schema, adds or changes a column
or table, adds an Alembic migration, changes `app/engine.py`'s core
signal-handling path, `app/lifecycle/manager.py`'s state machine,
`app/capital_allocator.py`'s admission logic, or anything else other
in-flight or planned work is likely to read or depend on.

Route this as foundation-style work:
- Expect and plan for an Alembic revision collision with a concurrent
  sibling — check `alembic/versions/` immediately before choosing a
  revision number, and again at rebase time (`docs/process/GIT.md`'s
  `cdfee8b` pattern).
- Prefer additive-only schema changes (new columns/tables), matching this
  codebase's established convention (`app/db.py`'s own comment on why
  `_COLUMN_MIGRATIONS` is frozen in favor of Alembic).
- Treat `docs/process/REVIEW_CHECKLIST.md`'s fail-closed and
  permissive-default questions as mandatory self-review before calling
  it done, since this is exactly the class of change the P0-* audit wave
  was fixing.
- Expect the mypy CI-scoped file list (`docs/process/DEFINITION_OF_DONE.md`)
  to need updating if a new module joins the financially-sensitive path.

## UI / screen change → feature-style work

Signals: the task adds or changes an `app/static/views/*.js` screen, a
scoped endpoint that only that screen consumes, dashboard rendering,
chart/visualization work, or anything downstream of an already-landed
foundation change (reading `/system/readiness`, a capability-state
badge, an already-existing table).

Route this as feature-style work:
- Scope changes to that screen's own files as much as possible, to
  minimize collision surface with concurrent feature agents working on
  other screens.
- Verify with a real running server + Playwright, not just a unit test
  of the endpoint (see `docs/agents/TOOLS.md`) — this branch's UI work
  (Chart.js panels, tables, forms) is routinely verified this way.
- Still run the full `docs/process/DEFINITION_OF_DONE.md` checklist —
  feature work is not exempt from ruff/mypy/pytest/rebase just because
  it doesn't touch the schema.
- If the screen needs something the data model doesn't yet expose,
  that's a signal the task actually has a foundation-style component
  buried in it — split it out and route that part accordingly rather
  than improvising a schema change inside what was scoped as a UI task.

## Investigation → read-only

Signals: "why does X happen," "what does Y currently do," "is Z already
implemented," auditing for a specific pattern (e.g. a fail-closed sweep
like `d363e79`'s search for `x or None` on financial quantities), or
producing a status/handoff summary.

Route this as read-only:
- No commits, no file edits, unless the investigation itself turns up a
  concrete fix — at which point re-route that fix through foundation- or
  feature-style work as appropriate, with its own verification.
- Grep/read across the codebase is the right tool set (see
  `docs/agents/TOOLS.md`) — don't spin up a running server or reach for
  Playwright for a question that's answerable by reading code and tests.
- Findings belong in a handoff or status document
  (`docs/agents/HANDOFF.md`, `docs/state/`), not as unreviewed inline
  code comments left behind in application code.
