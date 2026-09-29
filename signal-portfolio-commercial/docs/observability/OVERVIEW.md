# Observability

## What actually exists

This service has one real, structured observability mechanism: the
**append-only audit trail** (`app/models/audit_event.py`,
`app/services/audit_log.py`) plus its verbatim export
(`app/services/evidence_manifest.py`). There is **no Prometheus (or any
other metrics-library) instrumentation anywhere in this codebase** -- a
direct search for `prometheus_client`/`import prometheus` across the
repository returns nothing. Any "metrics" language elsewhere in the code
(e.g. `platform_performance.py`'s "scoped financial/provider metrics")
refers to computed financial/business figures shown on dashboard screens,
not to a metrics-collection/exposition system.

## The audit trail (AD-18)

### Writer: `app/services/audit_log.py::append_audit_event`

The **only** way an `AuditEvent` row is ever created. Every row carries
`tenant_id`, `actor_user_id`, `object_type`, `object_id`, `action`, and an
`outcome` (`AuditOutcome`, defaults to `SUCCESS`).

### Enforcement: append-only at the database layer

`app/db.py::enforce_append_only()` installs the same `forbid_ledger_mutation()`
trigger on `audit_events` that guards the ledger tables -- "Audit cannot be
edited through UI" holds even against a caller who forgot this discipline,
not just one who follows it. See `docs/security/ARCHITECTURE.md` section 3
and `docs/operations/DR.md`.

### Real writers into the store (i.e. what is actually audited today)

Traced directly to `append_audit_event()` call sites read for this
document:

| Writer | `object_type` | `action`(s) |
|---|---|---|
| `app/services/local_auth.py::create_web_session`/`delete_web_session` | `"session"` | `"login"`, `"logout"` |
| `app/services/token_revocation.py::revoke_token`/`revoke_all_tokens_for_user` | `"api_token"` | `"revoke_token"`, `"revoke_all_tokens"` |
| `app/services/staff_access.py::invite_staff_member`/`revoke_staff_member` | `"membership"` | `"invite_staff_member:<role>"`, `"revoke_staff_member"` |
| `app/services/evidence_manifest.py::generate_evidence_manifest` | `"audit_log"` | `"export_evidence_manifest"` |

Per `INTEGRATION_ACCEPTANCE_STATUS.md`'s own slice-21 note (sibling repo's
integration status document, read for cross-reference): `staff_access.py`
was, at one point, "the FIRST real writer... other services do not write
here yet" for research-job creation, content-document draft
save/review/publish, and copy-mandate create/cancel -- those command
routes existed as separately-permissioned HTTP endpoints
(`require_permission(scope.role, "<distinct-string>")` per command family)
before being wired into `append_audit_event`. This document reflects the
writers verified present in the modules read for this pass; a broader
audit of every `app/api/dashboard_routes.py` command route against this
list was out of scope here.

### Readers

- `list_audit_events()` -- filterable by `actor_user_id`/`object_id`/
  `action`, always scoped to `tenant_id`, ordered newest-first.
- `get_object_timeline()` -- all events for one `object_id`, ordered
  **oldest-first** deliberately, "so a timeline reads in the order things
  actually happened, never reordered to imply a better outcome."

### The evidence-manifest export (AD-18)

`app/services/evidence_manifest.py::generate_evidence_manifest()` is a
**synchronous**, not queued, export -- an operator picks a bounded filter
(the same actor/object/action filter the search panel supports) and gets
back a verbatim bundle:

- Every matching `AuditEvent` row, in full, with **no summarizing,
  redacting, or reordering** (`_event_to_row()` includes every column).
- A `content_hash` (`compute_manifest_content_hash`, reusing
  `signal_platform_contracts.compute_payload_hash` -- "a stable hash of a
  payload's canonical JSON form: sorted keys, no whitespace") over the
  exact, ordered row list, so a later re-hash of the same rows confirms
  nothing in the export changed, and a single altered field genuinely
  changes the hash.
- Generating the manifest **is itself logged** as a new `AuditEvent`
  (`EVIDENCE_MANIFEST_EXPORT_ACTION = "export_evidence_manifest"`,
  filed under `object_type="audit_log"`, `object_id="evidence_manifest"`)
  -- an evidence export is a real command against the audit trail, not a
  silent read.
- Access is gated by `export_evidence_manifest` in
  `app/services/permissions.py`, deliberately identical to `view_audit_log`
  (OWNER, REVIEWER) -- see `docs/security/AUTHORIZATION.md`.

## Health/status surfaces that exist in this service

- `deploy/entrypoint-commercial.sh` fails the container's own startup
  loudly (non-zero exit, `set -eu`) if either `alembic upgrade head` or
  `ops/bootstrap.py` fails -- there is no silent partial-startup state.
- No `GET /health`-style endpoint was found in the files read for this
  document within this service itself (unlike signal-copier's own
  `/health`, which does expose `outbox_backlog_ok`/`relay_ok`/
  `provider_scout_ok` -- see `docs/integrations/CATALOG.md` and
  `docs/operations/SLO.md`). `app/api/dashboard_routes.py`/`relay_routes.py`
  were not read in full for this pass; a dedicated health endpoint may
  exist there and should be confirmed before this line is treated as
  exhaustive.

## Genuine gaps

1. **No metrics/counters system.** There is no request-count, latency
   histogram, or error-rate instrumentation anywhere in this codebase.
   Anything resembling "monitoring" that a real deployment needs (per
   `spec/docs/13_operations_deployment_and_cost.md`'s long monitoring
   list -- source staleness, publication backlog, model-vs-actual tracking
   gap, subscribed-account capacity, billing event lag, tenant-denial
   anomalies, per-channel error/quotas, cost budgets, backup-generation
   age, active-writer evidence) is a **stated requirement**, not an
   implemented system. None of those specific signals has corresponding
   code in this service.
2. **No structured application logging convention identified.** No
   `structlog`/`logging` configuration pattern common to every module was
   found in the files read for this document -- signal-copier's own
   `relay_scheduler.py` is documented (via INT-040) as emitting a
   structured `ERROR`-level log on every poll pass while over the outbox
   ceiling, but no equivalent was found on this service's own side for
   ingest lag or relay-verification failures.
3. **No distributed tracing.** No correlation-ID/trace-ID propagation
   mechanism was found across the relay ingress -> ledger write path.
4. **No alerting integration.** Nothing in this codebase pages, emails, or
   posts to a webhook on any condition -- the audit trail and (on
   signal-copier's side) the `/health` endpoint are both pull-based
   surfaces an external monitor would have to poll.

For the one concrete, tested numeric threshold this ecosystem does have
(the export outbox size ceiling), see `docs/operations/SLO.md`.
