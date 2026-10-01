"""Regression tests for Track 57 routing mutation testing.

These tests specifically target real bugs found by mutmut in routing.py:
- RoutingRule.symbol_filter None default (survived)
- RoutingConfig.rules None default (survived)
- RoutingConfig.accounts None default (survived)
"""
from app.models import DestinationAccount
from app.routing import RoutingConfig, RoutingRule


def _account(account_id: str, **overrides) -> DestinationAccount:
    return DestinationAccount(account_id=account_id, broker="paper", **overrides)


class TestRoutingRuleSymbolFilterDefault:
    """Regression: RoutingRule.symbol_filter must default to None, not ""."""

    def test_symbol_filter_default_is_none_not_empty_string(self):
        """Mutant 2: symbol_filter = "" would cause wrong type."""
        rule = RoutingRule(source="tv", destinations=["acct1"])
        # Default must be None for "no filter" semantics
        assert rule.symbol_filter is None

    def test_symbol_filter_none_means_accept_all_symbols(self):
        """A rule with None symbol_filter matches any symbol."""
        acct1 = _account("acct1")
        config = RoutingConfig(
            rules=[RoutingRule(source="tv", destinations=["acct1"])],
            accounts={"acct1": acct1},
        )
        # With None (no filter), both symbols should match
        assert len(config.destinations_for("tv", "BTCUSDT")) == 1
        assert len(config.destinations_for("tv", "ETHUSDT")) == 1


class TestRoutingConfigRulesDefault:
    """Regression: RoutingConfig.rules must be a list, not None."""

    def test_routing_config_rules_default_is_empty_list(self):
        """Mutant 4: rules = None would cause AttributeError when iterating."""
        config = RoutingConfig()
        # Must be a list, not None
        assert config.rules == []
        assert isinstance(config.rules, list)

    def test_routing_config_evaluate_with_no_rules_returns_empty(self):
        """Evaluating with empty rules list must work."""
        config = RoutingConfig(accounts={"acct1": _account("acct1")})
        accounts, trace = config.evaluate("tv", "BTCUSDT")
        assert accounts == []
        assert trace == []

    def test_routing_config_destinations_for_with_no_rules(self):
        """destinations_for with no rules must return empty list."""
        config = RoutingConfig(accounts={"acct1": _account("acct1")})
        assert config.destinations_for("tv", "BTCUSDT") == []


class TestRoutingConfigAccountsDefault:
    """Regression: RoutingConfig.accounts must be a dict, not None."""

    def test_routing_config_accounts_default_is_empty_dict(self):
        """Mutant 5: accounts = None would cause AttributeError when accessing."""
        config = RoutingConfig()
        # Must be a dict, not None
        assert config.accounts == {}
        assert isinstance(config.accounts, dict)

    def test_routing_config_with_rule_but_no_accounts_skips_missing_account(self):
        """A rule referencing an account not in the config is silently skipped."""
        config = RoutingConfig(rules=[RoutingRule(source="tv", destinations=["missing_acct"])])
        # No account in config, so nothing routed
        assert config.destinations_for("tv", "BTCUSDT") == []

    def test_routing_config_get_missing_account_returns_none(self):
        """Direct access to missing account returns None."""
        config = RoutingConfig()
        # Even with empty accounts dict, .get() must work
        assert config.accounts.get("missing") is None


class TestRoutingConfigConsistency:
    """Regression: routing must work correctly with all defaults."""

    def test_default_routing_config_is_fully_operational(self):
        """A routing config with all defaults must not crash."""
        config = RoutingConfig()
        accounts, trace = config.evaluate("any_source", "any_symbol")
        assert accounts == []
        assert trace == []

    def test_routing_config_with_only_rules_no_accounts_is_safe(self):
        """Rules without corresponding accounts must not crash."""
        config = RoutingConfig(
            rules=[
                RoutingRule(source="tv", destinations=["acct1", "acct2"]),
                RoutingRule(source="webhook", destinations=["acct3"]),
            ]
        )
        # All accounts are missing; should return empty safely
        assert config.destinations_for("tv", "BTCUSDT") == []
        assert config.destinations_for("webhook", "EURUSD") == []

    def test_routing_config_with_only_accounts_no_rules_is_safe(self):
        """Accounts without rules must not crash."""
        config = RoutingConfig(
            accounts={"acct1": _account("acct1"), "acct2": _account("acct2")}
        )
        # No rules to route signals to; should return empty safely
        assert config.destinations_for("any_source", "any_symbol") == []
