# ADR-0011: A dedicated, append-only table for `TARGET_UPDATE`/`STOP_UPDATE` source revisions -- never new `LedgerEntry` columns

## Status

Accepted. Implemented in
`app/models/source_stop_target_revision.py::SourceStopTargetRevision`,
`app/services/source_stop_target_revisions.py`, and wired into
`app/services/integration_inbox.py::_apply_projection`'s
`EventType.SOURCE_EVENT` branch.

## Context

Track 35 found that `SourceEventKind.TARGET_UPDATE`/`STOP_UPDATE` always
parked as `source_event_kind_not_ledger_representable:<kind>` --
genuinely understood kinds, but `app/models/ledger.py::LedgerEntry` has
no stop-loss/target column at all, so there was nowhere honest to write
either one even once correlated. That park also permanently blocks
`_next_expected_sequence` for every later event on the same stream
(`InboxEvent.applied_at` stays `NULL` forever for that row and
everything behind it) -- today a disclosed, theoretical gap (no
signal-copier adapter emits either kind yet), but a real, live-impact
bug the moment a real external account/adapter does.

Track 41 was asked to build the real representation this gap needs,
with two live options actually investigated before deciding:

1. **New nullable `stop_loss`/`take_profit`/`targets` columns directly
   on `LedgerEntry`.** Rejected. ADR-0004's own four-book model is
   explicitly an ECONOMIC journal: every existing `LedgerEntry` row is a
   real recommended-or-executed TRADE fact, which is exactly why
   `quantity`/`price`/`side` are NOT NULL columns on that table. A
   stop-loss/take-profit revision is not a trade -- it carries no
   quantity, no price, often not even a side; it REVISES a risk
   parameter attached to an EXISTING `Book.SOURCE` recommendation.
   Bolting nullable risk-parameter columns onto a table whose every
   other row is a trade would blur that invariant for every future
   reader (`compute_book_performance`/`compute_analyst_attribution`
   would need new "is this row actually a trade" filtering they don't
   need today), for a kind of row that was never a trade in the first
   place.
2. **Teach `SOURCE_EVENT`'s existing provenance-only handling
   (DELETE/CANCEL/CLOSE's own no-op-advance idiom) to just apply
   TARGET_UPDATE/STOP_UPDATE as a no-op too, same as those three.**
   Rejected as the FULL answer, though it's half of what this ADR does:
   that alone would stop the permanent block (closing the operational
   half of the gap), but it would leave the revision's own real content
   (the revised stop/target/targets) recoverable only by re-parsing
   `envelope_json` -- exactly the kind of "theoretically durable but not
   actually queryable" state this codebase's own `InboxEvent.source_
   event_native_key` precedent (Track 35) was built specifically to
   avoid for EDIT. The person running this project has said real
   external accounts/adapters are coming soon, at which point a
   dashboard or a future managed-stop feature will genuinely need to
   answer "what is this position's current recommended stop" --
   re-parsing every row's raw JSON to answer that is not a real
   representation.

Investigated and confirmed NOT the same concept: signal-copier's own
`app/lifecycle/models.py`/`manager.py` `StopTargetEventType`/
`stop_target_events` table tracks THAT codebase's OWN broker-level
protective-stop lifecycle (`STOP_PLACED`/`STOP_TIGHTENED`/
`PROTECTION_FAILED`/`TARGET_HIT`) -- i.e., what the PLATFORM's own
execution engine actually did with a real resting broker order. This
ADR's `SourceStopTargetRevision` is a different axis entirely: what the
SOURCE (the analyst/provider) said about their OWN recommended stop/
target, before any execution. Reusing signal-copier's own vocabulary
here would conflate "the analyst revised their recommendation" with
"the platform's own broker order moved," which are genuinely different
facts about different subjects (ADR-0004's whole point, applied one
level down).

## Decision

A new, dedicated, append-only, tenant-scoped table --
`source_stop_target_revisions` (`SourceStopTargetRevision`) -- records
every real `TARGET_UPDATE`/`STOP_UPDATE` revision this build receives,
independent of `ledger_entries`:

- `kind` (`target_update`/`stop_update`), `source_event_native_key` (this
  revision's OWN native-provider identity, the identical
  `f"{tenant_id}|{source_provider_id}|{source_channel_id}|
  {source_event_id}"` shape Track 35 built for `SOURCE_EVENT`/
  `SOURCE_RECEIPT` rows), `instrument`, `stop_loss`, `take_profit`,
  `targets_json` (the revised ordered multi-target collection,
  serialized verbatim) -- the revision's own real content, never
  fabricated or defaulted.
- `resolved_source_entry_id`: a best-effort, NEVER required,
  correlation to the `Book.SOURCE` `LedgerEntry` this revision concerns,
  via `source.parent_event_id` (the same field `ADD`/`CANCEL` already
  use to name an earlier `ORIGINAL`/`ADD`) resolved through the exact
  same tenant-scoped, exact-match native-key mechanism `EDIT` uses.
  Unlike `EDIT`, failing to resolve this is NOT a reason to park: no
  money is at stake in recording risk-parameter metadata that happens
  not to correlate cleanly (yet, or ever) -- an unresolved revision is
  still real, honestly-received provenance, so it is recorded with
  `resolved_source_entry_id = NULL`, never guessed and never blocked.
- `app/services/source_stop_target_revisions.py::append_stop_target_
  revision` is the one sanctioned way to write a row -- always an
  INSERT, since every real revision is its own distinct historical
  fact (there is no "correction" concept here the way `ledger.
  append_correction` has one: nothing is ever superseded, only added
  to).
- Tenant-scoped (RLS, ADR-0001) and append-only (ADR-0008's own
  trigger, applied to this table too) -- the SAME two database-level
  guarantees every other historical-fact table in this codebase gets,
  for the same reason: a later revision for the same position is a NEW
  row, never an edit of an earlier one's.
- `_apply_projection`'s `SOURCE_EVENT` branch now writes a real
  `SourceStopTargetRevision` row for `TARGET_UPDATE`/`STOP_UPDATE` and
  sets `applied_at` -- the permanent-stream-block class of bug Track 35
  already closed for every OTHER kind is now closed for these two as
  well, before any real adapter ever emits them.

## Consequences

- `TARGET_UPDATE`/`STOP_UPDATE` no longer park or block their stream --
  the `source_event_kind_not_ledger_representable` parked reason is
  retired (no code path produces it any more); a real external
  stop/target-revising adapter landing later needs no further ingest
  work to avoid stalling its own stream.
- A position's full stop/target revision history is a real, queryable
  table (`list_stop_target_revisions`), not something a future feature
  has to recover by re-parsing `envelope_json`.
- `Book.SOURCE`'s own `LedgerEntry` rows stay exactly what ADR-0004
  says they are -- real trade facts only, never risk-parameter
  metadata -- so `compute_book_performance`/`compute_analyst_
  attribution` need no new "is this actually a trade" filtering.
- `resolved_source_entry_id` carries no FOREIGN KEY, same reasoning as
  `LedgerEntry.sleeve_id`/`.follower_connection_id`: a soft reference
  that survives the referenced row's own lifecycle, never a hard
  dependency a future cleanup could be blocked by.
- This is still deliberately NOT a "what is the position's current
  protective stop at the broker" answer -- that is signal-copier's own
  `stop_target_events`/`PositionLifecycleManager` concern, a different
  subject (the PLATFORM's own execution, not the SOURCE's
  recommendation), out of this table's scope by design, not by
  oversight.
