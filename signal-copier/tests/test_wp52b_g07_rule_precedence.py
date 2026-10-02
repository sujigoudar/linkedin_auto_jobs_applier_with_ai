"""Regression test for WP-52b: G-07 rule precedence.

G-07: Rule precedence is insertion order, so a catch-all rule listed first
beats a more specific symbol-filtered rule listed later.
Expected fix: Explicit priority or most-specific-first semantics.

This test verifies that the routing engine prioritizes rules by specificity:
1. Symbol-filtered rules are evaluated before catch-all rules
2. Catch-all rules apply only to symbols not matched by filtered rules
3. Among rules of the same specificity, insertion order is preserved
"""
from app.models import DestinationAccount
from app.routing import RoutingConfig, RoutingRule


def test_g07_catchall_inserted_first_symbol_filtered_second():
    """G-07: Catch-all inserted first should NOT beat symbol-filtered rule.

    The most-specific (symbol-filtered) rule should win regardless of
    insertion order. This is the exact scenario described in G-07.
    """
    config = RoutingConfig()

    acct_catch_all = DestinationAccount(account_id="acct_catch_all", broker="paper")
    acct_filtered = DestinationAccount(account_id="acct_filtered", broker="paper")
    config.accounts = {
        "acct_catch_all": acct_catch_all,
        "acct_filtered": acct_filtered,
    }

    # G-07: Catch-all inserted FIRST, symbol-filtered SECOND
    # The bug would have catch-all win due to insertion order
    # The fix should make the more specific rule win
    rule_catchall = RoutingRule(source="tradingview", destinations=["acct_catch_all"])
    rule_aapl_filtered = RoutingRule(
        source="tradingview", destinations=["acct_filtered"], symbol_filter=["AAPL"]
    )
    config.rules = [rule_catchall, rule_aapl_filtered]

    # For AAPL, the symbol-filtered rule should win despite being inserted second
    destinations, trace = config.evaluate("tradingview", "AAPL")

    # The filtered account should be chosen for AAPL (higher precedence)
    account_ids = [a.account_id for a in destinations]
    assert (
        account_ids[0] == "acct_filtered"
    ), f"G-07 fix failed: expected acct_filtered first for AAPL, got {account_ids}"

    # Trace should show filtered rule evaluated first (precedence 0)
    assert trace[0]["precedence"] == 0
    assert trace[0]["matched"] is True
    assert trace[0]["admitted"] == ["acct_filtered"]

    # Catch-all rule evaluated second (precedence 1)
    assert trace[1]["precedence"] == 1
    assert trace[1]["matched"] is True
    assert trace[1]["admitted"] == ["acct_catch_all"]


def test_g07_multiple_filters_preserve_specificity():
    """G-07: Verify multiple filtered rules are evaluated before catch-all."""
    config = RoutingConfig()

    acct_aapl = DestinationAccount(account_id="acct_aapl", broker="paper")
    acct_goog = DestinationAccount(account_id="acct_goog", broker="paper")
    acct_catch = DestinationAccount(account_id="acct_catch", broker="paper")
    config.accounts = {
        "acct_aapl": acct_aapl,
        "acct_goog": acct_goog,
        "acct_catch": acct_catch,
    }

    # Catch-all inserted first (wrong insertion order)
    rule_catch = RoutingRule(source="tradingview", destinations=["acct_catch"])
    rule_aapl = RoutingRule(
        source="tradingview", destinations=["acct_aapl"], symbol_filter=["AAPL"]
    )
    rule_goog = RoutingRule(
        source="tradingview", destinations=["acct_goog"], symbol_filter=["GOOG"]
    )
    config.rules = [rule_catch, rule_aapl, rule_goog]

    # For GOOG, the GOOG filter should be evaluated first
    destinations, _ = config.evaluate("tradingview", "GOOG")
    account_ids = [a.account_id for a in destinations]

    # GOOG-filtered rule should be first (higher precedence than catch-all)
    assert account_ids[0] == "acct_goog", (
        f"G-07 fix failed: expected acct_goog first for GOOG, got {account_ids}"
    )


def test_g07_ensure_catch_all_only_for_unmatched_symbols():
    """G-07: Catch-all should only apply to symbols not matched by filters."""
    config = RoutingConfig()

    acct_filtered = DestinationAccount(account_id="acct_filtered", broker="paper")
    acct_catch = DestinationAccount(account_id="acct_catch", broker="paper")
    config.accounts = {
        "acct_filtered": acct_filtered,
        "acct_catch": acct_catch,
    }

    rule_catch = RoutingRule(source="tradingview", destinations=["acct_catch"])
    rule_filtered = RoutingRule(
        source="tradingview",
        destinations=["acct_filtered"],
        symbol_filter=["AAPL", "MSFT"],
    )
    config.rules = [rule_catch, rule_filtered]

    # For AAPL (in filter), only the filtered rule should match
    destinations, _ = config.evaluate("tradingview", "AAPL")
    account_ids = [a.account_id for a in destinations]
    assert "acct_filtered" in account_ids
    assert account_ids[0] == "acct_filtered"

    # For TSLA (not in filter), only the catch-all should match
    destinations, _ = config.evaluate("tradingview", "TSLA")
    account_ids = [a.account_id for a in destinations]
    assert account_ids == ["acct_catch"], (
        f"G-07 fix failed: for unmatched symbol TSLA, expected only catch-all, "
        f"got {account_ids}"
    )
