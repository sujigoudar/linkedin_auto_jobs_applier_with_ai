"""PU-01 "Public home" -- get_service_status's own tests. Pure function,
no database needed."""
from app import config
from app.services.public_site import get_service_status


def test_environment_status_reflects_the_real_configured_environment(monkeypatch):
    monkeypatch.setattr(config, "ENVIRONMENT", "LOCAL_SIM")
    statuses = {item.name: item.status for item in get_service_status()}
    assert statuses["Environment"] == "LOCAL_SIM"


def test_billing_is_reported_not_configured_with_the_placeholder_secret(monkeypatch):
    monkeypatch.setattr(config, "STRIPE_WEBHOOK_SECRET", "whsec_LOCAL_SIM_not_a_real_stripe_secret")
    statuses = {item.name: item.status for item in get_service_status()}
    assert statuses["Billing (Stripe)"] == "NOT_CONFIGURED"


def test_billing_is_reported_configured_once_a_real_secret_is_set(monkeypatch):
    monkeypatch.setattr(config, "STRIPE_WEBHOOK_SECRET", "whsec_a_real_looking_secret")
    statuses = {item.name: item.status for item in get_service_status()}
    assert statuses["Billing (Stripe)"] == "CONFIGURED"
