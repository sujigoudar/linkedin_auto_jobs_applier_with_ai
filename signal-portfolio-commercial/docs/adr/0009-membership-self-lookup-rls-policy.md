# ADR-0009: A narrow, self-scoped RLS policy for the sign-in/verify-email membership bootstrap lookup

## Status

Accepted. Implemented in `alembic/versions/f4a2590f17e2_add_membership_self_lookup_policy.py`,
`app/db.py::_apply_membership_self_lookup_policy`/`enable_membership_self_lookup_policy`/
`set_current_user_scope`, and consumed by `app/api/dashboard_routes.py::sign_in_submit`
and `verify_email_page`.

## Context

`sign_in_submit` and `verify_email_page` each authenticate a real user (by
password, or by consuming a real verification token) and then must look up
that user's own `memberships` row — `select(Membership).where(Membership.user_id
== user.user_id)` — to discover which tenant they belong to, so a real
`app.tenant_id` scope (ADR-0001) can finally be set and a web session created
for it. At the moment this query runs, no tenant scope exists yet — discovering
it is the entire point of the query. `memberships` carries `ENABLE`/`FORCE ROW
LEVEL SECURITY` and the generic `tenant_isolation` policy
(`tenant_id = current_setting('app.tenant_id', true)`), which only ever permits
a session **already** scoped to its own tenant. Under real RLS enforcement (the
non-superuser `app_role` production actually uses — never the Postgres
superuser fixture most of this codebase's tests still use, which bypasses RLS
unconditionally and could not have caught this), this lookup therefore always
returns zero rows once a real membership exists. A real sign-in or
verify-email flow failed in production with "This identity has no tenant
membership." — a genuine, previously live, production-blocking outage, not a
security leak, and not caught by any existing test because every existing
test drove these two routes through the superuser `db_session` fixture.

This is the identical bootstrap chicken-and-egg shape ADR-0002 already solved
for `relay_role`'s own `export_stream_registrations` lookup — but for a
different role (`app_role`, the ordinary browser-facing login role, not a
separate restricted worker role) and a different axis of scoping (the
caller's own `user_id`, not an unscoped table-wide lookup).

## Alternatives considered

1. **Move `memberships` off RLS-gated storage entirely**, the way
   `user_identities`/`web_sessions`/`auth_tokens`/`issued_tokens`/
   `revoked_tokens` already do (`app/models/token_revocation.py`'s own
   docstring: "not tenant-scoped via `app/db.py`'s row-level-security
   policy... looked up... BEFORE any tenant scope is otherwise
   established"). **Rejected.** Those tables hold no real command
   authority and are looked up only by an opaque, unguessable, per-row
   identifier (a session id, a token's own `jti`) — being unscoped costs
   nothing, because there is no meaningful adjacent row to leak. `memberships`
   is the opposite: it is the actual tenant/role authority table (ADR-0001:
   "real command authority... membership/roles"), looked up by a
   comparatively low-entropy `user_id`. Removing RLS from it entirely would
   let any query anywhere in the codebase read every tenant's full
   membership roster the instant a bug or a future refactor forgot to add an
   explicit `tenant_id` filter — silently, with no database-level backstop.
   That is precisely the class of bug RLS with `FORCE` exists to make
   structurally impossible, and this table is the last one that should lose
   it.
2. **Widen the existing `tenant_isolation` policy itself** (e.g. `OR
   user_id = current_setting('app.current_user_id', true)`) instead of adding
   a second, separate policy. **Rejected**, to keep this decision as narrow
   and auditable as the ADR-0002 precedent: a second, independently named,
   independently droppable `AS PERMISSIVE FOR SELECT` policy documents its
   own reason for existing, can be reasoned about (and removed) in isolation,
   and cannot accidentally acquire INSERT/UPDATE/DELETE semantics the way an
   edit to the shared policy's single `USING` clause more easily could over
   time.
3. **Have the application resolve the tenant without touching `memberships`
   at all** (e.g. store `tenant_id` directly on `user_identities`, which
   isn't RLS-gated). **Rejected**: a user can hold memberships in more than
   one tenant (`app/models/tenancy.py`'s own docstring: "the platform owner
   also has an operator membership; a support agent has memberships in many
   tenants"), so "the" tenant for a login genuinely lives on `memberships`,
   not on the identity row, and duplicating it onto `user_identities` would
   create a second source of truth that could drift.
4. **The chosen approach**: add one narrow, additional, permissive,
   SELECT-only RLS policy on `memberships`, `membership_self_lookup`, that
   matches only the row whose `user_id` equals a new, dedicated session
   variable, `app.current_user_id` — set for exactly the length of this one
   bootstrap lookup by a new helper, `set_current_user_scope`, mirroring
   `set_tenant_scope`'s own `set_config(..., true)` (transaction-scoped)
   convention. Postgres combines multiple `PERMISSIVE` policies on the same
   table with `OR`, so this does not weaken `tenant_isolation` for anyone —
   it adds one more way to satisfy this one table's `SELECT`, and only for
   the one row matching the session's own declared identity.

## Decision

- `_apply_membership_self_lookup_policy` (`app/db.py`) creates
  `membership_self_lookup ON memberships AS PERMISSIVE FOR SELECT USING
  (user_id = current_setting('app.current_user_id', true))`. No `TO <role>`
  clause: unlike `relay_role`'s bespoke policy, this one applies to the
  ordinary `app_role` login itself, on the same connection/session as every
  other query that role runs — it is scoped by *value* (the caller's own
  `user_id`), not by a separate restricted role.
- It is `SELECT`-only. No matching `INSERT`/`UPDATE`/`DELETE` policy exists,
  or is needed: neither route ever writes to `memberships` — `create_account`
  is the only writer, and it already runs under a real `set_tenant_scope`
  call for the brand-new tenant it just created.
- `set_current_user_scope(session, user_id)` sets `app.current_user_id` via
  `set_config(..., true)` — transaction-scoped, exactly like
  `set_tenant_scope`, and for the same reason (`SET` takes no bind
  parameters; `set_config` avoids building SQL by hand from a caller-supplied
  string). It is called only in `sign_in_submit` and `verify_email_page`,
  immediately before the self-lookup query, with the just-authenticated
  user's own `user_id` — never a browser-supplied value trusted for
  anything beyond this one call, matching ADR-0001's own "never accept a
  browser tenant ID as proof of access."
- Once the membership row is found, both routes now also call
  `set_tenant_scope(session, membership.tenant_id)` before calling
  `create_web_session` — completing the bootstrap for real. This was
  necessary in addition to the new policy: `create_web_session` appends a
  real `login` `AuditEvent` row (`app/services/local_auth.py`), and
  `audit_events` is itself tenant-scoped by the ordinary `tenant_isolation`
  policy, which gates `INSERT`'s `WITH CHECK` exactly as it gates `SELECT`'s
  `USING` — an audit-event insert made before the tenant is scoped would
  itself be rejected by RLS. This is the same session that then serves the
  rest of the request, so every other query these two routes make already
  runs under the same, unmodified `tenant_isolation` policy every other
  route and table uses.

## Consequences

- A real sign-in or verify-email flow under real RLS enforcement now finds
  its own membership row and completes, resolved and confirmed against a
  real, disposable Postgres cluster running the same non-superuser `app_role`
  production uses (`tests/test_login_membership_bootstrap_rls.py`).
- The one intentional asymmetry this introduces — a session that has called
  `set_current_user_scope` can `SELECT` its own `memberships` row even with
  no `app.tenant_id` scope set — is narrow by construction (one row, keyed
  to the caller's own declared `user_id`, `SELECT` only) and cannot be used
  to read another user's row: an adversarial test
  (`test_membership_self_lookup_policy_cannot_read_another_users_membership_row`)
  confirms that a session scoped to user A's `app.current_user_id` gets zero
  rows back when querying for user B's `user_id`, regardless of what the
  query's own `WHERE` clause asks for — the policy is keyed off the
  session's own declared identity, never off the shape of the query.
- A session that never calls `set_current_user_scope` (every other request
  path in this codebase) leaves `app.current_user_id` unset, so
  `membership_self_lookup`'s `USING` clause never matches and grants
  nothing — fail-closed, the same convention `tenant_isolation` itself
  already follows when `app.tenant_id` is unset
  (`test_membership_self_lookup_policy_grants_nothing_when_unset`).
- The existing, tenant-scoped access pattern for `memberships` used
  everywhere else in the app (post-login, under a real `app.tenant_id`
  scope) is untouched: `tenant_isolation` still governs every other query
  against this table exactly as before, and the full existing
  `tests/test_row_level_security.py` suite passes unmodified.
- A future engineer adding a third way to read `memberships` must add its
  own narrowly-scoped policy and document it here or in a new ADR, the same
  discipline ADR-0002 already asks of any future grant to `relay_role` — the
  value of this boundary is in each policy's grant being short, single-
  purpose, and exhaustively documented, not merely "narrower than the
  generic case."
