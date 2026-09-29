# ADR-0007: Local password auth + revocable JWT sessions instead of a third-party auth provider

## Status

Accepted. Implemented in `app/models/local_auth.py` (`AuthToken`, `WebSession`),
`app/models/token_revocation.py` (`IssuedToken`, `RevokedToken`),
`app/services/local_auth.py`, and `app/services/auth.py`.

## Context

A real production deployment's customer/operator identity is intended to come from
Supabase Auth — `app/services/auth.py`'s own docstring is explicit that this is "an
external deployment task, not something this module ever becomes." But before this
slice, no such integration existed *and* there was no working local alternative
either: no password field on `UserIdentity`, no session-cookie mechanism, no `/auth`
route at all — `ops/bootstrap.py` minting a token out of band was the only way for
anyone, operator or customer, to get one. Application code and tests also need a
real, cryptographically verifiable token — not a hand-built dict — to exercise the
tenant-scope/role enforcement `app/db.py`'s RLS policies and
`app/services/permissions.py`'s role checks actually key off of.

A separate, later gap: a Bearer-token JWT is cryptographically self-contained and,
without a revocation mechanism, stays valid until its natural `exp` regardless of
what happens afterward — there was no way to kill one early (e.g. after a suspected
compromise, or "log out everywhere"), even though the cookie-based web session
mechanism (`web_sessions`) was already revocable by deleting its row.

## Decision

Build a real, local, working auth system for this deployment — deliberately not a
Supabase Auth integration, and disclosed as such:

- **Password auth**: `pwdlib` (argon2id) hashing on `UserIdentity.password_hash`,
  the same library and construction signal-copier's own `app/auth.py` already uses
  for its single-owner case.
- **Browser sessions**: a server-side, revocable session (`web_sessions`, primary
  key `session_id`) carried as an httponly cookie, plus a separate CSRF token
  returned once in the login/verify response body — the same two-part design as
  signal-copier's own `app/auth.py`, for the same reason.
- **Single-use tokens**: `auth_tokens` (`AuthToken`) for email verification and
  password reset — the token string itself is the primary key (a high-entropy
  `secrets.token_urlsafe`), matching signal-copier's own precedent of using the
  random value as its own lookup key. Email delivery is explicitly **not** wired: no
  SMTP/SendGrid integration exists; the token is real and returned directly to the
  caller, and a real deployment's route handler is expected to email its verify/
  reset link once a provider is configured — the same disclosed-gap pattern this
  project already uses for SMS/WhatsApp providers elsewhere.
- **Bearer JWTs**: `app/services/auth.py::issue_token`/`decode_token`, HS256, for
  API/service-to-service auth and tests. Every token carries a fresh `jti`
  (`uuid.uuid4()`, never a hash of the claims, so two tokens minted a second apart
  with identical claims cannot collide). `issued_tokens` (`IssuedToken`) records one
  row per token issued *with* a session, so "revoke all of this user's tokens" can
  enumerate what is currently outstanding — a bare `issue_token(...)` with no
  session (`ops/bootstrap.py`'s one-off provisioning, pure-function tests) still
  works but isn't enumerable that way, though it can still be revoked individually
  once its own `jti` is known. `revoked_tokens` (`RevokedToken`) is the actual
  denylist: presence of a row, not a status column, is the signal, checked by a
  cheap primary-key lookup on `jti` on the hot path of every authenticated request.
  `expires_at` is copied from the token's own `exp` claim at revoke time so a future
  pruning job can delete naturally-expired denylist rows without re-decoding
  anything.
- Neither `auth_tokens`/`web_sessions` nor `issued_tokens`/`revoked_tokens` is
  tenant-scoped via RLS — a token/session is looked up by its own key (token string,
  `session_id`, `jti`) *before* any tenant scope is otherwise established;
  `tenant_id` is stored on them for audit/reporting only, never as an RLS gate.
- `create_web_session`/`delete_web_session` each append a real `AuditEvent`
  (object_type `"session"`, action `"login"`/`"logout"`) into the same append-only
  audit store other admin actions already use.

## Consequences

- The platform has a real, working login/logout/reset flow today, without depending
  on an external identity provider being wired up first — but it is explicitly a
  local-deployment mechanism, not the intended production identity system, and code
  must not conflate the two.
- A JWT can now be individually revoked (once its `jti` is known) or revoked in bulk
  for a user who used a session-bound issuance path; a bare, session-less
  `issue_token` call is not enumerable for bulk revocation and that limitation is
  deliberate, not a bug to silently work around.
- Password-reset/email-verification tokens are unusable by an end user until a real
  email provider is wired into the route handler that hands the token off — that gap
  is disclosed, not hidden, and must stay disclosed in any user-facing documentation
  of this feature.
