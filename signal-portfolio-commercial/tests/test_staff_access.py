"""AD-16 "Staff roles and access reviews" -- app/services/staff_access.py's
own tests. Real Postgres, real tenant-scoped session."""
import pytest

from app.models.tenancy import Membership, MembershipRole, Tenant, UserIdentity
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
    with pytest.raises(UnknownUserIdentityError):
        invite_staff_member(
            db_session, tenant_id="tenant-a", user_id="no-such-user", role=MembershipRole.RESEARCHER,
            acting_user_id="owner-a",
        )


def test_invite_staff_member_rejects_granting_owner(db_session):
    _seed_tenant_with_owner(db_session)
    db_session.add(UserIdentity(user_id="new-staff", email="new-staff@example.com"))
    db_session.commit()
    with pytest.raises(InvalidStaffGrantError):
        invite_staff_member(db_session, tenant_id="tenant-a", user_id="new-staff", role=MembershipRole.OWNER, acting_user_id="owner-a")


def test_invite_staff_member_rejects_granting_customer(db_session):
    _seed_tenant_with_owner(db_session)
    db_session.add(UserIdentity(user_id="new-staff", email="new-staff@example.com"))
    db_session.commit()
    with pytest.raises(InvalidStaffGrantError):
        invite_staff_member(db_session, tenant_id="tenant-a", user_id="new-staff", role=MembershipRole.CUSTOMER, acting_user_id="owner-a")


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

    with pytest.raises(AlreadyAMemberError):
        invite_staff_member(db_session, tenant_id="tenant-a", user_id="new-staff", role=MembershipRole.REVIEWER, acting_user_id="owner-a")


def test_revoke_staff_member_rejects_revoking_the_owner(db_session):
    _seed_tenant_with_owner(db_session)
    with pytest.raises(CannotRevokeOwnerError):
        revoke_staff_member(db_session, tenant_id="tenant-a", user_id="owner-a", acting_user_id="owner-a")


def test_revoke_staff_member_rejects_revoking_yourself(db_session):
    _seed_tenant_with_owner(db_session)
    db_session.add(UserIdentity(user_id="new-staff", email="new-staff@example.com"))
    db_session.commit()
    invite_staff_member(db_session, tenant_id="tenant-a", user_id="new-staff", role=MembershipRole.RESEARCHER, acting_user_id="owner-a")
    db_session.commit()

    with pytest.raises(CannotRevokeSelfError):
        revoke_staff_member(db_session, tenant_id="tenant-a", user_id="new-staff", acting_user_id="new-staff")


def test_revoke_staff_member_requires_an_existing_membership(db_session):
    _seed_tenant_with_owner(db_session)
    with pytest.raises(MembershipNotFoundError):
        revoke_staff_member(db_session, tenant_id="tenant-a", user_id="never-invited", acting_user_id="owner-a")


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
