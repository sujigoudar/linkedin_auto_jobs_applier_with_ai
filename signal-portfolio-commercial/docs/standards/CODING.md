# Coding Standards

Evidenced from the actual codebase (`app/`, `tests/`), not aspirational.
Every rule below is cross-checked against a real file and cited.

## 1. Honest disclosure of scope limits

This codebase is disciplined about stating, in the module docstring of any
report/computation, exactly what it does and does not cover -- and never
fabricating or estimating a number it cannot really compute.

The canonical example is `app/services/source_coverage.py`. Its module
docstring:

- Names the exact three dispositions the data model can actually
  distinguish (`LEDGER_RECORDED`, `PARKED`, `RECEIVED_NO_LEDGER_ENTRY`),
  and says plainly that `RECEIVED_NO_LEDGER_ENTRY` is **not** further split
  into "genuinely no quantity/price" vs. "swept by an activated bootstrap
  snapshot", because `InboxEvent` does not persist enough of the original
  payload to tell those apart after the fact.
- States a previously-real gap (`routing_outcome` did not exist) and how
  it was closed, without pretending the fix is now infinitely precise:
  `routing_outcome` stays `None` until the correlated event has actually
  arrived, "never guessed from the receipt's own disposition."
- Explicitly refuses to invent values outside the real outcome vocabulary
  (no `"canceled"`, no `"loss"`/`"commentary"` outcomes) because "no
  cancellation code path exists anywhere ... inventing either would be
  exactly the fabrication this report and this package's own tests
  (INT-035) forbid."

Follow this pattern for any new report, aggregate, or computed metric:

- State in the module (or function) docstring exactly which states/paths
  the underlying data model can distinguish, and which it cannot.
- Never synthesize a value (an outcome, a lineage id, a lifecycle state)
  that the stored data doesn't actually support -- return `None`/omit the
  field instead, and say so.
- When a real gap is closed later, keep the "this used to be a gap, here
  is exactly how it's closed and what's still partial" narrative in the
  docstring rather than deleting the history (see the "Real, now closed,
  gap" paragraph in `source_coverage.py`).
- Cite the exact column/model that backs a claim (e.g. `InboxEvent.
  ledger_entry_id is set`) rather than describing behavior in the
  abstract.

Other examples of the same discipline: `app/services/audit_log.py`'s
docstring states plainly that `append_audit_event` is "the only way a row
is ever created here" and that the DB itself refuses UPDATE/DELETE, not
just app-level convention; `app/rate_limit.py`'s docstring states the rate
limit is a defensive ceiling, not a constraint on legitimate use, and
names the exact condition (multi-process behind a load balancer) under
which the current in-memory store would stop being correct.

## 2. RLS-aware query patterns

Real Postgres row-level security (`app/db.py`), not an application-level
`WHERE tenant_id = ...` filter alone, is what actually isolates tenants.
Any service code that touches a tenant-scoped table must respect these
rules:

- **Call `set_tenant_scope(session, tenant_id)` before any query** that
  should be tenant-scoped. This sets the Postgres session variable
  (`app.tenant_id`) the RLS policies key off of, via `set_config` (a bound
  parameter) -- never build `SET LOCAL app.tenant_id = ...` by hand, since
  Postgres's `SET` statement takes only string literals, not bind
  parameters, which would be a real injection point. In
  `app/api/dashboard_routes.py`, declare
  `scope: TenantScope = Depends(require_tenant_scope)`
  (`app/api/dependencies.py`) rather than calling `set_tenant_scope`
  imperatively in the handler body -- this is now the default way a
  route gets a tenant-scoped session, so a new route that simply
  declares the dependency gets correct isolation with no per-handler
  code. A manual call remains correct only for the dependency's own
  documented exceptions (see its docstring): a handler that
  `session.rollback()`s mid-request and must re-set scope for the query
  that follows, a route with no tenant-scoped query to make, the
  pre-authentication bootstrap flow (ADR-0009), and the relay ingress
  (`app/api/relay_routes.py`, a different restricted-role mechanism
  entirely).
- **Never trust a browser-supplied tenant id as proof of access.** Per
  `app/db.py`'s own docstring (quoting `docs/02`): "Never accept a browser
  tenant ID as proof of access." A tenant id only becomes real once it's
  been authenticated and is the value passed to `set_tenant_scope`.
- **Fail-closed, not fail-open.** An unset `app.tenant_id` must yield zero
  rows, never "everything" -- this is enforced at the Postgres policy
  level (`USING (tenant_id = current_setting('app.tenant_id', true))`,
  which is false for every row when the setting is unset) and verified
  directly by `tests/test_row_level_security.py::
  test_no_tenant_scope_set_means_no_rows_visible_not_all_rows`.
- **`FORCE ROW LEVEL SECURITY`, not just `ENABLE`.** Plain `ENABLE ROW
  LEVEL SECURITY` is bypassed by the table owner; `FORCE` is required
  because most of this app's own connections run as the table owner in
  this single-role-per-database development setup.
- **A table with more than one legitimate visibility rule gets its own
  named policy, not a shared one abused with extra conditions.** `products`
  and `content_documents` each have a bespoke policy
  (`enable_product_visibility_policy`, `enable_content_document_visibility_
  policy`) instead of being added to the generic `_TENANT_SCOPED_TABLES`
  tuple, because they must be visible either to their own tenant OR to
  anyone when `PUBLISHED` -- a rule the generic single-tenant policy can't
  express. Postgres combines multiple PERMISSIVE policies with OR, so a
  second, narrower policy (like `relay_role`'s `relay_stream_lookup`) is
  the right tool for "this one role needs one more way to see this one
  table," not weakening the general policy.
- **When RLS runs inside an Alembic migration** (an already-open,
  uncommitted transaction), call the `_apply_*` function directly on the
  connection, never the `enable_*` wrapper -- the wrapper opens its own
  `engine.begin()` and would not see the migration's own uncommitted DDL.
  A migration must also pass its **own frozen snapshot** of the affected
  table names, never the live, ever-growing module constant
  (`_TENANT_SCOPED_TABLES`/`_APPEND_ONLY_TABLES`) -- a historical
  migration replaying from scratch would otherwise reference a table that
  doesn't exist yet at that point in history. This was caught for real:
  "a fresh `alembic upgrade head` run failed with `UndefinedTable` once
  `release_reviews` was added here and this function still defaulted
  every caller to the current tuple" (`app/db.py`, `_apply_row_level_
  security` docstring).
- **Append-only tables are enforced by a real Postgres trigger**
  (`forbid_ledger_mutation`), not just by code discipline in
  `app/services/ledger.py`. A correction is always a new row, never an
  UPDATE/DELETE, and the database itself rejects the latter even from the
  table owner.
- **Tests that assert RLS must run as the restricted login role**
  (`app_role` / `relay_role` via `tenant_session_factory` /
  `relay_session_factory`), never as the `postgres` superuser used
  everywhere else in `db_session` -- Postgres superusers bypass RLS
  regardless of `FORCE ROW LEVEL SECURITY`, so a test running as superuser
  would pass even with a broken policy.

## 3. Custom exceptions belong to the service module they guard

See `docs/standards/ERROR_HANDLING.md` for the full convention; in short,
every service module that has invalid-input or invalid-transition cases
defines its own `...Error(Exception)` classes at module scope, named for
the exact condition, not a generic `ValidationError`.

## 4. No `logging` module -- audit events are the real log

This codebase does not use Python's `logging` module for
application/business events (there is no `logging.getLogger(...)` call
anywhere in `app/`). See `docs/standards/LOGGING.md`.
