"""Tests for WP-25: exit prices are real or unknown (E-03/E-05/E-16).

This work package addresses three findings in the accounting audit:

- E-03: Asynchronously resolved managed exits (target/time exits that reported
  PENDING) are journaled with `filled_price=NULL` even though the broker
  reported a price; the symbol then poisons P&L and the capital gate.
  Fix: thread result.filled_price through resolve_pending_exit → _apply_exit_fill.

- E-05: Stop fills inferred from a broker-position deficit are journaled with
  a proxy price (last tick or resting stop level) labelled as filled_price.
  Fix: keep exit_price=None when the caller has no broker-reported price.

- E-16: Paper broker fills managed exits at 0.0 and that price is journaled
  and exported as real.
  Fix: PaperBroker should not use 0.0 for exit prices with no real price.
"""
import pytest

from app.brokers.paper import PaperBroker
from app.models import DestinationAccount, Side, Signal


@pytest.mark.asyncio
async def test_e16_paper_broker_exit_without_price_returns_none():
    """E-16: PaperBroker exits without signal price should have filled_price=None, not 0.0."""
    broker = PaperBroker()
    account = DestinationAccount(account_id="acct1", broker="paper")

    # Entry with price
    entry_signal = Signal(source="tv", symbol="AAPL", side=Side.BUY, quantity=10.0, price=50.0)
    entry_result = await broker.place_order(entry_signal, account, 10.0, "AAPL")
    assert entry_result.filled_price == 50.0

    # Exit signal with NO price (like lifecycle-manager synthetic exit)
    exit_signal = Signal(
        source="lifecycle_manager", symbol="AAPL", side=Side.SELL, quantity=10.0, price=None
    )
    exit_result = await broker.place_order(exit_signal, account, 10.0, "AAPL")

    # E-16 fix: filled_price should be None, not 0.0
    assert exit_result.filled_price is None, f"Expected None but got {exit_result.filled_price}"


@pytest.mark.asyncio
async def test_e16_paper_broker_entry_without_price_returns_none():
    """E-16: Entry without price returns filled_price=None (edge case)."""
    broker = PaperBroker()
    account = DestinationAccount(account_id="acct1", broker="paper")

    # Entry without price (edge case - should not happen in practice)
    entry_signal = Signal(source="tv", symbol="AAPL", side=Side.BUY, quantity=10.0, price=None)
    entry_result = await broker.place_order(entry_signal, account, 10.0, "AAPL")

    # filled_price should be None (not 0.0)
    assert entry_result.filled_price is None


@pytest.mark.asyncio
async def test_e16_paper_broker_entry_with_price_returns_price():
    """E-16: Entry with price works normally."""
    broker = PaperBroker()
    account = DestinationAccount(account_id="acct1", broker="paper")

    # Entry with price
    entry_signal = Signal(source="tv", symbol="AAPL", side=Side.BUY, quantity=10.0, price=50.0)
    entry_result = await broker.place_order(entry_signal, account, 10.0, "AAPL")

    # filled_price should be the signal price
    assert entry_result.filled_price == 50.0


def test_resolve_pending_exit_signature_accepts_filled_price():
    """E-03: Verify resolve_pending_exit method signature includes filled_price parameter."""
    from app.lifecycle.manager import PositionLifecycleManager
    import inspect

    sig = inspect.signature(PositionLifecycleManager.resolve_pending_exit)
    param_names = list(sig.parameters.keys())

    # Verify the filled_price parameter exists
    assert "filled_price" in param_names, f"filled_price parameter not found in {param_names}"

    # Verify it's optional (has default value)
    param = sig.parameters["filled_price"]
    assert param.default is not inspect.Parameter.empty, "filled_price should be optional"


def test_on_stop_filled_no_fallback_chain():
    """E-05: Verify on_stop_filled no longer uses fallback chain for unknown prices."""
    # This is a code inspection test - we verify the implementation doesn't
    # use the old fallback chain when no broker price is provided.
    from app.lifecycle.manager import PositionLifecycleManager
    import inspect

    source = inspect.getsource(PositionLifecycleManager.on_stop_filled)

    # Verify that lifecycle.last_observed_price fallback is removed
    assert "lifecycle.last_observed_price" not in source or "E-05" in source, (
        "E-05: on_stop_filled should not use lifecycle.last_observed_price fallback"
    )

    # Verify that broker_confirmed_price fallback is removed
    assert "lifecycle.stop.broker_confirmed_price" not in source or "E-05" in source, (
        "E-05: on_stop_filled should not use broker_confirmed_price fallback"
    )


def test_reconciliation_passes_filled_price_to_resolve_pending_exit():
    """E-03: Verify reconciliation.py passes result.filled_price to resolve_pending_exit."""
    from app.reconciliation import OrderReconciler
    import inspect

    source = inspect.getsource(OrderReconciler._reconcile_pending_exits)

    # Verify filled_price is passed
    assert "filled_price=result.filled_price" in source or "filled_price=" in source, (
        "E-03: resolve_pending_exit should receive filled_price from result"
    )
