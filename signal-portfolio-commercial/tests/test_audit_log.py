"""AD-18 "Audit log and release evidence" -- app/services/audit_log.py's
own tests. Real Postgres, real tenant-scoped session."""
import pytest
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError

from app.services.audit_log import append_audit_event, get_object_timeline, list_audit_events


def test_list_audit_events_is_empty_before_any_are_appended(db_session):
    assert list_audit_events(db_session, tenant_id="tenant-a") == []


def test_append_then_list_shows_the_real_event(db_session):
    append_audit_event(
        db_session,
        tenant_id="tenant-a",
        actor_user_id="owner-a",
        object_type="membership",
        object_id="user-b",
        action="invite_staff_member:researcher",
    )
    db_session.commit()

    events = list_audit_events(db_session, tenant_id="tenant-a")
    assert len(events) == 1
    assert events[0].action == "invite_staff_member:researcher"


def test_list_audit_events_is_scoped_to_the_callers_own_tenant(db_session):
    append_audit_event(
        db_session, tenant_id="tenant-a", actor_user_id="owner-a", object_type="membership", object_id="x", action="a"
    )
    append_audit_event(
        db_session, tenant_id="tenant-b", actor_user_id="owner-b", object_type="membership", object_id="y", action="b"
    )
    db_session.commit()

    events = list_audit_events(db_session, tenant_id="tenant-a")
    assert len(events) == 1
    assert events[0].object_id == "x"


def test_get_object_timeline_orders_oldest_first(db_session):
    append_audit_event(
        db_session, tenant_id="tenant-a", actor_user_id="owner-a", object_type="membership", object_id="obj-1", action="first"
    )
    db_session.commit()
    append_audit_event(
        db_session, tenant_id="tenant-a", actor_user_id="owner-a", object_type="membership", object_id="obj-1", action="second"
    )
    db_session.commit()

    timeline = get_object_timeline(db_session, tenant_id="tenant-a", object_id="obj-1")
    assert [e.action for e in timeline] == ["first", "second"]


def test_audit_events_table_refuses_a_direct_update(db_session):
    event = append_audit_event(
        db_session, tenant_id="tenant-a", actor_user_id="owner-a", object_type="membership", object_id="obj-1", action="first"
    )
    db_session.commit()

    with pytest.raises(ProgrammingError, match="append-only"):
        db_session.execute(text("UPDATE audit_events SET action = 'changed' WHERE event_id = :id"), {"id": event.event_id})
    db_session.rollback()


def test_audit_events_table_refuses_a_direct_delete(db_session):
    event = append_audit_event(
        db_session, tenant_id="tenant-a", actor_user_id="owner-a", object_type="membership", object_id="obj-1", action="first"
    )
    db_session.commit()

    with pytest.raises(ProgrammingError, match="append-only"):
        db_session.execute(text("DELETE FROM audit_events WHERE event_id = :id"), {"id": event.event_id})
    db_session.rollback()
