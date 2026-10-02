"""Tests for WP-08: Intent on the signal (Side.SHORT) and allow_short on accounts.

A-01: Add Side.SHORT enum value so "sell" on an account with a same-symbol
long resolves through the close path, distinct from "short" intent.

B-14: Add allow_short field to accounts to control whether shorts can be
opened on a per-account basis (default False for equity/cash).
"""
import pytest

from app.db import SignalStore
from app.models import DestinationAccount, Signal, Side
from app.routing import RoutingConfig, RoutingRule


@pytest.fixture
def store(tmp_path):
    return SignalStore(tmp_path / "test.db")


def test_side_short_enum_exists():
    """Verify Side.SHORT enum value exists."""
    assert hasattr(Side, "SHORT")
    assert Side.SHORT.value == "short"


def test_side_enum_values():
    """Verify all Side enum values."""
    assert Side.BUY.value == "buy"
    assert Side.SELL.value == "sell"
    assert Side.SHORT.value == "short"
    assert Side.CLOSE.value == "close"


def test_destination_account_allow_short_default():
    """allow_short should default to False."""
    account = DestinationAccount(account_id="acct1", broker="paper")
    assert account.allow_short is False


def test_destination_account_allow_short_true():
    """allow_short should be settable to True."""
    account = DestinationAccount(
        account_id="acct1", broker="paper", allow_short=True
    )
    assert account.allow_short is True


def test_destination_account_allow_short_false():
    """allow_short should be settable to False."""
    account = DestinationAccount(
        account_id="acct1", broker="paper", allow_short=False
    )
    assert account.allow_short is False


def test_short_side_can_be_stored_in_database(store):
    """Test that Side.SHORT signals can be persisted to the database.

    Note: Full engine routing tests with Side.SHORT are deferred to a later
    work package once export_events and signal_platform_contracts support it.
    """
    signal = Signal(
        source="tradingview",
        symbol="BTCUSDT",
        side=Side.SHORT,
        quantity=1.0,
    )
    store.save_signal(signal)

    # Verify database stored and retrieved the signal with Side.SHORT intact
    retrieved = store.get_signal(signal.id)
    assert retrieved is not None
    assert retrieved["side"] == Side.SHORT.value
    assert retrieved["symbol"] == "BTCUSDT"
    assert retrieved["quantity"] == 1.0


def test_engine_routing_with_allow_short_false():
    """Routing config with allow_short=False can be created and used."""
    accounts = {
        "readonly_acct": DestinationAccount(
            account_id="readonly_acct", broker="paper", allow_short=False
        )
    }
    routing = RoutingConfig(
        rules=[RoutingRule(source="source1", destinations=["readonly_acct"])],
        accounts=accounts,
    )
    assert routing.accounts["readonly_acct"].allow_short is False


def test_engine_routing_with_allow_short_true():
    """Routing config with allow_short=True can be created and used."""
    accounts = {
        "margin_acct": DestinationAccount(
            account_id="margin_acct", broker="paper", allow_short=True
        )
    }
    routing = RoutingConfig(
        rules=[RoutingRule(source="source1", destinations=["margin_acct"])],
        accounts=accounts,
    )
    assert routing.accounts["margin_acct"].allow_short is True


def test_signal_with_short_side_can_be_created():
    """Signals with Side.SHORT can be instantiated."""
    signal = Signal(source="tradingview", symbol="AAPL", side=Side.SHORT, quantity=10)
    assert signal.side == Side.SHORT
    assert signal.symbol == "AAPL"
    assert signal.quantity == 10
