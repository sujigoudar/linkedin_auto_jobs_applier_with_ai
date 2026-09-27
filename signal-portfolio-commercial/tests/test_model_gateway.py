"""app/services/model_gateway.py -- the optional model/LLM assistance
boundary. Pure function tests, no database needed."""
import pytest

from app.services.model_gateway import (
    DisallowedModelActionError,
    InvalidApiKeyRoleError,
    InvalidModelGatewayConfigError,
    ModelPurpose,
    build_model_gateway_config,
    require_model_permitted_action,
    requires_human_review,
    validate_api_key_role,
)

_DISALLOWED_ACTIONS = [
    "invent_signal_parameters",
    "invent_missing_history",
    "invent_platform_api",
    "decide_rights_eligibility",
    "decide_jurisdiction_eligibility",
    "approve_marketing",
    "change_portfolio_weights",
    "change_risk_limits",
    "place_order",
    "cancel_order",
    "grant_entitlement",
    "grant_permission",
    "alter_high_water_mark",
    "alter_nav",
    "alter_fees",
    "cross_tenant_read",
    "use_customer_api_key_for_evaluation",
    "recommend_personalized_portfolio",
    "approve_own_production_change",
    "auto_publish_output",
]


@pytest.mark.parametrize("action", _DISALLOWED_ACTIONS)
def test_every_documented_disallowed_action_is_rejected(action):
    with pytest.raises(DisallowedModelActionError):
        require_model_permitted_action(action)


def test_an_allowed_action_is_not_rejected():
    require_model_permitted_action("summarize_frozen_report")  # does not raise


def test_every_model_purpose_requires_human_review():
    for purpose in ModelPurpose:
        assert requires_human_review(purpose) is True


def _valid_config_kwargs(**overrides):
    defaults = dict(
        purpose=ModelPurpose.REPORT_SUMMARY,
        model_version="test-model-v1",
        max_input_tokens=1000,
        max_output_tokens=500,
        max_cost_budget_cents=100,
        tenant_id="tenant-a",
        data_use_agreement_id="dua-1",
    )
    defaults.update(overrides)
    return defaults


def test_a_valid_config_builds_successfully():
    config = build_model_gateway_config(**_valid_config_kwargs())
    assert config.purpose == ModelPurpose.REPORT_SUMMARY
    assert config.tenant_id == "tenant-a"


def test_a_non_enum_purpose_is_rejected():
    with pytest.raises(InvalidModelGatewayConfigError):
        build_model_gateway_config(**_valid_config_kwargs(purpose="REPORT_SUMMARY"))


@pytest.mark.parametrize("field", ["max_input_tokens", "max_output_tokens", "max_cost_budget_cents"])
def test_non_positive_limits_are_rejected(field):
    with pytest.raises(InvalidModelGatewayConfigError):
        build_model_gateway_config(**_valid_config_kwargs(**{field: 0}))


def test_a_missing_tenant_id_is_rejected():
    """A model call must always be tenant-scoped -- there is no
    "global"/unscoped config."""
    with pytest.raises(InvalidModelGatewayConfigError):
        build_model_gateway_config(**_valid_config_kwargs(tenant_id=""))


def test_a_missing_data_use_agreement_is_rejected():
    with pytest.raises(InvalidModelGatewayConfigError):
        build_model_gateway_config(**_valid_config_kwargs(data_use_agreement_id=""))


def test_a_read_only_research_key_role_is_accepted():
    validate_api_key_role("read_only_research")  # does not raise


@pytest.mark.parametrize("role", ["trading", "admin", "full_access", ""])
def test_any_other_key_role_is_rejected(role):
    """"Provider API keys are read-only research role secrets, not
    trading keys." -- nothing but the one recognized research role
    passes."""
    with pytest.raises(InvalidApiKeyRoleError):
        validate_api_key_role(role)
