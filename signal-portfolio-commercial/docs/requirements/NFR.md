# Non-Functional Requirements

Every requirement below cites the real mechanism that enforces it — not an aspiration.

## Multi-tenant isolation

**Requirement**: no tenant can read or write another tenant's rows, even given a
bug in application code, a missing `WHERE tenant_id = ...`, or a raw SQL script run
by an operator.

**Enforcement**: Postgres row-level security, `FORCE`d (not merely enabled) so it
holds even for the table owner, with a single fail-closed policy
(`tenant_isolation`) on every tenant-scoped table — an unset `app.tenant_id` session
setting matches zero rows, never "everything." See ADR-0001 and
`docs/database/SCHEMA.md`'s RLS column. Two tables (`products`,
`content_documents`) have a deliberately different, still fail-closed, bespoke
policy for public-catalog visibility. `app/db.py::set_tenant_scope` must be called,
with a real authenticated tenant_id, at the start of every request/job before any
tenant-scoped table is touched — a caller-supplied tenant ID is never itself proof
of access.

**Verification**: `tests/` exercises RLS against a real, disposable Postgres
cluster (`tests/conftest.py`), not a mock — SQLite cannot express `FORCE ROW LEVEL
SECURITY` or Postgres-specific column types this schema uses (`ARRAY(String)`,
etc.), so a test suite that ran against SQLite would not actually verify this
requirement.

## Restricted, least-privilege ingest boundary

**Requirement**: a fully compromised relay/ingest credential must never be able to
reach command-authority tables (billing, publication, membership/roles, platform
connections, copy mandates).

**Enforcement**: `relay_role`, a distinct, non-superuser, `NOBYPASSRLS` login role
with an exhaustively short, documented grant list (`SELECT` on
`export_stream_registrations` via one bespoke permissive policy; `SELECT/INSERT/
UPDATE` on `inbox_events`; `INSERT` only on `ledger_entries`) and **no grant at all**
on any other tenant-scoped table. See ADR-0002.

## Idempotent ingest

**Requirement**: at-least-once delivery from the relay must never produce a
duplicate financial fact, and a redelivered event with a conflicting payload must
never be silently accepted.

**Enforcement**: `InboxEvent.event_id` as primary key (redelivery of identical
bytes is a harmless no-op); a payload-hash mismatch under the same `event_id` raises
`EventIntegrityError` rather than overwriting. See ADR-0003.

## Ordered, gap-safe ingest

**Requirement**: an out-of-order event must never be applied ahead of its
predecessor, and must never be silently dropped.

**Enforcement**: separate `received_at`/`applied_at` high-water marks per
`InboxEvent`, `_next_expected_sequence` keyed strictly off already-applied rows, and
`_apply_and_cascade`'s forward-unblocking replay. See ADR-0003 and
`docs/design/INGEST_PIPELINE.md`.

## Transport authenticity and replay protection

**Requirement**: the relay ingress must reject a forged or stale request, and a
compromised relay credential must never be usable against the billing webhook's own
secret or vice versa.

**Enforcement**: HMAC-SHA256-over-`"{timestamp}.{body}"` (`app/services/
relay_auth.py`), a distinct secret from `stripe_webhook.py`'s own, `hmac.
compare_digest` throughout, a 300-second default timestamp-tolerance window, and a
documented two-secret (current/previous) zero-downtime rotation procedure. In-window
replay is explicitly not defended by this layer alone — it relies on idempotent
ingest (above) to make a same-window replay harmless.

## Append-only ledger integrity

**Requirement**: a ledger entry, once written, must never be editable or
deletable — by any role, including the table owner, and independent of whether the
application-level service-layer discipline (`append_entry`/`append_correction`) is
followed by every future code path.

**Enforcement**: a `BEFORE UPDATE OR DELETE` trigger (`append_only_guard` →
`forbid_ledger_mutation()`) installed on `ledger_entries`, `portfolio_versions`,
`portfolio_version_sleeves`, and `audit_events` at the database level. See
ADR-0008. No downgrade path exists that would silently remove this protection.

## Money correctness

**Requirement**: financial quantities must never be computed in binary floating
point.

**Enforcement**: every money/quantity column on `LedgerEntry` (`quantity`, `price`,
`multiplier`, `fee`) is `Numeric(28,10)`, mapped to Python `Decimal`; ledger and
attribution service code (`app/services/ledger.py`,
`app/services/analyst_attribution.py`, `app/services/platform_performance.py`)
operates on `Decimal` throughout. `fee = NULL` is a distinct, explicit state ("not
yet known") from `fee = Decimal(0)` ("verified zero") — a caller is never allowed to
default one into the other.

## Auditability

**Requirement**: administrative and session actions must leave a real, tamper-
evident (append-only) trail.

**Enforcement**: `audit_events` (append-only, above), written by
`app/services/audit_log.py` and, for session actions specifically, by
`app/services/local_auth.py::create_web_session`/`delete_web_session`
(`object_type="session"`, action `"login"`/`"logout"`).

## Credential revocability

**Requirement**: a compromised or unwanted credential must be killable before its
natural expiry.

**Enforcement**: browser sessions are always revocable by row deletion
(`web_sessions`); Bearer JWTs are revocable via the `issued_tokens`/`revoked_tokens`
denylist keyed by each token's own `jti` (ADR-0007), checked on every authenticated
request via a primary-key lookup.
