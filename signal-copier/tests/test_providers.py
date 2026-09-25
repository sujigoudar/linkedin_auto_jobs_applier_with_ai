import pytest

from app.providers import (
    AnalystConfig,
    ProviderConfig,
    ProviderRegistry,
    SettingsOverride,
    load_provider_registry,
)


def _account_defaults(**overrides) -> SettingsOverride:
    defaults = dict(multiplier=1.0, fixed_quantity=None, managed_lifecycle=False, enabled=True)
    defaults.update(overrides)
    return SettingsOverride(**defaults)


def test_no_matching_provider_inherits_account_defaults_unchanged():
    registry = ProviderRegistry()
    effective = registry.effective_settings(_account_defaults(multiplier=2.0), "unknown_source", None)
    assert effective.multiplier == 2.0
    assert effective.enabled is True


def test_provider_override_narrows_account_default():
    registry = ProviderRegistry(
        providers={"telegram": ProviderConfig(provider_id="telegram", settings=SettingsOverride(multiplier=0.5))}
    )
    effective = registry.effective_settings(_account_defaults(multiplier=1.0), "telegram", None)
    assert effective.multiplier == 0.5


def test_analyst_override_wins_over_provider_override():
    registry = ProviderRegistry(
        providers={
            "telegram": ProviderConfig(
                provider_id="telegram",
                settings=SettingsOverride(multiplier=0.5),
                analysts={"alice": AnalystConfig(analyst_id="alice", settings=SettingsOverride(multiplier=1.0))},
            )
        }
    )
    effective = registry.effective_settings(_account_defaults(multiplier=1.0), "telegram", "alice")
    assert effective.multiplier == 1.0  # analyst override wins over the provider default


def test_analyst_not_configured_falls_back_to_provider_level():
    registry = ProviderRegistry(
        providers={"telegram": ProviderConfig(provider_id="telegram", settings=SettingsOverride(multiplier=0.5))}
    )
    effective = registry.effective_settings(_account_defaults(multiplier=1.0), "telegram", "unconfigured_analyst")
    assert effective.multiplier == 0.5


def test_analyst_can_be_disabled_independent_of_provider():
    registry = ProviderRegistry(
        providers={
            "telegram": ProviderConfig(
                provider_id="telegram",
                analysts={"bob": AnalystConfig(analyst_id="bob", settings=SettingsOverride(enabled=False))},
            )
        }
    )
    effective = registry.effective_settings(_account_defaults(), "telegram", "bob")
    assert effective.enabled is False


def test_unset_fields_at_narrower_level_inherit_from_broader_level():
    registry = ProviderRegistry(
        providers={
            "telegram": ProviderConfig(
                provider_id="telegram",
                settings=SettingsOverride(multiplier=0.5, managed_lifecycle=True),
                analysts={"alice": AnalystConfig(analyst_id="alice", settings=SettingsOverride(multiplier=2.0))},
            )
        }
    )
    # alice only overrides multiplier -- managed_lifecycle must still inherit from the provider level
    effective = registry.effective_settings(_account_defaults(managed_lifecycle=False), "telegram", "alice")
    assert effective.multiplier == 2.0
    assert effective.managed_lifecycle is True


def test_load_provider_registry_from_yaml(tmp_path):
    path = tmp_path / "providers.yaml"
    path.write_text(
        """
providers:
  telegram:
    display_name: "Telegram signals"
    multiplier: 0.5
    analysts:
      alice:
        display_name: "Alice"
        multiplier: 1.0
      bob:
        enabled: false
"""
    )
    registry = load_provider_registry(path)

    provider = registry.get_provider("telegram")
    assert provider.display_name == "Telegram signals"
    assert provider.settings.multiplier == 0.5
    assert provider.analysts["alice"].settings.multiplier == 1.0
    assert provider.analysts["bob"].settings.enabled is False


def test_load_provider_registry_missing_file_returns_empty_registry(tmp_path):
    registry = load_provider_registry(tmp_path / "does-not-exist.yaml")
    assert registry.providers == {}
