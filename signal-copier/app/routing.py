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

    def destinations_for(self, source: str, symbol: str, *, include_disabled: bool = False) -> list[DestinationAccount]:
        # EXE-10/SIG-01: see `evaluate`'s own docstring below -- this is a
        # thin wrapper that discards its per-rule trace and keeps only the
        # final destination list, so real signal-time routing and the
        # TR-11 dry-run simulator's per-rule breakdown (app/main.py's
        # `POST /routing-rules/simulate`) run through exactly one matching
        # implementation. Never re-derive this list a second way -- a
        # second implementation is exactly the drift risk that endpoint
        # exists to avoid.
        accounts, _trace = self.evaluate(source, symbol, include_disabled=include_disabled)
        return accounts

    def evaluate(
        self, source: str, symbol: str, *, include_disabled: bool = False
    ) -> tuple[list[DestinationAccount], list[dict]]:
        """The real matching/precedence algorithm every signal is routed
        through (`destinations_for` above is a thin wrapper over this),
        PLUS a per-rule trace of why each rule did or didn't match and
        what it admitted -- the one thing a plain destination list can't
        show, and what TR-11's routing graph/simulator screen
        (app/static/views/tr11.js via `POST /routing-rules/simulate`)
        needs to render "every rule evaluated" honestly, from the same
        single implementation real signal-ingestion uses, never a
        reimplemented approximation that could drift from it.

        # EXE-10: `account.enabled=False` is meant as an entry PAUSE (stop
        # taking new positions on this account), not a way to also cut off
        # the ability to exit a position the account already has open --
        # the caller passes include_disabled=True for a CLOSE signal (see
        # app/engine.py's handle_signal) so an already-owned position can
        # still be found and exited even while new entries are paused.
        # SIG-01: two rules for the same source that both list the same
        # destination account (e.g. a catch-all rule and a
        # symbol-filtered one, or simple config duplication) used to
        # append that account once per matching rule -- routing a single
        # signal to the SAME account more than once, which submitted
        # duplicate orders for it. Each distinct account_id is routed to
        # at most once per signal, keeping the first rule's match order.

        Trace entries, one per rule in `self.rules`'s own real evaluation
        order (ascending rule id / DB insertion order):
        - `matched: False` with a `reason` -- this rule's `source` or
          `symbol_filter` didn't match; nothing of it was evaluated further.
        - `matched: True` with `admitted` (account ids this rule newly
          routed to), `deduped` (account ids this rule named but a prior
          rule already claimed for this signal -- SIG-01) and `paused`
          (account ids this rule named that exist but are disabled and
          this is not a CLOSE -- EXE-10) -- every destination on the rule
          ends up in exactly one of these three, or is silently dropped
          only if the account id itself isn't configured at all (a config
          error, not a routing outcome worth a bucket of its own).
        """
        accounts: list[DestinationAccount] = []
        seen_account_ids: set[str] = set()
        trace: list[dict] = []
        for rule in self.rules:
            if rule.source != source:
                trace.append({
                    "rule": rule,
                    "matched": False,
                    "reason": f"rule source '{rule.source}' does not match signal source '{source}'",
                })
                continue
            if rule.symbol_filter and symbol not in rule.symbol_filter:
                trace.append({
                    "rule": rule,
                    "matched": False,
                    "reason": f"symbol '{symbol}' is not in this rule's symbol_filter {rule.symbol_filter}",
                })
                continue
            admitted: list[str] = []
            deduped: list[str] = []
            paused: list[str] = []
            for account_id in rule.destinations:
                if account_id in seen_account_ids:
                    deduped.append(account_id)
                    continue
                account = self.accounts.get(account_id)
                if account is None:
                    continue
                if account.enabled or include_disabled:
                    accounts.append(account)
                    seen_account_ids.add(account_id)
                    admitted.append(account_id)
                else:
                    paused.append(account_id)
            trace.append({
                "rule": rule,
                "matched": True,
                "admitted": admitted,
                "deduped": deduped,
                "paused": paused,
            })
        return accounts, trace


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
                max_notional_exposure=spec.get("max_notional_exposure"),
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
            max_notional_exposure=row["max_notional_exposure"],
        )
        for row in store.list_config_accounts()
    }
    rules = [
        RoutingRule(source=row["source"], destinations=row["destinations"], symbol_filter=row["symbol_filter"])
        for row in store.list_config_routing_rules()
    ]
    return RoutingConfig(rules=rules, accounts=accounts)
