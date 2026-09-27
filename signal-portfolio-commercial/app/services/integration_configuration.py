"""AD-17 "Integrations, data rights and quotas" -- the real save/list
service backing F-INTEGRATION. See dashboard_spec/screens/AD-17.md for
the full screen contract this implements a bounded slice of.

`_REVIEWED_PROVIDERS_BY_PURPOSE` is a real, reviewed allowlist -- it
names only the adapter modules that actually exist and have been
reviewed in this codebase (app/services/stripe_webhook.py for billing;
collective2_publisher.py/etoro_adapter.py/copyfactory_close_only.py for
publication). "Reviewed allowlist; No arbitrary service URL" (AD-17's
own field help text) is enforced by construction: research/quotes/
reference/monitoring have NO reviewed adapter in this build at all, so
every provider for those purposes is refused, never silently accepted.

Only `test` is an authorized environment -- no real live/demo
credentials or sandbox access exist for any of these providers in this
environment (see app/services/public_site.py's own compatibility
directory for the publication-channel case; Stripe billing is likewise
still on its local placeholder secret).
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.integration_configuration import (
    IntegrationConfiguration,
    IntegrationEnvironment,
    IntegrationPurpose,
)

#: Only the adapter modules that actually exist and have been reviewed
#: in this codebase. An empty set for a purpose means NO integration for
#: that purpose can be saved yet, not "anything goes".
_REVIEWED_PROVIDERS_BY_PURPOSE: dict[IntegrationPurpose, frozenset[str]] = {
    IntegrationPurpose.BILLING: frozenset({"stripe"}),
    IntegrationPurpose.PUBLICATION: frozenset({"collective2", "etoro", "copyfactory"}),
    IntegrationPurpose.RESEARCH: frozenset(),
    IntegrationPurpose.QUOTES: frozenset(),
    IntegrationPurpose.REFERENCE: frozenset(),
    IntegrationPurpose.MONITORING: frozenset(),
}

_ONLY_AUTHORIZED_ENVIRONMENT = IntegrationEnvironment.TEST


class InvalidIntegrationConfigurationError(Exception):
    pass


def list_integration_configurations(session: Session, *, tenant_id: str) -> list[IntegrationConfiguration]:
    return list(
        session.scalars(
            select(IntegrationConfiguration)
            .where(IntegrationConfiguration.tenant_id == tenant_id)
            .order_by(IntegrationConfiguration.created_at.desc())
        ).all()
    )


def create_integration_configuration(
    session: Session,
    *,
    tenant_id: str,
    provider_registry_id: str,
    purpose: str,
    environment: str,
    credential_ref: str | None,
    entitlement_evidence_id: str | None,
    quota_profile_id: str,
) -> IntegrationConfiguration:
    try:
        purpose_enum = IntegrationPurpose(purpose)
    except ValueError as exc:
        raise InvalidIntegrationConfigurationError(f"{purpose!r} is not a known purpose") from exc
    try:
        environment_enum = IntegrationEnvironment(environment)
    except ValueError as exc:
        raise InvalidIntegrationConfigurationError(f"{environment!r} is not a known environment") from exc
    if not quota_profile_id or not quota_profile_id.strip():
        raise InvalidIntegrationConfigurationError("quota_profile_id is required -- no paid fallback")

    reviewed = _REVIEWED_PROVIDERS_BY_PURPOSE.get(purpose_enum, frozenset())
    if provider_registry_id not in reviewed:
        raise InvalidIntegrationConfigurationError(
            f"REVIEWED_PROVIDER_NOT_FOUND: no reviewed adapter exists for purpose={purpose!r}, "
            f"provider={provider_registry_id!r}"
        )

    if environment_enum != _ONLY_AUTHORIZED_ENVIRONMENT:
        raise InvalidIntegrationConfigurationError(
            f"EXTERNAL_ENVIRONMENT_NOT_AUTHORIZED: no real {provider_registry_id!r} demo/live credentials exist "
            f"in this environment -- only {_ONLY_AUTHORIZED_ENVIRONMENT.value!r} can be saved"
        )

    configuration = IntegrationConfiguration(
        tenant_id=tenant_id,
        provider_registry_id=provider_registry_id,
        purpose=purpose_enum,
        environment=environment_enum,
        credential_ref=credential_ref or None,
        entitlement_evidence_id=entitlement_evidence_id or None,
        quota_profile_id=quota_profile_id,
    )
    session.add(configuration)
    session.flush()
    return configuration
