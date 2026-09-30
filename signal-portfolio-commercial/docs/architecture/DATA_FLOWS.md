# Data Flows

## Flow 1: signal-copier execution → commercial ledger → analyst attribution

The real, end-to-end path from a signal-copier fill to a number a staff
member or customer sees.

```mermaid
sequenceDiagram
    participant SC as signal-copier<br/>relay worker
    participant Relay as relay_routes.py<br/>POST /internal/relay/ingest-batch<br/>(relay_role)
    participant Auth as relay_auth.py<br/>verify_relay_signature
    participant Inbox as integration_inbox.py<br/>ingest_export_event
    participant DB as Postgres<br/>(RLS: tenant_isolation)
    participant Ledger as ledger.py<br/>append_entry
    participant Attr as analyst_attribution.py<br/>compute_analyst_attribution

    SC->>Relay: POST batch of envelope JSON strings<br/>(X-Relay-Signature: HMAC)
    Relay->>Auth: verify_relay_signature(body, header, secret)
    Auth-->>Relay: OK (or 401)
    loop each envelope in batch
        Relay->>Inbox: ingest_export_event(session, envelope_json)
        Inbox->>DB: SELECT export_stream_registrations<br/>WHERE source_stream = ? (relay_stream_lookup policy,<br/>unscoped — the ONE lookup relay_role can do pre-scope)
        DB-->>Inbox: tenant_id
        Inbox->>DB: set_tenant_scope(session, tenant_id)<br/>(set_config app.tenant_id, is_local=true)
        Inbox->>DB: SELECT inbox_events WHERE event_id = ?<br/>(idempotency check, now tenant-scoped)
        alt event_id already seen, same payload_hash
            DB-->>Inbox: existing row (no-op)
        else event_id seen, DIFFERENT payload_hash
            Inbox-->>Relay: raise EventIntegrityError
        else new event, export_sequence == next expected
            Inbox->>DB: INSERT inbox_events (received_at set)
            Inbox->>Ledger: append_entry(book=SOURCE or PLATFORM,<br/>evidence_class=envelope.evidence_class, ...)
            Ledger->>DB: INSERT ledger_entries<br/>(append-only trigger allows INSERT)
            DB-->>Inbox: entry_id
            Inbox->>DB: UPDATE inbox_events SET applied_at, ledger_entry_id
            Inbox->>Inbox: cascade: apply any already-received<br/>event now next-in-line
        else export_sequence > expected (gap)
            Inbox->>DB: INSERT inbox_events (received_at set,<br/>applied_at stays NULL — parked)
        end
        Relay->>DB: session.commit() (per event, before reading attributes)
    end
    Relay-->>SC: {"results": [{"status": "applied"|"parked"|..., ...}]}

    Note over Attr: Later, on demand (AD-05/AD-06/AD-12 screens)
    Attr->>DB: SELECT ledger_entries WHERE tenant_id=? AND book=PLATFORM<br/>ORDER BY event_time (RLS-scoped read)
    DB-->>Attr: ordered LedgerEntry rows
    Attr->>Attr: FIFO-lot replay per (instrument):<br/>tag each lot with originating_analyst_id,<br/>consume oldest-first on a reducing fill
    Attr-->>Attr: AnalystAttributionReport<br/>(per-analyst realized P&L, completed episodes, win rate)
```

Key invariants this flow enforces (see ARCHITECTURE.md §2–3 for why):

- The envelope never carries a `tenant_id` — only the owner-registered
  `ExportStreamRegistration` row determines which tenant an event
  belongs to.
- Nothing is ever silently coerced: an unsupported schema version,
  unimplemented event type, generation rollback, or manifest
  inconsistency **parks** the row with a named `parked_reason` rather
  than fabricating a projection.
- `ledger_entries` is append-only at the database level (a trigger, not
  just application discipline) — a `FEE` correction is a **new** row
  (`correction_of`), never an edit of the original `EXECUTION_APPLIED`
  entry.
- `analyst_attribution.py`'s per-analyst sum is guaranteed (by
  construction of the FIFO replay) to reconcile exactly to
  `platform_performance.py`'s blended aggregate for the same
  tenant/book — no P&L is ever duplicated or dropped between the two
  views.

## Flow 2: Publication admission (staff intent → publisher adapter)

```mermaid
sequenceDiagram
    participant Staff as Staff (PUBLISHER_OPERATOR)
    participant Routes as dashboard_routes.py
    participant Admission as publication_admission.py<br/>admit_publication_intent
    participant Rights as portfolio_rights.py<br/>check_portfolio_rights
    participant Entitle as entitlement.py
    participant Adapter as collective2_publisher.py /<br/>etoro_adapter.py (build-only)

    Staff->>Routes: (queue a PublicationIntent elsewhere)
    Routes->>Admission: admit_publication_intent(intent)
    Admission->>Rights: check_portfolio_rights(portfolio_version)<br/>(RE-CHECKED fresh, not trusted from an earlier read)
    Rights-->>Admission: every member sleeve qualifies? y/n
    Admission->>Entitle: authorizes_new_entry? (OPEN/ADD only —<br/>risk-reducing actions like STOP_UPDATE are exempt)
    Entitle-->>Admission: y/n
    alt both pass
        Admission->>Adapter: build_order(intent) [never transmitted —<br/>no real platform credentials in this build]
        Adapter-->>Admission: validated request shape
    else either fails
        Admission-->>Routes: refused, named reason
    end
```

## Flow 3: Audit trail → evidence manifest export (AD-18)

```mermaid
sequenceDiagram
    participant Staff as Staff (OWNER/REVIEWER)
    participant Routes as dashboard_routes.py<br/>POST /ops/audit/evidence-manifest
    participant Manifest as evidence_manifest.py<br/>generate_evidence_manifest
    participant AuditLog as audit_log.py

    Staff->>Routes: filter (actor/object/action)
    Routes->>Manifest: generate_evidence_manifest(filter)
    Manifest->>AuditLog: list_audit_events(filter)
    AuditLog-->>Manifest: matching AuditEvent rows (verbatim)
    Manifest->>Manifest: compute_manifest_content_hash(rows)<br/>(signal_platform_contracts.compute_payload_hash)
    Manifest->>AuditLog: append_audit_event(action="export_evidence_manifest")<br/>(the export itself is an auditable command)
    Manifest-->>Routes: EvidenceManifest{rows, content_hash, ...}
```
