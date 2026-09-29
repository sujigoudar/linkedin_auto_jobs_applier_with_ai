# Disaster recovery

What this codebase's real, already-built mechanisms provide toward
replay/recovery, and what is genuinely not built. Traced to
`app/services/integration_inbox.py`, `app/db.py`'s append-only
enforcement, and `spec/docs/13_operations_deployment_and_cost.md`'s
"Failover" section.

## What the append-only ledger already provides

`app/db.py::enforce_append_only()` installs a database-level trigger
(`forbid_ledger_mutation()`) on `ledger_entries`, `portfolio_versions`,
`portfolio_version_sleeves`, and `audit_events` that rejects any UPDATE or
DELETE, full stop -- "this must hold even for the table owner and even for
a caller who forgot (or a future refactor that removed) the
application-level `append_entry`/`append_correction` discipline in
`app/services/ledger.py`." Concretely, this means:

- **No recovery scenario can involve "the ledger got corrupted by a bad
  UPDATE"** -- that class of failure is structurally impossible against
  this schema. A restore only ever needs to reconcile *forward* (missing
  or duplicated rows since the backup), never *repair* an edited row,
  because no row is ever edited.
- **Every correction is itself a new, traceable row** --
  `append_correction()` references the row it corrects via
  `correction_of`, so a post-incident reconciliation can always walk the
  full correction chain for any entry; nothing was silently overwritten
  along the way.
- **The same applies to `audit_events`** -- the evidentiary record backing
  AD-18's evidence-manifest export (`docs/observability/OVERVIEW.md`)
  cannot itself have been altered after the fact, including by whatever
  process is performing the recovery.

## What idempotent ingest already provides

`app/services/integration_inbox.py::ingest_export_event()` is the real
mechanism that makes *redelivery* safe, which is the core operation any
replay-based recovery depends on:

- **Same `event_id` + same `payload_hash` is a harmless no-op** -- a
  recovery process that re-sends a batch of already-applied export events
  (because it isn't sure what landed before an interruption) does not
  double-apply them. This is verified directly:
  `existing.payload_hash != envelope.payload_hash` is the only condition
  that raises (`EventIntegrityError`); an exact-duplicate redelivery
  returns the existing row untouched.
- **Same `event_id` with a *different* payload_hash is a loud, named
  error** (`EventIntegrityError`), never silently resolved by
  last-write-wins -- a genuine data-integrity incident during recovery is
  surfaced, not masked.
- **Out-of-order delivery is handled, not just simple redelivery.** Every
  envelope is always received and durably stored the moment it arrives,
  regardless of order (`InboxEvent.received_at`); it is only *applied* (a
  real ledger projection) once its own `export_sequence` is next in line.
  `_apply_and_cascade()` re-checks after every application whether the
  next expected sequence is now sitting in the table already received and
  unapplied, and keeps walking forward -- so a recovery replay that
  delivers events out of order (42-then-41) still ends with both correctly
  applied in the right order, without 42 needing redelivery a second time.
- **Generation rollback/advance detection.** `_established_generation()`
  enforces that a stream's own established `producer_generation` cannot
  silently drift: an envelope claiming an *older* generation than what's
  already established is a detected rollback
  (`PARKED_REASON_GENERATION_ROLLBACK_DETECTED`, "Old economic IDs do not
  apply again"); a *newer* one requires a reconciled bootstrap this build
  does not automate (`PARKED_REASON_NEW_GENERATION_REQUIRES_BOOTSTRAP`).
  This is directly relevant to DR: if signal-copier's own private store is
  restored from an older backup and resumes emitting, this receiver does
  not silently accept its replayed history as new financial fact -- it
  parks it, visibly, pending an operator decision.
- **Snapshot/delta overlap is reconciled, not double-counted.** A bootstrap
  snapshot (`POSITION_SNAPSHOT` manifest) establishes a `cutoff_sequence`
  floor; any non-snapshot event at or below that floor is marked applied
  with **no second ledger entry**, whether it arrived before or after the
  snapshot activated -- "Position snapshot is not an extra execution."
- **Every "parked" (received-not-applied) state has a named,
  machine-readable reason** on `InboxEvent.parked_reason` -- see
  `docs/TROUBLESHOOTING.md` for the full list and how to diagnose each
  one. A recovery operator does not have to guess why an event hasn't
  applied; the row says so.

## What this genuinely gives a recovery operator

Taken together: signal-copier's own private export outbox (see the
sibling app's INT-040 work, `docs/observability/SLO.md`) is the durable
source of truth for every event this service has ever received, and
`ingest_export_event()` can safely replay any subset of it -- fully,
partially, out of order, or with duplicates -- without corrupting the
ledger or silently fabricating history. This is the real mechanism behind
`spec/docs/13_operations_deployment_and_cost.md`'s "Failover" requirement
to "preserve broker/platform accepted-but-unobserved effects, recover
exact portfolio/rights/mandate/event lineage."

## Honest gaps

1. **No automated point-in-time restore tooling in this codebase.** The
   append-only/idempotent-ingest properties above make a Postgres
   PITR-style restore *safe to replay against*, but this repository
   contains no backup-taking, restore-orchestration, or automated
   replay-triggering script. A real restore is a manual/platform-level
   operation today.
2. **No automated "resume from last applied sequence" replay driver.**
   `_next_expected_sequence()` can always answer "what is next expected
   for this stream," but nothing in this codebase automatically re-requests
   a gap from signal-copier's outbox -- a gap (`parked`, sequence ahead of
   expected) sits waiting for redelivery, which today depends on
   signal-copier's own relay worker's retry/backfill behavior on the
   sending side, not a pull mechanism on this side.
3. **No documented RPO/RTO target.** Nothing in this codebase or the specs
   read for this document states a quantitative recovery-point or
   recovery-time objective. `spec/docs/13_operations_deployment_and_cost.md`
   requires "Core command latency/service SLO must be set from selected
   strategy/route and measured, not an arbitrary web SLA" -- the same
   discipline has not yet been applied to a DR-specific RPO/RTO number.
   See `docs/operations/SLO.md`.
4. **Session/credential invalidation on restore is a stated requirement,
   not an implemented mechanism.** `spec/docs/13_operations_deployment_and_cost.md`:
   "A backup restore must revoke stale customer sessions as appropriate
   while retaining financial deduplication and open obligations." The
   *primitive* for this exists (`revoke_all_tokens_for_user()` denylists
   every unexpired `jti` for a user; `WebSession` rows are just table
   rows that a restore could truncate/expire), but there is no automated
   "on restore, revoke everything issued after the backup's timestamp"
   step in this codebase today.
5. **`_established_generation()`'s "new generation requires bootstrap"
   path is a detection, not a remediation.** The row is parked with a
   clear reason, but resolving it (performing the reconciled bootstrap) is
   explicitly out of this build's scope (INT-008/INT-009, per the
   module's own docstring) -- an operator must intervene manually.
6. **No cross-region or multi-AZ failover story is present in code.**
   Everything above concerns data-level recovery correctness within a
   single Postgres target; infrastructure-level high availability is not
   something this repository configures.

See `docs/operations/BACKUPS.md` for what exists (and doesn't) around
actually taking the backups this section assumes are available to restore
from.
