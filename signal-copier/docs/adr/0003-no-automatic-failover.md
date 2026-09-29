# ADR-0003: No automatic failover — promotion is a deliberate, human-executed action

Status: Accepted
Date: 2026-09-29

## Context

A signal-copier engine that trades real brokerage accounts has a
failure mode strictly worse than downtime: two processes both able to
submit orders for the same account at once. `docs/FAILOVER.md` states
the decision's premise directly: "Treating 'I hold the lock' as 'I am
provably the only writer' is exactly the assumption that lets two
processes both trade the same account." Automatic promotion based
purely on a lease appearing expired — a network partition, a slow GC
pause, or a standby simply believing the primary is gone — cannot
distinguish "the primary is actually dead" from "the primary is alive
but slow to renew," and choosing wrong in the second case is a
same-account double-write, not a graceful failover.

The system's own coordination substrate (SQLite, see ADR-0001) has no
cross-host advisory lock and no way to independently verify a remote
process has stopped. Only a human, checking outside this codebase — a
cloud provider's stop/terminate confirmation, or a revoked/rotated
brokerage credential — can establish that fact with confidence.

## Decision

There is no automatic failover anywhere in this codebase, and — per
`docs/FAILOVER.md`'s own stated intent — there never silently will be.
A standby process (`STANDBY_MODE=true`) never acquires, renews, or
attempts to promote a lease; it is refused write access by
`app/main.py`'s `_standby_read_only_gate` and never starts ingestion,
independent of the lease mechanism entirely. The only way a different
site ever becomes the writer is a human running
`python -m app.promote_cli promote` after independently completing
`deploy/RUNBOOK.md`'s checklist.

`promote_cli.py` requires three explicit `--confirm-*` flags, every
time — deliberately not a single `--yes`:

- `--confirm-old-writer-fenced` — the operator has independently
  confirmed (cloud console, SSH, or a revoked/rotated brokerage
  credential) that the prior writer's host cannot reach the broker.
- `--confirm-reconciled` — the operator has reconciled any commands
  the prior writer may have submitted before dying; `promote_cli`
  prints the current `command_ledger`'s unresolved entries as part of
  this step (see ADR-0004).
- `--confirm-identity` — re-verifies brokerage/account identity before
  the new writer begins submitting commands (calls
  `get_account_balance` for every account whose broker adapter
  supports it, unless explicitly skipped with a loudly-logged flag).

`promote_writer_lease` itself also refuses outright
(`LeaseStillValidError`) if the existing lease does not look expired,
independent of the confirm flags — an automatic check layered on top
of, never instead of, the human confirmation.

## Consequences

- No code path anywhere can move the writer role between sites without
  a human deliberately invoking `promote_cli` with all three
  confirmations — this is the actual enforcement of "no automatic
  failover," not merely documentation of intent.
- Ordinary restarts of the same configured active site remain cheap
  and automatic (see ADR-0002) — the manual ceremony is reserved for
  genuine site changes, not routine deploys/crash-restarts.
- Availability during a genuine primary outage is bounded by how
  quickly a human can complete the runbook, not by an automatic
  detection-and-promotion loop — an explicit, accepted trade of
  faster automatic recovery for eliminating the double-write risk that
  recovery would otherwise carry.
- This mechanism has no visibility into Litestream replication
  freshness — a promoted site may be missing the most recent writes,
  which is why `deploy/RUNBOOK.md` step 3 requires manually verifying
  `lifecycle_state`'s pending entries/exits before resuming live
  trading.
