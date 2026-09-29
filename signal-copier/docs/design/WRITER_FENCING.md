# Writer Fencing: cross-process/cross-host single-writer design

Source of truth: `app/writer_lease.py` (module docstring and
implementation), `docs/FAILOVER.md`, `app/promote_cli.py`,
`app/db.py`'s `writer_lease` table. See ADR-0002 and ADR-0003 for the
decisions this design implements.

## The coordination problem

This deployment's only shared coordination point across hosts is the
SQLite database file itself, replicated between sites via Litestream
(`deploy/RUNBOOK.md`). SQLite has no Postgres-style cross-host advisory
lock, and even if it did, a lock alone has the same fundamental gap:
holding a lock does not prove the previous holder has actually
stopped. What a shared database *can* give reliably, even across hosts
and even if an operator skips a step, is a single, monotonically
increasing **fencing token**, issued exactly once per takeover, that
every command-execution path re-checks against the current token
immediately before acting.

## Token issuance

A single row (`id = 1`) in `writer_lease` holds:

| column | meaning |
|---|---|
| `fencing_token` | monotonically increasing; only ever bumped by an acquire/reacquire or an explicit promotion |
| `site_id` | the configured site identity (`WRITER_SITE_ID`, falls back to hostname) |
| `holder_id` | `f"{site_id}:{pid}:{uuid4[:8]}"` — unique per process instance |
| `acquired_at` / `expires_at` / `renewed_at` | lease timing, used by `promote_writer_lease`'s expiry check — **never** used by the per-command fencing check itself |

Three operations mutate or read it:

- **Acquire / reacquire** (`SignalStore.acquire_or_reacquire_writer_lease`,
  called once by `app/main.py`'s `lifespan` for the ACTIVE,
  non-`STANDBY_MODE` process only): the first lease ever, or a restart
  of the *same* configured site, bumps the token and succeeds
  automatically. A *different* site attempting to acquire is always
  refused (`WriterLeaseHeldByAnotherSiteError`), whether or not the
  existing lease looks expired.
- **Renew** (`SignalStore.renew_writer_lease`, called on a heartbeat
  interval by `app/main.py`'s `_writer_lease_heartbeat`): extends
  `expires_at` for the current holder/token. If this process no longer
  holds the current token, renewal fails (logged as a critical fencing
  event) — it does **not** re-acquire; a fenced process never tries to
  silently re-arm itself.
- **Promote** (`SignalStore.promote_writer_lease`, called only from
  `python -m app.promote_cli promote`): the sole path that lets a
  *different* site take over. Verifies the current lease is genuinely
  expired (`expires_at` in the past) and refuses with
  `LeaseStillValidError` otherwise.

## `WriterLeaseGuard`: the process-local fencing object

One `WriterLeaseGuard` instance per active process, holding its own
in-memory `_token: int | None` plus a `threading.Lock` (`self._lock`).

```python
def acquire(self) -> int:
    if self._token is not None:
        self.require_active()
        return self._token
    with self._lock:
        if self._token is not None:      # re-check inside the lock
            self.require_active()
            return self._token
        record = self.store.acquire_or_reacquire_writer_lease(...)
        self._token = record.fencing_token
        return self._token
```

`acquire()` is idempotent **per guard instance / OS process**: a
genuinely new process always has `_token is None`, so its first
`acquire()` always performs the real database acquisition, correctly
bumping the token and superseding any previous instance of the same
site. A *repeated* call on a guard that has already acquired (which
never happens in real uvicorn/gunicorn deployment, but does happen
across this codebase's own test suite re-entering `TestClient`/
`lifespan` many times against one process-wide `app.main` singleton)
does **not** bump the token again — it would otherwise fence out this
same process's own in-flight work every time. Instead it just
re-verifies the existing token is still current and returns it
unchanged.

`require_active()` is the actual per-command-execution check, called
immediately before any broker-write call:

```python
def require_active(self) -> None:
    if self._token is None:
        raise FencedOutError(...)
    try:
        current = self.store.get_writer_lease()
    except Exception as exc:
        raise FencedOutError(...) from exc          # fail closed on a DB error
    if current is None or current.fencing_token != self._token:
        raise FencedOutError(...)                    # fail closed on any mismatch
```

Deliberately **does not check `expires_at`**: a token mismatch fences
a process out the instant a new token is issued, even if the process's
own (now-superseded) lease row would otherwise still look unexpired to
it. This is the concrete meaning of "the second acquiring process's
new token immediately fences the first."

## Thread-safety: why `threading.Lock`, not `asyncio.Lock`

`acquire()`/`renew()` can be entered concurrently from **different OS
threads** against the same guard instance — not just different
coroutines on one event loop — because FastAPI's `lifespan()` runs on
anyio's thread-based portal, and this codebase's test suite opens many
overlapping `TestClient` context managers, each re-entering
`lifespan()` (and thus `acquire()`) on its own worker thread against
the one process-wide `app.main` singleton guard.

Without a real thread lock, two threads could both observe `_token is
None` (or both observe the same stale `_token`), both perform a real
acquisition/renewal against the store (bumping the database's fencing
token twice), and then race to write `self._token` — whichever write
lands last wins, and it can be the stale, lower value, leaving this
process fenced out **by its own second acquisition**
(`FencedOutError` on the very next `require_active()`), not by any
genuine second writer. A plain `threading.Lock` (which excludes OS
threads, unlike `asyncio.Lock`, which only excludes coroutines on one
loop) closes this.

## Where it's checked

Every path that can reach a broker write calls
`self.lease_guard.require_active()` first:

- `app/engine.py`: `SignalCopierEngine._handle_signal` (every
  signal-driven entry/close/plain-close) and `close_position` (the
  dashboard's manual "Exit now"/"Flatten" actions).
- `app/lifecycle/manager.py`: `on_entry_fill`, `resolve_pending_entry`,
  `on_price_update` (guards the time-exit/target-firing/tighten-stop/
  trailing paths, never the honest price/MAE/MFE observation itself),
  `request_exit`, `resolve_pending_exit`, and
  `retry_unprotected_positions` — reached both from `app/engine.py`
  and directly from background loops (`app/reconciliation.py`,
  `app/pricing.py`'s `PriceMonitor`) that never go through
  `app/engine.py` at all, so each needed its own, independent check.

`GET /health` reports `writer_lease_ok` (true/false/null) for the
active process's own token — a live, honest signal, not a static
"deployed as active" flag — and folds a `false` value into the overall
`status: degraded`.

## `NullLeaseGuard`

The default for `SignalCopierEngine`/`PositionLifecycleManager`
construction, so every existing test or ad-hoc embedding that does not
wire real fencing is unaffected — `require_active()`/`acquire()`/
`renew()` are all no-ops. `app/main.py` is the one place that
constructs a real `WriterLeaseGuard`, only for the app's own live
ACTIVE process, never for `STANDBY_MODE` (which is already blocked
from writing by `_standby_read_only_gate` independent of this class
entirely).

## Promotion (`app/promote_cli.py`)

See ADR-0003 for the full three-`--confirm-*`-flag contract. Mechanically,
`promote` refuses outright with `LeaseStillValidError` (exit code 4) if
the existing lease does not look expired, independent of the confirm
flags, and prints `command_ledger`'s unresolved entries (ADR-0004) as
part of the `--confirm-reconciled` step when that table exists.

## What this does not solve

- **Litestream replication lag / RPO** — a promoted site's database may
  be missing the most recent writes; this module has no visibility
  into replication freshness at all.
- **Proving the old process is dead** — that is `deploy/RUNBOOK.md`'s
  job, never this module's; the fencing token is a second, automatic
  layer underneath the manual procedure, not a replacement for it.
- **A shared broker session/API key used outside this app** — fencing
  this application's own command-execution paths does not revoke a
  brokerage credential some other process (or a human) could still use
  directly at the broker.
