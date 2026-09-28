"""AD-21 "Commercial incidents and obligations" -- the real query/command
service backing F-INCIDENT. See dashboard_spec/screens/AD-21.md for the
full screen contract this implements a bounded slice of.

Bounded scope: no monitoring/alerting pipeline creates `Incident` rows
in this build -- see app/models/incident.py's own docstring -- so this
module has no "create incident" entry point at all; a real incident is
opened directly against a real affected object this tenant already
owns (by whatever operational process later grows one), never
fabricated here. What IS real: the four F-INCIDENT operations
(acknowledge/assign/reconcile/propose_resolution), each of which
enforces "Acknowledge never marks resolved" and "Reconcile is
read-only... unless separately approved corrective command" (AD-21's
own acceptance text) as actual state-machine guards, and each of which
appends a real `AuditEvent` -- that append IS AD-21-P03 "Timeline",
reusing app/services/audit_log.py rather than inventing a second
history store.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.incident import Incident, IncidentState
from app.models.tenancy import Membership, MembershipRole
from app.services.audit_log import append_audit_event

_MAX_NOTE_LENGTH = 2000
#: "Cannot assign financial authority" (F-INCIDENT's own field help for
#: assignee_id) -- BILLING_OPERATOR is excluded; CUSTOMER has no
#: operator scope at all and is excluded for the same reason every
#: other AD-* operator action excludes it.
_ASSIGNABLE_ROLES: frozenset[MembershipRole] = frozenset(
    {
        MembershipRole.OWNER,
        MembershipRole.RESEARCHER,
        MembershipRole.REVIEWER,
        MembershipRole.PUBLISHER_OPERATOR,
        MembershipRole.SUPPORT_READONLY,
    }
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


class InvalidIncidentOperationError(Exception):
    pass


def list_incidents(
    session: Session,
    *,
    tenant_id: str,
    service: str | None = None,
    severity: str | None = None,
    state: str | None = None,
) -> list[Incident]:
    query = select(Incident).where(Incident.tenant_id == tenant_id)
    if service:
        query = query.where(Incident.service == service)
    if severity:
        query = query.where(Incident.severity == severity)
    if state:
        query = query.where(Incident.state == state)
    query = query.order_by(Incident.created_at.desc(), Incident.incident_id.desc())
    return list(session.scalars(query).all())


def get_incident(session: Session, incident_id: str, *, tenant_id: str) -> Incident | None:
    incident = session.get(Incident, incident_id)
    if incident is None or incident.tenant_id != tenant_id:
        return None
    return incident


def _validate_note(note: str) -> None:
    if not note or not (1 <= len(note) <= _MAX_NOTE_LENGTH):
        raise InvalidIncidentOperationError(f"note must be 1..{_MAX_NOTE_LENGTH} characters")


def acknowledge_incident(session: Session, incident: Incident, *, actor_user_id: str, note: str) -> Incident:
    """"Acknowledge never marks resolved" -- the ONLY state transition
    this makes is OPEN -> ACKNOWLEDGED; acknowledging an
    already-acknowledged/assigned/resolved incident is refused rather
    than silently re-accepted, so a stale double-submit can never be
    mistaken for real progress."""
    _validate_note(note)
    if incident.state != IncidentState.OPEN:
        raise InvalidIncidentOperationError(f"incident is {incident.state.value}, not OPEN; cannot acknowledge again")
    incident.state = IncidentState.ACKNOWLEDGED
    incident.updated_at = _now()
    append_audit_event(
        session,
        tenant_id=incident.tenant_id,
        actor_user_id=actor_user_id,
        object_type="incident",
        object_id=incident.incident_id,
        action=f"acknowledge_incident:{note}",
    )
    return incident


def assign_incident(
    session: Session, incident: Incident, *, actor_user_id: str, assignee_id: str, note: str
) -> Incident:
    _validate_note(note)
    if incident.state == IncidentState.RESOLVED:
        raise InvalidIncidentOperationError("incident is RESOLVED; cannot reassign a resolved incident")
    membership = session.get(Membership, (incident.tenant_id, assignee_id))
    if membership is None:
        raise InvalidIncidentOperationError("assignee_id does not reference a member of this tenant")
    if membership.role not in _ASSIGNABLE_ROLES:
        raise InvalidIncidentOperationError(f"role {membership.role.value!r} is not incident-eligible")
    incident.assignee_user_id = assignee_id
    incident.state = IncidentState.ASSIGNED
    incident.updated_at = _now()
    append_audit_event(
        session,
        tenant_id=incident.tenant_id,
        actor_user_id=actor_user_id,
        object_type="incident",
        object_id=incident.incident_id,
        action=f"assign_incident:{assignee_id}:{note}",
    )
    return incident


def reconcile_incident(session: Session, incident: Incident, *, actor_user_id: str, note: str) -> Incident:
    """"Reconcile is read-only broker action unless separately approved
    corrective command" (AD-21's own acceptance text) -- no corrective-
    command execution path exists in this build, so this NEVER changes
    `incident.state`; it only ever records the read-only reconciliation
    note in the timeline, exactly as read-only as the spec requires."""
    _validate_note(note)
    if incident.state == IncidentState.RESOLVED:
        raise InvalidIncidentOperationError("incident is RESOLVED; nothing left to reconcile")
    append_audit_event(
        session,
        tenant_id=incident.tenant_id,
        actor_user_id=actor_user_id,
        object_type="incident",
        object_id=incident.incident_id,
        action=f"reconcile_incident:{note}",
    )
    return incident


def propose_resolution(
    session: Session, incident: Incident, *, actor_user_id: str, note: str, evidence_ids: list[str]
) -> Incident:
    """F-INCIDENT: "evidence_ids... required for resolution proposal" --
    enforced here as a real, named validation error, never silently
    accepted with an empty list."""
    _validate_note(note)
    if incident.state == IncidentState.RESOLVED:
        raise InvalidIncidentOperationError("incident is already RESOLVED")
    if not evidence_ids:
        raise InvalidIncidentOperationError("evidence_ids is required to propose a resolution")
    incident.resolution_note = note
    incident.evidence_ids = list(evidence_ids)
    incident.state = IncidentState.RESOLVED
    incident.updated_at = _now()
    append_audit_event(
        session,
        tenant_id=incident.tenant_id,
        actor_user_id=actor_user_id,
        object_type="incident",
        object_id=incident.incident_id,
        action=f"propose_resolution:{note}",
    )
    return incident
