"""AD-16 "Staff roles and access reviews" -- the real membership-grant/
revoke service backing F-ACCESS. See dashboard_spec/screens/AD-16.md
for the full screen contract this implements a bounded slice of.

No new model: `Membership` (app/models/tenancy.py) already carries
every field this screen needs (tenant_id, user_id, role). "Staff" here
means every non-CUSTOMER membership in the tenant.

`invite_staff_member` requires a real, already-existing `UserIdentity`
row -- "verified existing or invited identity; No arbitrary JWT claims"
(AD-16's own field help text). There is no signup/invite-email flow in
this build yet (ID-01/ID-02 aren't implemented), so in practice a
UserIdentity row must already exist through some other real path before
this can grant it a role; this module never creates one implicitly.

Two real, named safety guards on revoke, not a single generic
'not allowed': the OWNER role can never be revoked through this form
("Owner grants not through self-service form"), and a caller can never
revoke their own membership -- an owner accidentally locking themselves
out of their own tenant is exactly the kind of failure a form like this
must make structurally impossible, not just discouraged.

Every real invite/revoke also appends a real AuditEvent
(app/services/audit_log.py) -- the first real writer into AD-18's
append-only audit store, resolving the "no audit-log store exists" gap
AD-16/AD-11 both previously documented.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.tenancy import Membership, MembershipRole, UserIdentity
from app.services.audit_log import append_audit_event

#: OWNER is granted only by direct database/founder action, never
#: through this self-service form ("Owner grants not through
#: self-service form" -- AD-16's own field help text). CUSTOMER is not
#: a staff role at all.
GRANTABLE_ROLES: frozenset[MembershipRole] = frozenset(
    {
        MembershipRole.RESEARCHER,
        MembershipRole.REVIEWER,
        MembershipRole.PUBLISHER_OPERATOR,
        MembershipRole.BILLING_OPERATOR,
        MembershipRole.SUPPORT_READONLY,
    }
)


class InvalidStaffGrantError(Exception):
    pass


class UnknownUserIdentityError(InvalidStaffGrantError):
    pass


class AlreadyAMemberError(InvalidStaffGrantError):
    pass


class MembershipNotFoundError(Exception):
    pass


class CannotRevokeOwnerError(Exception):
    pass


class CannotRevokeSelfError(Exception):
    pass


def list_staff_memberships(session: Session, *, tenant_id: str) -> list[Membership]:
    return list(
        session.scalars(
            select(Membership)
            .where(Membership.tenant_id == tenant_id, Membership.role != MembershipRole.CUSTOMER)
            .order_by(Membership.created_at.desc())
        ).all()
    )


def invite_staff_member(
    session: Session, *, tenant_id: str, user_id: str, role: MembershipRole, acting_user_id: str
) -> Membership:
    if role not in GRANTABLE_ROLES:
        raise InvalidStaffGrantError(f"{role.value!r} cannot be granted through this form")
    if session.get(UserIdentity, user_id) is None:
        raise UnknownUserIdentityError(
            f"no existing identity for user_id {user_id!r} -- an invite/signup flow must create it first"
        )
    if session.get(Membership, (tenant_id, user_id)) is not None:
        raise AlreadyAMemberError(f"{user_id!r} already has a membership in this tenant")

    membership = Membership(tenant_id=tenant_id, user_id=user_id, role=role)
    session.add(membership)
    session.flush()
    append_audit_event(
        session,
        tenant_id=tenant_id,
        actor_user_id=acting_user_id,
        object_type="membership",
        object_id=user_id,
        action=f"invite_staff_member:{role.value}",
    )
    return membership


def revoke_staff_member(session: Session, *, tenant_id: str, user_id: str, acting_user_id: str) -> None:
    membership = session.get(Membership, (tenant_id, user_id))
    if membership is None:
        raise MembershipNotFoundError(f"no membership for {user_id!r} in this tenant")
    if membership.role == MembershipRole.OWNER:
        raise CannotRevokeOwnerError("the owner role cannot be revoked through this form")
    if user_id == acting_user_id:
        raise CannotRevokeSelfError("cannot revoke your own membership")

    session.delete(membership)
    session.flush()
    append_audit_event(
        session,
        tenant_id=tenant_id,
        actor_user_id=acting_user_id,
        object_type="membership",
        object_id=user_id,
        action="revoke_staff_member",
    )
