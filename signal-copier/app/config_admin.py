"""One-time migration: import existing config/*.yaml into the database
(app/db.py's config_accounts/config_routing_rules/config_providers/
config_analysts tables) so an existing hand-edited setup keeps working
after upgrading to the GUI/API-managed config layer, without anyone
having to re-enter it by hand.

Only runs when the database has zero accounts recorded — once you have
any account (whether from this import or created through the GUI/API),
the database is the live source of truth and the YAML files are no
longer read. See app/main.py, app/routing.py's `load_routing_config_from_store`,
and app/providers.py's `load_provider_registry_from_store`.
"""
from __future__ import annotations

import logging

from app.db import SignalStore
from app.routing import load_routing_config
from app.providers import load_provider_registry
from app import config

logger = logging.getLogger(__name__)


def seed_from_yaml_if_empty(store: SignalStore) -> bool:
    """Returns True if a seed import actually happened."""
    if store.list_config_accounts():
        return False  # already has live-managed config -- never overwrite it with YAML

    routing_config = load_routing_config(config.ROUTING_CONFIG_PATH, config.ACCOUNTS_CONFIG_PATH)
    if not routing_config.accounts and not routing_config.rules:
        return False  # nothing to import either -- a genuinely fresh install

    for account in routing_config.accounts.values():
        store.upsert_config_account(
            account_id=account.account_id,
            broker=account.broker,
            multiplier=account.multiplier,
            fixed_quantity=account.fixed_quantity,
            symbol_map=account.symbol_map,
            enabled=account.enabled,
            managed_lifecycle=account.managed_lifecycle,
        )
    for rule in routing_config.rules:
        store.insert_config_routing_rule(
            source=rule.source, destinations=rule.destinations, symbol_filter=rule.symbol_filter
        )

    provider_registry = load_provider_registry(config.PROVIDERS_CONFIG_PATH)
    for provider in provider_registry.providers.values():
        store.upsert_config_provider(
            provider_id=provider.provider_id,
            display_name=provider.display_name,
            multiplier=provider.settings.multiplier,
            fixed_quantity=provider.settings.fixed_quantity,
            managed_lifecycle=provider.settings.managed_lifecycle,
            enabled=provider.settings.enabled,
        )
        for analyst in provider.analysts.values():
            store.upsert_config_analyst(
                provider_id=provider.provider_id,
                analyst_id=analyst.analyst_id,
                display_name=analyst.display_name,
                multiplier=analyst.settings.multiplier,
                fixed_quantity=analyst.settings.fixed_quantity,
                managed_lifecycle=analyst.settings.managed_lifecycle,
                enabled=analyst.settings.enabled,
            )

    logger.info(
        "imported %d account(s) and %d routing rule(s) from config/*.yaml into the database "
        "(one-time migration — the database is now the live source of truth)",
        len(routing_config.accounts),
        len(routing_config.rules),
    )
    return True
