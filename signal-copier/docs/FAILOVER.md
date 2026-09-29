# Failover model: deliberately manual, safety-first, not automatic HA

This document states plainly what this codebase does and does not do for
redundancy, and why. It exists alongside (and never replaces)
`deploy/RUNBOOK.md`, which is the actual, step-by-step, human-executed
promotion procedure. Read that document to actually run a promotion —
this one explains the mechanism underneath it and the boundary of what
it proves.

## The one-sentence version

**There is no automatic failover in this codebase, anywhere, and there
never silently will be.** A standby never promotes itself. The only way a
different site ever becomes the writer is a human running
`python -m app.promote_cli promote` after independently completing
`deploy/RUNBOOK.md`'s checklist.

## Two layers, not one

It's tempting to reach for "the database has a lock table, so we have
distributed locking" and stop there. That's not true for SQLite (there is
no cross-host advisory lock the way Postgres has one), and even if it
were true, a lock alone doesn't prove the previous holder actually
stopped running — a network partition, a slow GC pause, or a process that
just hasn't noticed its lease expired yet can all leave an old writer
still able to reach the broker even though some coordination record says
otherwise. Treating "I hold the lock" as "I am provably the only writer"
is exactly the assumption that lets two processes both trade the same
account.

So this codebase has two independent layers, and both must hold:

1. **`deploy/RUNBOOK.md`'s manual procedure** — the actual proof. A human
   confirms, at the infrastructure or brokerage level (a cloud provider's
   stop/terminate confirmation, or a revoked/rotated API credential),
   that the prior writer genuinely cannot reach the broker any more.
   Nothing in this codebase can verify that automatically, so nothing
   here claims to.

2. **`app/writer_lease.py`'s fencing token** (this document's subject) —
   an automatic, database-backed guard that sits *underneath* the manual
   procedure. It doesn't prove the old process is dead. What it does
   provide: the instant a new fencing token is issued (only ever via the
   explicit promotion command), every command-execution path in this
   process — `SignalCopierEngine.handle_signal`/`close_position`,
   `PositionLifecycleManager`'s entry/exit/stop paths — refuses to
   proceed on its very next check, in this process and in every other
   process still holding an older token, without waiting for anything to
   "look expired." If step 1 above were somehow skipped or wrong, this
   layer is what keeps a *second* writer from executing broker commands
   once a promotion has actually happened, and what keeps a stale
   process from continuing to execute once it's been superseded.

Neither layer is optional. Skipping layer 1 ("the lease looked expired,
so I just ran promote") is exactly the failure mode `deploy/RUNBOOK.md`
and `app/promote_cli.py`'s required `--confirm-old-writer-fenced` flag
exist to stop. Not having layer 2 would mean a manually-verified
promotion still relies on every process's own good behavior (renewing
correctly, noticing its own staleness) with no independent check.

## The mechanism: `writer_lease` and fencing tokens

A single row in the `writer_lease` table (see `app/db.py`'s schema
comment) holds the CURRENT lease: a monotonically increasing
`fencing_token`, the `site_id`/`holder_id` that issued it, and
`acquired_at`/`expires_at`/`renewed_at` timestamps.

- **Acquire** (`SignalStore.acquire_or_reacquire_writer_lease`, called
  once by `app/main.py`'s `lifespan` for the ACTIVE, non-`STANDBY_MODE`
  process only): the *first* lease ever, or a *restart of the same
  configured site* (same `WRITER_SITE_ID`), bumps the token and succeeds
  automatically — this is ordinary operations (a deploy, a crash
  restart), not a failover. A DIFFERENT site attempting to acquire is
  always refused, whether or not the existing lease looks expired — see
  `WriterLeaseHeldByAnotherSiteError`. This is the actual code that
  answers the audit's original question: *a second host cannot start
  trading the same account merely because the first heartbeat
  disappeared*, because acquiring across sites is never automatic, full
  stop.

- **Renew** (`SignalStore.renew_writer_lease`, called on a heartbeat
  interval by `app/main.py`'s `_writer_lease_heartbeat` background task):
  extends `expires_at` for the current holder/token. If this process no
  longer holds the current token, renewal fails and is logged as a
  critical fencing event — but note this does NOT re-acquire; a fenced
  process never tries to silently re-arm itself.

- **Require-active** (`WriterLeaseGuard.require_active()`, called at the
  top of every command-execution path — see "Where it's checked" below):
  the actual per-command fencing check. Compares this process's
  in-memory token against whatever the database says is current RIGHT
  NOW. A mismatch raises `FencedOutError` immediately — deliberately
  independent of `expires_at`: a stale writer is fenced out the moment a
  new token exists, even if its own lease row would otherwise still look
  unexpired to it. This is what "the second acquiring process's new
  token immediately fences the first, even before the first's own lease
  would look expired to itself" means concretely.

- **Promote** (`SignalStore.promote_writer_lease`, called ONLY from
  `python -m app.promote_cli promote`): the sole path that lets a
  DIFFERENT site take over. Verifies the current lease is genuinely
  expired (`expires_at` in the past) and refuses with
  `LeaseStillValidError` otherwise — refusing to promote over what might
  still be a live writer. This is one automated check on top of, never
  instead of, the human confirmation `deploy/RUNBOOK.md` and
  `--confirm-old-writer-fenced` require.

## Where it's checked

Every path that can reach a broker write checks
`self.lease_guard.require_active()` before doing so:

- `app/engine.py`: `SignalCopierEngine._handle_signal` (covers every
  signal-driven entry/close/plain-close) and `SignalCopierEngine.close_position`
  (the dashboard's manual "Exit now"/"Flatten" actions) — the two
  top-level entry points into this engine.
- `app/lifecycle/manager.py`: `PositionLifecycleManager.on_entry_fill`,
  `resolve_pending_entry`, `on_price_update` (guards the
  time-exit/target-firing/tighten-stop/trailing paths, but never the
  honest price/MAE/MFE observation that happens first), `request_exit`,
  `resolve_pending_exit`, and `retry_unprotected_positions` — these are
  reached both from `app/engine.py` above AND directly from background
  loops (`app/reconciliation.py`, `app/pricing.py`'s `PriceMonitor`) that
  never go through `app/engine.py` at all, so each needed its own,
  independent check.

`GET /health` also reports `writer_lease_ok` (true/false/null) for the
active process's own token — a live, honest signal, not a static
"deployed as active" flag — and folds a `false` value into the overall
`status: degraded`.

## What promotion actually requires (`app/promote_cli.py`)

```
python -m app.promote_cli status
python -m app.promote_cli promote \
    --confirm-old-writer-fenced \
    --confirm-reconciled \
    --confirm-identity
```

All three `--confirm-*` flags are required, explicitly, every time —
there is no single `--yes`. Each one is a human acknowledgment of a real
step, not a formality:

- **`--confirm-old-writer-fenced`**: you have independently confirmed
  (cloud console, SSH, or a revoked/rotated brokerage credential) that
  the prior writer's host cannot reach the broker any more —
  `deploy/RUNBOOK.md` step 1. This tool's own lease-expiry check inside
  `promote_writer_lease` is a second, automatic guard on top of this,
  never a replacement — it can tell you a lease looks unexpired (and
  refuse), but it can never by itself tell you the old process is
  actually dead.
- **`--confirm-reconciled`**: you have reconciled any commands the prior
  writer may have submitted before dying (`deploy/RUNBOOK.md` step 2).
  `promote_cli status`/`promote` both print the current
  `command_ledger`'s unresolved entries as part of this step, when that
  table exists (see "Integration with the command ledger" below).
- **`--confirm-identity`**: you want this command to re-verify
  brokerage/account identity before the new writer begins submitting
  commands. Unless `--skip-identity-check` is also passed (strongly
  discouraged, and loudly logged when used), `promote_cli` calls
  `get_account_balance` for every configured destination account whose
  broker adapter has real balance-readback capability
  (`BrokerAdapter.has_balance_capability`) and refuses to promote if any
  of them fails or returns nothing meaningful. A broker with no verified
  balance-readback at all is reported, not silently skipped — the
  operator is responsible for confirming those accounts manually.

The command also refuses outright (`LeaseStillValidError`, exit code 4)
if the existing lease does not look expired — independent of the
confirm flags — so an operator cannot accidentally promote over what the
database itself still thinks is a live writer.

## Integration with the command ledger (P0-2)

A sibling change (P0-2) introduces a durable `command_ledger` recording
every command a writer submitted whose outcome might not have been
confirmed before it stopped being writer. `promote_cli.py` already prints
that table's unresolved entries as part of the promotion confirmation
step, *if the table exists* in the database being promoted. If P0-2
hasn't been merged/migrated into a given checkout yet, `promote_cli`
prints a clear note instead of silently reporting "0 unresolved" — this
is a known follow-up: once `command_ledger`'s final schema lands, wire
its actual per-row detail (not just a count) into this same printout, and
consider requiring the operator to acknowledge each row individually
rather than a single count.

## Ordinary restarts vs. failover — why they're different code paths

A crash or deploy restart of the SAME configured active site
(`WRITER_SITE_ID` unchanged) reacquires the lease automatically at
startup — see "Acquire" above. This is deliberate: requiring the full
`promote_cli` ceremony (including re-verifying broker identity and
reconciling commands) for every routine restart of the one site that was
already supposed to be active would make ordinary operations
impractical, without adding any real safety — the fencing token still
bumps on every such reacquire, so a lingering zombie instance from before
the restart is fenced exactly the same way a genuine failover fences the
old site.

What is NOT automatic, ever, under any configuration: a DIFFERENT
`WRITER_SITE_ID` acquiring the lease. That always requires
`promote_cli.py`, regardless of how long the existing lease has looked
expired.

## What this does not claim to solve

- **Litestream replication lag / RPO.** A promoted site's database may be
  missing the most recent writes from before the incident. See
  `deploy/RUNBOOK.md` step 3 for the manual verification this requires
  (`lifecycle_state`'s pending entries/exits in particular) — this
  module has no visibility into replication freshness at all.
- **Proving the old process is dead.** Covered above — this is
  `deploy/RUNBOOK.md`'s job, never this module's.
- **Split-brain from a shared broker session/API key used outside this
  app.** Fencing this application's own command-execution paths does not
  revoke a brokerage credential that some other process (or a human)
  could still use directly at the broker.

`deploy/RUNBOOK.md`'s own "Automatic promotion eligibility" checklist
remains the authoritative list of everything that would need to be true
before *any* of this could safely become automatic — none of it is met
today, and this module does not attempt to meet it. It only makes the
manual procedure that checklist describes safer to execute, and makes a
skipped or incorrect step fail closed instead of fail silent.
