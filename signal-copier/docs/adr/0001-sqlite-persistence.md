# ADR-0001: SQLite (stdlib sqlite3, no ORM) as the engine's persistence layer

Status: Accepted
Date: 2026-09-29

## Context

signal-copier is a single-owner, single-tenant trading-signal-copier
engine (`config_accounts`/`sessions` carry no per-user scoping — see
`app/db.py`'s `saved_views` table comment: "This engine is single-owner
(one OWNER_PASSWORD, no per-user accounts anywhere in this schema)").
It needs durable storage for signals, orders, positions, lifecycle
state, and a growing set of audit/reconciliation tables, but it does
not need multi-writer concurrency at database-engine scale, a separate
database process to operate, or client-server network administration.

`app/db.py`'s own module docstring states the decision plainly:

> "Minimal SQLite persistence for signal/order history. Kept
> deliberately simple (stdlib sqlite3, no ORM) since this is a
> scaffold — swap for SQLAlchemy + Postgres when volume/concurrency
> needs it."

The engine is deployed as one active process (plus, per
`docs/FAILOVER.md`, an optional standby replicated via Litestream) —
not a horizontally-scaled service with many concurrent writers. A
single-file, zero-administration, embedded database matches that
deployment shape directly: no separate DB server to provision, back
up, or fail over independently of the application file itself, and
the entire durable state (signals, orders, positions, the command
ledger, capital reservations, the writer lease) fits naturally as
tables in one file that can be shipped, copied, or replicated as a
unit.

## Decision

Use SQLite via Python's stdlib `sqlite3` module, with a single
hand-written `SCHEMA` string (`app/db.py`) executed with
`CREATE TABLE IF NOT EXISTS`/`CREATE INDEX IF NOT EXISTS` on every
`SignalStore` construction, plus an additive `_COLUMN_MIGRATIONS` list
(frozen as of the introduction of proper Alembic revisions — see
ADR and `docs/database/MIGRATIONS.md`) for columns added to
already-existing tables. No ORM sits between application code and
SQL: `SignalStore` methods issue parameterized SQL directly.

Every `_connect()` explicitly enables `PRAGMA foreign_keys = ON`
(SQLite disables FK enforcement by default per new connection
regardless of the schema's own `FOREIGN KEY` declarations — see
`app/db.py`'s `DB-01` comment), so declared foreign keys (e.g.
`orders.signal_id -> signals.id`) are actually enforced, not merely
documented.

## Consequences

- Zero external database service to run, patch, or fail over
  independently — the whole durable state is one file, replicated
  with Litestream per `docs/FAILOVER.md`/`deploy/RUNBOOK.md`.
- No native cross-host advisory locking (unlike Postgres) — this is
  precisely why cross-process/cross-host coordination needed its own
  purpose-built mechanisms: `close_claims` (a UNIQUE-constraint claim
  for plain-account close mutual exclusion) and `writer_lease` (a
  monotonic fencing token — see ADR-0002). SQLite's own
  single-writer-at-a-time locking on the file is not, by itself, proof
  that a stale process has stopped running.
- A minimal, additive migration cost: `CREATE TABLE IF NOT EXISTS`
  is backfill-safe for a brand-new table, and `_COLUMN_MIGRATIONS`'
  "ignore duplicate column" pattern kept ad hoc column additions cheap
  until the codebase adopted Alembic for genuinely new revisions (see
  `docs/database/MIGRATIONS.md`).
- A real, disclosed scaling ceiling: this is explicitly a scaffold
  decision ("swap for SQLAlchemy + Postgres when volume/concurrency
  needs it"), not a claim that SQLite is the right choice at higher
  write concurrency or multi-tenant scale. No such migration has been
  undertaken as of this ADR — the single-owner, single-active-writer
  deployment model this codebase actually runs has not required it.
- No ORM means schema and query logic are exactly what's written in
  `app/db.py` — every column's semantics are documented in the SCHEMA
  string's own inline comments rather than derived from a mapped
  class, which is also why those comments carry the majority of the
  system's real behavioral contracts (see `docs/database/SCHEMA.md`
  and `docs/database/DATA_DICTIONARY.md`).
