"""Provider/analyst settings inheritance: account -> provider -> analyst.

Multiple providers/analysts often feed the same destination account (two
different Telegram channels, or several traders posting in one channel)
with risk profiles the account owner wants to control independently,
without duplicating a whole `DestinationAccount` per analyst. This module
resolves one *effective* settings object for a given (account, source,
analyst) combination by merging overrides from the most specific level
down to the account's own defaults — a narrower override always wins over
a broader default, never the reverse.

`source` here is `Signal.source` (e.g. "telegram", "tradingview") — read
as "provider" in this module, matching how config/providers.yaml names it.
`analyst` is `Signal.analyst`, an optional identifier for who *within*
that source posted the signal (e.g. a specific trader in a shared Discord
server). Neither field is required — a signal with no analyst, or a
provider with no config/providers.yaml entry at all, just inherits the
account's own settings unchanged (this module's absence changes nothing
about existing behavior, matching the project's opt-in pattern used
elsewhere, e.g. `managed_lifecycle`).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml


@dataclass
class SettingsOverride:
    """Any subset of these may be set; `None` means "inherit from the next
    broader level." `enabled=False` at any level disables routing through
    it regardless of what a broader level says (an analyst can be muted
    without touching the provider or account)."""

    multiplier: Optional[float] = None
    fixed_quantity: Optional[float] = None
    managed_lifecycle: Optional[bool] = None
    enabled: Optional[bool] = None


@dataclass
class AnalystConfig:
    analyst_id: str
    display_name: str = ""
    settings: SettingsOverride = field(default_factory=SettingsOverride)


@dataclass
class ProviderConfig:
    provider_id: str  # matches Signal.source
    display_name: str = ""
    settings: SettingsOverride = field(default_factory=SettingsOverride)
    analysts: dict[str, AnalystConfig] = field(default_factory=dict)


@dataclass
class ProviderRegistry:
    providers: dict[str, ProviderConfig] = field(default_factory=dict)

    def effective_settings(
        self, account_defaults: SettingsOverride, source: str, analyst: Optional[str]
    ) -> SettingsOverride:
        """Merge account -> provider -> analyst. `account_defaults` should
        have every field populated from the `DestinationAccount` itself
        (its own values are the final fallback, not `None`) — this method
        only narrows from there."""
        merged = SettingsOverride(
            multiplier=account_defaults.multiplier,
            fixed_quantity=account_defaults.fixed_quantity,
            managed_lifecycle=account_defaults.managed_lifecycle,
            enabled=account_defaults.enabled if account_defaults.enabled is not None else True,
        )
        provider = self.providers.get(source)
        if provider is None:
            return merged
        merged = _apply_override(merged, provider.settings)
        if analyst:
            analyst_cfg = provider.analysts.get(analyst)
            if analyst_cfg is not None:
                merged = _apply_override(merged, analyst_cfg.settings)
        return merged

    def get_provider(self, source: str) -> Optional[ProviderConfig]:
        return self.providers.get(source)


def _apply_override(base: SettingsOverride, override: SettingsOverride) -> SettingsOverride:
    return SettingsOverride(
        multiplier=override.multiplier if override.multiplier is not None else base.multiplier,
        fixed_quantity=override.fixed_quantity if override.fixed_quantity is not None else base.fixed_quantity,
        managed_lifecycle=(
            override.managed_lifecycle if override.managed_lifecycle is not None else base.managed_lifecycle
        ),
        enabled=override.enabled if override.enabled is not None else base.enabled,
    )


def load_provider_registry(path: Path) -> ProviderRegistry:
    if not path.exists():
        return ProviderRegistry()

    raw = yaml.safe_load(path.read_text()) or {}
    providers: dict[str, ProviderConfig] = {}
    for provider_id, spec in (raw.get("providers") or {}).items():
        spec = spec or {}
        analysts: dict[str, AnalystConfig] = {}
        for analyst_id, analyst_spec in (spec.get("analysts") or {}).items():
            analyst_spec = analyst_spec or {}
            analysts[analyst_id] = AnalystConfig(
                analyst_id=analyst_id,
                display_name=analyst_spec.get("display_name", analyst_id),
                settings=_settings_from_spec(analyst_spec),
            )
        providers[provider_id] = ProviderConfig(
            provider_id=provider_id,
            display_name=spec.get("display_name", provider_id),
            settings=_settings_from_spec(spec),
            analysts=analysts,
        )
    return ProviderRegistry(providers=providers)


def load_provider_registry_from_store(store) -> ProviderRegistry:
    """The live, GUI/API-editable equivalent of `load_provider_registry` —
    reads `SignalStore`'s `config_providers`/`config_analysts` tables
    (app/db.py) instead of static YAML. See app/main.py's provider/analyst
    CRUD endpoints, which write to the same tables and mutate the engine's
    live `ProviderRegistry` in place, so changes take effect immediately."""
    providers: dict[str, ProviderConfig] = {}
    for row in store.list_config_providers():
        providers[row["provider_id"]] = ProviderConfig(
            provider_id=row["provider_id"],
            display_name=row["display_name"],
            settings=SettingsOverride(
                multiplier=row["multiplier"],
                fixed_quantity=row["fixed_quantity"],
                managed_lifecycle=row["managed_lifecycle"],
                enabled=row["enabled"],
            ),
        )
    for row in store.list_config_analysts():
        provider = providers.setdefault(row["provider_id"], ProviderConfig(provider_id=row["provider_id"]))
        provider.analysts[row["analyst_id"]] = AnalystConfig(
            analyst_id=row["analyst_id"],
            display_name=row["display_name"],
            settings=SettingsOverride(
                multiplier=row["multiplier"],
                fixed_quantity=row["fixed_quantity"],
                managed_lifecycle=row["managed_lifecycle"],
                enabled=row["enabled"],
            ),
        )
    return ProviderRegistry(providers=providers)


def _settings_from_spec(spec: dict) -> SettingsOverride:
    return SettingsOverride(
        multiplier=spec.get("multiplier"),
        fixed_quantity=spec.get("fixed_quantity"),
        managed_lifecycle=spec.get("managed_lifecycle"),
        enabled=spec.get("enabled"),
    )
