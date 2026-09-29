# Migration Policy

Source of truth: `app/db.py`'s `SCHEMA`/`_COLUMN_MIGRATIONS`/
`_stamp_alembic_head_if_needed`, and `alembic/versions/0001_initial_schema.py`'s
own docstring, which states the policy explicitly.

## Two historical mechanisms, now one going forward

This codebase has run two different schema-change mechanisms over its
life, and the transition between them is deliberate and documented in
code, not incidental.

### 1. The original mechanism: `SCHEMA` + frozen `_COLUMN_MIGRATIONS` (pre-Alembic)

Every database this app has ever created got its schema from
`app/db.py`'s `SCHEMA` string, executed with `executescript` on every
`SignalStore.__init__`, plus `_COLUMN_MIGRATIONS` — a list of
`(table, column, coltype)` tuples applied with `ALTER TABLE ... ADD
COLUMN`, swallowing the "duplicate column name" `sqlite3.OperationalError`
so a column already present (a pre-existing deployment that already
has it) is a silent no-op rather than a failure. This is how a column
added to an already-existing table got backward-compatible without a
real migration framework: `CREATE TABLE IF NOT EXISTS` only helps a
brand-new database, and `_COLUMN_MIGRATIONS` is what brought an
existing on-disk database's older tables up to date on every process
start.

**`_COLUMN_MIGRATIONS` is now frozen** (`app/db.py`'s own comment: "no
`_COLUMN_MIGRATIONS` entry needed — those are only for adding a column
to an already-existing table" — and, from `alembic/versions/0001`'s
docstring: "this stops growing as of this commit"). No new tuples
should be appended to it. This does not mean existing databases stop
receiving those historical column additions — `SignalStore.__init__`
still runs the full frozen list on every open, so an old,
never-upgraded deployment still catches up — it means the mechanism
itself is retired for anything new.

### 2. The current mechanism: real Alembic revisions

Any schema change from here on is a **new Alembic revision**
(`alembic revision -m "..."`), with a real, reviewed `upgrade()` —
never another entry in `_COLUMN_MIGRATIONS`, and never a bare mutation
of the `SCHEMA` string alone for anything beyond keeping `SCHEMA`
itself as the single, current, authoritative DDL text.

`SCHEMA` remains the canonical schema definition text — `app/db.py`'s
own bootstrap (`SignalStore.__init__`'s `executescript(SCHEMA)`) is
still what actually creates tables for every real, `SignalStore`-backed
database, both fresh and pre-existing (`CREATE TABLE IF NOT EXISTS` is
backfill-safe for a table that doesn't exist yet in an older database).
A new Alembic revision's `upgrade()` should therefore describe, in
proper Alembic operations, the same change that was also made directly
to `SCHEMA` — see `0011`–`0015` for the real precedent (each adds one
new table, both as a `CREATE TABLE IF NOT EXISTS` block in `SCHEMA` and
as an Alembic revision with real `sa` column definitions).

## Why `0001_initial_schema.py` exists and what it is (and isn't)

`alembic/versions/0001_initial_schema.py`'s `upgrade()` is literally
`op.get_bind().connection.executescript(SCHEMA)` — it runs the exact
same `SCHEMA` string `SignalStore` always has. Its own docstring is
explicit about what this is for:

> "This is NOT how this database actually got here for any existing
> deployment: every database this app has ever created got its schema
> from `app/db.py`'s SCHEMA executescript + the (now-frozen)
> `_COLUMN_MIGRATIONS` list, never from this file."

It exists for two real purposes:

1. `alembic upgrade head` against a genuinely empty database — someone
   provisioning one purely through the Alembic CLI, bypassing
   `SignalStore` entirely — produces the exact same schema
   `SignalStore`'s own bootstrap does.
2. Every actual `SignalStore`-created database (fresh or pre-existing)
   is stamped at this revision **without re-running it** (see
   `_stamp_alembic_head_if_needed` below) — its schema is already
   exactly this, by construction, so re-running `CREATE TABLE` would
   be redundant (and, for a statement without `IF NOT EXISTS`, an
   error).

`downgrade()` raises `NotImplementedError` deliberately — this
codebase has no downgrade path for the initial schema, "the same
one-way-migration precedent" `_COLUMN_MIGRATIONS` itself always had
(it never had a downgrade path either).

## How a `SignalStore`-created database gets stamped

`SignalStore.__init__` calls `_stamp_alembic_head_if_needed()` after
running `SCHEMA` and `_COLUMN_MIGRATIONS`. That method checks whether
an `alembic_version` table already exists; if it does, it does
nothing. If it doesn't, it calls `alembic.command.stamp(cfg, "head")`
— marking the database as being at whatever revision
`alembic/versions/` currently considers head, **without** re-running
any `upgrade()` (which would try to `CREATE TABLE` something already
there). This is safe precisely because `SignalStore.__init__` has
*already* brought the schema to exactly what every migration up to
head would produce, by running `SCHEMA` (the always-current DDL) and
every not-yet-applied `_COLUMN_MIGRATIONS` entry, immediately before
this call.

Practical effect: a database that `SignalStore` opens is **always**
already schema-current the moment `__init__` returns, regardless of
whether it's brand new or an old deployment catching up — it never
needs (and should never be run through) `alembic upgrade head`
separately. `alembic upgrade head` is for the CLI-only provisioning
case described above.

`SignalStore.schema_version()` reads the real, live `version_num` off
the `alembic_version` table for this exact database file — genuine
evidence, not a cached or assumed value. `alembic_code_head()`
(`app/db.py`) reads the migration revision the deployed code itself
expects to be at head, straight from `alembic/versions/` on disk via
`ScriptDirectory.get_current_head()` — never a hardcoded version
string that could silently drift from the real migration scripts.
Comparing `schema_version()` against `alembic_code_head()` for a live
database is the real "is this database's schema reproducible from this
exact release" check.

## Current head revision

As of this document: **`0015`** (`alembic/versions/0015_add_writer_lease_table.py`,
adding the `writer_lease` table for cross-process/cross-host single-writer
fencing — see ADR-0002).

Full chain (each file's own docstring names what it adds and why):

| revision | adds |
|---|---|
| `0001` | initial schema snapshot (see above) |
| `0002` | `orders.reserved_notional` |
| `0003` | `export_events` table |
| `0004` | `position_excursions` table |
| `0005` | `orders.submitted_at` / `protection_confirmed_at` |
| `0006` | `account_equity_snapshots` table |
| `0007` | `stop_target_events` table |
| `0008` | `orders.purpose` / `family_id` |
| `0009` | `backtest_runs` table |
| `0010` | `backtest_runs.capital_contention_json` |
| `0011` | `capital_reservations` table (P0-4) |
| `0012` | `orders`' distinct-quantity fields (AUD-01) |
| `0013` | `route_qualifications` table |
| `0014` | `command_ledger` table (P0-2) |
| `0015` | `writer_lease` table |

Every revision from `0011` onward follows the "adds one thing, matches
what was also added to `SCHEMA` directly" pattern this policy expects
going forward.

## Practical rule for the next schema change

1. Add the new table/column to `app/db.py`'s `SCHEMA` string (with a
   real, load-bearing inline comment explaining what it is and why —
   the convention every existing table already follows).
2. Do **not** add anything to `_COLUMN_MIGRATIONS` — it is frozen.
3. Write a new Alembic revision (`alembic revision -m "..."`) whose
   `upgrade()` performs the equivalent change with real `op.*`/`sa.*`
   calls (not another `executescript(SCHEMA)` call — that pattern is
   reserved for `0001`).
4. Update `docs/database/SCHEMA.md` (and, if the change affects one of
   the tricky columns that document covers, `docs/database/
   DATA_DICTIONARY.md`) and this file's head-revision table.
