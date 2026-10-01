# Ledger Model Design

Source of truth: `app/models/ledger.py`, `app/services/ledger.py`,
`app/services/integration_inbox.py`, `app/services/platform_performance.py`. See
also ADR-0004 (four books) and ADR-0008 (append-only enforcement).

## The four books

`LedgerEntry.book` (`app/models/ledger.py::Book`):

| Book | Meaning | Populated by |
|---|---|---|
| `SOURCE` | What the provider/analyst originally recommended. | `SOURCE_RECEIPT` ingest, only when the recommendation carried both quantity and price. |
| `MODEL` | The canonical portfolio-version model's own instructions. | Not yet populated by any path in this build (reserved). |
| `PLATFORM` | The owner's actual discretionary/strategy account. | `EXECUTION_APPLIED` ingest, and activated bootstrap-snapshot baselines. |
| `FOLLOWER` | A specific customer's actual executed account. | Not yet populated (no real observation connector exists yet — `follower_connection_id` is ready for it). |

"An alert delivered is not an executed trade" (SOURCE vs. PLATFORM). "Platform
strategy model performance is not customer actual performance" (PLATFORM vs.
FOLLOWER). The four books are never summed together as one number.

## LedgerEntry columns

| Column | Type | Notes |
|---|---|---|
| `entry_id` | String (UUID) PK | |
| `tenant_id` | String, indexed | RLS-scoped (ADR-0001). |
| `book` | Enum | See above. |
| `instrument` | String | |
| `side` | Enum (`buy`/`sell`) | |
| `quantity`, `price`, `multiplier` | `Numeric(28,10)` | Money/quantity fields are always `Decimal`, never `Float` — "do not calculate exact money in floating point." |
| `currency` | String | |
| `fee` | `Numeric(28,10)`, nullable | `NULL` means "not yet known," never zero — "importing a zero default is not proof of a verified zero fee." A caller that genuinely knows the fee is zero passes `Decimal(0)` explicitly. |
| `event_time` | DateTime, tz-aware | When the economic fact happened. |
| `receipt_time` | DateTime, tz-aware, default now | When this service recorded it. |
| `source_authority` | String | Free-text provenance tag, e.g. `signal-copier-relay:<producer_id>` or `signal-copier-snapshot-relay:<producer_id>`. |
| `evidence_class` | Enum (`signal_platform_contracts.EvidenceClass`), nullable | See `DATA_DICTIONARY.md`. `NULL` only for rows written before the column existed. |
| `reconciliation_state` | Enum (`unreconciled`/`reconciled`/`disputed`), default `unreconciled` | |
| `follower_connection_id` | String, nullable, indexed | Meaningful only for `book == FOLLOWER`. No FK — a connection can be removed later without invalidating the historical fact of an observation through it. |
| `external_observation_id` | String, nullable, indexed | Third-party fill identifier (e.g. a Collective2 TradeId), for `book == FOLLOWER` observation dedup. See `DATA_DICTIONARY.md`. |
| `originating_analyst_id` | String, nullable, indexed | See `DATA_DICTIONARY.md` / `ATTRIBUTION.md`. |
| `sleeve_id` | String, nullable, indexed | Which `Sleeve` this `SOURCE`-book entry matched at ingest time. No FK, same reasoning as `follower_connection_id`. |
| `correction_of` | FK → `ledger_entries.entry_id`, nullable | Set only on a correction row. |
| `created_at` | DateTime, tz-aware, default now | |

## Append-only enforcement

`ledger_entries` (along with `portfolio_versions`, `portfolio_version_sleeves`, and
`audit_events`) has a `BEFORE UPDATE OR DELETE` trigger
(`append_only_guard` → `forbid_ledger_mutation()`) installed at the database level —
see ADR-0008. A mistake is never fixed by editing or deleting the original row; it
is fixed by appending a new row.

## How a ledger entry gets created from an ingested event

All real ledger writes go through `app/services/ledger.py`'s `append_entry`/
`append_correction` — never a raw `session.add(LedgerEntry(...))` elsewhere in the
codebase — so the append-only discipline and required fields (e.g. `evidence_class`)
are enforced in one place.

1. **`EXECUTION_APPLIED`** (`integration_inbox.py::_apply_projection`): validates
   `ExecutionAppliedPayload`, calls `append_entry(book=PLATFORM, instrument=...,
   side=..., quantity=payload.filled_quantity, price=payload.filled_price,
   currency=..., multiplier=..., fee=payload.fee, event_time=envelope.event_time,
   source_authority=f"signal-copier-relay:{producer_id}",
   evidence_class=envelope.evidence_class,
   originating_analyst_id=payload.originating_analyst_id)`. The resulting
   `entry_id` is written back onto `InboxEvent.ledger_entry_id`, and
   `execution_correlation_key` (`{broker}|{broker_order_id}`) is cached for a later
   `FEE` event to correlate against.

2. **`SOURCE_RECEIPT`** (same function): validated `SourceReceiptPayload`.
   `inbox_event.source_event_native_key` is always set first (Track 41, the
   same formula Track 35 built for `SOURCE_EVENT`), so a LATER revision can
   resolve back to this exact row. Then, only if `quantity` **and** `price` are
   both present:
   - If `source.original_source_event_id` is **unset** (a brand-new economic
     fact, never a revision): resolves a `Sleeve` via
     `resolve_sleeve_for_source(tenant_id, source_provider_id, analyst_id,
     parser_version)`, then `append_entry(book=SOURCE, ..., sleeve_id=
     sleeve.sleeve_id if resolved else None)` — exactly as before.
   - If it **is** set (Track 41: signal-copier dedupes an edited message by
     exact `(channel_id, message_id, revision_id)`, so an edit always arrives
     as its OWN, independent `SOURCE_RECEIPT` — never a mutation of the
     original's): resolves it, via the identical tenant-scoped native-key
     mechanism `SourceEventKind.EDIT` uses, to an earlier, already-applied
     `SOURCE_RECEIPT` row that booked a real ledger entry. If resolved, books
     `append_correction(original_entry_id=original.entry_id, quantity=
     source_payload.quantity, price=source_payload.price, instrument=...,
     side=..., currency=..., multiplier=..., event_time=envelope.event_time,
     source_authority=...)` — the REVISED values, so the SAME economic fact is
     superseded, never double-booked as a second independent `SOURCE` entry.
     If unresolved, parks as `edit_without_resolvable_target:<key>` — never
     guessed.

   A receipt with neither quantity nor price still marks the `InboxEvent`
   applied — it just produces no ledger row (an honest partial-coverage
   outcome, not a parked/incomplete one).

3. **`FEE`**: correlates by `{broker}|{broker_order_id}` against an already-applied
   `EXECUTION_APPLIED` row's `ledger_entry_id`. If found, `append_correction(
   original_entry_id=original.entry_id, quantity=original.quantity,
   price=original.price, event_time=envelope.event_time, source_authority=...,
   fee=payload.fee)` — a new row, `correction_of` pointing at the original, carrying
   the now-known fee (`instrument`/`side`/`currency`/`multiplier` are left at
   their `append_correction` default of `None`, i.e. inherited unchanged — `FEE`
   never revises any of those). If no matching execution has applied yet, parks as
   `fee_target_not_found:<key>` rather than being coerced onto some other entry.

6. **`SOURCE_EVENT`, kind `TARGET_UPDATE`/`STOP_UPDATE`** (ADR-0011): writes a
   `SourceStopTargetRevision` row (`app/services/source_stop_target_revisions.py
   ::append_stop_target_revision`) — never a `LedgerEntry` (no quantity/price
   economic fact). See `DATA_DICTIONARY.md`'s own entry for the table.

4. **`POSITION_SNAPSHOT`** (bootstrap activation, `_activate_snapshot_and_reconcile`,
   see `INGEST_PIPELINE.md` §5): one `append_entry(book=PLATFORM, ...,
   source_authority=f"signal-copier-snapshot-relay:{producer_id}",
   event_time=<earliest event_time across the manifest's pages>)` per
   `PositionSnapshotEntry`, across every page — "position snapshot is not an extra
   execution."

5. **`ROUTING_ADMISSION_OUTCOME`** never creates a ledger row — it only sets
   `InboxEvent.routing_outcome` on the correlated `SOURCE_RECEIPT` row.

## Downstream readers

- `app/services/platform_performance.py::compute_book_performance` — one running
  volume-weighted average cost per `(tenant, book, instrument)`; correct for
  current holdings/basis, blind to which analyst contributed what.
- `app/services/analyst_attribution.py::compute_analyst_attribution` — the FIFO-lot
  replay that recovers per-analyst attribution; see `ATTRIBUTION.md`. Its
  `account_total_realized_pnl` always reconciles to `compute_book_performance`'s own
  aggregate for the same tenant/book.
- `app/services/source_coverage.py::compute_source_coverage` — cross-references
  every `SOURCE_RECEIPT` `InboxEvent`'s disposition (whether it produced a ledger
  row) against its routing outcome; see `DATA_DICTIONARY.md`.

**Track 41**: every reader above (plus `customer_performance_report.py::
compute_customer_equity_series`) replays `platform_performance.
load_ordered_root_entries`'s own real query, then resolves each root entry's
REAL, effective fields through the shared `platform_performance.
effective_fill(entry, correction)` helper — a correction may now revise
`instrument`/`side`/`quantity`/`price`/`multiplier`/`fee`, not only `fee` (the
only thing it could ever revise before this track), so no reader may read
`entry.instrument`/`.side`/`.quantity`/`.price`/`.multiplier`/`.fee` directly
any more without first checking for a correction — `effective_fill` is the one
place that decision is made, so every reader agrees.
