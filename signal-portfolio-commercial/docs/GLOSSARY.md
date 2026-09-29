# Glossary

Real domain terms, pulled from the codebase's own docstrings. Where a
definition is quoted, it is verbatim from the module cited.

## The four books

`app/models/ledger.py::Book` — an append-only `ledger_entries` table
spans exactly four independent books, never blended:

- **SOURCE** — "what the provider/analyst originally recommended." A
  `SOURCE_RECEIPT` from signal-copier, applied only when it carried a
  real quantity AND price. Never claims an execution happened.
- **MODEL** — the canonical portfolio-version model's own instructions
  (not populated by any live path in this build yet).
- **PLATFORM** — "the owner's actual discretionary/strategy account."
  Populated from `EXECUTION_APPLIED` and `POSITION_SNAPSHOT` relay
  events.
- **FOLLOWER** — "a specific customer's actual executed account."
  Meaningful only with a real `follower_connection_id` — "a copied
  model alert or subscription alone cannot populate this book." Not yet
  populated by any live connector in this build
  (`app/services/follower_observation.py` is the reserved boundary).

## `relay_role` vs `app_role`

Two distinct, non-superuser Postgres login roles (`app/db.py`):

- **`app_role`** — the ordinary browser/dashboard connection
  (`app.state.session_factory`). Full read/write on every table,
  gated only by RLS.
- **`relay_role`** — the restricted relay-worker connection
  (`app.state.relay_session_factory`, `_apply_relay_role_access`).
  `LOGIN NOSUPERUSER NOBYPASSRLS`, granted **only**: unscoped `SELECT`
  on `export_stream_registrations` (to discover a tenant before any
  scope is set), and (once scoped) `SELECT/INSERT/UPDATE` on
  `inbox_events` plus `INSERT` on `ledger_entries`. No other table,
  no command authority, no broker/order code path exists for it —
  "a stolen telemetry credential cannot become a trading credential."

## RLS (Row-Level Security)

Postgres's `ENABLE ROW LEVEL SECURITY` + `FORCE ROW LEVEL SECURITY` +
a `CREATE POLICY` on every tenant-scoped table. **FORCE** is load-bearing:
plain `ENABLE` is bypassed by the table owner, which is exactly the role
most queries run as here. The generic policy
(`tenant_isolation`) permits a row only when
`tenant_id = current_setting('app.tenant_id', true)` — **fail-closed**:
no scope set means no visibility, never "everything." Two tables
(`products`, `content_documents`) get a bespoke OR'd policy so an
anonymous, unscoped session can see their `PUBLISHED` rows too.

## `export_stream_registrations`

The owner-controlled, server-side binding of a signal-copier
`source_stream` to exactly one tenant (`app/models/integration_inbox.py`).
An inbound envelope carries no `tenant_id` field at all — this table is
the *only* source of truth for which tenant an event belongs to. "Never
accept an arbitrary tenant_id, book or account from a sender merely
because the request is authenticated."

## `inbox_events`

One row per relay-delivered event, ever (`event_id` is the primary
key — a redelivery is a harmless re-detect, never a second row). Tracks
two separate high-water marks: `received_at` (durably stored, any
order) and `applied_at` (its real projection — a ledger entry or a
routing-outcome update — has been applied, only once its
`export_sequence` is next in line).

## `parked_reason`

Set (and `applied_at` left `NULL`, permanently, for this row and every
later `export_sequence` on the same stream) when a row is received but
cannot be honestly applied yet. Named prefixes:
`unsupported_schema_version:`, `unimplemented_event_type:`,
`generation_rollback_detected:`, `new_generation_requires_bootstrap:`,
`manifest_metadata_mismatch:`, `manifest_generation_mismatch:`. An
ordinary out-of-order gap wait (a later sequence sitting behind a
still-missing lower one) is *also* unapplied but carries **no**
`parked_reason` of its own — it is a real, honest "waiting," not a
named fault.

## FIFO-lot attribution

`app/services/analyst_attribution.py`'s replay algorithm (ported from
signal-copier's own `provider_value.py::compute_provider_value`): per
(tenant, book, instrument), an ordered list of open "lots," each tagged
with its own `originating_analyst_id`. A reducing fill consumes lots
**oldest-first**, crediting realized P&L to *each consumed lot's own*
analyst — never the analyst on the closing fill itself, and never split
arbitrarily between two analysts sharing a symbol. A "completed
episode" is exactly one lot from open to full closure; its `pnl` is the
sum of every real closing fill's realized P&L against that lot.

## Bootstrap snapshot

A `POSITION_SNAPSHOT` manifest — a multi-page bundle of a stream's
current positions at some `cutoff_sequence`, used to re-establish a
baseline (e.g. after a new `producer_generation`). Received **outside**
the ordinary sequence gate so a manifest can activate the instant its
last page arrives, regardless of arrival order. Once activated, its
`cutoff_sequence` becomes a hard floor: no event at or below it is ever
double-counted against the snapshot's own baseline entries.

## Evidence manifest

`app/services/evidence_manifest.py`'s AD-18 export: a synchronous,
**verbatim** (never summarized/redacted/reordered) bundle of matching
`AuditEvent` rows plus a `content_hash`
(`signal_platform_contracts.compute_payload_hash` over the exact
ordered row list) so the export's own integrity can be verified later.
Generating the manifest is itself logged as a new `AuditEvent`
(`export_evidence_manifest`).

## `evidence_class`

A required field on every `LedgerEntry` (`signal_platform_contracts.EvidenceClass`)
declaring what kind of evidence an entry is — synthetic test fixture,
internal paper trade, hypothetical backtest, an actually observed
owner/follower execution, or a platform's own reported model result.
"There is no honest default for 'what kind of evidence is this.'"

## `producer_generation`

Bumped by the producer when a stream is re-bootstrapped from a fresh
snapshot. `export_sequence` is only ever monotonic **within** one
generation — a new generation's sequence 0 is not "behind" the previous
generation's sequence 100, it is a different numbering entirely. Once a
tenant's stream has an established generation (from its own applied
history), an envelope claiming an older generation is a detected
rollback; a newer one requires a reconciled bootstrap this build does
not have — neither is ever silently applied.

## Route qualification (per-role authorization)

`app/services/permissions.py::_ALLOWED` — an explicit, closed
action→role allow-list, not a hierarchy. An action with no entry denies
**every** role, including `OWNER`. Distinguishes read/view authority
from write/manage authority for the same subject even when granted to
overlapping roles (e.g. `view_rights_register` vs. `grant_rights`;
`view_audit_log` vs. `manage_incidents`).
