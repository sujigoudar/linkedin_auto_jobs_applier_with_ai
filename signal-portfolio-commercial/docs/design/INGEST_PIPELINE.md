# Ingest Pipeline Design

Source of truth: `app/services/integration_inbox.py`, `app/models/integration_inbox.py`,
`app/services/relay_auth.py`, `app/api/relay_routes.py`. See also ADR-0002,
ADR-0003, ADR-0006.

## Purpose

The commercial platform never generates its own trading events — every economic
fact it records for `Book.SOURCE` and `Book.PLATFORM` originates in signal-copier
(the upstream, single-owner execution engine) and arrives through a private export
outbox, relayed by a restricted worker into this service's `inbox_events` table.
This document describes that path end to end: transport authentication, tenant
resolution, idempotency, ordering, and bootstrap/resume.

## 1. Transport: HMAC signature verification

Implemented in `app/services/relay_auth.py`. Per
`INTEGRATION_DECISION.md` S4 ("Use the existing qualified authentication mechanism
when available. Otherwise use a maintained mTLS or audience-bound signed-service-
token implementation, with expiry, key rotation and replay protection") — no mTLS
PKI exists in this environment, so the signed-service-token half is implemented:

- Header shape: `X-Relay-Signature: t=<unix ts>,v1=<hex hmac>` — the same
  `"{timestamp}.{body}"` HMAC-SHA256 scheme `app/services/stripe_webhook.py` uses
  for the billing webhook, deliberately **not** sharing code or secret material with
  it: "a relay credential compromise must never be reachable through, or confused
  with, the billing webhook's own secret and vice versa" (S11).
- `verify_relay_signature(payload, sig_header, secret, secret_previous=None,
  tolerance_seconds=300)` raises on any failure (malformed header, mismatched
  signature, or a timestamp outside the tolerance window) rather than returning a
  boolean a caller could ignore.
- **Key rotation**: two configured secrets, `RELAY_SIGNING_SECRET` (current, tried
  first) and `RELAY_SIGNING_SECRET_PREVIOUS` (previous, tried only if current
  doesn't match), both compared with `hmac.compare_digest` (so accepting PREVIOUS is
  not a timing side-channel on CURRENT). An operator rotates in two zero-downtime
  steps: set PREVIOUS to the current secret and CURRENT to the new one (both
  accepted during overlap), then once every deployment is confirmed signing with the
  new secret, unset PREVIOUS.
- **Honest limitation**: the timestamp-tolerance window defends against a captured
  request replayed *later* (outside the window). A request replayed *within* the
  window is not rejected by this module — it relies on §3 below (idempotent ingest)
  to make an in-window replay harmless rather than a duplicate fill.

## 2. Tenant resolution (the RLS chicken-and-egg)

`ingest_export_event` cannot call `set_tenant_scope` before it knows which tenant an
envelope belongs to, and the inbound `EventEnvelope` itself carries **no**
`tenant_id` field at all — signal-copier has no commercial-tenant concept. The only
source of tenant binding is `ExportStreamRegistration`, an owner-controlled table
(never sender-controlled) keyed by `source_stream`, populated once per real
signal-copier deployment via `register_export_stream` (S6: "Bind permitted stream/
account/tenant pairs from server-controlled registration. Never accept an arbitrary
tenant_id ... from a sender merely because the request is authenticated.").

Sequence, every call:

1. `_registered_tenant_id(session, envelope.source_stream)` — looked up through
   `relay_role`'s one bespoke unscoped-SELECT policy on `export_stream_registrations`
   (ADR-0002). `None` → `UnregisteredStreamError`, never a guessed tenant.
2. `set_tenant_scope(session, tenant_id)` — from this point on, every other
   read/write in the call (including the dedup lookup by `event_id`) goes through
   the ordinary, unmodified `tenant_isolation` RLS policy every other table uses.

## 3. Idempotency

`InboxEvent.event_id` (= `EventEnvelope.event_id`, the producer's own idempotency
key) is the table's primary key.

- Same `event_id`, same `payload_hash` → the existing row is returned; a harmless
  no-op.
- Same `event_id`, **different** `payload_hash` → `EventIntegrityError`, a real
  fault — never resolved by last-write-wins.
- A fresh `event_id` that reuses an already-consumed `(source_stream,
  producer_generation, export_sequence)` slot (unique constraint
  `uq_inbox_events_stream_generation_sequence`) → `SequenceSlotAlreadyConsumedError`.
  This is INT-010's "Producer restored to older database" case surfaced as a clear
  error rather than a raw constraint violation: a producer reusing a sequence number
  it already used within the same generation is never silently applied as new
  financial history.

See ADR-0003 for the full idempotency/ordering rationale.

## 4. Ordering and gap-wait

Two separate high-water marks per stream, folded onto `InboxEvent`:

- `received_at` — set the instant a (validated, tenant-resolved) envelope is
  durably inserted, regardless of order.
- `applied_at` — set only once `export_sequence` is next in line, computed by
  `_next_expected_sequence`: one past the highest already-**applied** sequence for
  `(tenant_id, source_stream, producer_generation)`, floored at
  `_snapshot_floor(...) + 1` when an activated bootstrap snapshot exists (§5).

An event that arrives ahead of its predecessor is **parked**: stored, `applied_at`
left `NULL`, no `parked_reason` (a gap-wait is distinct from a named parked reason —
see §6). `_apply_and_cascade` applies the just-arrived event, then repeatedly checks
whether the row now sitting at the newly-expected sequence was already received
earlier and applies it too, until it hits a genuine gap or runs out of received
rows — so redelivering the one missing sequence number unblocks an arbitrarily long
run of already-received later events without needing any of them redelivered again.

`producer_generation` scopes all of this: `export_sequence` is only monotonic
*within* one generation (bumped when a stream is re-bootstrapped from a fresh
snapshot). `_established_generation` finds the generation of a stream's own already-
applied history; an envelope claiming an **older** generation than established is a
detected rollback (`generation_rollback_detected`); a **newer**, unverified one
requires a reconciled bootstrap this build does not have
(`new_generation_requires_bootstrap`). Neither is ever silently applied.

## 5. Bootstrap-snapshot protocol

See ADR-0006 for the full design. In summary: `POSITION_SNAPSHOT` envelopes are
pages of a manifest (`manifest_id`, `page_count`, `cutoff_sequence`), received and
validated against siblings unconditionally (never gated by the ordinary sequence
gate, or an out-of-order page could deadlock the manifest's own completeness check).
The manifest activates — real `Book.PLATFORM` baseline entries written, every page's
`applied_at` set, `cutoff_sequence` becomes a hard floor, already-received rows at
or below the floor get swept as covered (no double-count), rows above it cascade
forward — the instant its last page arrives, in whatever order.

## 6. `parked_reason` taxonomy

See `docs/database/DATA_DICTIONARY.md` for the authoritative list of every real
`parked_reason` prefix and when each is set. A gap-wait (waiting on a missing lower
`export_sequence`) is parked but carries **no** `parked_reason` of its own — it is
still counted as `PARKED` by `app/services/source_coverage.py`.

## 7. Event-type projections

Dispatched in `_apply_projection`, gated by schema-version support first (an
envelope whose `schema_version` isn't in `_SUPPORTED_SCHEMA_VERSIONS` parks as
`unsupported_schema_version:<version>` before any type-specific handling, per
INT-007's "Unknown event is parked without economic application," never "best-effort
financial coercion"):

| `EventType` | Projection |
|---|---|
| `EXECUTION_APPLIED` | New `Book.PLATFORM` ledger entry (`append_entry`). |
| `SOURCE_RECEIPT` | New `Book.SOURCE` ledger entry, only if the payload carried both quantity and price; resolves a `Sleeve` via `resolve_sleeve_for_source`. Always marked applied even with no ledger row. |
| `FEE` | A ledger **correction** (`append_correction`) against the `EXECUTION_APPLIED` entry sharing the same `{broker}\|{broker_order_id}` correlation key. Parks as `fee_target_not_found:<key>` if that execution hasn't arrived/applied yet — an honest "waiting," not a fault. |
| `ROUTING_ADMISSION_OUTCOME` | Sets `routing_outcome` on the correlated `SOURCE_RECEIPT` row (found by `originating_source_event_id`, that receipt's own `event_id`). Parks as `routing_outcome_target_not_found:<id>` if the receipt hasn't applied yet. |
| `POSITION_SNAPSHOT` | Handled entirely by §5, outside this function. |
| `SOURCE_EVENT` | See the dedicated table below — never a flat "apply/park", one disposition per `SourceEventKind`. |
| anything else | Parks as `unimplemented_event_type:<type>` — permanently, and permanently blocks every later sequence on the same stream (the honest cost of strict ordering). |

### 7a. `SOURCE_EVENT` — one disposition per `SourceEventKind`

Every `SOURCE_EVENT` row gets an indexed `InboxEvent.source_event_native_key`
(`f"{tenant_id}|{source_provider_id}|{source_channel_id}|{source_event_id}"`)
regardless of kind — the one native-provider identity
`signal_platform_contracts.identity.SourceIdentity`'s own docstring says is
stable across redelivery, so a later event can resolve back to this one
without ever guessing from re-parsed text or arrival order.

| `SourceEventKind` | Disposition |
|---|---|
| `ORIGINAL`, `ADD`, `REPLY` | Applied as a no-op (advances the sequence, no second ledger entry) — verified against signal-copier's own adapters: each calls `on_signal` on its own fresh `Signal.id` before exporting this row, so any real instruction it carries is independently booked by its own `SOURCE_RECEIPT`. A `REPLY` with `signal is None` has nothing economic to book regardless. |
| `DELETE`, `CANCEL`, `CLOSE` | Applied as a no-op — the taxonomy's own definition guarantees none of these ever carries bookable instrument/side/quantity content. |
| `EDIT` | Correlates `source.original_source_event_id` against `source_event_native_key`. Resolves to a real, already-applied `SOURCE_EVENT` row for the same tenant → applied as a no-op (its revised content, if any, is independently booked by its own `SOURCE_RECEIPT`). Doesn't resolve (missing reference, or no match) → parks as `edit_without_resolvable_target:<missing_original_source_event_id \| the unresolved key>`. |
| `TARGET_UPDATE`, `STOP_UPDATE` | Always parks as `source_event_kind_not_ledger_representable:<kind>` — `LedgerEntry` has no stop-loss/target column at all, so there is nowhere to write this even with a perfect correlation. |

See `docs/KNOWN_ISSUES.md`'s own SOURCE_EVENT section for what is
deliberately NOT built here (e.g. a `DELETE`/`CANCEL`/`CLOSE` does not
mark the earlier `Book.SOURCE` entry it retracts as void).
