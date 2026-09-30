# Disaster recovery

This is the honest DR posture of this codebase today: what's real, what's
manual by design, and what remains genuinely untested.

## Posture, one sentence

**There is no automatic failover.** Recovery from a lost site is a
human-executed procedure (`deploy/RUNBOOK.md`), backed by two independent
mechanisms this codebase actually implements: durable financial-intent
state that survives a restart, and a database-backed fencing token that
prevents two processes from both acting as writer. See `docs/FAILOVER.md`
for the full mechanism writeup — this document summarizes it from a DR
operations angle and adds honest gaps that document didn't fully spell out.

## What survives a restart

### `command_ledger` — durable, pre-effect command intent

`app/command_ledger.py` writes and **commits** a row to `command_ledger`
*before* every broker call this codebase makes (entry, close, stop,
target, replace, cancel, flatten) — never after. If the process dies
between deciding to submit a command and recording its outcome, the
`command_ledger` row is still there, in `PENDING_SUBMISSION` or
`UNKNOWN_AMBIGUOUS` state, for a restart or a human reconciliation pass to
find. Before this table existed, this codebase's only record of "we tried
to submit an order" was the `orders` table row — written *after* the
broker call returned, which meant a mid-flight process death left no
durable trace a command was ever attempted at all.

`python -m app.promote_cli status`/`promote` both print the current
`command_ledger`'s unresolved entries as part of the promotion confirmation
step, when the table exists in the database being promoted.

### `capital_reservations` — durable notional commitment

`app/capital_allocator.py`'s `reserve_locked()` writes a durable row to
`capital_reservations` for every notional commitment made against the
owner-wide exposure ceiling, before that commitment is allowed to count.
On restart, `__init__` reloads any unresolved reservations via
`SignalStore.sum_unresolved_capital_reservations()`, so a crash between
reserving capital and resolving that reservation does not silently lose
track of committed exposure.

### `lifecycle_state` — pending entry/exit tracking

`PositionLifecycleManager`'s persisted `PendingEntry`/`PendingExit` rows
are, per `deploy/RUNBOOK.md`, "the *only* record of 'an order is in flight
and this symbol is not yet safely protected.'" A restore missing recent
writes (within Litestream's RPO window — see `docs/operations/BACKUPS.md`)
may be missing exactly these rows, which the runbook treats as "protection
state unknown," never "assumed flat/protected."

### `writer_lease` — cross-process/cross-host fencing

A single row holding the current fencing token, site id, and lease
timestamps. See `docs/FAILOVER.md` for the full mechanism. Relevant to DR
specifically: the token is what stops a recovered/lingering old-site
process from executing broker commands once a new site has been promoted,
even if step 1 of the manual runbook (confirming the old writer is dead)
were somehow incomplete.

## The manual promotion procedure

`deploy/RUNBOOK.md` requires four independently-true preconditions before
promotion: (1) the old writer is confirmed unable to write (cloud-provider
stop/terminate confirmation, or revoked/rotated brokerage credential — a
missing heartbeat or expired lease is explicitly **not** sufficient proof);
(2) outstanding broker-side order/fill effects are reconciled against local
records; (3) the recovered database state is verified usable
(`PRAGMA integrity_check`, `lifecycle_state` rows checked, routing/account
config present); (4) the new site is confirmed actually eligible (broker
reachability, fresh session secrets, real resource headroom).

`python -m app.promote_cli promote` then requires three explicit
`--confirm-*` flags — no single `--yes` shortcut — each a human
acknowledgment of one of those steps, plus its own automatic guard
(`LeaseStillValidError`) refusing to promote over a lease that doesn't look
genuinely expired.

## Honest gaps

- **Real OS-level disk exhaustion is untested.** `EXPORT_OUTBOX_SIZE_CEILING_BYTES`
  (see `docs/operations/SLO.md`) is an application-level alerting
  threshold on the export outbox specifically — it fires well before disk
  exhaustion under the deployment sizes this project's own INT-040 work
  reasoned about (comfortably weeks of headroom on a modest 5-10 GB
  deployment disk at plausible signal volumes). It is **not** a test of
  actual OS-level disk-full behavior: what happens to a mid-write SQLite
  transaction, WAL growth, or Litestream replication when the underlying
  disk genuinely fills is untested by this session's own work and remains
  an open gap. Treat any incident involving actual disk exhaustion as
  unverified territory — behavior is not guaranteed to be "clean failure."
- **Litestream replication lag / RPO is not measured against any specific
  target.** `docs/FAILOVER.md` states this plainly: "this module has no
  visibility into replication freshness at all." The RUNBOOK's step 3
  manual verification (checking `lifecycle_state` pending rows after
  restore) is the only real check that a restore is usable, not a
  guarantee about how much data a given incident actually lost.
- **The automatic-promotion-eligibility checklist is, by design, not met.**
  `deploy/RUNBOOK.md`'s own "Automatic promotion eligibility" section lists
  five preconditions (a tested independently-enforceable fencing mechanism,
  a single serialized promotion authority, measured Litestream RPO,
  complete broker order-history readback wired into the promotion path,
  tested partition/crash/fence-refusal/old-node-return scenarios) — none
  of them implemented as automation today. This is the deliberate
  baseline, not a placeholder for something coming later without review.
- **Multi-site deployment itself is unproven against a real cloud account.**
  See `docs/operations/DEPLOYMENT.md` — the Terraform/systemd/cloud-init
  draft is static-validated, never actually applied.
- **No tested partition/crash/fence-refusal/old-node-return scenarios.**
  The fencing mechanism's logic is covered by `tests/test_writer_lease_fencing.py`,
  but that is unit/integration-level testing of the mechanism, not a
  rehearsed, timed disaster-recovery exercise against a real deployed pair
  of hosts.
