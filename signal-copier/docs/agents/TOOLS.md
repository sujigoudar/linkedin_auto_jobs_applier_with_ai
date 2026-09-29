# Tools for the task at hand

Which tool proves what, in this repository. The common failure this
avoids is treating "I read the code and it looks correct" as equivalent
to having actually run something — it isn't, and this branch's own
history routinely catches the gap between the two (see
`docs/agents/VERIFICATION.md`).

## Investigation → grep / read

For "what does this actually do," "is this already implemented,"
"where's the real current behavior documented" — read the module
docstring first (this codebase's docstrings are written to state real,
current behavior and real, current gaps — see
`app/capital_allocator.py`'s or `app/engine.py`'s), then the relevant
tests (the executable statement of current behavior), then the
implementation. Grep across `app/`, `tests/`, and `alembic/versions/`
for the pattern in question rather than guessing from memory or from
one file in isolation. This is read-only and needs no running process —
see `docs/agents/ROUTING.md`'s "investigation" category.

## Logic / data-model verification → pytest, run for real

For anything in `app/engine.py`, `app/capital_allocator.py`,
`app/lifecycle/`, `app/db.py`/Alembic, or a broker/source adapter:
`pytest -q` (full suite) or a scoped run against the relevant file, plus
the exact CI-scoped `ruff check .` / `mypy ... --follow-imports=silent`
commands in `docs/process/DEFINITION_OF_DONE.md`. A claim that "the tests
pass" is not evidence until the command has actually been run against
the current tree and its real output read — not assumed from having
read the test file and judged it "should" pass.

For a bug fix or a new invariant specifically, pytest alone is not
enough — the load-bearing revert/confirm-fails/restore/reconfirm cycle
(`docs/agents/VERIFICATION.md`) is what actually proves the test would
have caught the bug.

## UI / screen verification → a real running server + Playwright

This project's screens (`app/static/views/*.js`) are verified by
actually running the app and driving a real browser against it, not by
reading the JS and asserting it looks right. `.github/workflows/signal-copier-ci.yml`
installs Playwright's Chromium specifically for this
(`playwright install --with-deps chromium`, tagged C14/C33/C34 in that
workflow). For UI work: start the real FastAPI app (`app/main.py`)
against a real (test/paper) database, drive the screen with Playwright,
and check the actual rendered output — a chart that actually draws,
a table that actually populates from a real endpoint response, a form
that actually submits and reflects a real state change — rather than
trusting that the endpoint's JSON shape and the JS's rendering code are
individually correct and therefore must compose correctly together.

## Never just trust a self-report

Regardless of which tool produced a result, the practice this branch's
history repeatedly follows (see `docs/agents/ROLES.md`'s verification/
integration role and `docs/agents/VERIFICATION.md`) is to re-run the
check independently — ideally from a fresh checkout — rather than take
"I ran it and it passed" at face value, especially after a wave of
concurrent work has landed and the individual self-reports were each
made against a different, now-stale tree.
