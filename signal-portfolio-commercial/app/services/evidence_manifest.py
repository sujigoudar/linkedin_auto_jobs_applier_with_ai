"""AD-18 "Audit log and release evidence" -- the real, synchronous
"Evidence manifest" export. See dashboard_spec/screens/AD-18.md for the
full screen contract this implements a bounded slice of, and
app/templates/ad18_audit.html's own former "UNSUPPORTED -- no
evidence-bundling model exists in this build" note for the gap this
module closes.

Deliberately synchronous, not a job queue: an operator picks a real,
bounded filter (the same actor/object/action filter AD-18's own search
panel already supports -- see app/services/audit_log.py::list_audit_events),
and this module bundles the matching `AuditEvent` rows -- VERBATIM,
never summarized, redacted or reordered -- into one manifest with a
content hash so the export's own integrity can later be verified.

The hash reuses `signal_platform_contracts.compute_payload_hash`
(the same "stable hash of a payload's canonical JSON form -- sorted
keys, no whitespace" this platform already uses to detect the same
identity arriving with a different hash as tampering, per
`signal_platform_contracts.envelope`'s own docstring) rather than
inventing a second hashing convention for the same job.

Generating this manifest is itself a real, auditable command -- see
this module's own `EVIDENCE_MANIFEST_EXPORT_ACTION` -- logged via
`app/services/audit_log.py::append_audit_event`, the one real writer
to `audit_events`, not a second logging path.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.models.audit_event import AuditEvent
from app.services.audit_log import append_audit_event, list_audit_events
from signal_platform_contracts import compute_payload_hash

#: The action string this export itself is logged under -- an evidence
#: export is a real command against the audit trail, so it leaves a
#: real trail of its own, same as every other AD-18 adjacent command.
EVIDENCE_MANIFEST_EXPORT_ACTION = "export_evidence_manifest"
#: The `object_type`/`object_id` this export's own AuditEvent is filed
#: under -- there is no single object this command changes, so it is
#: filed against the audit log itself, matching how a tenant-wide
#: command with no single target object is otherwise represented.
EVIDENCE_MANIFEST_OBJECT_TYPE = "audit_log"
EVIDENCE_MANIFEST_OBJECT_ID = "evidence_manifest"


def _event_to_row(event: AuditEvent) -> dict[str, Any]:
    """The exact, verbatim fields of one AuditEvent row -- no summarizing,
    redacting or reformatting. Every column on the model is included."""
    return {
        "event_id": event.event_id,
        "tenant_id": event.tenant_id,
        "actor_user_id": event.actor_user_id,
        "object_type": event.object_type,
        "object_id": event.object_id,
        "action": event.action,
        "outcome": event.outcome.value,
        "event_time": event.event_time.isoformat(),
        "created_at": event.created_at.isoformat(),
    }


@dataclass(frozen=True)
class EvidenceManifest:
    """A bounded, verbatim bundle of real AuditEvent rows plus a header
    that lets the export's own integrity be verified later."""

    generated_at: datetime
    generated_by_actor_user_id: str
    tenant_id: str
    filter_criteria: dict[str, str | None]
    row_count: int
    content_hash: str
    rows: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "manifest": {
                "generated_at": self.generated_at.isoformat(),
                "generated_by_actor_user_id": self.generated_by_actor_user_id,
                "tenant_id": self.tenant_id,
                "filter_criteria": self.filter_criteria,
                "row_count": self.row_count,
                "content_hash": self.content_hash,
            },
            "rows": self.rows,
        }


def compute_manifest_content_hash(rows: list[dict[str, Any]]) -> str:
    """The manifest's own content hash -- a hash of the exact, ordered
    list of verbatim rows being bundled, so a later re-hash of the same
    rows (in the same order) confirms nothing in the export changed,
    and a single altered field genuinely changes the hash (see this
    module's own load-bearing tamper-detection test)."""
    return compute_payload_hash({"rows": rows})


def generate_evidence_manifest(
    session: Session,
    *,
    tenant_id: str,
    generated_by_actor_user_id: str,
    actor_user_id: str | None = None,
    object_id: str | None = None,
    action: str | None = None,
) -> EvidenceManifest:
    """Bundle the real AuditEvent rows matching this filter into one
    synchronous, verbatim evidence manifest, and log the export itself
    as a new real AuditEvent (reusing `append_audit_event` -- an
    evidence export is itself an auditable command, not a silent read).
    """
    events = list_audit_events(
        session,
        tenant_id=tenant_id,
        actor_user_id=actor_user_id,
        object_id=object_id,
        action=action,
    )
    rows = [_event_to_row(event) for event in events]
    content_hash = compute_manifest_content_hash(rows)
    filter_criteria = {
        "actor_user_id": actor_user_id,
        "object_id": object_id,
        "action": action,
    }

    append_audit_event(
        session,
        tenant_id=tenant_id,
        actor_user_id=generated_by_actor_user_id,
        object_type=EVIDENCE_MANIFEST_OBJECT_TYPE,
        object_id=EVIDENCE_MANIFEST_OBJECT_ID,
        action=EVIDENCE_MANIFEST_EXPORT_ACTION,
    )

    return EvidenceManifest(
        generated_at=datetime.now(timezone.utc),
        generated_by_actor_user_id=generated_by_actor_user_id,
        tenant_id=tenant_id,
        filter_criteria=filter_criteria,
        row_count=len(rows),
        content_hash=content_hash,
        rows=rows,
    )
