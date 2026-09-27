"""Role separation (docs/02: "Operator roles are separate memberships:
owner, researcher, reviewer, publisher_operator, billing_operator,
support_readonly. A billing operator cannot release a strategy;
researcher cannot grant rights; customer cannot query another tenant;
support cannot view broker credentials.").

An explicit allow-list per action, not a role hierarchy or an
inheritance chain -- "owner can do everything a lower role can" is
exactly the kind of implicit assumption that would silently let a
future role addition acquire authority nobody reviewed. Every action
this system can gate must be named here before anything can perform it;
an action string with no entry in `_ALLOWED` denies every role,
including OWNER.
"""
from __future__ import annotations

from app.models.tenancy import MembershipRole as Role

#: Action -> the roles allowed to perform it. Deliberately explicit and
#: closed: a role not listed for an action is denied, full stop.
_ALLOWED: dict[str, frozenset[Role]] = {
    "grant_rights": frozenset({Role.OWNER}),
    "release_strategy": frozenset({Role.OWNER, Role.REVIEWER}),
    "publish_intent": frozenset({Role.OWNER, Role.PUBLISHER_OPERATOR}),
    "manage_billing": frozenset({Role.OWNER, Role.BILLING_OPERATOR}),
    "view_broker_credentials": frozenset({Role.OWNER}),
    "run_research_job": frozenset({Role.OWNER, Role.RESEARCHER}),
    "view_support_case": frozenset({Role.OWNER, Role.SUPPORT_READONLY}),
    "view_own_customer_profile": frozenset({Role.CUSTOMER, Role.OWNER, Role.SUPPORT_READONLY}),
    #: AD-07 "Products and portfolio versions" -- create/edit a Product
    #: draft. Explicitly not BILLING_OPERATOR/PUBLISHER_OPERATOR/
    #: SUPPORT_READONLY/CUSTOMER: drafting a product is a research/
    #: release decision, not a billing, publication, support or
    #: customer action.
    "manage_product_draft": frozenset({Role.OWNER, Role.RESEARCHER, Role.REVIEWER}),
}


class PermissionDenied(Exception):
    pass


def is_allowed(role: Role, action: str) -> bool:
    return role in _ALLOWED.get(action, frozenset())


def require_permission(role: Role, action: str) -> None:
    if not is_allowed(role, action):
        raise PermissionDenied(f"role {role.value!r} is not permitted to perform {action!r}")
