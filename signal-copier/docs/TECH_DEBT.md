# Technical debt

Real, disclosed design-level gaps and deferred work — distinct from
`docs/KNOWN_ISSUES.md`'s more immediate/actionable items, though several
overlap. Sourced from module docstrings and commit history.

## Capital allocator: deliberately narrower than a full institutional risk engine

`app/capital_allocator.py`'s own docstring lists, verbatim, what it does
not attempt:

- No multi-currency/FX conversion.
- No per-analyst overlap accounting (two providers both recommending the
  same underlying position are not netted or flagged as correlated
  exposure).
- No portfolio stress-loss modeling.
- No cross-account netting beyond plain summation (an owner-wide ceiling
  sums `confirmed_open_notional` across accounts; it does not net
  offsetting long/short positions across accounts against each other).

None of these are silently broken — they're explicitly out of scope, and
every admission gate that IS implemented is fail-closed on the inputs it
does use (see `docs/process/REVIEW_CHECKLIST.md`). But a future task that
assumes this module already does portfolio-level stress testing or FX
netting will be wrong.

## SQLite as the only supported datastore

`app/db.py` uses plain stdlib `sqlite3` (not an ORM), with schema changes
as versioned Alembic migrations. This is a stated, deliberate choice
("Core is enough; an ORM rewrite isn't needed for the volume here") — see
`docs/ASSUMPTIONS.md` for the volume/single-writer assumptions that make
it appropriate. It is debt in the sense that migrating to a different
datastore (for a genuinely multi-writer or much-higher-volume deployment)
would be a real rewrite, not a config change — `deploy/litestream/`
(SQLite replication) is the current answer to durability at this scale,
not a database swap.

## Migration history was frozen mid-flight

`app/db.py` records that `_COLUMN_MIGRATIONS` (an earlier, ad hoc
append-a-tuple schema-migration mechanism) is frozen as of the switch to
Alembic (`C03`) — every database, fresh or pre-Alembic, gets stamped at
the Alembic head on open rather than re-running the old mechanism. This
is clean going forward, but means the true history of schema changes
before that freeze point lives in the old mechanism's code, not in
`alembic/versions/`, which starts fresh from `0001_initial_schema.py`.

## `trading_authority`/`release_status` placeholders (readiness endpoint)

Tracked as a known issue (`docs/KNOWN_ISSUES.md`) and a pending decision
(`docs/state/PENDING_DECISIONS.md`) rather than repeated in full here —
but worth naming as debt in its own right: `/system/readiness` currently
cannot answer "is this process actually the fenced, active writer" or
"is this release approved" from a single field without a human reading
the `reason` string. Closing this is P0-6-wiring (now actionable) and
P0-7 (not yet landed) respectively.

## CI-scoped mypy is an explicit allowlist, not the whole codebase

`.github/workflows/signal-copier-ci.yml` runs mypy against a hand-
maintained list of specific files, not `mypy app/`. This is intentional
(scoping strict type checking to the financially-sensitive core), but it
is debt in the sense that a new module in a sensitive path is only
type-checked in CI if someone remembers to add it to that list — see
`docs/process/REVIEW_CHECKLIST.md`'s explicit check for this.

## Two broker adapters carry permanent, structural risk

`app/brokers/schwab.py` and `app/brokers/robinhood.py` talk to
unofficial/reverse-engineered endpoints with no sandbox. This isn't
"debt to pay down" in the usual sense — there is no official API to
migrate to for either — but it is a standing maintenance burden: either
adapter can silently break the moment the broker changes an undocumented
endpoint, with no sandbox available to catch it before it happens in a
real account.
