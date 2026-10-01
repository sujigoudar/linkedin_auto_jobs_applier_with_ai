"""CP-013 "Role separation": docs/02's three named examples, plus the
"no role listed for an action is trusted" fail-closed default -- these
are pure function tests, no database needed."""
import pytest

from app.models.tenancy import MembershipRole as Role
from app.services.permissions import PermissionDenied, is_allowed, require_permission


def test_a_billing_operator_cannot_release_a_strategy():
    assert is_allowed(Role.BILLING_OPERATOR, "release_strategy") is False


def test_a_researcher_cannot_grant_rights():
    assert is_allowed(Role.RESEARCHER, "grant_rights") is False


def test_a_customer_cannot_view_broker_credentials():
    assert is_allowed(Role.CUSTOMER, "view_broker_credentials") is False


def test_support_readonly_cannot_view_broker_credentials():
    assert is_allowed(Role.SUPPORT_READONLY, "view_broker_credentials") is False


def test_only_the_owner_can_view_broker_credentials():
    for role in Role:
        expected = role is Role.OWNER
        assert is_allowed(role, "view_broker_credentials") is expected


def test_an_action_with_no_entry_at_all_denies_every_role_including_owner():
    for role in Role:
        assert is_allowed(role, "some_action_nobody_registered") is False


def test_require_permission_raises_on_denial_and_returns_none_on_allow():
    require_permission(Role.OWNER, "grant_rights")  # does not raise
    with pytest.raises(PermissionDenied):
        require_permission(Role.RESEARCHER, "grant_rights")


#: Every action `_ALLOWED` names, with the exact role set the module's
#: own docstring/comments document for it. Hardcoded here rather than
#: read off `_ALLOWED` itself: a test that imported the module's own
#: dict and compared it to itself could never fail, no matter how the
#: dict were mutated. Mutation testing (Track 53) found that none of
#: these 47 action keys were exercised by anything above except the
#: handful named explicitly, so a mutant renaming a single action's
#: dict key (silently denying every role, including OWNER, for that
#: action -- `_ALLOWED.get(action, frozenset())` falls through to the
#: empty default) survived undetected for every action this list
#: doesn't already name above.
_EXPECTED_ALLOWED: dict[str, frozenset[Role]] = {
    "grant_rights": frozenset({Role.OWNER}),
    "release_strategy": frozenset({Role.OWNER, Role.REVIEWER}),
    "publish_intent": frozenset({Role.OWNER, Role.PUBLISHER_OPERATOR}),
    "manage_billing": frozenset({Role.OWNER, Role.BILLING_OPERATOR}),
    "view_broker_credentials": frozenset({Role.OWNER}),
    "run_research_job": frozenset({Role.OWNER, Role.RESEARCHER}),
    "view_support_case": frozenset({Role.OWNER, Role.SUPPORT_READONLY}),
    "view_own_customer_profile": frozenset({Role.CUSTOMER, Role.OWNER, Role.SUPPORT_READONLY}),
    "manage_product_draft": frozenset({Role.OWNER, Role.RESEARCHER, Role.REVIEWER}),
    "view_rights_register": frozenset({Role.OWNER, Role.REVIEWER}),
    "manage_sleeve_draft": frozenset({Role.OWNER, Role.RESEARCHER, Role.REVIEWER}),
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
    "manage_own_eligibility": frozenset({Role.CUSTOMER}),
    "manage_staff_access": frozenset({Role.OWNER}),
    "manage_own_support_case": frozenset({Role.CUSTOMER}),
    "manage_publisher_destinations": frozenset({Role.OWNER, Role.PUBLISHER_OPERATOR}),
    "manage_own_api_keys": frozenset({Role.CUSTOMER}),
    "view_business_economics": frozenset({Role.OWNER, Role.BILLING_OPERATOR}),
    "view_customer_support_record": frozenset({Role.OWNER, Role.SUPPORT_READONLY}),
    "manage_integration_configurations": frozenset({Role.OWNER, Role.PUBLISHER_OPERATOR}),
    "manage_pricing": frozenset({Role.OWNER, Role.BILLING_OPERATOR}),
    "manage_content_documents": frozenset({Role.OWNER, Role.REVIEWER}),
    "manage_managed_programs": frozenset({Role.OWNER, Role.REVIEWER}),
    "view_audit_log": frozenset({Role.OWNER, Role.REVIEWER}),
    "export_evidence_manifest": frozenset({Role.OWNER, Role.REVIEWER}),
    "manage_workspace_settings": frozenset({Role.OWNER}),
    "view_research_run_detail": frozenset({Role.OWNER, Role.RESEARCHER, Role.REVIEWER}),
    "view_publication_intent_detail": frozenset({Role.OWNER, Role.PUBLISHER_OPERATOR}),
    "manage_own_portfolio_selections": frozenset({Role.CUSTOMER}),
    "manage_own_notification_preferences": frozenset({Role.CUSTOMER}),
    "manage_own_display_preferences": frozenset({Role.CUSTOMER}),
    "view_managed_operations": frozenset({Role.OWNER, Role.PUBLISHER_OPERATOR, Role.REVIEWER}),
    "view_deployment_status": frozenset({Role.OWNER, Role.PUBLISHER_OPERATOR}),
    "manage_own_platform_connections": frozenset({Role.CUSTOMER}),
    "manage_own_copy_mandates": frozenset({Role.CUSTOMER}),
    "view_own_customer_overview": frozenset({Role.CUSTOMER}),
    "view_own_selection_detail": frozenset({Role.CUSTOMER}),
    "view_integration_status": frozenset({Role.OWNER, Role.RESEARCHER}),
    "compare_research_candidates": frozenset({Role.OWNER, Role.RESEARCHER, Role.REVIEWER}),
    "view_incident_register": frozenset({Role.OWNER, Role.PUBLISHER_OPERATOR, Role.SUPPORT_READONLY}),
    "manage_incidents": frozenset({Role.OWNER, Role.PUBLISHER_OPERATOR}),
    "view_own_alerts": frozenset({Role.CUSTOMER}),
    "view_own_activity_detail": frozenset({Role.CUSTOMER}),
    "view_own_performance": frozenset({Role.CUSTOMER}),
    "view_own_billing": frozenset({Role.CUSTOMER}),
    "view_own_managed_programs": frozenset({Role.CUSTOMER}),
    "manage_operating_costs": frozenset({Role.OWNER, Role.BILLING_OPERATOR}),
}


@pytest.mark.parametrize("action", sorted(_EXPECTED_ALLOWED))
def test_every_named_action_grants_exactly_its_documented_role_set(action):
    expected = _EXPECTED_ALLOWED[action]
    for role in Role:
        assert is_allowed(role, action) is (role in expected), (
            f"{action!r} x {role.value!r}: expected allowed={role in expected}"
        )


def test_every_documented_action_name_is_a_real_key_in_the_allow_list():
    # Guards the test list above itself against drifting out of sync
    # with a real new/renamed action in the module (as opposed to a
    # mutmut-injected mutation, which this file never imports `_ALLOWED`
    # to compare against).
    from app.services.permissions import _ALLOWED as allowed

    assert set(_EXPECTED_ALLOWED) == set(allowed)
