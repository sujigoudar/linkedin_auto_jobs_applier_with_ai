# Troubleshooting

Real symptom -> cause -> fix entries, traced to the actual code paths that
produce each condition.

## RLS / role permission errors

### Symptom: `psql: FATAL: role "commercial" does not exist`

**Cause**: the runtime Postgres role was never created. Nothing in the
Alembic migration chain creates it -- only `ops/bootstrap.py` does
(`_bootstrap_runtime_role()`), and it must run after migrations, before
the app connects. This was a real, previously-undiscovered gap this
build's own bootstrap script documents finding and fixing.

**Fix**: run `alembic upgrade head` then `ops/bootstrap.py` (in that
order) with `COMMERCIAL_MIGRATOR_DATABASE_URL` pointed at an elevated
connection, before starting the app. `deploy/entrypoint-commercial.sh`
already does this in order -- if you're bypassing the entrypoint (e.g.
running the app directly against a fresh database), run those two steps
manually first.

### Symptom: `fe_sendauth: no password supplied`

**Cause**: the `commercial`/`relay_role` roles were created without a
password (Postgres's own `trust` auth method is the only thing that ever
let a passwordless role connect) -- the official `postgres` Docker image,
and any real managed Postgres, default to password auth.

**Fix**: set `COMMERCIAL_RUNTIME_ROLE_PASSWORD` and `RELAY_ROLE_PASSWORD`
as real environment variables before running `ops/bootstrap.py` -- it
refuses to run with either unset, specifically to prevent this state.

### Symptom: a query against a tenant-scoped table returns zero rows
unexpectedly, even though rows genuinely exist

**Cause**: `app.tenant_id` was never set on this session (RLS fails
closed -- `app/db.py`'s `tenant_isolation` policy matches nothing when the
session variable is unset). This happens if `set_tenant_scope()` is never
called, or is called on a different `Session` object than the one that
runs the subsequent query (e.g. a request handler that opens a second
session by mistake).

**Fix**: confirm `set_tenant_scope(session, tenant_id)` is called, with a
real authenticated `tenant_id`, on the exact `Session` object the query
runs against, before the query. For a relay caller, confirm
`ingest_export_event()`'s own tenant-resolution step ran first (it calls
`set_tenant_scope` internally as soon as it resolves the tenant from
`export_stream_registrations`).

### Symptom: `permission denied for table <x>` against `relay_role`
specifically

**Cause**: `relay_role` only ever has `SELECT` on
`export_stream_registrations`, `SELECT/INSERT/UPDATE` on `inbox_events`,
and `INSERT` on `ledger_entries` (`app/db.py::_apply_relay_role_access`).
Any other table access from a relay-authenticated code path is expected
to fail -- this is the isolation working as designed, not a bug to route
around by widening the grant.

**Fix**: if a real new relay use case genuinely needs a new grant, it
belongs in a new, narrowly-scoped migration extending
`_apply_relay_role_access` (or a sibling function) -- never a blanket
`GRANT ALL` or `BYPASSRLS` on `relay_role`.

### Symptom: `UndefinedTable` during `alembic upgrade head` on a fresh
database (historical migration failure)

**Cause**: a migration that calls `app/db.py`'s row-level-security helper
functions with the *current, live* `_TENANT_SCOPED_TABLES`/
`_APPEND_ONLY_TABLES` tuple instead of its own frozen snapshot -- a table
added by a *later* migration doesn't exist yet when an *earlier*
migration replays from scratch. `app/db.py`'s own comments document
catching this for real when `release_reviews` was added.

**Fix**: any migration calling `_apply_row_level_security`/
`_apply_append_only` directly must pass its own explicit, frozen tuple of
table names as they existed at that point in migration history -- never
the live module constant.

## Ingest inbox: gap-wait / `parked_reason` states

Every one of these is set on `InboxEvent.parked_reason` by
`app/services/integration_inbox.py` and is a **real, honest, durable**
state -- the row is always received and stored; it is simply not yet
(or never) applied to the ledger. None of these represent data loss.

| `parked_reason` prefix | Meaning | How to resolve |
|---|---|---|
| (no prefix -- a genuine sequence gap) | `export_sequence > expected` -- an earlier event on this stream/generation hasn't arrived yet. | Wait for redelivery of the missing sequence(s); `_apply_and_cascade()` automatically walks forward through everything already parked once the gap fills. No manual intervention needed beyond ensuring signal-copier's own relay worker retries. |
| `unsupported_schema_version:<version>` | The envelope's `schema_version` isn't in this build's `_SUPPORTED_SCHEMA_VERSIONS` (exact-match only, no semver guessing). | Requires a build upgrade that adds real support for that schema version -- never coerced or guessed at. |
| `unimplemented_event_type:<type>` | This build has no payload handler for this `EventType` yet. **Note**: this permanently parks every *later* sequence on the same stream too, since `_next_expected_sequence` is keyed off `applied_at`. | Requires a build upgrade implementing that event type. Until then, this stream is effectively stalled from this sequence forward -- check for this specifically if a stream's backlog stops draining entirely rather than just having isolated gaps. |
| `fee_target_not_found:<correlation_key>` | A FEE event arrived before its correlated EXECUTION_APPLIED event has been applied. | Ordinary redelivery/backfill timing -- resolves itself once the execution event applies (see `_apply_projection`'s FEE branch, which re-looks-up by `execution_correlation_key`). Not itself an error. |
| `routing_outcome_target_not_found:<event_id>` | A ROUTING_ADMISSION_OUTCOME arrived before its target SOURCE_RECEIPT was applied. | Same as above -- should be effectively unreachable in the ordinary flow since both share a stream and sequence ordering makes this near-impossible, but is a real, checked defense, not an assumption. |
| `generation_rollback_detected:<generation>` | An envelope claims an *older* `producer_generation` than this stream's already-established one -- signal-copier's own storage may have been restored to an older backup. | **Requires an operator decision** -- this is never auto-resolved; "Old economic IDs do not apply again" is the stated posture. Investigate why the producer's generation regressed before deciding how to proceed. |
| `new_generation_requires_bootstrap:<generation>` | An envelope claims a *newer* generation than established, without a reconciled bootstrap. | **Requires an operator-driven, out-of-scope reconciled bootstrap** (INT-008/INT-009) -- this build does not automate it. |
| `manifest_generation_mismatch:<manifest_id>` | A POSITION_SNAPSHOT page claims a different `producer_generation` than sibling pages of the same manifest. | Indicates a producer-side bug or corrupted manifest -- investigate the source, don't force-apply. |
| `manifest_metadata_mismatch:<manifest_id>` | A snapshot page claims a different `page_count`/`cutoff_sequence` than its siblings. | Same as above -- a manifest whose own metadata disagrees with itself is never silently reconciled. |

**General diagnostic approach**: query `inbox_events` for rows where
`applied_at IS NULL`, group by `parked_reason`, and cross-reference against
the table above. For the plain sequence-gap case (`parked_reason IS NULL`
but `applied_at IS NULL`), find the lowest missing `export_sequence` for
that `(tenant_id, source_stream, producer_generation)` and confirm
signal-copier's own outbox still has (or can regenerate) that event.

## Disposable-Postgres-per-test-session quirks (local test runs)

### Symptom: tests using `db_session`/`postgres_cluster` are skipped, not
run

**Cause**: `_pg_available()` didn't find
`/usr/lib/postgresql/16/bin/{initdb,pg_ctl}` -- the `postgresql-16` server
binaries aren't installed. This is a deliberate skip, not a silent
fallback to SQLite or a mock (this codebase's Postgres-specific column
types, e.g. `ARRAY(String)`, would behave differently against anything
else).

**Fix**: install the `postgresql-16` server package. There is no
alternative test path.

### Symptom: `initdb`/`pg_ctl` fail with a permissions error under a root
test runner

**Cause**: Postgres refuses to run its own server/init tools as root.
`_run_as_postgres()` already drops to the system `postgres` user via
`su postgres -c` when the runner is root -- but pytest's own `tmp_path_factory`
creates ancestor directories (`basetemp`, `pytest-of-root`, ...) as `0700
root:root`, which blocks the `postgres` user from even *traversing down*
to the data directory regardless of the data directory's own ownership.

**Fix**: this is already handled in `postgres_cluster` (`chown -R
postgres:postgres` on the data dir, plus `chmod 0711` on every ancestor up
to but not including the shared `/tmp`) -- if you see this failure, check
whether a customized pytest `tmp_path` configuration has moved the base
temp directory somewhere this chmod loop doesn't reach.

### Symptom: a test that ran fine alone fails when run as part of the full
suite (or vice versa)

**Cause**: `postgres_cluster` is `scope="session"` (one real cluster for
the whole test run -- expensive to start), but `db_session` drops and
recreates every table **per test function**. If a test imports its own
model module lazily instead of relying on the models already imported at
the top of `tests/conftest.py`, running that one test file in isolation
can create only the tables that file's own imports registered on
`Base.metadata`, silently dropping tables other tests in the same
session/run depend on existing.

**Fix**: never rely on a test file's own imports to populate
`Base.metadata` -- `tests/conftest.py` already imports every model module
explicitly (with `# noqa: F401`) specifically so this can't happen; if a
new model module is added, it must be added to that same import list.

### Symptom: `role "app_role" already exists` (or similar) when running
tests repeatedly

**Cause**: `app_role`/`relay_role` are created once per **cluster**
(session-scoped), not per test -- this is expected and idempotent by
design (`enable_relay_role_access` itself uses `IF NOT EXISTS` for the
role, per-test grant/policy application is naturally idempotent via `DROP
POLICY IF EXISTS`/`CREATE POLICY`). If you see a hard failure here rather
than a silent no-op, it likely indicates a change to the role-creation SQL
that removed its own idempotency guard -- check the `DO $$ ... IF NOT
EXISTS ...` wrapper is still present.

## Relay signature verification failures

### Symptom: `RelaySignatureMismatchError` on every relay request after a
secret rotation

**Cause**: `RELAY_SIGNING_SECRET` was changed on the receiving
(commercial) side before every signal-copier relay worker instance picked
up the new value, **and** `RELAY_SIGNING_SECRET_PREVIOUS` was not set to
the old value during the overlap.

**Fix**: follow the two-step rotation procedure in
`docs/security/ARCHITECTURE.md` section 4 -- set
`RELAY_SIGNING_SECRET_PREVIOUS` to the old secret *before* rotating
`RELAY_SIGNING_SECRET`, and only unset `RELAY_SIGNING_SECRET_PREVIOUS`
once every sending instance is confirmed on the new secret.

### Symptom: `StaleRelayTimestampError` on requests that appear to have
just been sent

**Cause**: clock skew between the signal-copier host and the commercial
service host exceeds the 300-second default tolerance
(`_DEFAULT_TOLERANCE_SECONDS`), or a request genuinely sat in a queue/retry
loop longer than that window.

**Fix**: check NTP sync on both hosts first (this is the far more common
cause than a genuinely stale request). If legitimate delivery delays
regularly exceed 300 seconds, `tolerance_seconds` can be widened per call
to `verify_relay_signature()` -- but widening it also widens the in-window
replay surface (see `docs/security/THREAT_MODEL.md` gap 3).

## Stream registration

### Symptom: every SOURCE_RECEIPT event from a paired signal-copier
deployment parks as `unregistered_stream` (raises `UnregisteredStreamError`)
even though EXECUTION_APPLIED events apply fine

**Cause**: `ops/bootstrap.py` (or a manual `register_export_stream()` call)
only registered the account-id stream(s), not the source-name stream(s).
signal-copier exports on **two independent** stream namespaces:
`signal-copier:<account_id>` (EXECUTION_APPLIED) and
`signal-copier:source:<source_name>` (SOURCE_RECEIPT) -- registering only
one leaves the other permanently unregistered.

**Fix**: re-run `ops/bootstrap.py` with **both** `--account-id` (one per
enabled account in the paired deployment's `accounts.yaml`) and
`--source-name` (one per `source` in its `routing.yaml`) -- both flags are
repeatable and both are required at least once each.
