"""WP-23 recovery edges: lost entries, venue > tracked adoption, and stop cancellation with symbol.

D-14: Lost-response managed entry resolution with grace period
D-15: Partial coverage deficit retry with uncovered_quantity > 0
D-16: Crash recovery with venue > tracked adoption
D-17: ccxt cancel_order/replace_stop_quantity symbol kwarg
"""
from __future__ import annotations

import inspect
from app.brokers.base import BrokerAdapter
from app.brokers.paper import PaperBroker
from app.brokers.alpaca import AlpacaBroker
from app import config


def test_lost_entry_grace_seconds_config():
    """Verify LOST_ENTRY_GRACE_SECONDS is configured with default 300"""
    assert hasattr(config, "LOST_ENTRY_GRACE_SECONDS")
    assert config.LOST_ENTRY_GRACE_SECONDS == 300
    assert isinstance(config.LOST_ENTRY_GRACE_SECONDS, int)


def test_cancel_order_has_symbol_parameter():
    """D-17: cancel_order signature includes symbol kwarg"""
    # Base class
    sig = inspect.signature(BrokerAdapter.cancel_order)
    assert "symbol" in sig.parameters
    param = sig.parameters["symbol"]
    assert param.default is None  # Optional parameter

    # Paper broker
    sig = inspect.signature(PaperBroker.cancel_order)
    assert "symbol" in sig.parameters

    # Alpaca broker
    sig = inspect.signature(AlpacaBroker.cancel_order)
    assert "symbol" in sig.parameters


def test_replace_stop_quantity_has_symbol_parameter():
    """D-17: replace_stop_quantity signature includes symbol kwarg"""
    # Base class
    sig = inspect.signature(BrokerAdapter.replace_stop_quantity)
    assert "symbol" in sig.parameters
    param = sig.parameters["symbol"]
    assert param.default is None  # Optional parameter

    # Paper broker
    sig = inspect.signature(PaperBroker.replace_stop_quantity)
    assert "symbol" in sig.parameters

    # Alpaca broker
    sig = inspect.signature(AlpacaBroker.replace_stop_quantity)
    assert "symbol" in sig.parameters


def test_lifecycle_manager_has_adopt_venue_ownership():
    """D-16: PositionLifecycleManager has adopt_venue_ownership method"""
    from app.lifecycle.manager import PositionLifecycleManager

    assert hasattr(PositionLifecycleManager, "adopt_venue_ownership")
    assert callable(PositionLifecycleManager.adopt_venue_ownership)

    # Check signature
    sig = inspect.signature(PositionLifecycleManager.adopt_venue_ownership)
    params = list(sig.parameters.keys())
    assert "account" in params
    assert "symbol" in params
    assert "venue_owned_quantity" in params


def test_reconciler_has_resolve_lost_entries():
    """D-14: OrderReconciler has _resolve_lost_entries method"""
    from app.reconciliation import OrderReconciler

    assert hasattr(OrderReconciler, "_resolve_lost_entries")
    assert callable(OrderReconciler._resolve_lost_entries)
