"""AD-17 "Integrations, data rights and quotas" -- app/services/integration_configuration.py's
own tests. Real Postgres, real tenant-scoped session."""
import pytest

from app.services.integration_configuration import (
    InvalidIntegrationConfigurationError,
    create_integration_configuration,
    list_integration_configurations,
)


def test_list_integration_configurations_is_empty_before_any_are_saved(db_session):
    assert list_integration_configurations(db_session, tenant_id="tenant-a") == []


def test_create_accepts_a_real_reviewed_billing_provider(db_session):
    create_integration_configuration(
        db_session,
        tenant_id="tenant-a",
        provider_registry_id="stripe",
        purpose="billing",
        environment="test",
        credential_ref=None,
        entitlement_evidence_id=None,
        quota_profile_id="qp-1",
    )
    db_session.commit()

    configurations = list_integration_configurations(db_session, tenant_id="tenant-a")
    assert len(configurations) == 1
    assert configurations[0].provider_registry_id == "stripe"


def test_create_rejects_an_unreviewed_provider_for_billing(db_session):
    with pytest.raises(InvalidIntegrationConfigurationError, match="REVIEWED_PROVIDER_NOT_FOUND"):
        create_integration_configuration(
            db_session,
            tenant_id="tenant-a",
            provider_registry_id="some-random-payment-processor",
            purpose="billing",
            environment="test",
            credential_ref=None,
            entitlement_evidence_id=None,
            quota_profile_id="qp-1",
        )


def test_create_rejects_any_provider_for_a_purpose_with_no_reviewed_adapter(db_session):
    with pytest.raises(InvalidIntegrationConfigurationError, match="REVIEWED_PROVIDER_NOT_FOUND"):
        create_integration_configuration(
            db_session,
            tenant_id="tenant-a",
            provider_registry_id="stripe",
            purpose="research",
            environment="test",
            credential_ref=None,
            entitlement_evidence_id=None,
            quota_profile_id="qp-1",
        )


def test_create_rejects_a_non_test_environment(db_session):
    with pytest.raises(InvalidIntegrationConfigurationError, match="EXTERNAL_ENVIRONMENT_NOT_AUTHORIZED"):
        create_integration_configuration(
            db_session,
            tenant_id="tenant-a",
            provider_registry_id="stripe",
            purpose="billing",
            environment="live",
            credential_ref=None,
            entitlement_evidence_id=None,
            quota_profile_id="qp-1",
        )


def test_create_rejects_an_empty_quota_profile_id(db_session):
    with pytest.raises(InvalidIntegrationConfigurationError):
        create_integration_configuration(
            db_session,
            tenant_id="tenant-a",
            provider_registry_id="stripe",
            purpose="billing",
            environment="test",
            credential_ref=None,
            entitlement_evidence_id=None,
            quota_profile_id="",
        )


def test_create_accepts_every_real_reviewed_publication_provider(db_session):
    for provider in ("collective2", "etoro", "copyfactory"):
        create_integration_configuration(
            db_session,
            tenant_id="tenant-a",
            provider_registry_id=provider,
            purpose="publication",
            environment="test",
            credential_ref=None,
            entitlement_evidence_id=None,
            quota_profile_id="qp-1",
        )
    db_session.commit()
    assert len(list_integration_configurations(db_session, tenant_id="tenant-a")) == 3
