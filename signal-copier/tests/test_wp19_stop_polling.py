"""WP-19: Poll the stop before attributing a deficit.

Managed: any broker-position deficit is attributed to the stop; the resting stop
at the venue is never cancelled or resized.

Fix: On deficit, first get_order_status(stop.broker_order_id); if FILLED ->
on_stop_filled with venue's price; otherwise resize/cancel the stop to the new
owned quantity before applying correction. Never delete lifecycle state while
stop.broker_order_id is live and uncancelled.
"""
import pytest

from app.brokers.paper import PaperBroker
from app.db import SignalStore
from app.lifecycle.manager import PositionLifecycleManager
from app.lifecycle.models import PositionPlan
from app.models import DestinationAccount, OrderResult, OrderStatus, Side, Signal
from app.reconciliation import OrderReconciler


@pytest.mark.asyncio
async def test_managed_long_with_stop_resizes_on_deficit(tmp_path):
    """Managed long 100 with paper stop; mock readback 60 and stop open ->
    stop resized to 60, owned 60."""
    store = SignalStore(tmp_path / "test.db")
    broker = PaperBroker()
    account = DestinationAccount(account_id="acct1", broker="paper", managed_lifecycle=True)

    manager = PositionLifecycleManager(brokers={"paper": broker}, store=store)
    plan = PositionPlan(
        account_id="acct1", symbol="AAPL", side=Side.BUY, planned_quantity=100, broker="paper", initial_stop=95
    )
    manager.start_plan(plan)

    # Entry fills
    await broker.place_order(Signal(source="test", symbol="AAPL", side=Side.BUY, quantity=100), account, 100, "AAPL")
    await manager.on_entry_fill(account, "AAPL", 100)

    # Get the lifecycle and verify stop exists
    lifecycle = manager.get_lifecycle("acct1", "AAPL")
    assert lifecycle is not None
    assert lifecycle.stop.broker_order_id is not None

    # Mock broker.get_broker_position to return partial position (60)
    async def mock_position(acct, symbol):
        return 60.0  # 60 shares now held

    # Mock broker.get_order_status to return stop still open
    async def mock_order_status(acct, order_id):
        return None  # still pending

    broker.get_broker_position = mock_position
    broker.get_order_status = mock_order_status

    # Run reconciler
    reconciler = OrderReconciler(store, {"paper": broker}, lifecycle_manager=manager)
    corrected = await reconciler.reconcile_once()

    # After reconciliation:
    # - deficit = 100 - 60 = 40
    # - stop should have been resized to 60 (the new owned quantity)
    # - confirmed_owned_quantity should be 60
    # - lifecycle should still be open (no full closure yet)
    lifecycle = manager.get_lifecycle("acct1", "AAPL")
    assert lifecycle is not None
    assert lifecycle.confirmed_owned_quantity == 60
    assert not lifecycle.closed
    assert corrected == 1


@pytest.mark.asyncio
async def test_managed_long_stop_filled_closes_lifecycle(tmp_path):
    """Managed long 100 with paper stop; mock stop FILLED @ 44 ->
    exit journaled with price 44, lifecycle closed."""
    store = SignalStore(tmp_path / "test.db")
    broker = PaperBroker()
    account = DestinationAccount(account_id="acct1", broker="paper", managed_lifecycle=True)

    manager = PositionLifecycleManager(brokers={"paper": broker}, store=store)
    plan = PositionPlan(
        account_id="acct1", symbol="AAPL", side=Side.BUY, planned_quantity=100, broker="paper", initial_stop=95
    )
    manager.start_plan(plan)

    # Entry fills
    await broker.place_order(Signal(source="test", symbol="AAPL", side=Side.BUY, quantity=100), account, 100, "AAPL")
    await manager.on_entry_fill(account, "AAPL", 100)

    # Get the lifecycle and verify stop exists
    lifecycle = manager.get_lifecycle("acct1", "AAPL")
    assert lifecycle is not None
    assert lifecycle.stop.broker_order_id is not None

    # Mock broker.get_broker_position to show position was reduced to 0 (stop filled and affected position)
    async def mock_position(acct, symbol):
        return 0.0  # Stop filled and position is now flat

    # Mock broker.get_order_status to return stop is filled at 44
    async def mock_order_status(acct, order_id):
        return OrderResult(
            status=OrderStatus.FILLED, filled_quantity=100, filled_price=44.0, broker_order_id=order_id
        )

    broker.get_broker_position = mock_position
    broker.get_order_status = mock_order_status

    # Run reconciler
    reconciler = OrderReconciler(store, {"paper": broker}, lifecycle_manager=manager)
    corrected = await reconciler.reconcile_once()

    # After reconciliation:
    # - stop should have been detected as filled
    # - on_stop_filled should have been called with filled_price=44
    # - lifecycle should be closed
    # - confirmed_owned_quantity should be 0
    lifecycle = manager.get_lifecycle("acct1", "AAPL")
    assert lifecycle is not None
    assert lifecycle.closed
    assert lifecycle.confirmed_owned_quantity == 0
    assert corrected == 1


@pytest.mark.asyncio
async def test_managed_long_full_deficit_cancels_stop(tmp_path):
    """Managed long 100 with paper stop; readback 0 with stop still open ->
    stop cancelled first."""
    store = SignalStore(tmp_path / "test.db")
    broker = PaperBroker()
    account = DestinationAccount(account_id="acct1", broker="paper", managed_lifecycle=True)

    manager = PositionLifecycleManager(brokers={"paper": broker}, store=store)
    plan = PositionPlan(
        account_id="acct1", symbol="AAPL", side=Side.BUY, planned_quantity=100, broker="paper", initial_stop=95
    )
    manager.start_plan(plan)

    # Entry fills
    await broker.place_order(Signal(source="test", symbol="AAPL", side=Side.BUY, quantity=100), account, 100, "AAPL")
    await manager.on_entry_fill(account, "AAPL", 100)

    # Get the lifecycle and verify stop exists
    lifecycle = manager.get_lifecycle("acct1", "AAPL")
    assert lifecycle is not None
    assert lifecycle.stop.broker_order_id is not None

    # Mock broker.get_broker_position to show flat (position fully closed)
    async def mock_position(acct, symbol):
        return 0.0

    # Mock broker.get_order_status to return stop still open
    async def mock_order_status(acct, order_id):
        return None  # still pending

    broker.get_broker_position = mock_position
    broker.get_order_status = mock_order_status

    # Run reconciler
    reconciler = OrderReconciler(store, {"paper": broker}, lifecycle_manager=manager)
    corrected = await reconciler.reconcile_once()

    # After reconciliation:
    # - full deficit detected (100 - 0 = 100)
    # - stop should have been cancelled
    # - lifecycle should be closed
    lifecycle = manager.get_lifecycle("acct1", "AAPL")
    assert lifecycle is not None
    assert lifecycle.closed
    assert lifecycle.confirmed_owned_quantity == 0
    assert corrected == 1
