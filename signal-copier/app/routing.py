"""Loads routing rules: which sources feed which destination accounts."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

from app.models import DestinationAccount, ManagementRecipe


@dataclass
class RoutingRule:
    source: str
    destinations: list[str]
    symbol_filter: Optional[list[str]] = None
    #: ALLOC-01: "single" (default) -- the rule's destinations are
    #: ALTERNATIVE places to hold ONE intended trade (listed in priority
    #: order); exactly one account is selected per signal. "replicate" --
    #: an explicit, owner-configured instruction that every destination
    #: receives its own separately authorized copy (the pre-ALLOC-01
    #: broadcast behavior). Never inferred from the number of accounts.
    delivery_mode: str = "single"

    def __post_init__(self) -> None:
        if self.delivery_mode not in DELIVERY_MODES:
            raise ValueError(f"delivery_mode must be one of {DELIVERY_MODES}, got {self.delivery_mode!r}")


DELIVERY_MODES = ("single", "replicate")


@dataclass
class AllocationPool:
    """The approved eligible destinations for one signal, split by how
    they may be used: `single` accounts are alternatives (at most ONE
    may receive the trade), `replicate` accounts each get an explicitly
    configured separate copy."""

    single: list[DestinationAccount] = field(default_factory=list)
    replicate: list[DestinationAccount] = field(default_factory=list)


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

    def pool_for(self, source: str, symbol: str, *, include_disabled: bool = False) -> AllocationPool:
        """Splits `evaluate`'s single matching result by each admitting
        rule's `delivery_mode`, preserving rule order (= priority) then
        in-rule destination order. One matching implementation only."""
        _accounts, trace = self.evaluate(source, symbol, include_disabled=include_disabled)
        pool = AllocationPool()
        for entry in trace:
            if not entry["matched"]:
                continue
            target = pool.replicate if entry["rule"].delivery_mode == "replicate" else pool.single
            for account_id in entry["admitted"]:
                target.append(self.accounts[account_id])
        return pool

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
        # WP-07: rules are evaluated in precedence order: rules whose
        # `symbol_filter` names the symbol first (in insertion order), then
        # rules with no filter (insertion order). This ensures most-specific
        # rules win.

        Trace entries, one per rule in evaluation order (most-specific first):
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
        - `precedence`: 0-based evaluation position in the evaluation order.
        """
        accounts: list[DestinationAccount] = []
        seen_account_ids: set[str] = set()
        trace: list[dict] = []

        # Separate rules into symbol-filtered and catch-all, preserving insertion order within each group
        symbol_filtered_rules = []
        catchall_rules = []
        for i, rule in enumerate(self.rules):
            if rule.symbol_filter is not None:
                symbol_filtered_rules.append((i, rule))
            else:
                catchall_rules.append((i, rule))

        # Evaluate in precedence order: symbol-filtered first, then catch-all
        evaluation_order = symbol_filtered_rules + catchall_rules

        for precedence, (_original_index, rule) in enumerate(evaluation_order):
            if rule.source != source:
                trace.append({
                    "rule": rule,
                    "matched": False,
                    "reason": f"rule source '{rule.source}' does not match signal source '{source}'",
                    "precedence": precedence,
                })
                continue
            if rule.symbol_filter and symbol not in rule.symbol_filter:
                trace.append({
                    "rule": rule,
                    "matched": False,
                    "reason": f"symbol '{symbol}' is not in this rule's symbol_filter {rule.symbol_filter}",
                    "precedence": precedence,
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
                "precedence": precedence,
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
                risk_percent_of_equity=spec.get("risk_percent_of_equity"),
                # P0-5: an explicit management_recipe in accounts.yaml is
                # honored as-is (including a deliberate mismatch against
                # managed_lifecycle, surfaced/audited rather than
                # silently resolved -- see ManagementRecipe's own
                # docstring); omitted, DestinationAccount.__post_init__
                # fills it from managed_lifecycle the same way it always
                # has for every other unset field here.
                management_recipe=(
                    ManagementRecipe(spec["management_recipe"]) if spec.get("management_recipe") else None
                ),
                qualification_level=spec.get("qualification_level"),
                exclusive_writer_qualified=spec.get("exclusive_writer_qualified", False),
<<<<<<< HEAD
                daily_loss_limit_percent=spec.get("daily_loss_limit_percent"),
                min_equity_threshold=spec.get("min_equity_threshold"),
<<<<<<< HEAD
                currency=spec.get("currency"),
                max_gross_leverage=spec.get("max_gross_leverage"),
                allow_short=spec.get("allow_short", False),
                sizing_mode=spec.get("sizing_mode", "multiplier"),
                risk_fraction=spec.get("risk_fraction"),
=======
                allow_short=spec.get("allow_short", False),  # WP-08: allow_short setting
>>>>>>> 30cc4ad (WP-43: TR-11 precedence and intent resolution in the simulator)
=======
                max_gross_leverage=spec.get("max_gross_leverage"),
>>>>>>> c659a00 (WP-32b: Enforce max_gross_leverage cap and implement paper broker margin/equity)
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
                    delivery_mode=rule.get("delivery_mode", "single"),
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
            risk_percent_of_equity=row["risk_percent_of_equity"],
            management_recipe=ManagementRecipe(row["management_recipe"]) if row.get("management_recipe") else None,
            qualification_level=row.get("qualification_level"),
            exclusive_writer_qualified=bool(row.get("exclusive_writer_qualified", False)),
<<<<<<< HEAD
            daily_loss_limit_percent=row.get("daily_loss_limit_percent"),
            min_equity_threshold=row.get("min_equity_threshold"),
<<<<<<< HEAD
            evidence_class=row.get("evidence_class"),  # WP-38 (G-C-13)
            paper_order_id_sequence=row.get("paper_order_id_sequence"),  # WP-38 (G-C-24) (WP-30: Loss limits end-to-end wiring (B-08/F-02))
            currency=row.get("currency"),
            max_gross_leverage=row.get("max_gross_leverage"),
            allow_short=bool(row.get("allow_short", False)),
            sizing_mode=row.get("sizing_mode", "multiplier"),
            risk_fraction=row.get("risk_fraction"),
=======
            allow_short=bool(row.get("allow_short", False)),  # WP-08: allow_short setting
>>>>>>> 30cc4ad (WP-43: TR-11 precedence and intent resolution in the simulator)
=======
            max_gross_leverage=row.get("max_gross_leverage"),
>>>>>>> c659a00 (WP-32b: Enforce max_gross_leverage cap and implement paper broker margin/equity)
        )
        for row in store.list_config_accounts()
    }
    rules = [
        RoutingRule(
            source=row["source"],
            destinations=row["destinations"],
            symbol_filter=row["symbol_filter"],
            delivery_mode=row.get("delivery_mode", "single"),
        )
        for row in store.list_config_routing_rules()
    ]
    return RoutingConfig(rules=rules, accounts=accounts)
