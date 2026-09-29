# ADR-0001: Postgres with row-level security for multi-tenant isolation

## Status

Accepted. Implemented in `alembic/versions/f1cbfcc4819c_initial_schema.py` (schema)
and `alembic/versions/04c418cbb547_row_level_security_and_append_only_.py` (RLS +
append-only enforcement), with the live policy logic in `app/db.py`.

## Context

signal-portfolio-commercial is a single Postgres database shared by every tenant
(`tenants` table). Every tenant-scoped table — `memberships`, `customer_profiles`,
`ledger_entries`, `sleeves`, `subscriptions`, `portfolio_versions`, and (as the
schema grew) `release_reviews`, `research_runs`, `eligibility_assessments`,
`support_cases`, `publisher_destinations`, `api_keys`, `integration_configurations`,
`price_versions`, `managed_programs`, `audit_events`, `workspace_settings`,
`portfolio_selections`, `notification_preferences`, `customer_display_preferences`,
`platform_connections`, `copy_mandates`, `export_stream_registrations`,
`inbox_events`, `incidents` (see `app/db.py`'s `_TENANT_SCOPED_TABLES`) — carries a
`tenant_id` column. Application code is trusted to filter by tenant, but a single
missed `WHERE tenant_id = ...` in any query path, present or future, would leak one
tenant's ledger, customer PII, or research into another tenant's session.

Two tables — `products` and `content_documents` — need a different rule: they must
be visible to an anonymous, no-tenant session too, for the public catalog and public
methodology/legal document pages, while still restricting non-published rows to
their owning tenant.

## Decision

Enforce tenant isolation as an actual Postgres constraint, not just an
application-level `WHERE` clause:

- Every tenant-scoped table gets `ALTER TABLE ... ENABLE ROW LEVEL SECURITY` **and**
  `FORCE ROW LEVEL SECURITY`. `FORCE` is required because plain `ENABLE` is bypassed
  by the table owner and by superusers — exactly the role this single-role-per-
  database development setup usually connects as, which would make `ENABLE` alone a
  no-op in practice (`app/db.py::_apply_row_level_security` docstring).
- A single generic policy, `tenant_isolation`, is created on each such table:
  `USING (tenant_id = current_setting('app.tenant_id', true))`. The `true` argument
  makes an unset setting resolve to `NULL` rather than raising, and `NULL =
  tenant_id` is never true in SQL — so an unscoped session sees zero rows,
  fail-closed, not "everything" (`enable_row_level_security` docstring).
- `app/db.py::set_tenant_scope` sets `app.tenant_id` via `set_config` (a bound
  parameter, not string-built `SET LOCAL`, which cannot take bind parameters at all
  and would otherwise be an injection point) at the start of every request/job that
  touches a tenant-scoped table.
- `products` and `content_documents` get their own bespoke policies
  (`product_visibility`, `content_document_visibility`) that OR in a
  `lifecycle_state = 'PUBLISHED'` / `state = 'PUBLISHED'` clause, deliberately kept
  out of the generic `_TENANT_SCOPED_TABLES` list so the uniform policy is never
  weakened to accommodate this one exception.
- The RLS migration itself pins a **frozen, historical snapshot** of table names
  (`_TABLES_AT_THIS_REVISION`) rather than importing `app.db`'s live, ever-growing
  `_TENANT_SCOPED_TABLES` constant — a real `alembic upgrade head` run once failed
  with `UndefinedTable: relation "release_reviews" does not exist` when a migration
  defaulted to the current tuple before that table existed at that point in history.

## Consequences

- Tenant isolation holds even against a bug in application code, an ORM query
  missing a filter, or a future ad-hoc SQL script run against the database — the
  database itself refuses to return another tenant's rows once RLS is force-enabled.
- Every request/job path must call `set_tenant_scope` before touching a tenant-scoped
  table, or it sees nothing (fail-closed) rather than silently seeing everything.
- A caller-supplied tenant ID (e.g. from a browser) must never be trusted as proof of
  access on its own — it is only ever the input to a call that itself performs real
  authorization and then calls `set_tenant_scope`.
- Every Alembic migration that touches `_apply_row_level_security`/
  `_apply_append_only` must pass its own frozen table list, not the live module
  constant, or replays of migration history will break against tables that did not
  exist yet at that point.
- `products`/`content_documents` intentionally sit outside the uniform policy; a
  future engineer adding tenant scoping to a new table must add it to
  `_TENANT_SCOPED_TABLES` (and to the next migration's own frozen snapshot), not
  assume the generic policy already covers it.
