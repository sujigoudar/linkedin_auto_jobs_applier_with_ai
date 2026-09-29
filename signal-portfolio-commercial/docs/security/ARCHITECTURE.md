# Security architecture

This is a description of the security model as it actually exists in this
codebase today, traced to the modules that implement it. It is not a
description of a target state -- gaps are called out explicitly where they
exist (see `docs/security/THREAT_MODEL.md` for the fuller gap register).

## Four independent controls

signal-portfolio-commercial's security posture rests on four mechanisms that
are each independently real and independently testable:

1. Local password authentication (`app/services/local_auth.py`)
2. Revocable JWT sessions with a fail-closed denylist
   (`app/services/auth.py`, `app/services/token_revocation.py`)
3. Postgres row-level security for multi-tenant isolation (`app/db.py`)
4. HMAC-signed relay ingress with key rotation
   (`app/services/relay_auth.py`)

None of these four is a placeholder for a future implementation. Each is a
real, currently-running control with its own test coverage. What follows
walks each one in turn, then how they compose end to end for one request.

## 1. Local password authentication

`app/services/local_auth.py` is a genuine sign-in/sign-up/verify/recover
system, deliberately **not** a Supabase Auth integration -- the module's own
docstring is explicit that a real production deployment's customer/operator
identity is expected to come from Supabase Auth (an external deployment
task this codebase never becomes; see `spec/docs/02_architecture_and_tenancy.md`'s
"Public/customer control plane" section). This module exists because,
before it was built, there was no password field on `UserIdentity`, no
session-cookie mechanism, and no `/auth` route anywhere in the app --
`ops/bootstrap.py` minting a token out of band was the only way in.

Concretely:

- Passwords are hashed with `pwdlib.PasswordHash.recommended()` (argon2id),
  never stored or logged in plaintext (`_password_hasher.hash(password)` in
  `create_account`/`reset_password`).
- `authenticate()` raises the identical `InvalidCredentialsError` whether
  the email doesn't exist or the password is wrong -- no email enumeration
  via a differentiated error.
- `create_account()` similarly never reveals "that email is taken" as a
  distinct message from any other signup failure.
- `request_password_reset()` returns `None` (not an error) for an unknown
  email, so the caller can show the same "if that email has an account, a
  reset link was sent" message either way.
- Email verification and password-reset tokens (`AuthToken`) are single-use:
  `_consume_token()` sets `consumed_at` and a second attempt with the same
  token raises `InvalidTokenError`.
- **Disclosed gap**: there is no SMTP/SendGrid/any email-provider
  integration in this codebase. `create_account`/`request_password_reset`
  return the real token directly to the caller; a real deployment's route
  handler is expected to email that token's verify/reset link. Until an
  email provider is wired in, the token is only as private as whatever
  channel hands it to the user (e.g. shown once on a confirmation page in
  LOCAL_SIM/dev).

### Web sessions

`create_web_session()` issues a server-side-revocable session
(`web_sessions` table) carried as an httponly cookie
(`SESSION_COOKIE_NAME = "cp_session"`, `app/api/dependencies.py`), plus a
separate CSRF token returned once in the response body. A cookie-authenticated
mutation (any method other than GET/HEAD/OPTIONS) additionally requires a
matching `X-CSRF-Token` header (`get_current_scope` in
`app/api/dependencies.py`) -- Bearer-token callers are exempt from this
check because nothing auto-attaches a custom header the way a cookie
auto-attaches, so that path was never CSRF-vulnerable to begin with.

Sessions expire (`_SESSION_TTL = timedelta(days=7)`); `get_web_session()`
deletes and returns `None` for an expired row rather than raising, so a
caller gets a clean 401 instead of a 500.

`FORCE_SECURE_COOKIES` (`app/config.py`) governs whether the session cookie
is marked `Secure`. It defaults to `False` because `request.url.scheme`
alone only ever sees `"http"` behind a TLS-terminating reverse proxy (the
proxy, not this process, terminates TLS) -- a real deployment behind one
must set this explicitly rather than trusting a spoofable
`X-Forwarded-Proto` header by default.

### Session audit trail

`create_web_session`/`delete_web_session` each append a real `AuditEvent`
(`object_type="session"`, action `"login"`/`"logout"`) into the same
append-only AD-18 audit store described in
`docs/observability/OVERVIEW.md`. This is a real writer, not a stub --
session login/logout history is genuinely queryable.

## 2. Revocable JWT sessions (Bearer-token path)

`app/services/auth.py` is described in its own docstring as "a controlled,
local JWT issuer for LOCAL_SIM/tests only" -- the same module the Bearer-token
API path (relay callers, `ops/bootstrap.py`-minted owner tokens, scripted
API callers) uses in every environment this build currently supports,
pending a real Supabase Auth integration.

- Every issued token carries a fresh `jti` (`uuid.uuid4()` per token,
  `issue_token()`), never a hash of the payload, so two tokens with
  identical claims minted a second apart never collide.
- `decode_token()` is a pure, DB-free signature/claims check: it verifies
  the JWT signature (`HS256`, `config.LOCAL_JWT_SECRET`), decodes
  `tenant_id`/`user_id`/`role`/`jti`, and fails closed (raises
  `InvalidTokenError`) on anything malformed, expired, or carrying an
  unrecognized role. It never returns a partially-trusted scope.
- `verify_token()` -- the function every real request-verification call
  site (`get_current_scope` in `app/api/dependencies.py`) must use instead
  of calling `decode_token()` directly -- wraps `decode_token()` and adds a
  DB-backed denylist check via `is_token_revoked()`.

### Fail-closed denylist, explicitly

`verify_token()`'s docstring states the contract directly: "Fail CLOSED,
never open: any exception raised while checking the denylist (a DB error, a
timeout, anything) is caught here and turned into `InvalidTokenError` -- it
is never treated as 'not revoked' by default and never silently skipped. A
broken denylist check denies the request; it can never make an
otherwise-revoked token pass." This is implemented as a bare
`except Exception` around the `is_token_revoked()` call in `verify_token()`.

### Two tables, two lifecycles (`app/services/token_revocation.py`)

- `IssuedToken` (via `record_issued_token()`) -- every `jti` minted by a
  caller that has a DB session available is recorded here, making it later
  enumerable for "log out everywhere". A caller with no session (e.g.
  `ops/bootstrap.py`'s one-off provisioning, or pure-function tests) still
  gets a fully valid, individually-revocable token; it just isn't
  enumerable until it's used at least once against a real DB-backed caller.
- `RevokedToken` (via `revoke_token()`/`revoke_all_tokens_for_user()`) --
  the actual denylist `is_token_revoked()` checks. Idempotent: revoking an
  already-revoked `jti` is a no-op.
- Every revocation appends a real `AuditEvent` (`object_type="api_token"`,
  action `"revoke_token"`/`"revoke_all_tokens"`) -- revoking token access is
  itself an auditable command-authority action.
- Real callers: `app/services/auth.py` (issue + verify), and the
  owner/customer-facing revoke routes in `app/api/dashboard_routes.py`
  (CU-13 "Revoke my API tokens", AD-16 per-staff-member "Revoke API
  tokens").

## 3. Row-level-security multi-tenant isolation

`app/db.py` is the single place tenant isolation is enforced at the
database layer, on top of (not instead of) every service-layer
`tenant_id` filter.

### `app_role`: the ordinary, per-tenant-scoped role

`enable_row_level_security()` runs, for every table in
`_TENANT_SCOPED_TABLES` (memberships, customer_profiles, ledger_entries,
sleeves, subscriptions, portfolio_versions, release_reviews, research_runs,
eligibility_assessments, support_cases, publisher_destinations, api_keys,
integration_configurations, price_versions, managed_programs,
audit_events, workspace_settings, portfolio_selections,
notification_preferences, customer_display_preferences,
platform_connections, copy_mandates, export_stream_registrations,
inbox_events, incidents):

```sql
ALTER TABLE <table> ENABLE ROW LEVEL SECURITY;
ALTER TABLE <table> FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON <table>
  USING (tenant_id = current_setting('app.tenant_id', true));
```

`FORCE ROW LEVEL SECURITY` (not just `ENABLE`) is deliberate: plain `ENABLE`
is bypassed by the table owner, and in this single-role-per-database
development setup the table owner is usually the very role queries run
through -- `FORCE` is what actually makes isolation hold for that role too.

`set_tenant_scope(session, tenant_id)` sets the Postgres session variable
the policy keys off of, via `set_config('app.tenant_id', :tenant_id, true)`
(a bound parameter through a function call -- never a hand-built `SET
LOCAL app.tenant_id = ...` string, since Postgres's `SET` statement doesn't
accept bind parameters at all and building it by hand would be a SQL
injection point). **Fail-closed by construction**: when `app.tenant_id` is
unset, the policy's `USING` clause has nothing to match, so an unscoped
session sees zero rows in these tables, never "everything".

Two tables get bespoke policies instead of the generic one, because a
single per-table policy can't express "own tenant OR public":

- `products` (`enable_product_visibility_policy`): visible if
  `tenant_id = current_setting('app.tenant_id', true) OR lifecycle_state =
  'PUBLISHED'` -- the public catalog must show every tenant's PUBLISHED
  products to an anonymous, unscoped session, while an admin session must
  still see its own tenant's rows in any lifecycle state.
- `content_documents` (`enable_content_document_visibility_policy`): same
  shape, keyed on `state = 'PUBLISHED'`.

### `relay_role`: the restricted, non-superuser ingress role

`_apply_relay_role_access()` (`app/db.py`, installed by Alembic migration
`3f7a19c02b8e_add_relay_role_access.py`) creates `relay_role` as `LOGIN
NOSUPERUSER NOBYPASSRLS` and grants it exactly:

- `SELECT` on `export_stream_registrations`, via one extra **PERMISSIVE**
  policy (`relay_stream_lookup`, `USING (true)`) -- Postgres combines
  multiple permissive policies on the same table with OR, so this does not
  weaken `tenant_isolation` for `app_role` or any other role; it only gives
  `relay_role` a second way to satisfy this one table's SELECT, which it
  needs to discover a tenant by `source_stream` *before* any
  `app.tenant_id` scope can be set (see "RLS chicken-and-egg" below).
- `SELECT, INSERT, UPDATE` on `inbox_events`.
- `INSERT` on `ledger_entries`.

`relay_role` never receives `BYPASSRLS`. Every table it touches other than
`export_stream_registrations` is reached only through the ordinary
`tenant_isolation` policy, after `set_tenant_scope()` has been called with
the tenant that the lookup found.

**RLS chicken-and-egg, resolved**: `app/services/integration_inbox.py`'s
`ingest_export_event()` resolves the tenant from
`export_stream_registrations` by `source_stream` *first* (the only
unscoped read `relay_role` is permitted), calls `set_tenant_scope()`
immediately after, and only then touches `inbox_events`/`ledger_entries`
-- both protected by the same unmodified `tenant_isolation` policy every
other table uses. The relay never trusts a tenant_id an inbound envelope
itself might claim; envelopes have no such field to claim one with.

### Append-only enforcement, also at the database layer

`enforce_append_only()` installs a `BEFORE UPDATE OR DELETE` trigger
(`forbid_ledger_mutation()`) on `ledger_entries`, `portfolio_versions`,
`portfolio_version_sleeves`, and `audit_events` that raises on any attempt
to mutate an existing row. This holds even for the table owner and even if
a future refactor removes the application-level `append_entry`/
`append_correction` discipline in `app/services/ledger.py` -- see
`docs/operations/DR.md` for what this buys operationally.

### Test-time RLS provisioning

`tests/conftest.py`'s `db_session` fixture recreates the schema per test
function, grants `app_role` full DML, then calls
`enable_row_level_security()`, `enable_product_visibility_policy()`,
`enable_content_document_visibility_policy()`, `enable_relay_role_access()`,
and `enforce_append_only()` -- the exact same functions a real deployment's
migrations call. Two further fixtures, `tenant_session_factory` and
`relay_session_factory`, connect as the actual `app_role`/`relay_role`
logins (not the Postgres superuser used for schema setup) so RLS tests
exercise the same isolation a real connection would see, never a
superuser's bypassed view of it. This is how `docs/operations/DEPLOYMENT.md`'s
role-provisioning story is verified in CI on every run.

## 4. HMAC-signed relay ingress with rotation

`app/services/relay_auth.py` implements the signed-service-token half of
the Signal Platform Integration Correction Pack's own
`INTEGRATION_DECISION.md` S4 instruction ("Use the existing qualified
authentication mechanism when available. Otherwise use a maintained mTLS
or audience-bound signed-service-token implementation, with expiry, key
rotation and replay protection.") -- no mTLS PKI exists in this environment
to issue and rotate real certificates against, so this is the alternative
the decision record explicitly allows.

- Scheme: HMAC-SHA256 over `"{timestamp}.{body}"`, header shape
  `t=<unix ts>,v1=<hex hmac>` -- the same shape
  `app/services/stripe_webhook.py` uses for its own purpose, deliberately
  **not shared code** with it: a relay credential compromise must never be
  reachable through, or confused with, the billing webhook's own secret,
  and vice versa (`INTEGRATION_DECISION.md` S11's "A stolen telemetry
  credential cannot become a trading credential").
- `verify_relay_signature()` checks `config.RELAY_SIGNING_SECRET`
  (CURRENT) first, using `hmac.compare_digest`; if that doesn't match and
  `RELAY_SIGNING_SECRET_PREVIOUS` (PREVIOUS) is set, it checks that too,
  also with `hmac.compare_digest` (so accepting PREVIOUS is not a timing
  side-channel on CURRENT). A signature matching neither is always
  rejected -- there is no "rotation mode" bypass.
- Replay tolerance: `_DEFAULT_TOLERANCE_SECONDS = 300`. A timestamp outside
  the window raises `StaleRelayTimestampError`.
- **Honest limitation, stated in the module's own docstring**: the
  timestamp-tolerance window is real replay protection for a captured
  request replayed *later* (outside the window), but a request replayed
  *within* the window is not rejected by this module alone.
  `app/services/integration_inbox.py`'s idempotent ingest (same `event_id`
  + same `payload_hash` is a no-op; same `event_id` + different hash is a
  loud `EventIntegrityError`) is what makes an in-window replay harmless
  rather than a duplicate fill.

### Zero-downtime rotation procedure

1. Set `RELAY_SIGNING_SECRET_PREVIOUS` to the current (soon-to-be-old)
   secret on the receiving (commercial) service, then set
   `RELAY_SIGNING_SECRET` to the new value. Update signal-copier's own
   relay worker to sign with the new secret. During the overlap, this
   ingress accepts both (already-updated worker instances signing with the
   new secret, and not-yet-updated instances still signing with the old
   one) -- real traffic never fails verification mid-switch.
2. Once every signal-copier deployment is confirmed signing with the new
   secret, unset `RELAY_SIGNING_SECRET_PREVIOUS`. A signature made with the
   old secret is rejected from that point on.

See `docs/security/SECRETS.md` for provisioning of these values and
`docs/integrations/CATALOG.md` for the relay's place in the overall
integration topology.

## How these four compose for one request

**A browser request to a dashboard route:**
`get_current_scope()` (`app/api/dependencies.py`) reads the `cp_session`
cookie, resolves it via `get_web_session()` (local_auth), checks CSRF on
mutations, and returns a `TenantScope`. The route handler then calls
`require_permission(scope.role, "<action>")` (see
`docs/security/AUTHORIZATION.md`) before doing anything, and
`set_tenant_scope(session, scope.tenant_id)` before touching any RLS-guarded
table.

**A Bearer-token API/relay caller:** `get_current_scope()` calls
`verify_token()` (fail-closed denylist check) instead. Everything else --
permission check, `set_tenant_scope()` -- is identical.

**The relay ingress specifically**
(`app/api/relay_routes.py`, not read in full for this document but wired
per its own docstrings): the HMAC signature is verified first
(`relay_auth.verify_relay_signature`), then `ingest_export_event()`
resolves tenant via the `relay_role`-only `export_stream_registrations`
lookup and calls `set_tenant_scope()` before any tenant-scoped write.

Three independent layers -- authentication/session validity, permission
allowlist, and database-level RLS -- must all agree for a write to land.
None of the three alone is treated as sufficient.
