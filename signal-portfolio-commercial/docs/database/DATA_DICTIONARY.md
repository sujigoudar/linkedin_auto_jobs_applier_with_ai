# Data Dictionary — trickiest columns

Field-level semantics for the columns that are easiest to misread or misuse. Every
value/behavior below is taken directly from the owning model's or service's own
docstring — see the cited file for the authoritative source.

## `ledger_entries.evidence_class`

**Source**: `app/models/ledger.py`, type `signal_platform_contracts.EvidenceClass`
(re-exported, not redefined, so this app never carries a second, potentially-
drifting definition of the same values).

Declares, by construction, what *kind* of evidence a ledger entry represents — a
synthetic test fixture, an internal paper trade, a hypothetical backtest, an
actually observed owner/follower execution, or a platform's own reported model
result. A dashboard must never have to guess this from context, and a bridge
importing an observation from signal-copier must never leave it implicit.

- **Nullable at the database level only** so an additive migration never breaks a
  pre-existing row.
- Every code path that actually writes a ledger entry in this application
  (`append_entry`/`append_correction`) requires it as a real, non-optional argument.
- `NULL` on a real row means exactly one thing: "written before this column
  existed." It never means "no evidence class applies" — there is no such row from
  this codebase going forward.

## `inbox_events.parked_reason`

**Source**: `app/models/integration_inbox.py`, set by
`app/services/integration_inbox.py`.

`NULL` for every normally-applied row **and** for a gap-wait row (an out-of-order
receipt sitting behind a still-missing lower `export_sequence` — see
`_next_expected_sequence`'s own docstring). A gap-wait is exactly as un-applied as a
named parked reason, but intentionally carries no reason string of its own.

Real, machine-parseable prefixes actually set by this build (a caller like
`app/services/integration_status.py`/`source_coverage.py` groups on the prefix
before any `:<detail>` suffix):

| Prefix | Set when | Permanent? |
|---|---|---|
| `unsupported_schema_version:<version>` | The envelope's `schema_version` is not in `_SUPPORTED_SCHEMA_VERSIONS` (exact match only — no semver "compatible minor bump" guessing). Checked before any type-specific dispatch. | Permanently un-applied for this row; also blocks every later `export_sequence` on the same stream (see ADR-0003). |
| `unimplemented_event_type:<event_type>` | The `EventType` has no implemented payload model yet in this build. | Same permanent-block behavior as above. |
| `fee_target_not_found:<broker>\|<broker_order_id>` | A `FEE` event arrived but no matching applied `EXECUTION_APPLIED` row shares its correlation key yet. | Not permanent — resolves once the matching execution applies and this row (or a redelivery of it) is reprocessed. |
| `routing_outcome_target_not_found:<originating_source_event_id>` | A `ROUTING_ADMISSION_OUTCOME` event's target `SOURCE_RECEIPT` row hasn't applied yet. | Not permanent — same resolve-on-reprocess pattern. In the ordinary flow this should be unreachable (both event types share a stream, so the outcome's higher sequence can't apply before the receipt's lower one already has), but is kept as a real, checked defense. |
| `generation_rollback_detected:...` | An envelope claims a `producer_generation` **older** than this stream's already-established generation (INT-010 "Producer restored to older database"). | Permanent for this row — a real rollback is never silently applied. |
| `new_generation_requires_bootstrap:...` | An envelope claims a **newer** `producer_generation` than established, without a reconciled bootstrap. | Permanent for this row in this build (no reconciled-bootstrap path exists yet). |
| `manifest_metadata_mismatch:<manifest_id>` | A `POSITION_SNAPSHOT` page disagrees with an already-received sibling page on `page_count`/`cutoff_sequence`. | Permanent for this page. |
| `manifest_generation_mismatch:<manifest_id>` | A `POSITION_SNAPSHOT` page claims a different `producer_generation` than an already-received sibling. | Permanent for this page. |
| `edit_without_resolvable_target:<missing_original_source_event_id \| key>` | A `SOURCE_EVENT` of kind `EDIT` either has no `source.original_source_event_id` at all, or that reference doesn't resolve (via `source_event_native_key`) to any already-applied `SOURCE_EVENT` for this tenant. | Not permanent in principle (resolves once/if a matching original is received and reprocessed), but if the reference is genuinely missing or wrong it never resolves. |
| `source_event_kind_not_ledger_representable:<target_update\|stop_update>` | A `SOURCE_EVENT` of kind `TARGET_UPDATE`/`STOP_UPDATE` — `LedgerEntry` has no stop-loss/target column, so there is nowhere to write this even with a correct correlation. | Permanent in this build (needs a ledger-model change, not a correlation fix). |

None of these is ever "resolved" by silently coercing or guessing a value — every
one is an honest, visible "not applied yet, and here's exactly why."

## `inbox_events.routing_outcome`

**Source**: `app/models/integration_inbox.py`; set by the
`ROUTING_ADMISSION_OUTCOME` branch of `_apply_projection`.

Meaningful **only** on a `SOURCE_RECEIPT` row. `NULL` until a real, later, correlated
`ROUTING_ADMISSION_OUTCOME` event (by `originating_source_event_id` = this row's own
`event_id`) has been received and applied — never guessed from this row's own
disposition alone. This column is what closed the gap
`app/services/source_coverage.py`'s module docstring used to name before it existed.

Real values are exactly signal-copier's own routing/admission vocabulary
(`signal_platform_contracts.payloads._KNOWN_ROUTING_OUTCOMES`): `not_routed`,
`disabled_by_settings`, `admitted_filled`, `admitted_unfilled`, `rejected`, `error`.
This vocabulary explicitly does **not** include `canceled` (no cancellation code
path exists anywhere in signal-copier's engine or broker adapters) or `loss`/
`commentary` (neither is a routing outcome at all — a loss is a P&L fact computed
later from closed positions, never a state routing/admission reports). Inventing
either of those would be exactly the fabrication `source_coverage.py` and INT-035's
own tests forbid.

## `inbox_events.source_event_native_key`

**Source**: `app/models/integration_inbox.py`; set by the `SOURCE_EVENT` branch
of `_apply_projection` (`app/services/integration_inbox.py::_source_event_native_key`).

Set on **every** `SOURCE_EVENT` row, regardless of `SourceEventKind` (even one
that ends up parked, e.g. `TARGET_UPDATE`) — `f"{tenant_id}|{source_provider_id}|
{source_channel_id}|{source_event_id}"`, the one native-provider identity
`signal_platform_contracts.identity.SourceIdentity`'s own docstring says is
stable across redelivery ("true deduplication and edit/delete/reply correlation
both key off this pair, never off re-parsed message text"). Tenant-scoped on
purpose: two tenants whose adapters happen to relay the same public channel
must never let one tenant's `EDIT` resolve against the other's `ORIGINAL`. A
later `SourceEventKind.EDIT` names its target by `source.
original_source_event_id`, which this same formula turns into the exact key to
look up — never a looser match (e.g. "any row on this provider"). `NULL` for
every non-`SOURCE_EVENT` row.

## `ledger_entries.sleeve_id`

**Source**: `app/models/ledger.py`; set by `resolve_sleeve_for_source`
(`app/services/sleeve_mapping.py`), consumed in the `SOURCE_RECEIPT` branch of
`integration_inbox.py::_apply_projection`.

Meaningful only for `book == Book.SOURCE` entries. `NULL` means "no `Sleeve` was
admitted for this exact `(provider, analyst, parser_version)` tuple yet" — never
"the whole account" and never some other sleeve. No `FOREIGN KEY` constraint: a
sleeve can be redefined or retired later without invalidating the historical fact
that *this* entry matched it at the time it was recorded — ledger history is
append-only (ADR-0008) and must survive that.

## `ledger_entries.external_observation_id`

**Source**: `app/models/ledger.py`.

Meaningful only for `book == Book.FOLLOWER` entries — the real observation-connector
boundary (S12 step 6, `app/services/follower_observation.py`, not yet built).
The observation source's own stable identifier for this fill (e.g. a Collective2
"TradeId" or an eToro "PositionID"). `NULL` for every other book. This is the
idempotency key a redelivered/re-polled observation of the *same* real fill will be
deduplicated by — the same "same identity, harmless re-detect" contract
`inbox_events.event_id` already gives the private-relay boundary (§ ingest), applied
here to the third-party-observation boundary instead. Nothing in this build
populates this column yet.

## Related, worth knowing

- `ledger_entries.follower_connection_id` — the sibling column to
  `external_observation_id`: which customer's `PlatformConnection` a `FOLLOWER`-book
  entry is an authorized observation *of*. Also unpopulated today, also no FK, for
  the same historical-survivability reason.
- `ledger_entries.originating_analyst_id` — carried straight through from
  `ExecutionAppliedPayload.originating_analyst_id`/`SourceReceiptPayload.source.
  analyst_id`. `NULL` means "not attributed to a specific analyst" — its own real
  bucket in `analyst_attribution.py`, never folded into another analyst's numbers
  (INT-026).
- `inbox_events.execution_correlation_key` — `f"{broker}|{broker_order_id}"`, set
  only on an applied `EXECUTION_APPLIED` row. The one shared identity a later,
  separately-delivered `FEE` event for the same execution can correlate against —
  neither payload carries the other's `event_id` or this app's `ledger_entry_id`, so
  this column is the only link between the two.
