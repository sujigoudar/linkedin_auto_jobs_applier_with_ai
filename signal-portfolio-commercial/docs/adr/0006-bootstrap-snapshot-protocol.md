# ADR-0006: Bootstrap-snapshot protocol for cold-start/resume of the ingest pipeline

## Status

Accepted. Implemented in `app/services/integration_inbox.py`
(`_ingest_position_snapshot_page`, `_activate_snapshot_and_reconcile`,
`_snapshot_floor`) and cached on `InboxEvent`'s `manifest_id`/`snapshot_page_index`/
`snapshot_page_count`/`snapshot_cutoff_sequence` columns
(`alembic/versions/c9f3a6d21e84_inbox_events_snapshot_manifest.py`).

## Context

A stream that only ever ships incremental deltas has no way to cold-start (a brand
new tenant onboarding an existing, already-running signal-copier deployment has no
usable "apply every delta since sequence 0") or to resume cleanly after a
`producer_generation` bump (S6/INT-010: a producer re-bootstrapped from a fresh
snapshot must never have its new generation's deltas silently interleaved with the
old one's sequence numbers). Both cases need a real, point-in-time baseline the
ledger can start from — "Snapshot plus deltas" (S6) — and that baseline must be
provably complete before it is trusted: INT-009's own "Partial snapshot labeled
complete" is an explicitly prohibited outcome, and INT-009's "Interrupted bootstrap
resumes" requires that whichever pages arrive first, in whatever order, still end up
correctly activated the instant the *last* one lands.

## Decision

A bootstrap snapshot is a manifest of `POSITION_SNAPSHOT` pages, each carrying the
same `manifest_id`, `page_count`, and `cutoff_sequence`. Page receipt is
deliberately **not** gated by the ordinary `_next_expected_sequence` gap mechanism
(ADR-0003) — gating it that way would let an out-of-order page get silently
gap-parked by the generic mechanism without its own manifest metadata ever being
cached, permanently deadlocking that manifest's own completeness check.

- Every page is validated against its already-received siblings (same
  `manifest_id`) before its metadata is cached: a page claiming a different
  `producer_generation` parks as `manifest_generation_mismatch` (INT-009's "mix a
  page from another generation... rejected"); one whose `page_count`/
  `cutoff_sequence` disagrees with a sibling's parks as `manifest_metadata_mismatch`.
- The page's own metadata is cached onto its `InboxEvent` row unconditionally (once
  validated), regardless of arrival order.
- The manifest **activates** — real `Book.PLATFORM` ledger baseline entries are
  written, one per `PositionSnapshotEntry` across every page — the instant every
  `page_index` in `0..page_count-1` has been received, whichever page happens to be
  the one that completes the set. Activation timestamps every baseline entry at the
  **earliest** `event_time` among the manifest's own pages (the snapshot as a whole
  describes one single point-in-time state, even though its pages were emitted and
  may have arrived separately), tagged with a distinct `source_authority` prefix
  (`signal-copier-snapshot-relay:`, never the ordinary `signal-copier-relay:` an
  `EXECUTION_APPLIED` row gets).
- Activation sets `cutoff_sequence` as a hard floor (`_snapshot_floor`):
  `_next_expected_sequence` is never behind `floor + 1` from that point on, and
  `_apply_projection` treats any **non**-snapshot envelope at or below the floor as
  already reflected in the snapshot's own baseline — applied (it must still advance
  past, never park forever) but producing no second ledger entry, so a delta already
  covered by the snapshot is never double-counted.
- Activation also sweeps and cascades: every already-received-but-unapplied
  non-snapshot row at or below the cutoff is applied (covered, no new entry); every
  row above the cutoff is cascaded forward exactly like an ordinary gap-unblock,
  since a bootstrap snapshot activating is exactly the kind of event that can
  unblock a long run of previously out-of-order deltas.

Explicitly out of this slice's bounded scope: a non-snapshot row at or below
`cutoff_sequence` that was already applied **normally** (with its own real ledger
entry) before the manifest activated is never retroactively revisited, removed, or
netted out against the snapshot's own baseline.

## Consequences

- Onboarding an already-running stream, or resuming after a `producer_generation`
  bump, has a real, well-defined cold-start path that does not require replaying
  every historical delta from sequence 0.
- A snapshot can be resumed from an interruption safely: partially-received pages
  stay durably stored and un-applied, and whichever later call completes the set —
  regardless of order — is the one that activates it.
- The un-revisited-normal-application edge case is a known, disclosed gap, not a
  silent one: a future slice that needs retroactive snapshot reconciliation against
  already-applied deltas has to be built deliberately, not assumed to already work.
