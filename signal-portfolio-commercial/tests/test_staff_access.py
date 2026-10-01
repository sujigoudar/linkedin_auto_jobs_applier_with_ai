"""AD-16 "Staff roles and access reviews" -- app/services/staff_access.py's
own tests. Real Postgres, real tenant-scoped session."""
import pytest

from app.models.tenancy import Membership, MembershipRole, Tenant, UserIdentity
from app.services.audit_log import list_audit_events
from app.services.staff_access import (
    AlreadyAMemberError,
    CannotRevokeOwnerError,
    CannotRevokeSelfError,
    InvalidStaffGrantError,
    MembershipNotFoundError,
    UnknownUserIdentityError,
    invite_staff_member,
    list_staff_memberships,
    revoke_staff_member,
)


def _seed_tenant_with_owner(db_session, *, tenant_id="tenant-a", owner_user_id="owner-a"):
    db_session.add(Tenant(tenant_id=tenant_id, display_name="Tenant", environment="LOCAL_SIM"))
    db_session.flush()
    db_session.add(UserIdentity(user_id=owner_user_id, email=f"{owner_user_id}@example.com"))
    db_session.flush()
    db_session.add(Membership(tenant_id=tenant_id, user_id=owner_user_id, role=MembershipRole.OWNER))
    db_session.commit()


def test_list_staff_memberships_excludes_customer_rows(db_session):
    _seed_tenant_with_owner(db_session)
    db_session.add(UserIdentity(user_id="customer-a", email="customer-a@example.com"))
    db_session.flush()
    db_session.add(Membership(tenant_id="tenant-a", user_id="customer-a", role=MembershipRole.CUSTOMER))
    db_session.commit()

    memberships = list_staff_memberships(db_session, tenant_id="tenant-a")
    assert {m.user_id for m in memberships} == {"owner-a"}


def test_invite_staff_member_requires_an_existing_user_identity(db_session):
    _seed_tenant_with_owner(db_session)
    with pytest.raises(UnknownUserIdentityError) as exc_info:
        invite_staff_member(
            db_session, tenant_id="tenant-a", user_id="no-such-user", role=MembershipRole.RESEARCHER,
            acting_user_id="owner-a",
        )
    assert str(exc_info.value) == (
        "no existing identity for user_id 'no-such-user' -- an invite/signup flow must create it first"
    )


def test_invite_staff_member_rejects_granting_owner(db_session):
    _seed_tenant_with_owner(db_session)
    db_session.add(UserIdentity(user_id="new-staff", email="new-staff@example.com"))
    db_session.commit()
    with pytest.raises(InvalidStaffGrantError) as exc_info:
        invite_staff_member(db_session, tenant_id="tenant-a", user_id="new-staff", role=MembershipRole.OWNER, acting_user_id="owner-a")
    assert str(exc_info.value) == "'owner' cannot be granted through this form"


def test_invite_staff_member_rejects_granting_customer(db_session):
    _seed_tenant_with_owner(db_session)
    db_session.add(UserIdentity(user_id="new-staff", email="new-staff@example.com"))
    db_session.commit()
    with pytest.raises(InvalidStaffGrantError) as exc_info:
        invite_staff_member(db_session, tenant_id="tenant-a", user_id="new-staff", role=MembershipRole.CUSTOMER, acting_user_id="owner-a")
    assert str(exc_info.value) == "'customer' cannot be granted through this form"


def test_invite_then_reload_creates_a_real_membership(db_session):
    _seed_tenant_with_owner(db_session)
    db_session.add(UserIdentity(user_id="new-staff", email="new-staff@example.com"))
    db_session.commit()

    invite_staff_member(db_session, tenant_id="tenant-a", user_id="new-staff", role=MembershipRole.RESEARCHER, acting_user_id="owner-a")
    db_session.commit()

    memberships = {m.user_id: m.role for m in list_staff_memberships(db_session, tenant_id="tenant-a")}
    assert memberships["new-staff"] == MembershipRole.RESEARCHER


def test_invite_staff_member_rejects_an_already_existing_membership(db_session):
    _seed_tenant_with_owner(db_session)
    db_session.add(UserIdentity(user_id="new-staff", email="new-staff@example.com"))
    db_session.commit()
    invite_staff_member(db_session, tenant_id="tenant-a", user_id="new-staff", role=MembershipRole.RESEARCHER, acting_user_id="owner-a")
    db_session.commit()

    with pytest.raises(AlreadyAMemberError) as exc_info:
        invite_staff_member(db_session, tenant_id="tenant-a", user_id="new-staff", role=MembershipRole.REVIEWER, acting_user_id="owner-a")
    assert str(exc_info.value) == "'new-staff' already has a membership in this tenant"


def test_revoke_staff_member_rejects_revoking_the_owner(db_session):
    _seed_tenant_with_owner(db_session)
    with pytest.raises(CannotRevokeOwnerError) as exc_info:
        revoke_staff_member(db_session, tenant_id="tenant-a", user_id="owner-a", acting_user_id="owner-a")
    assert str(exc_info.value) == "the owner role cannot be revoked through this form"


def test_revoke_staff_member_rejects_revoking_yourself(db_session):
    _seed_tenant_with_owner(db_session)
    db_session.add(UserIdentity(user_id="new-staff", email="new-staff@example.com"))
    db_session.commit()
    invite_staff_member(db_session, tenant_id="tenant-a", user_id="new-staff", role=MembershipRole.RESEARCHER, acting_user_id="owner-a")
    db_session.commit()

    with pytest.raises(CannotRevokeSelfError) as exc_info:
        revoke_staff_member(db_session, tenant_id="tenant-a", user_id="new-staff", acting_user_id="new-staff")
    assert str(exc_info.value) == "cannot revoke your own membership"


def test_revoke_staff_member_requires_an_existing_membership(db_session):
    _seed_tenant_with_owner(db_session)
    with pytest.raises(MembershipNotFoundError) as exc_info:
        revoke_staff_member(db_session, tenant_id="tenant-a", user_id="never-invited", acting_user_id="owner-a")
    assert str(exc_info.value) == "no membership for 'never-invited' in this tenant"


def test_revoke_then_reload_really_removes_the_membership(db_session):
    _seed_tenant_with_owner(db_session)
    db_session.add(UserIdentity(user_id="new-staff", email="new-staff@example.com"))
    db_session.commit()
    invite_staff_member(db_session, tenant_id="tenant-a", user_id="new-staff", role=MembershipRole.RESEARCHER, acting_user_id="owner-a")
    db_session.commit()

    revoke_staff_member(db_session, tenant_id="tenant-a", user_id="new-staff", acting_user_id="owner-a")
    db_session.commit()

    memberships = list_staff_memberships(db_session, tenant_id="tenant-a")
    assert "new-staff" not in {m.user_id for m in memberships}


def test_invite_staff_member_appends_a_real_audit_event_with_the_right_fields(db_session):
    # Mutation testing (Track 53) found that nothing asserted
    # `invite_staff_member`'s audit write actually carries the right
    # `object_type`/`action` -- a mutant corrupting either (e.g. a
    # wrong object_type, or an action string that drops which role was
    # granted) survived invisibly. This is AD-18's own append-only
    # audit trail, which an ops/compliance reviewer relies on to know
    # exactly what happened; a silently-wrong record is as bad as no
    # record.
    _seed_tenant_with_owner(db_session)
    db_session.add(UserIdentity(user_id="new-staff", email="new-staff@example.com"))
    db_session.commit()

    invite_staff_member(db_session, tenant_id="tenant-a", user_id="new-staff", role=MembershipRole.RESEARCHER, acting_user_id="owner-a")
    db_session.commit()

    events = list_audit_events(db_session, tenant_id="tenant-a", object_id="new-staff")
    assert len(events) == 1
    event = events[0]
    assert event.object_type == "membership"
    assert event.action == "invite_staff_member:researcher"
    assert event.actor_user_id == "owner-a"
    assert event.object_id == "new-staff"


def test_revoke_staff_member_appends_a_real_audit_event_with_the_right_fields(db_session):
    # Same gap as the invite case above, on the revoke side: nothing
    # asserted `revoke_staff_member`'s own audit write's
    # `object_type`/`action`.
    _seed_tenant_with_owner(db_session)
    db_session.add(UserIdentity(user_id="new-staff", email="new-staff@example.com"))
    db_session.commit()
    invite_staff_member(db_session, tenant_id="tenant-a", user_id="new-staff", role=MembershipRole.RESEARCHER, acting_user_id="owner-a")
    db_session.commit()

    revoke_staff_member(db_session, tenant_id="tenant-a", user_id="new-staff", acting_user_id="owner-a")
    db_session.commit()

    events = list_audit_events(db_session, tenant_id="tenant-a", object_id="new-staff", action="revoke_staff_member")
    assert len(events) == 1
    event = events[0]
    assert event.object_type == "membership"
    assert event.action == "revoke_staff_member"
    assert event.actor_user_id == "owner-a"
    assert event.object_id == "new-staff"
