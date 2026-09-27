"""The optional, bounded model/LLM assistance boundary, per
spec/docs/11_models_skills_and_optional_ai.md: "No LLM is needed to
calculate covariance, choose a constrained portfolio, account for fees,
conserve quantities, authenticate customers or process billing... Keep
the financial runtime operational when all model providers are disabled
or unavailable."

No model provider API key exists in this environment, and none is
needed to build this module -- the boundary itself (what a model
integration is even allowed to be asked to do, and the hard, unconfigurable
list of things no purpose or configuration can ever authorize) is the
actual deliverable here, mirroring app/services/permissions.py's
explicit allow-list pattern from Phase 02. A future real model
integration calls `require_model_permitted_action` before acting and
`requires_human_review` before treating any output as approved; neither
function needs a live model to be correct or testable.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass


class ModelPurpose(str, enum.Enum):
    """The spec's own closed list of "allowed candidates" -- a purpose
    not on this list is not a model use case this build recognizes at
    all, regardless of how it's phrased in a request."""

    TAG_PROPOSAL = "TAG_PROPOSAL"
    PARSER_RULE_DRAFT = "PARSER_RULE_DRAFT"
    REPORT_SUMMARY = "REPORT_SUMMARY"
    METHOD_EXPLANATION = "METHOD_EXPLANATION"
    SUPPORT_TRIAGE = "SUPPORT_TRIAGE"
    PRODUCT_COPY_DRAFT = "PRODUCT_COPY_DRAFT"
    OFFLINE_CLASSIFICATION_COMPARISON = "OFFLINE_CLASSIFICATION_COMPARISON"


#: The spec's own "Disallowed" list -- a closed, hard-coded deny-list
#: with NO bypass parameter anywhere in this module. No `ModelPurpose`,
#: no configuration, no caller-supplied role/flag can ever get one of
#: these authorized; that is the entire point of listing them here
#: instead of leaving them as "whatever the allow-list doesn't cover."
_ALWAYS_DISALLOWED_ACTIONS: frozenset[str] = frozenset(
    {
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
    }
)


class DisallowedModelActionError(Exception):
    pass


def require_model_permitted_action(action: str) -> None:
    """Raises if `action` is on the hard deny-list -- callers must
    invoke this before letting any model-driven code path proceed,
    regardless of purpose, tenant, or how privileged the request claims
    to be."""
    if action in _ALWAYS_DISALLOWED_ACTIONS:
        raise DisallowedModelActionError(
            f"{action!r} is never permitted for a model/LLM integration, regardless of purpose or configuration"
        )


def requires_human_review(purpose: ModelPurpose) -> bool:
    """Always True: "Model-generated content cannot be automatically
    published as advice or an approved signal." There is no
    `ModelPurpose` this returns False for -- every model output needs a
    human/review gate before it is treated as approved or published."""
    return True


class InvalidModelGatewayConfigError(Exception):
    pass


@dataclass(frozen=True)
class ModelGatewayConfig:
    purpose: ModelPurpose
    model_version: str
    max_input_tokens: int
    max_output_tokens: int
    max_cost_budget_cents: int
    tenant_id: str
    data_use_agreement_id: str


def build_model_gateway_config(
    *,
    purpose: ModelPurpose,
    model_version: str,
    max_input_tokens: int,
    max_output_tokens: int,
    max_cost_budget_cents: int,
    tenant_id: str,
    data_use_agreement_id: str,
) -> ModelGatewayConfig:
    """Validates every field a real ModelGateway call would need before
    it can run at all -- "Use a typed ModelGateway with allowed purpose,
    version, maximum input/output/budget, tenant scope and data-use
    agreement." Missing or non-positive values are refused outright,
    never defaulted to "unlimited" or "unscoped.\""""
    if not isinstance(purpose, ModelPurpose):
        raise InvalidModelGatewayConfigError(f"{purpose!r} is not a recognized ModelPurpose")
    if not model_version:
        raise InvalidModelGatewayConfigError("model_version is required")
    if max_input_tokens <= 0 or max_output_tokens <= 0:
        raise InvalidModelGatewayConfigError("max_input_tokens and max_output_tokens must be positive")
    if max_cost_budget_cents <= 0:
        raise InvalidModelGatewayConfigError("max_cost_budget_cents must be positive")
    if not tenant_id:
        raise InvalidModelGatewayConfigError("tenant_id is required -- a model call is always tenant-scoped")
    if not data_use_agreement_id:
        raise InvalidModelGatewayConfigError("data_use_agreement_id is required")

    return ModelGatewayConfig(
        purpose=purpose,
        model_version=model_version,
        max_input_tokens=max_input_tokens,
        max_output_tokens=max_output_tokens,
        max_cost_budget_cents=max_cost_budget_cents,
        tenant_id=tenant_id,
        data_use_agreement_id=data_use_agreement_id,
    )


_RESEARCH_ONLY_KEY_ROLE = "read_only_research"


class InvalidApiKeyRoleError(Exception):
    pass


def validate_api_key_role(role: str) -> None:
    """"Provider API keys are read-only research role secrets, not
    trading keys." Any role other than the one recognized research role
    is refused -- there is no path by which a model integration's key
    could be, or be mistaken for, a trading credential."""
    if role != _RESEARCH_ONLY_KEY_ROLE:
        raise InvalidApiKeyRoleError(
            f"model provider API key role {role!r} is not {_RESEARCH_ONLY_KEY_ROLE!r} -- "
            "a model integration must never hold a trading-capable credential"
        )
