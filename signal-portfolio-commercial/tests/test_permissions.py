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
