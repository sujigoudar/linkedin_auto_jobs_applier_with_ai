"""AD-18 "Audit log and release evidence" -- the real append/search
service backing this screen. See dashboard_spec/screens/AD-18.md for
the full screen contract this implements a bounded slice of.

`append_audit_event` is the only way a row is ever created here, and
the database itself refuses any UPDATE/DELETE against `audit_events`
(app/db.py's `enforce_append_only`) -- "Audit cannot be edited through
UI" holds even against a caller who forgot this discipline, not just
one who follows it.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.audit_event import AuditEvent, AuditOutcome


def append_audit_event(
    session: Session,
    *,
    tenant_id: str,
    actor_user_id: str,
    object_type: str,
    object_id: str,
    action: str,
    outcome: AuditOutcome = AuditOutcome.SUCCESS,
) -> AuditEvent:
    event = AuditEvent(
        tenant_id=tenant_id,
        actor_user_id=actor_user_id,
        object_type=object_type,
        object_id=object_id,
        action=action,
        outcome=outcome,
    )
    session.add(event)
    session.flush()
    return event


def list_audit_events(
    session: Session,
    *,
    tenant_id: str,
    actor_user_id: str | None = None,
    object_id: str | None = None,
    action: str | None = None,
) -> list[AuditEvent]:
    query = select(AuditEvent).where(AuditEvent.tenant_id == tenant_id)
    if actor_user_id:
        query = query.where(AuditEvent.actor_user_id == actor_user_id)
    if object_id:
        query = query.where(AuditEvent.object_id == object_id)
    if action:
        query = query.where(AuditEvent.action == action)
    query = query.order_by(AuditEvent.event_time.desc(), AuditEvent.event_id.desc())
    return list(session.scalars(query).all())


def get_object_timeline(session: Session, *, tenant_id: str, object_id: str) -> list[AuditEvent]:
    """Immutable ordered events for one object -- oldest first, so a
    timeline reads in the order things actually happened, never
    reordered to imply a better outcome (AD-18's own panel contract)."""
    return list(
        session.scalars(
            select(AuditEvent)
            .where(AuditEvent.tenant_id == tenant_id, AuditEvent.object_id == object_id)
            .order_by(AuditEvent.event_time.asc(), AuditEvent.event_id.asc())
        ).all()
    )
