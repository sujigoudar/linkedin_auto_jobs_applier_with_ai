# Assumptions

Real, load-bearing assumptions this codebase makes — stated honestly,
because several of them are the actual boundary between "this is a
real tenant-isolation guarantee" and "this would break under a
different deployment topology."

## Single Postgres instance

`app/db.py`'s `make_engine` builds one engine against
`config.COMMERCIAL_DATABASE_URL`. There is no sharding, no read
replica routing, and no multi-database tenant-per-database model —
every tenant's data lives in the same physical database, isolated
only at the row level. This is a real architectural choice, not an
oversight: the four-book append-only ledger, the RLS policies, and the
`relay_role` boundary are all designed around one shared schema, one
shared set of Postgres roles, and Postgres's own session-scoped
`current_setting('app.tenant_id')` mechanism.

## Row-level security is the sole tenant-isolation boundary

There is no separate application-level tenant filter layered on top of
RLS as a defense-in-depth measure — `app/db.py`'s own comment is
explicit about why: "`FORCE ROW LEVEL SECURITY` is what makes that
hold true for the table owner too... which would make this a no-op
against the very role these tables are usually queried through in this
single-role-per-database development setup." The assumption is that
`FORCE ROW LEVEL SECURITY` plus a policy keyed on `current_setting
('app.tenant_id', true)`, applied to every table in
`_TENANT_SCOPED_TABLES`, is sufficient — there is no second,
independent check that would catch a bug in that one mechanism. This
is why `tests/test_relay_role_access.py` and the equivalent RLS tests
run against the *actual* restricted Postgres login, not a mock: the
assumption's correctness is only as good as that one enforcement
point, so it's proven against the real thing, not asserted in
application code.

A corollary: `set_tenant_scope` (whatever sets
`app.tenant_id` for a session) must be called correctly on every
connection before any tenant-scoped query runs — a call site that
forgets this doesn't get a second application-level check to catch it;
RLS with an unset `app.tenant_id` fails closed (zero rows), which
fails safe but may fail confusingly if the real bug is "forgot to
scope the session" rather than "queried the wrong tenant."

## `relay_role` is the one deliberate, narrow exception to per-tenant scoping

The restricted relay ingress needs to look up
`export_stream_registrations` *before* it knows which tenant a
message belongs to — this is a real, load-bearing exception to "every
query is tenant-scoped first," carved out as narrowly as possible (one
extra permissive policy, one table, `SELECT` only) and proven not to
weaken the generic `tenant_isolation` policy for any other role
(Postgres combines multiple `PERMISSIVE` policies with `OR`, so this
adds a second way in for `relay_role` specifically, never a bypass for
`app_role`).

## Postgres-only, always — no SQLite fallback assumed anywhere

`app/db.py`'s comment states this directly: "This process never opens
[signal-copier's SQLite execution store] and that process never opens
this one." The entire test suite assumes a real, local
`postgresql-16` binary is available (`tests/conftest.py`'s
`postgres_cluster` fixture). Nothing in this codebase should be
written to also work against SQLite — that would risk masking a real
RLS/append-only-trigger bug behind a database that doesn't implement
either feature.

## No live financial authority exists in this build

Every publisher/broker/payment adapter assumes it will never be asked
to transmit for real in this environment — `collective2_publisher.py`,
`etoro_adapter.py`, and `stripe_webhook.py` are all built and tested
against synthetic data with this as an explicit, structural
assumption (e.g. `etoro_adapter.py`'s hard refusal of any non-`"demo"`
`account_mode`), not just an operational habit. See
`docs/KNOWN_ISSUES.md` and `docs/PENDING_DECISIONS.md` for what
changes once that assumption is lifted (the six owner-only action
cards).

## Every tenant-scoped table is enumerated explicitly, never inferred

`_TENANT_SCOPED_TABLES` and `_APPEND_ONLY_TABLES` in `app/db.py` are
hand-maintained tuples, not derived from a schema convention (e.g. "any
table with a `tenant_id` column"). The assumption is that a human (or
agent) reviewing a new table always remembers to add it to the correct
list — there is no automated check that a new `tenant_id`-bearing
model is actually RLS-protected. See `docs/TECH_DEBT.md` and
`docs/process/REVIEW_CHECKLIST.md`.
