# ADR-0002: Cross-process writer-lease fencing via a monotonic token, not lease-expiry alone

Status: Accepted
Date: 2026-09-29

## Context

SQLite has no cross-host advisory lock the way Postgres does (see
ADR-0001), and — as `app/writer_lease.py`'s module docstring states —
even a lock alone would not prove a previous holder has actually
stopped running: "a network partition, a slow GC pause, or a process
that just hasn't noticed its lease expired yet can all leave an old
writer still able to reach the broker even though some coordination
record says otherwise." A conventional lease-with-TTL scheme (holder
renews before `expires_at`; anyone may take over once it looks
expired) has exactly this weakness: "looks expired to a third party"
is not the same fact as "has stopped executing."

This deployment already has a first, authoritative layer for that
proof: `deploy/RUNBOOK.md`'s manual, human-executed promotion
procedure, which requires independently confirming — at the
infrastructure or brokerage level (a cloud provider's stop/terminate
confirmation, or a revoked/rotated API credential) — that the prior
writer genuinely cannot reach the broker any more. That manual
confirmation is real proof; nothing in code can verify it
automatically. But a second, automatic, code-level guard was still
needed underneath it, for the case where that manual step is skipped
or wrong, or where a genuinely stale process simply hasn't yet
"noticed" its own lease looks expired.

## Decision

`writer_lease` (one row, `id=1`) carries a `fencing_token` that only
ever increases, issued exactly once per takeover (an initial
acquisition, an ordinary same-site restart/reacquire, or an explicit
promotion). Every command-execution path — `SignalCopierEngine`'s
signal-driven entry/close paths, `PositionLifecycleManager`'s
entry/exit/stop paths, reached both from the engine and directly from
background loops (`app/reconciliation.py`, `app/pricing.py`'s
`PriceMonitor`) — calls `WriterLeaseGuard.require_active()`
immediately before any broker-write call. `require_active()`
deliberately does **not** check `expires_at`: it compares this
process's in-memory token against whatever the database currently
says is current, and raises `FencedOutError` on any mismatch,
regardless of whether the process's own (now-superseded) lease row
would otherwise still look unexpired to it.

This makes fencing immediate rather than eventual: the instant a new
token is issued (only ever via the explicit `python -m app.promote_cli
promote` command — never automatically), every process still holding
an older token is refused on its very next command-execution check,
"not just eventually, once its lease looks expired to itself" (module
docstring).

Acquisition itself stays asymmetric by design: a restart of the SAME
configured site (`WRITER_SITE_ID` unchanged) reacquires and bumps the
token automatically — ordinary operations, not a failover. A
DIFFERENT site attempting to acquire is always refused
(`WriterLeaseHeldByAnotherSiteError`), whether or not the existing
lease looks expired; the only way a different site ever becomes writer
is the explicit `promote_cli` command, which itself refuses
(`LeaseStillValidError`) unless the existing lease is genuinely
expired. See ADR-0003 for why promotion stays a manual, human-gated
action rather than becoming automatic.

## Consequences

- The fencing token is a second, independent, automatic layer that
  sits *underneath* the manual runbook procedure, never a replacement
  for it: `deploy/RUNBOOK.md`'s human confirmation remains the actual
  proof a prior writer's host cannot reach the broker; the fencing
  token is what keeps a second writer from executing broker commands
  once a promotion has actually happened, and what fails a stale
  process closed even if the manual step was somehow skipped or wrong.
- `require_active()` fails closed on every ambiguous outcome — no
  acquired token, a lease row that's gone, or a DB error reading it —
  never treated as "still valid."
- `WriterLeaseGuard`'s check-then-act acquire/renew path is guarded by
  a plain `threading.Lock` (not `asyncio.Lock`), because FastAPI's
  `lifespan()` and this codebase's test suite can re-enter
  `acquire()`/`renew()` from genuinely different OS threads against
  the one process-wide guard instance — an `asyncio.Lock` would only
  exclude coroutines on a single event loop, not threads, and a race
  there could otherwise fence a process out by its own second
  acquisition, not by any genuine second writer.
- A promoted site's database may still be missing the most recent
  writes if Litestream replication lagged before the incident — this
  mechanism has no visibility into replication freshness at all; that
  remains `deploy/RUNBOOK.md`'s own manual verification step.
- Fencing this application's own command-execution paths does not
  revoke a brokerage credential some other process (or a human) could
  still use directly at the broker — a disclosed, out-of-scope gap.
