# ADR-0003: Idempotent, ordered ingest inbox instead of at-least-once with dedup-after-the-fact

## Status

Accepted. Implemented in `app/models/integration_inbox.py` (`InboxEvent`,
`ExportStreamRegistration`) and `app/services/integration_inbox.py`
(`ingest_export_event`, `_next_expected_sequence`, `_apply_and_cascade`).

## Context

The relay worker delivers signal-copier's exported events at-least-once — a network
retry, a crashed worker, or a redelivered batch can all cause the same event to
arrive twice, and nothing guarantees FIFO delivery across a whole stream. Two
different failure classes have to be handled honestly rather than papered over:

1. **Duplicate delivery** of the identical event (same `event_id`, same bytes) must
   be a harmless no-op — but a redelivery under the same `event_id` with a
   **different** payload must never be silently accepted (last-write-wins on a
   financial fact is exactly the kind of corruption the ledger cannot tolerate).
2. **Out-of-order delivery** (event 42 arrives before event 41) must never be applied
   out of order, and must never be silently dropped or skipped.

A simpler "just dedup by event_id after the fact" design does not address ordering:
it would let 42 apply before 41, corrupting the ledger's own economic sequence.

## Decision

`InboxEvent.event_id` (the producer's own idempotency key,
`EventEnvelope.event_id`) is the table's primary key — a redelivery of the same
event is a harmless re-insert-or-detect-existing, never a second row.
`ingest_export_event` checks the existing row's `payload_hash` against the new
envelope's: identical hash → return the existing row as a no-op; different hash
under the same `event_id` → raise `EventIntegrityError`, a real, loud integrity
fault, never resolved by last-write-wins.

Ordering is kept as two separate high-water marks per `InboxEvent` row:

- `received_at` is set the instant an envelope is durably stored, **regardless of
  order** — every envelope is always received.
- `applied_at` is set only once the event's own `export_sequence` is next in line
  for its `(source_stream, producer_generation)`, per
  `_next_expected_sequence`, which is keyed strictly off the highest already-
  **applied** sequence (never off what has merely been received — a received-but-
  parked event must never advance this boundary, or a real gap would become
  invisible).

An event that arrives ahead of its predecessor is **parked**: received and durably
stored, `applied_at` left `NULL`. `_apply_and_cascade` re-checks after applying each
event whether the row now sitting at the newly-expected sequence was already
received earlier (parked behind this one) and, if so, applies it too — repeating
until it hits a genuine gap or runs out of received rows. So a redelivery of the one
missing sequence number (41) unblocks every later event already sitting in the table
(42, 43, ...) without needing any of them redelivered again.

`producer_generation` (bumped by the producer when a stream is re-bootstrapped from
a fresh snapshot) scopes this ordering — `export_sequence` is only ever monotonic
**within** one generation, never across a rollback/rebootstrap
(`_established_generation`, INT-010 "Producer restored to older database").

A `POSITION_SNAPSHOT` bootstrap manifest sits outside this ordinary gate entirely
(see `INGEST_PIPELINE.md`) so an out-of-order snapshot page is never gap-parked by
the generic mechanism before its own manifest completeness can be evaluated.

## Consequences

- Redelivery of an identical event is always safe; redelivery of a corrupted or
  conflicting payload under the same `event_id` is always a loud, distinguishable
  error, never a silent overwrite.
- A single missing sequence number parks not just its own event but every later
  event on that stream until it is redelivered — this is the documented, intentional
  cost of "never apply out of order," not a bug. `source_coverage.py`'s
  `PARKED` disposition surfaces this without a fabricated reason string, since a
  gap-wait carries no `parked_reason` of its own.
- An unsupported `schema_version` or an unimplemented `EventType` also parks its row
  permanently un-applied — and, because it can never satisfy `_next_expected_
  sequence`, permanently parks every later sequence on that same stream too. This is
  the honest consequence of "keep separate received/applied cursors," resolved only
  by a future build adding real support for that version/type.
- Nothing here is defended against a genuinely malicious sender forging envelopes —
  that boundary is HMAC verification (`app/services/relay_auth.py`), a separate,
  earlier layer covered in `INGEST_PIPELINE.md`.
