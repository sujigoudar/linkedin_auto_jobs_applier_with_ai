# Logging Standards

## The real convention: `audit_events`, not Python `logging`

There is no `import logging` and no `logging.getLogger(...)` call
anywhere in `app/`. This is not an oversight to "fix" by adding a logger
-- it is the actual, deliberate convention, stated directly in
`app/services/evidence_manifest.py`'s module docstring: generating an
evidence manifest export is "a real, auditable command ... logged via
`app/services/audit_log.py::append_audit_event`, **the one real writer
to `audit_events`, not a second logging path**."

For anything that is a real user/system action worth a durable record
(a grant, a revoke, a publish, an export, a config change), the
convention is:

```python
from app.services.audit_log import append_audit_event

append_audit_event(
    session,
    tenant_id=scope.tenant_id,
    actor_user_id=scope.user_id,
    object_type="membership",
    object_id=user_id,
    action="revoke_staff_member",
    outcome=AuditOutcome.SUCCESS,  # or FAILURE
)
```

Properties this gives you that `logging.info(...)` would not:

- **It is a real, queryable database row** (`app/models/audit_event.py`),
  not a line in a log file that rotates away -- `list_audit_events` and
  `get_object_timeline` (`app/services/audit_log.py`) are the real read
  paths, and AD-18's own UI is built directly on them.
- **It is append-only, enforced by Postgres itself**
  (`app/db.py::enforce_append_only`, the `forbid_ledger_mutation`
  trigger), not just by convention -- "Audit cannot be edited through UI
  holds even against a caller who forgot this discipline, not just one
  who follows it" (`audit_log.py` docstring). A `logging` call gives no
  such guarantee.
- **It is tenant-scoped and RLS-protected** like every other tenant table
  (`audit_events` is in `app/db.py`'s `_TENANT_SCOPED_TABLES`), so one
  tenant's audit trail is not readable by another's session, unlike a
  shared process-wide log stream.
- **It is exportable with integrity verification**: `app/services/
  evidence_manifest.py` bundles matching rows verbatim and hashes them
  with `signal_platform_contracts.compute_payload_hash`, so a later
  tamper attempt against the exported bundle is detectable (see
  `tests/test_evidence_manifest.py::test_manifest_hash_detects_a_single_
  tampered_row`).

## When to append an audit event

Every service function that performs a real state-changing command that a
human operator (owner/staff) or a security-relevant flow initiates should
append one, following the pattern already used by `staff_access.py`
(invite/revoke), `product_admin.py`, `release_review.py`,
`publication_admin.py`, and `evidence_manifest.py` itself. Read-only
queries and internal helper functions do not.

Use a real, specific `action` string (e.g. `"revoke_staff_member"`,
`"generate_evidence_manifest"`) that matches the actual method/verb, not a
generic `"update"` -- the audit trail is only as useful as its own action
names are specific, and `EVIDENCE_MANIFEST_EXPORT_ACTION`-style named
constants (rather than inline string literals scattered across call
sites) are the existing pattern for an action that's referenced from more
than one place.

## Cross-service boundary

`app/db.py`'s own module docstring is explicit that this service's
Postgres store is "Entirely separate from `signal-copier/app/db.py`'s
SQLite execution store -- this process never opens that file and that
process never opens this one." The audit trail described here is
therefore this service's own; it does not, and must not be made to,
double as a log of what `signal-copier` itself does internally (that
service has its own logging/audit conventions).

## What this means in practice for a new module

- Do not add `import logging` / a module-level `logger = logging.
  getLogger(__name__)` to `app/` code. If you find yourself wanting to
  log "who did what, when," that is an `append_audit_event` call, not a
  log line.
- Debug/diagnostic output during development (e.g. a print statement)
  should never be committed -- there is currently zero use of `print(...)`
  in `app/` either, and adding one would be inconsistent with the rest of
  the codebase's real, structured, queryable audit trail.
