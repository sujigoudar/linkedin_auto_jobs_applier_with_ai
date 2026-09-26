"""RISK-04:
1. `SettingsOverride.enabled`'s docstring promises "enabled=False at any
   level disables routing through it regardless of what a broader level
   says", but the merge logic implemented plain override precedence
   (narrower wins whenever non-None) for every field including this one --
   so an analyst explicitly set to enabled=True could re-enable a provider
   its owner had disabled, the opposite of the documented and intended
   safety property.
2. The account/provider/analyst CRUD endpoints accepted any float for
   multiplier/fixed_quantity, including negative, zero, and booleans
   (pydantic coerces JSON true/false to 1.0/0.0 for a bare `float` field).
"""
import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app import config as app_config
from app.db import SignalStore
from app.providers import AnalystConfig, ProviderConfig, ProviderRegistry, SettingsOverride


def _account_defaults(**overrides) -> SettingsOverride:
    defaults = dict(multiplier=1.0, fixed_quantity=None, managed_lifecycle=False, enabled=True)
    defaults.update(overrides)
    return SettingsOverride(**defaults)


def test_analyst_cannot_re_enable_a_provider_disabled_by_its_owner():
    registry = ProviderRegistry(
        providers={
            "telegram": ProviderConfig(
                provider_id="telegram",
                settings=SettingsOverride(enabled=False),
                analysts={"alice": AnalystConfig(analyst_id="alice", settings=SettingsOverride(enabled=True))},
            )
        }
    )
    effective = registry.effective_settings(_account_defaults(), "telegram", "alice")
    assert effective.enabled is False


def test_provider_cannot_re_enable_an_account_disabled_by_its_owner():
    registry = ProviderRegistry(
        providers={"telegram": ProviderConfig(provider_id="telegram", settings=SettingsOverride(enabled=True))}
    )
    effective = registry.effective_settings(_account_defaults(enabled=False), "telegram", None)
    assert effective.enabled is False


def test_analyst_can_still_narrow_enabled_true_to_false():
    """The opposite direction (a narrower level disabling something a
    broader level allows) is exactly what the docstring describes and must
    keep working."""
    registry = ProviderRegistry(
        providers={
            "telegram": ProviderConfig(
                provider_id="telegram",
                settings=SettingsOverride(enabled=True),
                analysts={"alice": AnalystConfig(analyst_id="alice", settings=SettingsOverride(enabled=False))},
            )
        }
    )
    effective = registry.effective_settings(_account_defaults(), "telegram", "alice")
    assert effective.enabled is False


def test_enabled_still_inherits_normally_when_nothing_disables_it():
    registry = ProviderRegistry(
        providers={"telegram": ProviderConfig(provider_id="telegram", settings=SettingsOverride())}
    )
    effective = registry.effective_settings(_account_defaults(enabled=True), "telegram", None)
    assert effective.enabled is True


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(app_config, "OWNER_PASSWORD", "test-owner-password")
    monkeypatch.setattr(app_config, "SESSION_SECRET", "test-session-secret")
    store = SignalStore(tmp_path / "test.db")
    monkeypatch.setattr(main_module, "store", store)

    test_client = TestClient(main_module.app)
    login = test_client.post("/auth/login", json={"password": "test-owner-password"})
    assert login.status_code == 200
    test_client.headers["X-CSRF-Token"] = login.json()["csrf_token"]
    return test_client


@pytest.mark.parametrize("field", ["multiplier", "fixed_quantity"])
@pytest.mark.parametrize("bad_value", [-1, 0, True])
def test_account_crud_rejects_invalid_scaling_values(client, field, bad_value):
    payload = {"account_id": "acct1", "broker": "paper", field: bad_value}
    response = client.post("/accounts", json=payload)
    assert response.status_code == 422


def test_account_crud_accepts_valid_scaling_values(client):
    response = client.post("/accounts", json={"account_id": "acct1", "broker": "paper", "multiplier": 2.5, "fixed_quantity": 10.0})
    assert response.status_code == 200


@pytest.mark.parametrize("field", ["multiplier", "fixed_quantity"])
@pytest.mark.parametrize("bad_value", [-1, 0, True])
def test_provider_crud_rejects_invalid_scaling_values(client, field, bad_value):
    response = client.post("/providers/telegram", json={field: bad_value})
    assert response.status_code == 422


@pytest.mark.parametrize("field", ["multiplier", "fixed_quantity"])
@pytest.mark.parametrize("bad_value", [-1, 0, True])
def test_analyst_crud_rejects_invalid_scaling_values(client, field, bad_value):
    response = client.post("/providers/telegram/analysts/alice", json={field: bad_value})
    assert response.status_code == 422


def test_provider_crud_still_accepts_omitted_scaling_fields(client):
    response = client.post("/providers/telegram", json={"enabled": False})
    assert response.status_code == 200
