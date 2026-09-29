# Threat model

This document names the real assets this service holds, the real
mitigations already in the code for each, and the gaps that are genuinely
open today. It is written against the code as it exists, not against an
aspirational design -- see `docs/security/ARCHITECTURE.md` for how each
mitigation named here actually works.

## Assets

1. **Tenant data isolation** -- every other tenant's rows (memberships,
   customer profiles, ledger entries, sleeves, subscriptions, research
   runs, support cases, publisher destinations, API keys, integration
   configs, managed programs, audit events, workspace settings, portfolio
   selections, notification/display preferences, platform connections,
   copy mandates, export-stream registrations, inbox events, incidents)
   must never be readable or writable by a session scoped to a different
   tenant, or by an unscoped session at all.
2. **Customer financial performance data** -- ledger entries
   (`app/models/ledger.py`, four books: PLATFORM/SOURCE/FOLLOWER/etc per
   `app/services/ledger.py`), reconciliation state, and reported
   performance derived from them. This is real economic history, append-
   only by design (see below).
3. **Staff credentials and session material** -- password hashes
   (argon2id via `pwdlib`), web session IDs / CSRF tokens, issued JWTs and
   their `jti`s, API key secrets (only ever a SHA-256 hash is persisted).
4. **Command authority** -- the ability to grant rights, release a
   strategy, publish a trading intent, manage billing, view broker
   credentials, revoke staff access, or export the audit trail verbatim.
   These are gated by `app/services/permissions.py`'s explicit allowlist
   (see `docs/security/AUTHORIZATION.md`).
5. **The relay ingress itself** -- the one channel by which
   signal-copier's own private execution/source-receipt events reach this
   service's ledger. A forged or replayed relay event is a forged economic
   fact.
6. **The audit trail's own integrity** -- `audit_events` is the evidentiary
   record AD-18's evidence-manifest export relies on (see
   `docs/observability/OVERVIEW.md`); its append-only property is itself a
   security control, not just an operational convenience.

## Mitigations, by asset

### Tenant data isolation

- **Primary control**: Postgres RLS with `FORCE ROW LEVEL SECURITY` on
  every tenant-scoped table (`app/db.py::_apply_row_level_security`),
  fail-closed on an unset `app.tenant_id` session variable. See
  `docs/security/ARCHITECTURE.md` section 3 for the full mechanism.
- **Defense in depth**: service-layer functions also filter by
  `tenant_id` explicitly (e.g. every `select(...).where(Model.tenant_id ==
  tenant_id)` across `app/services/*.py`) -- RLS is not the *only* thing
  standing between a query and another tenant's rows, it is the layer that
  holds even if a service-layer filter is ever forgotten.
- **Never trust a caller-supplied tenant_id**: `spec/docs/02_architecture_and_tenancy.md`
  states this as a hard rule ("Never accept a browser tenant ID as proof
  of access"), and the code follows it -- `set_tenant_scope()` is always
  called with the tenant resolved from an authenticated source (the
  verified JWT/session's own `tenant_id` claim, or the relay's
  server-controlled `export_stream_registrations` lookup), never from a
  request body or query parameter.
- **`relay_role` scoping**: the one role with a legitimate reason to read
  before a tenant is known (`export_stream_registrations`, to discover the
  tenant) gets exactly one narrow, additive PERMISSIVE policy for that one
  table -- not `BYPASSRLS`, not a blanket exemption. See
  `app/db.py::_apply_relay_role_access`.

### Customer financial performance data

- **Append-only at the database layer**: `enforce_append_only()` installs
  a trigger that rejects UPDATE/DELETE on `ledger_entries`,
  `portfolio_versions`, `portfolio_version_sleeves`, and `audit_events` --
  a mistaken entry is corrected only by inserting a new row referencing the
  original via `correction_of` (`app/services/ledger.py::append_correction`).
  This holds even for the table owner.
- **`evidence_class` is a required argument**, never defaulted, on
  `append_entry()` -- per `INTEGRATION_DECISION.md` S7, "there is no
  honest default for 'what kind of evidence is this.'" `fee` defaults to
  `None` (unknown), never `Decimal(0)`, for the same reason ("Importing a
  zero default is not proof of a verified fee").
- **Idempotent, integrity-checked ingest**: `ingest_export_event()` treats
  the same `event_id` with a different `payload_hash` as a loud
  `EventIntegrityError`, never last-write-wins.
- **`relay_role` has no write access to command-authority tables**: it can
  `INSERT` into `ledger_entries` (the narrow act of recording an economic
  fact from a verified relay event) but has no grant at all on
  `publisher_destinations`, `release_reviews`, `rights_registry`,
  `api_keys`, `workspace_settings`, or any other table that would let a
  compromised relay credential *act* rather than merely *record*. A stolen
  relay signing secret can, at most, forge ledger-entry-shaped rows tied to
  an already-registered stream/tenant; it cannot grant rights, release a
  strategy, publish an intent, or touch billing/staff/API-key tables. This
  is the concrete meaning of "relay_role's zero write access to
  command-authority tables."

### Staff credentials and session material

- Argon2id password hashing (`pwdlib.PasswordHash.recommended()`), never
  reversible storage.
- Revocable sessions, fail-closed on any denylist-check failure (see
  `docs/security/ARCHITECTURE.md` section 2).
- API key secrets: `generate_scoped_key()` (`app/services/api_key.py`)
  returns the raw secret exactly once; only a SHA-256 hash is ever
  persisted (`_hash_secret`). Scopes are drawn from a fixed allowlist
  (`_VALID_SCOPES = {"alerts_read", "reports_read", "delivery_receive"}`)
  with **no trading/admin scope** representable at all -- an unknown or
  overly powerful scope string is always refused, never silently narrowed.
  Keys must have a future `expires_at`; there is no never-expire default.
- CSRF protection on cookie-authenticated mutations (`X-CSRF-Token`
  matched against the session's own stored token).

### Command authority

- `app/services/permissions.py`'s `_ALLOWED` dict is a closed allowlist:
  an action string with no entry denies every role, including OWNER. See
  `docs/security/AUTHORIZATION.md` for the full role/action matrix.
- Every grant/revoke of staff access (`app/services/staff_access.py`)
  writes a real `AuditEvent`; `invite_staff_member` cannot grant OWNER
  (only direct DB/founder action can), and `revoke_staff_member`
  structurally refuses to revoke the OWNER role or let a caller revoke
  their own membership.

### The relay ingress

- HMAC-SHA256 signature over `"{timestamp}.{body}"`, checked with
  `hmac.compare_digest`, with a distinct secret from every other webhook
  in the system (see `docs/security/ARCHITECTURE.md` section 4).
- Two-secret rotation window (`RELAY_SIGNING_SECRET` +
  `RELAY_SIGNING_SECRET_PREVIOUS`) so rotation never causes a real-traffic
  outage and never leaves a "rotation mode" bypass.
- Replay-tolerance window (300s default) plus idempotent, hash-checked
  ingest for in-window replays.

### Audit trail integrity

- Append-only trigger on `audit_events` (same mechanism as ledger
  entries).
- `generate_evidence_manifest()` bundles matching rows *verbatim* (no
  summarizing, redacting, or reordering) with a content hash
  (`compute_manifest_content_hash`, reusing
  `signal_platform_contracts.compute_payload_hash`) so a later re-hash of
  the same rows confirms nothing in the export changed.
- Generating the manifest is itself logged as a new `AuditEvent`
  (`EVIDENCE_MANIFEST_EXPORT_ACTION`) -- an evidence export is a
  real command against the audit trail, not a silent read.

## Genuine gaps (stated honestly)

1. **Email delivery is not wired in.** Verification and password-reset
   tokens are real and single-use, but nothing sends them anywhere. Until
   an SMTP/SendGrid provider is integrated, whoever holds the token
   determines whether an account takeover via a leaked token is possible
   through *this* path specifically -- disclosed directly in
   `local_auth.py`'s own docstring.
2. **Production customer/operator identity is not yet Supabase Auth.**
   `app/services/auth.py`'s JWT issuer is explicitly documented as a
   LOCAL_SIM/test-only controlled issuer; `local_auth.py` is the real
   interim path. A production `COMMERCIAL_LIVE` deployment that has not
   completed the Supabase Auth integration named in
   `spec/docs/02_architecture_and_tenancy.md` is running on the interim
   local-password system, not the target one.
3. **In-window relay replay is not rejected by signature verification
   alone.** It is made harmless by idempotent ingest, but a request
   replayed within the 300-second tolerance window is not itself detected
   as a replay by `relay_auth.py` -- this is a stated, deliberate
   division of responsibility, not an oversight, but it means the relay
   module cannot be evaluated for replay safety in isolation from
   `integration_inbox.py`.
4. **No mTLS.** `INTEGRATION_DECISION.md` S4 names mTLS as the preferred
   option "when available" and the signed-service-token scheme as the
   fallback "otherwise" -- this deployment has never had a PKI to issue
   client certificates against, so it has always run on the fallback.
   Anyone who can capture a valid signature within the tolerance window
   and reach the ingress network path can attempt a duplicate submission
   (rendered harmless by idempotency, per gap 3) but transport-level
   client authentication is not present.
5. **`COMMERCIAL_LIVE` has never run against a real Stripe account.**
   `app/services/stripe_webhook.py` reimplements Stripe's own signing
   scheme against synthetic payloads; there is no live
   `STRIPE_WEBHOOK_SIGNING_SECRET` and no Stripe SDK call anywhere in this
   build. The webhook-verification code path is real and tested against
   synthetic events, but has never been exercised against genuine Stripe
   traffic.
6. **Collective2/eToro adapters build requests but never transmit them.**
   Both `collective2_publisher.py` and `etoro_adapter.py` are real,
   tested request-builders with no live credentials, no sandbox access,
   and (for eToro) a structural refusal to select any `account_mode`
   other than `"demo"`. This is a deliberate, disclosed scope boundary
   (see `docs/integrations/CATALOG.md`), not a security bug, but it means
   the actual over-the-wire behavior against a live publisher endpoint has
   never been observed by this codebase.
7. **No mTLS / no PKI for staff access either** -- staff and customer
   auth both terminate in either a password+session or a Bearer JWT; there
   is no hardware-key/WebAuthn second factor implemented anywhere in this
   codebase today.
8. **Rate limiting is not implemented in the reviewed modules.** Nothing
   in `auth.py`, `local_auth.py`, or `dependencies.py` throttles repeated
   sign-in or token-verification attempts. A brute-force or credential-
   stuffing mitigation, if required, is not present in this code today.

Each of these is a real, named boundary of what currently exists -- not a
claim that the feature is unsafe, but a statement of exactly how far the
implemented control reaches.
