# ADR-0002: A separate, restricted `relay_role` for the export-ingest boundary

## Status

Accepted. Implemented in `alembic/versions/3f7a19c02b8e_add_relay_role_access.py`,
`app/db.py::_apply_relay_role_access`, and consumed by
`app/services/integration_inbox.py::ingest_export_event`.

## Context

signal-copier (the upstream, single-owner execution engine) exports its own event
stream — executions, source receipts, fees, routing outcomes, position snapshots —
through a private outbox. A restricted relay worker (`app/api/relay_routes.py`) is
the one thing standing between that outbox and this commercial platform's database.
Per the Signal Platform Integration Correction Pack's `INTEGRATION_DECISION.md` S11:
"A stolen telemetry credential cannot become a trading credential." The relay
ingress must never be able to reach — let alone mutate — the tables that carry real
command authority (subscriptions, billing, publication intents, membership/roles,
platform connections, copy mandates, etc.), even if the relay's own credential is
fully compromised.

There is also a genuine chicken-and-egg problem: `ingest_export_event` must look up
`export_stream_registrations` **by `source_stream`** to discover which tenant an
inbound envelope belongs to, before any `app.tenant_id` session scope can be set
(ADR-0001). The generic `tenant_isolation` RLS policy only ever permits a session
already scoped to its own tenant — it structurally cannot serve this one lookup.

## Decision

Create `relay_role`: a distinct, non-superuser, `NOBYPASSRLS` Postgres login role,
never `app_role`, never the browser-facing connection. It is granted access to
exactly three tables, and nothing else:

- `export_stream_registrations`: `SELECT` only, satisfied by one bespoke additional
  **permissive** policy, `relay_stream_lookup ... USING (true)`, scoped to
  `relay_role` alone. Postgres combines multiple permissive policies on the same
  table with OR, so this does not weaken `tenant_isolation` for `app_role` or any
  other role — it only gives `relay_role` a second way to satisfy this one table's
  SELECT, before any tenant scope exists.
- `inbox_events`: `SELECT, INSERT, UPDATE`. `UPDATE` is required (not just
  `SELECT`/`INSERT`) because `ingest_export_event` inserts the row first and later
  updates its own `applied_at`/`ledger_entry_id`/`routing_outcome` columns once
  projection completes — never touching any other row.
- `ledger_entries`: `INSERT` only, so the relay can append a real ledger projection
  of an ingested event. No `UPDATE`/`DELETE` grant exists at all (and none would
  matter: `ledger_entries` is append-only-enforced at the trigger level regardless
  of role — see ADR-0008).

Every other tenant-scoped table — subscriptions, billing, publication intents,
platform connections, copy mandates, memberships/roles, everything with real
command authority — is reached by `relay_role` **not at all**: it holds no grant on
them whatsoever. `ingest_export_event` resolves the tenant through the one bespoke
lookup, calls `set_tenant_scope` immediately after, and every subsequent read/write
in that call (including the redelivery dedup lookup by `event_id`) goes through the
same, unmodified `tenant_isolation` policy every other role and table uses.

## Consequences

- A fully compromised relay credential can forge or replay ingest events (bounded by
  HMAC verification and idempotency — see ADR-0003/`INGEST_PIPELINE.md`), and can
  append ledger rows, but it categorically cannot touch billing, publication,
  membership, or any other command-authority table — there is no grant path there at
  all, independent of any application-level bug.
- The one intentional asymmetry — `relay_role` can read every tenant's
  `export_stream_registrations` row unscoped — is narrow by construction (one
  column-free lookup table, SELECT only) and is the documented, load-bearing reason
  this role exists instead of reusing `app_role`.
- A future engineer must never grant `relay_role` write access to a new
  tenant-scoped table without updating this ADR and its own migration — the
  boundary's value is that its grant list is short and exhaustively documented, not
  merely "narrower than `app_role`."
