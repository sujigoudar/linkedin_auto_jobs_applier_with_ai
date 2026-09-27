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
    #: AD-02 "Rights and service approvals" -- read the grant register.
    #: Deliberately separate from "grant_rights" (owner-only, the actual
    #: approval authority): a reviewer can see the register and the
    #: scope-intersection evidence without being able to approve a grant
    #: -- "researcher cannot approve rights" (AD-02's own acceptance
    #: text) applies with equal force to reviewer for the write side,
    #: which this build doesn't implement yet (see product_admin's own
    #: precedent of not building the approval/publish step until it is).
    "view_rights_register": frozenset({Role.OWNER, Role.REVIEWER}),
    #: AD-03 "Research universe and sleeves" -- create/list sleeve
    #: lineage records. Same trio as `manage_product_draft`: a research/
    #: release decision, not billing/publication/support/customer.
    "manage_sleeve_draft": frozenset({Role.OWNER, Role.RESEARCHER, Role.REVIEWER}),
    #: AD-01 "Commercial operations overview" -- every operator role
    #: except CUSTOMER can see the cross-subsystem summary; a customer
    #: has no operator scope at all, matching this screen's own access
    #: list (owner, researcher, reviewer, publisher_operator,
    #: billing_operator, support_readonly).
    "view_operations_overview": frozenset(
        {
            Role.OWNER,
            Role.RESEARCHER,
            Role.REVIEWER,
            Role.PUBLISHER_OPERATOR,
            Role.BILLING_OPERATOR,
            Role.SUPPORT_READONLY,
        }
    ),
    #: ID-04 "Service eligibility onboarding" -- a customer's own facts,
    #: never another role's concern (an operator manages products/
    #: reviews/research, not a customer's own onboarding record).
    "manage_own_eligibility": frozenset({Role.CUSTOMER}),
    #: AD-16 "Staff roles and access reviews" -- owner-only, per this
    #: screen's own access list. Granting/revoking a colleague's role is
    #: exactly the kind of authority no other role should have, not even
    #: REVIEWER or SUPPORT_READONLY.
    "manage_staff_access": frozenset({Role.OWNER}),
    #: CU-14 "Support and incident case" -- a customer's own cases,
    #: same shape as "manage_own_eligibility".
    "manage_own_support_case": frozenset({Role.CUSTOMER}),
    #: AD-09 "Publisher channels and strategies" -- exactly this
    #: screen's own access list. Declaring which external strategy a
    #: tenant claims publication authority over is not a research,
    #: billing or support decision.
    "manage_publisher_destinations": frozenset({Role.OWNER, Role.PUBLISHER_OPERATOR}),
    #: CU-16 "API delivery, keys and exports" -- a customer's own keys,
    #: same shape as "manage_own_eligibility"/"manage_own_support_case".
    "manage_own_api_keys": frozenset({Role.CUSTOMER}),
    #: AD-12 "Business economics and royalties" -- exactly this
    #: screen's own access list. Business revenue is a billing/
    #: ownership decision, not a research/support/publisher concern.
    "view_business_economics": frozenset({Role.OWNER, Role.BILLING_OPERATOR}),
    #: AD-11 "Customers and scoped support record" -- exactly this
    #: screen's own access list. Reading another user's eligibility
    #: facts/support cases is a support/ownership decision, not a
    #: research, billing or publisher concern.
    "view_customer_support_record": frozenset({Role.OWNER, Role.SUPPORT_READONLY}),
    #: AD-17 "Integrations, data rights and quotas" -- exactly this
    #: screen's own access list. Same trio of concerns as AD-09's own
    #: destinations (a research/release/ops decision, not billing/
    #: support/customer).
    "manage_integration_configurations": frozenset({Role.OWNER, Role.PUBLISHER_OPERATOR}),
    #: AD-13 "Pricing, entitlements and billing operations" -- exactly
    #: this screen's own access list.
    "manage_pricing": frozenset({Role.OWNER, Role.BILLING_OPERATOR}),
    #: AD-19 "Content and disclosure publishing" -- exactly this
    #: screen's own access list.
    "manage_content_documents": frozenset({Role.OWNER, Role.REVIEWER}),
    #: AD-14 "Managed-program setup" -- exactly this screen's own access
    #: list.
    "manage_managed_programs": frozenset({Role.OWNER, Role.REVIEWER}),
    #: AD-18 "Audit log and release evidence" -- exactly this screen's
    #: own access list.
    "view_audit_log": frozenset({Role.OWNER, Role.REVIEWER}),
    #: AD-20 "Workspace customization and configuration" -- exactly this
    #: screen's own access list. Cosmetic as the fields are, changing the
    #: tenant-wide shared default is an ownership decision, not one any
    #: other operator role should carry.
    "manage_workspace_settings": frozenset({Role.OWNER}),
    #: AD-05 "Research run and full results" -- exactly this screen's
    #: own access list (owner, researcher, reviewer). Deliberately
    #: separate from "run_research_job" (owner/researcher, the create/
    #: list authority): a reviewer can read a run's own manifest and
    #: preview without being able to declare a new one, matching
    #: AD-02's own view/write split precedent.
    "view_research_run_detail": frozenset({Role.OWNER, Role.RESEARCHER, Role.REVIEWER}),
    #: AD-10 "Publication intent and cohort detail" -- exactly this
    #: screen's own access list.
    "view_publication_intent_detail": frozenset({Role.OWNER, Role.PUBLISHER_OPERATOR}),
    #: CU-02 "My portfolios" -- a customer's own selections, same shape
    #: as "manage_own_eligibility"/"manage_own_support_case"/
    #: "manage_own_api_keys".
    "manage_own_portfolio_selections": frozenset({Role.CUSTOMER}),
    #: CU-12 "Alert delivery preferences" -- a customer's own
    #: preferences, same shape as "manage_own_eligibility"/
    #: "manage_own_support_case"/"manage_own_api_keys"/
    #: "manage_own_portfolio_selections".
    "manage_own_notification_preferences": frozenset({Role.CUSTOMER}),
}


class PermissionDenied(Exception):
    pass


def is_allowed(role: Role, action: str) -> bool:
    return role in _ALLOWED.get(action, frozenset())


def require_permission(role: Role, action: str) -> None:
    if not is_allowed(role, action):
        raise PermissionDenied(f"role {role.value!r} is not permitted to perform {action!r}")
