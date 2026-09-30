# Assumptions

Real, load-bearing assumptions this codebase is built on — stated
explicitly so a future change doesn't violate one without realizing it
was ever an assumption at all.

## Single active writer per deployment

The entire writer-lease/fencing design (`app/writer_lease.py`,
`docs/FAILOVER.md`) assumes exactly one process is ever the genuine
active writer for a given account/site configuration at a time. Nothing
in this codebase is built for multiple simultaneous active writers
cooperating — the fencing mechanism exists specifically to make a SECOND
writer impossible, not to coordinate two legitimate ones. A future
requirement for true multi-writer/active-active operation would need a
fundamentally different design, not an extension of this one.

## This service is single-tenant

`app/capital_allocator.py`'s owner-wide notional ceiling sums exposure
"across EVERY configured account this single-tenant deployment's
`RoutingConfig` knows about" on the explicit reasoning that "this service
has exactly one owner/config; 'every configured account' already IS
'owner-wide.'" Multi-tenant use (multiple independent owners sharing one
deployment) is not supported and this assumption is baked into how
exposure ceilings are computed, not just a deployment convention.

## SQLite is adequate at the current volume

`app/db.py` uses plain stdlib `sqlite3`, explicitly reasoned as
sufficient ("Core is enough; an ORM rewrite isn't needed for the volume
here"). This assumes signal/order/position volume stays in a range
SQLite's single-writer-at-a-time model handles comfortably, and that
durability is handled by `deploy/litestream/`-style replication rather
than a natively-replicated database. A large increase in write volume or
a genuine requirement for concurrent writers from multiple processes
would invalidate this.

## A missing/unresolvable risk input means "reject," never "skip" or "zero"

Load-bearing across the capital allocator, reconciliation, and broker/
source fill-data paths (see the P0-3/P0-9/AUD-01 commits, and
`docs/process/REVIEW_CHECKLIST.md`'s fail-closed check): any code adding
a new gate or new exposure calculation is assumed to follow this same
rule — a value the system genuinely cannot resolve blocks or flags
rather than defaulting to a value that looks safe. Code that violates
this (even implicitly, via a falsy-coalescing pattern like `x or None`)
is treated as a bug, not a style choice — this branch has fixed exactly
that pattern twice (`app/brokers/ibkr.py`, `app/sources/rithmic.py`).

## Additive-only schema migrations

`app/db.py`'s comment on freezing `_COLUMN_MIGRATIONS` in favor of
Alembic, and every migration in `alembic/versions/` to date, assumes
schema changes only ever ADD columns/tables — never drop or rename a
column a currently-running process might still read the old shape of.
This is what makes a plain `git revert` of a migration-carrying commit
safe by default (see `docs/process/ROLLBACK.md`) — that safety would not
hold for a destructive migration, which this codebase has not needed
(and should be treated as a deliberate exception requiring extra care,
not a precedent) if one is ever genuinely required.

## Manual, human-verified promotion is the only acceptable failover

`docs/FAILOVER.md` states this as a design decision, not a temporary
limitation: automatic failover is assumed to be unsafe for this domain
(real broker commands, real money) because nothing in the stack can
independently prove the old writer is actually dead, only that its lease
looks expired. Any future automation of failover would need to solve
that proof problem first, not just wire `promote_cli`'s CLI into a
health-check-triggered script.

## Trailing timing-sensitive tests are acceptable, contained noise

`tests/test_c07_context_rate_limiting.py` asserting real wall-clock
timing is an accepted trade-off (real proof of throttling vs. occasional
CI-load flakiness) contained to that one file — not a precedent for
adding more real-time-dependent assertions elsewhere without the same
justification (needing to prove genuine throttling behavior that a
mocked clock couldn't demonstrate).
