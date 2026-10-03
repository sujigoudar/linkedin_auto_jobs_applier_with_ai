"""WP-07: most-specific routing rule wins.

Tests that rules are evaluated in precedence order: rules whose
symbol_filter names the symbol first (in insertion order), then
rules with no filter (insertion order).
"""
from app.models import DestinationAccount
from app.routing import RoutingConfig, RoutingRule


def test_most_specific_rule_wins_over_catchall():
    """Catch-all rule inserted first, symbol-filtered rule inserted second.
    The symbol-filtered rule should win for the filtered symbol."""
    config = RoutingConfig()

    # Add accounts
    acct_a = DestinationAccount(account_id="acct_a", broker="paper")
    acct_b = DestinationAccount(account_id="acct_b", broker="paper")
    config.accounts = {"acct_a": acct_a, "acct_b": acct_b}

    # Add rules: catch-all first, then symbol-filtered
    rule1_catchall = RoutingRule(source="tradingview", destinations=["acct_a"])
    rule2_filtered = RoutingRule(source="tradingview", destinations=["acct_b"], symbol_filter=["AAPL"])
    config.rules = [rule1_catchall, rule2_filtered]

    # For AAPL, the filtered rule is evaluated first (higher precedence)
    destinations, trace = config.evaluate("tradingview", "AAPL")

    # Both accounts match, but acct_b (from symbol-filtered rule) is returned first
    assert [a.account_id for a in destinations] == ["acct_b", "acct_a"]

    # Symbol-filtered rule is evaluated first (precedence 0)
    assert len(trace) == 2
    assert trace[0]["matched"] is True
    assert trace[0]["admitted"] == ["acct_b"]
    assert trace[0]["precedence"] == 0

    # Catch-all rule is evaluated second (precedence 1)
    assert trace[1]["matched"] is True
    assert trace[1]["admitted"] == ["acct_a"]
    assert trace[1]["precedence"] == 1


def test_catchall_applies_to_other_symbols():
    """Catch-all rule applies to symbols not in the filtered rule's list."""
    config = RoutingConfig()

    acct_a = DestinationAccount(account_id="acct_a", broker="paper")
    acct_b = DestinationAccount(account_id="acct_b", broker="paper")
    config.accounts = {"acct_a": acct_a, "acct_b": acct_b}

    rule1_catchall = RoutingRule(source="tradingview", destinations=["acct_a"])
    rule2_filtered = RoutingRule(source="tradingview", destinations=["acct_b"], symbol_filter=["AAPL"])
    config.rules = [rule1_catchall, rule2_filtered]

    # For GOOG (not in the filter), catch-all should apply
    destinations, trace = config.evaluate("tradingview", "GOOG")
    assert [a.account_id for a in destinations] == ["acct_a"]

    assert trace[0]["matched"] is False
    assert "symbol_filter" in trace[0]["reason"]
    assert trace[0]["precedence"] == 0

    assert trace[1]["matched"] is True
    assert trace[1]["admitted"] == ["acct_a"]
    assert trace[1]["precedence"] == 1


def test_multiple_symbol_filtered_rules_preserve_insertion_order():
    """Multiple symbol-filtered rules maintain insertion order within that group."""
    config = RoutingConfig()

    acct_a = DestinationAccount(account_id="acct_a", broker="paper")
    acct_b = DestinationAccount(account_id="acct_b", broker="paper")
    acct_c = DestinationAccount(account_id="acct_c", broker="paper")
    config.accounts = {"acct_a": acct_a, "acct_b": acct_b, "acct_c": acct_c}

    # Two filtered rules, then a catch-all
    rule1_filtered = RoutingRule(source="tradingview", destinations=["acct_a"], symbol_filter=["AAPL"])
    rule2_filtered = RoutingRule(source="tradingview", destinations=["acct_b"], symbol_filter=["GOOG"])
    rule3_catchall = RoutingRule(source="tradingview", destinations=["acct_c"])
    config.rules = [rule1_filtered, rule2_filtered, rule3_catchall]

    # For AAPL, first filtered rule matches (higher precedence than catch-all)
    destinations, trace = config.evaluate("tradingview", "AAPL")
    # Both acct_a (from rule1) and acct_c (from rule3) match
    assert [a.account_id for a in destinations] == ["acct_a", "acct_c"]
    assert trace[0]["precedence"] == 0
    assert trace[0]["matched"] is True
    assert trace[0]["admitted"] == ["acct_a"]
    # rule2 doesn't match AAPL
    assert trace[1]["precedence"] == 1
    assert trace[1]["matched"] is False
    # rule3 (catch-all) matches
    assert trace[2]["precedence"] == 2
    assert trace[2]["matched"] is True
    assert trace[2]["admitted"] == ["acct_c"]

    # For GOOG, second filtered rule is evaluated first and matches
    # (first filtered rule doesn't match GOOG)
    destinations, trace = config.evaluate("tradingview", "GOOG")
    # Both acct_b (from rule2) and acct_c (from rule3) match, but rule2 has higher precedence
    assert [a.account_id for a in destinations] == ["acct_b", "acct_c"]
    assert trace[0]["precedence"] == 0
    assert trace[0]["matched"] is False  # rule1 (AAPL filter) doesn't match GOOG
    assert trace[1]["precedence"] == 1
    assert trace[1]["matched"] is True
    assert trace[1]["admitted"] == ["acct_b"]
    assert trace[2]["precedence"] == 2
    assert trace[2]["matched"] is True
    assert trace[2]["admitted"] == ["acct_c"]

    # For OTHER, no filtered rule matches, so only catch-all matches
    destinations, trace = config.evaluate("tradingview", "OTHER")
    assert [a.account_id for a in destinations] == ["acct_c"]
    assert trace[0]["precedence"] == 0
    assert trace[0]["matched"] is False  # rule1 (AAPL filter) doesn't match OTHER
    assert trace[1]["precedence"] == 1
    assert trace[1]["matched"] is False  # rule2 (GOOG filter) doesn't match OTHER
    assert trace[2]["precedence"] == 2
    assert trace[2]["matched"] is True
    assert trace[2]["admitted"] == ["acct_c"]


def test_catchall_only():
    """A catch-all rule with no filtered rules."""
    config = RoutingConfig()

    acct_a = DestinationAccount(account_id="acct_a", broker="paper")
    config.accounts = {"acct_a": acct_a}

    rule1 = RoutingRule(source="tradingview", destinations=["acct_a"])
    config.rules = [rule1]

    destinations, trace = config.evaluate("tradingview", "ANYSYMBOL")
    assert [a.account_id for a in destinations] == ["acct_a"]
    assert trace[0]["precedence"] == 0
    assert trace[0]["matched"] is True


def test_no_matching_rules():
    """No rules match the source/symbol combination."""
    config = RoutingConfig()

    acct_a = DestinationAccount(account_id="acct_a", broker="paper")
    config.accounts = {"acct_a": acct_a}

    rule1 = RoutingRule(source="tradingview", destinations=["acct_a"], symbol_filter=["AAPL"])
    config.rules = [rule1]

    destinations, trace = config.evaluate("telegram", "AAPL")
    assert destinations == []
    assert trace[0]["matched"] is False
    assert "source" in trace[0]["reason"]
