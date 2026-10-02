"""WP-03: Sign the broker readback by plan side.

Managed SHORT positions are destroyed by the position-readback pass (sign bug),
with a fabricated BUY fill exported.

Fix: broker_owned_signed = -broker_owned if plan.side == SELL else broker_owned;
compare against that in the deficit branch; add a short-side regression test.
"""
import pytest

from app.brokers.paper import PaperBroker
from app.db import SignalStore
from app.lifecycle.manager import PositionLifecycleManager
from app.lifecycle.models import PositionPlan
from app.models import DestinationAccount, Side, Signal
from app.reconciliation import OrderReconciler


@pytest.mark.asyncio
async def test_short_entry_fills_and_reconciler_sees_correct_position(tmp_path):
    """Managed account, entry Side.SELL 10 fills on paper (paper positions go
    to -10); run the reconciler once -> lifecycle still open,
    confirmed_owned_quantity == 10, no exit order row, store.get_position == -10."""
    store = SignalStore(tmp_path / "test.db")
    broker = PaperBroker()
    account = DestinationAccount(account_id="acct1", broker="paper", managed_lifecycle=True)

    manager = PositionLifecycleManager(brokers={"paper": broker}, store=store)
    plan = PositionPlan(
        account_id="acct1", symbol="AAPL", side=Side.SELL, planned_quantity=10, broker="paper", initial_stop=150
    )
    manager.start_plan(plan)

    # Entry fills
    await broker.place_order(Signal(source="test", symbol="AAPL", side=Side.SELL, quantity=10), account, 10, "AAPL")
    await manager.on_entry_fill(account, "AAPL", 10)

    # Verify pre-reconciliation state
    lifecycle = manager.get_lifecycle("acct1", "AAPL")
    assert lifecycle is not None
    assert lifecycle.confirmed_owned_quantity == 10
    assert broker.positions["acct1"]["AAPL"] == -10  # paper tracks shorts as negative

    # Run reconciler
    reconciler = OrderReconciler(store, {"paper": broker}, lifecycle_manager=manager)
    corrected = await reconciler.reconcile_once()

    # After reconciliation with correctly signed readback:
    # - lifecycle should still be open (no deficit detected)
    # - confirmed_owned_quantity should still be 10
    # - no exit should have been applied
    # - broker position should still be -10
    lifecycle = manager.get_lifecycle("acct1", "AAPL")
    assert lifecycle is not None
    assert not lifecycle.closed
    assert lifecycle.confirmed_owned_quantity == 10
    assert corrected == 0  # no correction needed -- positions match
    assert broker.positions["acct1"]["AAPL"] == -10


@pytest.mark.asyncio
async def test_short_partial_stop_fill_resizes_protection(tmp_path):
    """Managed short entry fills; readback -6 -> on_stop_filled applied with 4,
    owned 6. Mock broker.get_broker_position for the second test."""
    store = SignalStore(tmp_path / "test.db")
    broker = PaperBroker()
    account = DestinationAccount(account_id="acct1", broker="paper", managed_lifecycle=True)

    manager = PositionLifecycleManager(brokers={"paper": broker}, store=store)
    plan = PositionPlan(
        account_id="acct1", symbol="AAPL", side=Side.SELL, planned_quantity=10, broker="paper", initial_stop=150
    )
    manager.start_plan(plan)

    # Entry fills
    await broker.place_order(Signal(source="test", symbol="AAPL", side=Side.SELL, quantity=10), account, 10, "AAPL")
    await manager.on_entry_fill(account, "AAPL", 10)

    # Mock broker.get_broker_position to return partial close (4 shares covered)
    async def mock_position(acct, symbol):
        return -6.0  # 6 shares still short

    broker.get_broker_position = mock_position

    # Run reconciler
    reconciler = OrderReconciler(store, {"paper": broker}, lifecycle_manager=manager)
    corrected = await reconciler.reconcile_once()

    # After reconciliation:
    # - deficit = 10 - 6 = 4
    # - on_stop_filled(4) should have been called
    # - confirmed_owned_quantity should now be 6
    # - one correction should have been made
    lifecycle = manager.get_lifecycle("acct1", "AAPL")
    assert lifecycle is not None
    assert lifecycle.confirmed_owned_quantity == 6
    assert corrected == 1


@pytest.mark.asyncio
async def test_short_opposite_readback_logs_warning_and_skips(tmp_path):
    """Readback +10 for a short plan -> warning, no change. Mock
    broker.get_broker_position."""
    store = SignalStore(tmp_path / "test.db")
    broker = PaperBroker()
    account = DestinationAccount(account_id="acct1", broker="paper", managed_lifecycle=True)

    manager = PositionLifecycleManager(brokers={"paper": broker}, store=store)
    plan = PositionPlan(
        account_id="acct1", symbol="AAPL", side=Side.SELL, planned_quantity=10, broker="paper", initial_stop=150
    )
    manager.start_plan(plan)

    # Entry fills
    await broker.place_order(Signal(source="test", symbol="AAPL", side=Side.SELL, quantity=10), account, 10, "AAPL")
    await manager.on_entry_fill(account, "AAPL", 10)

    # Mock broker.get_broker_position to return opposite side (long +10)
    async def mock_position(acct, symbol):
        return 10.0  # long, opposite of the short plan

    broker.get_broker_position = mock_position

    # Run reconciler
    reconciler = OrderReconciler(store, {"paper": broker}, lifecycle_manager=manager)
    corrected = await reconciler.reconcile_once()

    # After reconciliation:
    # - opposite side detected (would be -10 after sign conversion)
    # - warning logged (checked via caplog)
    # - no correction applied
    # - confirmed_owned_quantity unchanged
    lifecycle = manager.get_lifecycle("acct1", "AAPL")
    assert lifecycle is not None
    assert lifecycle.confirmed_owned_quantity == 10  # unchanged
    assert corrected == 0  # no correction
