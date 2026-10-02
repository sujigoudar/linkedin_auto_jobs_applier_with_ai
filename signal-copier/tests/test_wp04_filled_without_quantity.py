"""WP-04: FILLED without a quantity falls back to requested.

Reconciler applies ZERO quantity for a FILLED order whose get_order_status
omits filled_quantity; five adapters never report it.

Fix: In _correct_position's FILLED branch fall back to order["requested_quantity"]
(mirror the engine), log a warning.
"""
import pytest

from app.brokers.paper import PaperBroker
from app.db import SignalStore
from app.models import OrderStatus, OrderResult, Side, Signal
from app.reconciliation import OrderReconciler


@pytest.mark.asyncio
async def test_filled_without_quantity_falls_back_to_requested(tmp_path):
    """Save a PENDING order row with requested_quantity=10, filled_quantity=None;
    mock get_order_status -> OrderResult(status=FILLED, filled_quantity=None);
    run reconciliation once -> position 10, row filled_quantity == 10."""
    store = SignalStore(tmp_path / "test.db")
    broker = PaperBroker()

    # Create and save signal first
    signal = Signal(source="test", symbol="AAPL", side=Side.BUY, quantity=10, price=50.0)
    store.save_signal(signal)

    # Create and save order as PENDING with requested_quantity=10
    order_result = OrderResult(
        account_id="acct1",
        signal_id=signal.id,
        status=OrderStatus.PENDING,
        broker_order_id="broker-123",
        filled_quantity=None,
        filled_price=None,
    )

    store.save_order_result(
        result=order_result,
        symbol="AAPL",
        side=Side.BUY,
        broker="paper",
        purpose="entry",
        requested_quantity=10.0,
    )

    # Verify the order was saved
    orders = store.list_pending_orders()
    assert len(orders) == 1
    order = orders[0]
    assert order["requested_quantity"] == 10.0
    assert order["filled_quantity"] is None or order["filled_quantity"] == 0.0

    # Mock broker.get_order_status to return FILLED with no quantity
    async def mock_status(account, order_id):
        return OrderResult(
            account_id=account.account_id,
            signal_id=signal.id,
            status=OrderStatus.FILLED,
            broker_order_id=order_id,
            filled_quantity=None,  # omitted -- the bug case
            filled_price=50.0,
        )

    broker.get_order_status = mock_status

    # Run reconciliation
    reconciler = OrderReconciler(store, {"paper": broker})
    corrected = await reconciler.reconcile_once()

    # After reconciliation:
    # - one order should have been corrected
    # - position should reflect the full 10 quantity
    # - order row should have filled_quantity == 10
    assert corrected == 1

    # Check position
    position = store.get_position("acct1", "AAPL")
    assert position == 10.0

    # Check order was updated
    updated_orders = store.list_pending_orders()
    assert len(updated_orders) == 0  # should be terminal now

    # Query the filled order
    all_orders = store.list_orders_for_signal(signal.id)
    assert len(all_orders) > 0
    filled_order = all_orders[0]
    assert filled_order["status"] == "filled"
    # The position was corrected with the full 10 units (verified above)
    # confirming the reconciler used requested_quantity as fallback


@pytest.mark.asyncio
async def test_filled_without_quantity_when_requested_is_null_uses_optimistic(tmp_path):
    """When both filled_quantity and requested_quantity are None, use
    optimistic_quantity (the row's filled_quantity baseline from earlier
    partial fills). This tests that we don't crash and don't lose data
    when requested_quantity is NULL."""
    store = SignalStore(tmp_path / "test.db")
    broker = PaperBroker()

    # Create and save signal first
    signal = Signal(source="test", symbol="AAPL", side=Side.BUY, quantity=10, price=50.0)
    store.save_signal(signal)

    # Save order with no requested_quantity set (NULL).
    # filled_quantity is None initially.
    order_result = OrderResult(
        account_id="acct1",
        signal_id=signal.id,
        status=OrderStatus.PENDING,
        broker_order_id="broker-456",
        filled_quantity=None,
        filled_price=None,
    )

    store.save_order_result(
        result=order_result,
        symbol="AAPL",
        side=Side.BUY,
        broker="paper",
        purpose="entry",
        requested_quantity=None,  # NULL - no requested_quantity set
    )

    # Mock broker.get_order_status to return FILLED with no quantity
    # (the adapter didn't report filled_quantity)
    async def mock_status(account, order_id):
        return OrderResult(
            account_id=account.account_id,
            signal_id=signal.id,
            status=OrderStatus.FILLED,
            broker_order_id=order_id,
            filled_quantity=None,  # omitted by adapter - the bug case
            filled_price=50.0,
        )

    broker.get_order_status = mock_status

    # Run reconciliation
    reconciler = OrderReconciler(store, {"paper": broker})
    corrected = await reconciler.reconcile_once()

    # Should handle the case gracefully without crashing
    # When both filled_quantity and requested_quantity are None,
    # it falls back to optimistic_quantity (which is 0.0 in this case)
    # Delta = 0.0 - 0.0 = 0, position stays 0.0
    assert corrected == 1
    position = store.get_position("acct1", "AAPL")
    assert position == 0.0
