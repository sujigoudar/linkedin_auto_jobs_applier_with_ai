# Agent roles

Three real roles this repository's history shows agents taking on. These
are roles a task falls into, not fixed identities — the same agent can
take a foundation role on one task and a feature role on the next.

## Foundation / architecture agent

Works on the data model and shared engine/control logic that other work
will build on or depend on: `app/db.py` schema changes (and their Alembic
migrations), `app/engine.py`, `app/capital_allocator.py`,
`app/lifecycle/manager.py`, `app/writer_lease.py`, `app/command_ledger.py`,
shared UI infrastructure (`3d8e43b`'s design-system tokens/components).

Real examples: every P0-* commit (`d363e79`, `c6e4e7b`, `c88bb66`,
`a5d5aec`, `9d22b32`, `ae6a016`, `55976eb`), `d087bc0` (P0-4, durable
capital reservations), `1a7feb1`/`d774653`/`3d8e43b` (shared components
and capability-state badges other screens then use).

This role carries the most migration-collision risk (see
`docs/process/GIT.md`) because it's the role most likely to touch
`app/db.py` and add a migration in the same wave as a sibling doing the
same. It owns getting the renumbering right, not just producing a
working migration.

## Feature agent

Builds a specific screen, endpoint, or integration on top of what the
foundation already provides, generally touching its own
`app/static/views/trXX.js` (or `cuXX.js`/`adXX.js`) plus a scoped backend
endpoint, without needing to change the shared data model.

Real examples: the TR-0X screen batches (`5bb51f1`, `67fd5bf`, `d68ace9`,
`43decc7`), the broker-adapter additions (`d59feb2` TradeStation,
`74f3521` Tastytrade, `e2200a1` OANDA, `3845864` Tradovate, `fda6ffd`
Schwab, `90e0735` Robinhood), and the per-screen analytics work
(`4707587` TR-02 saved filter views, `6b2128f` TR-09 provider scorecard,
`f91e39f` TR-01 risk panel).

A feature agent still owns its own verification (tests, and Playwright
screen verification where relevant — see `docs/agents/TOOLS.md`) — this
role is not exempt from `docs/process/DEFINITION_OF_DONE.md` just because
it isn't touching shared state.

## Verification / integration role

Independently re-runs the full test suite from a **fresh checkout**
after a wave of work lands on the shared branch, rather than trusting the
individual agents' own self-reported "done." This is the role that
catches an interaction between two agents' otherwise-individually-correct
changes — an order-dependent test flake surfaced only when the whole
suite runs together (`9d22b32`'s commit message: *"Fix an order-dependent
flake ... found during full-suite verification"*), or a CI-only failure
that doesn't reproduce when a single file's tests are run alone
(`00665ce`, `857ae01`, `ab08221`, `8deb326`, `923ebeb` — all "Fix CI:"
commits that only ever surface once the full, real CI environment runs
the full suite).

This role's defining practice is stated in
`docs/agents/VERIFICATION.md`: never trust a self-report of "tests pass"
at face value — re-run it, from a clean checkout, and read the actual
output.
