"""Loads routing rules: which sources feed which destination accounts."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

from app.models import DestinationAccount


@dataclass
class RoutingRule:
    source: str
    destinations: list[str]
    symbol_filter: Optional[list[str]] = None


@dataclass
class RoutingConfig:
    rules: list[RoutingRule] = field(default_factory=list)
    accounts: dict[str, DestinationAccount] = field(default_factory=dict)

    def destinations_for(self, source: str, symbol: str) -> list[DestinationAccount]:
        accounts: list[DestinationAccount] = []
        for rule in self.rules:
            if rule.source != source:
                continue
            if rule.symbol_filter and symbol not in rule.symbol_filter:
                continue
            for account_id in rule.destinations:
                account = self.accounts.get(account_id)
                if account and account.enabled:
                    accounts.append(account)
        return accounts


def load_routing_config(routing_path: Path, accounts_path: Path) -> RoutingConfig:
    accounts: dict[str, DestinationAccount] = {}
    if accounts_path.exists():
        raw_accounts = yaml.safe_load(accounts_path.read_text()) or {}
        for account_id, spec in raw_accounts.get("accounts", {}).items():
            accounts[account_id] = DestinationAccount(
                account_id=account_id,
                broker=spec["broker"],
                multiplier=float(spec.get("multiplier", 1.0)),
                fixed_quantity=spec.get("fixed_quantity"),
                symbol_map=spec.get("symbol_map", {}) or {},
                enabled=spec.get("enabled", True),
                managed_lifecycle=spec.get("managed_lifecycle", False),
            )

    rules: list[RoutingRule] = []
    if routing_path.exists():
        raw_routing = yaml.safe_load(routing_path.read_text()) or {}
        for rule in raw_routing.get("rules", []):
            rules.append(
                RoutingRule(
                    source=rule["source"],
                    destinations=rule["destinations"],
                    symbol_filter=rule.get("symbol_filter"),
                )
            )

    return RoutingConfig(rules=rules, accounts=accounts)


def load_routing_config_from_store(store) -> RoutingConfig:
    """The live, GUI/API-editable equivalent of `load_routing_config` —
    reads `SignalStore`'s `config_accounts`/`config_routing_rules` tables
    (app/db.py) instead of static YAML. See app/main.py's account/routing
    CRUD endpoints, which write to the same tables and mutate the engine's
    live `RoutingConfig` in place, so changes take effect immediately."""
    accounts: dict[str, DestinationAccount] = {
        row["account_id"]: DestinationAccount(
            account_id=row["account_id"],
            broker=row["broker"],
            multiplier=row["multiplier"],
            fixed_quantity=row["fixed_quantity"],
            symbol_map=row["symbol_map"],
            enabled=row["enabled"],
            managed_lifecycle=row["managed_lifecycle"],
        )
        for row in store.list_config_accounts()
    }
    rules = [
        RoutingRule(source=row["source"], destinations=row["destinations"], symbol_filter=row["symbol_filter"])
        for row in store.list_config_routing_rules()
    ]
    return RoutingConfig(rules=rules, accounts=accounts)
