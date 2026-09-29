# Review checklist

Derived from what this repository's own agents have repeatedly checked
for — several of these items exist precisely because an earlier pass
through this codebase (the external release audit referenced across the
P0-* commits) found a real instance of the failure mode the check now
guards against.

## Does this weaken any fail-closed behavior?

Grep for `or None`, `or 0`, `if x else None`, and similar "coalesce a
falsy value" patterns on anything that carries a real financial
quantity (price, fill size, exposure, equity). A genuine `0.0` collapsing
to `None` is not cosmetic — this exact bug shipped twice in this
codebase (`app/brokers/ibkr.py`'s `get_order_status`,
`app/sources/rithmic.py`'s fill handler) and was fixed in `d363e79`
specifically because downstream code treats `None` as "unknown, fall
back to the full requested quantity" — a real zero fill was at risk of
being reported as a full fill.

More generally: does an error path, a missing value, or an unresolved
lookup now get treated as safe/zero/skip instead of being rejected or
surfaced as unknown? `c6e4e7b` found and fixed two instances of exactly
this in the capital allocator (a missing price used to skip the whole
admission check; an unresolvable `average_cost` used to contribute `0.0`
notional instead of blocking the account). If a change makes an
already-strict check more permissive, it needs an explicit, reviewed
reason — not just because it made a test pass.

## Does this introduce a permissive default on a missing risk input?

Any new config value, gate, or sizing input should default to **off /
None / not-configured = no additional exposure allowed**, never to
silently admitting unsized risk. `app.config.MAX_OWNER_NOTIONAL_EXPOSURE`
and `DestinationAccount.risk_percent_of_equity` (both added in `c6e4e7b`)
are opt-in and `None` by default — check that a new gate follows the
same shape, and that "not configured" and "resolved to zero risk" remain
distinguishable in the code, not conflated.

## Does this add a new Alembic migration that might collide with a concurrent one?

Check the current head in `alembic/versions/` against what this change's
migration assumes as its `down_revision`. If another agent's worktree
has landed a migration at the same number since this branch started,
renumber this one to chain after it (see `docs/process/GIT.md`'s
`cdfee8b` example) — don't just take whichever number was free when the
branch was cut. Also check: does the new migration only ever ADD
(columns/tables), matching this codebase's additive-only migration
convention (see `app/db.py`'s own comment on `_COLUMN_MIGRATIONS` being
frozen in favor of Alembic), rather than dropping or renaming something
a running process might still read the old shape of?

## Is there a genuine load-bearing test, not just a passing test?

A test suite that's green proves nothing about a bug fix unless someone
has confirmed it would have caught the bug. Check the PR/commit for
either:
- an explicit statement that the fix was reverted, the new test was
  confirmed to fail (and *why* it failed matches the bug, not an
  unrelated error), then the fix was restored and reconfirmed green
  (the pattern in `ae6a016`, `c6e4e7b`, `d363e79`); or
- if that wasn't done, do it yourself before approving.

A test added alongside a fix that was never run against the pre-fix code
is not evidence the fix works — see `docs/process/DEFINITION_OF_DONE.md`
and `docs/agents/VERIFICATION.md`.

## Other things worth a second look, seen repeatedly in this history

- **Does a new "not tracked"/"unknown" placeholder ever get silently
  folded into a green/OK rollup?** `9d22b32` (P0-8) exists specifically
  to stop `liveness=up` from masking an unknown `data_readiness`, and
  adds a test asserting `trading_authority=not_held` must block an
  ACTIVE rollup. A new status dimension should be independently visible,
  not just absorbed into an existing boolean.
- **Is a genuinely-owned FOLLOW-UP comment now stale?** `app/main.py`
  still has `FOLLOW-UP: integrate the P0-6 fencing/lease work once it
  lands` even though P0-6 (`55976eb`) has since landed — a real,
  currently-open gap this checklist would have caught if applied to that
  code today. See `docs/state/tasks.json`.
- **Does a CI-scoped tool's file list need updating?** Adding a new
  module to a financially-sensitive path (see the explicit list in
  `docs/process/DEFINITION_OF_DONE.md` item 2) without adding it to
  mypy's scoped file list in `.github/workflows/signal-copier-ci.yml`
  leaves it unchecked by CI going forward.
