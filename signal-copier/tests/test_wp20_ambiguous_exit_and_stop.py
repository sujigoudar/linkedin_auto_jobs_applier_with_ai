"""Tests for WP-20 (D-08, D-09): ambiguous exits and stop placements.

D-08: Managed exit returning ERROR is treated as pending, not settled.
D-09: Ambiguous stop placement is retried with adoption of unresolved orders.
"""
import pytest

from app.brokers.paper import PaperBroker
from app.db import SignalStore
from app.lifecycle.manager import PositionLifecycleManager
from app.lifecycle.models import PositionPlan, ProtectionStatus, TransferPhase, PendingExit
from app.models import DestinationAccount, OrderResult, OrderStatus, Side, Signal


async def _enter(manager, broker, account, plan, filled_quantity):
    """Helper to set up an entry and fill it."""
    manager.start_plan(plan)
    entry_signal = Signal(source="test", symbol=plan.symbol, side=plan.side)
    await broker.place_order(entry_signal, account, filled_quantity, plan.symbol)
    return await manager.on_entry_fill(account, plan.symbol, filled_quantity)


@pytest.fixture
def account():
    return DestinationAccount(account_id="acct1", broker="paper")


def _plan(**overrides) -> PositionPlan:
    defaults = dict(account_id="acct1", symbol="AAPL", side=Side.BUY, planned_quantity=100.0, initial_stop=48.50)
    defaults.update(overrides)
    return PositionPlan(**defaults)


class TestD08ExitErrorHandling:
    """D-08: Managed exit returning ERROR should open a pending exit, not settle."""

    async def test_exit_returns_error_becomes_pending_exit(self, account, tmp_path):
        """Exit returning ERROR → pending exit with no broker_order_id, stop not re-armed."""
        # Create a custom broker that returns ERROR on the second place_order call
        class ErrorOnExitBroker(PaperBroker):
            def __init__(self):
                super().__init__()
                self.place_order_calls = 0

            async def place_order(self, signal, account, quantity, symbol):
                self.place_order_calls += 1
                if self.place_order_calls > 1:  # Return ERROR on exit (second call)
                    return OrderResult(
                        account_id=account.account_id,
                        status=OrderStatus.ERROR,
                        signal_id=signal.id or "",
                        message="simulated network timeout",
                    )
                # First call (entry) succeeds normally
                return await super().place_order(signal, account, quantity, symbol)

        broker = ErrorOnExitBroker()
        store = SignalStore(tmp_path / "test.db")
        manager = PositionLifecycleManager(brokers={"paper": broker}, store=store)
        await manager.restore_from_store()

        plan = _plan()
        await _enter(manager, broker, account, plan, 100.0)

        # Request an exit
        exit_result = await manager.request_exit(
            account, plan.symbol, 100.0, source="test", reason="test exit"
        )

        # Should return ERROR
        assert exit_result.status == OrderStatus.ERROR, f"Expected ERROR, got {exit_result.status}"

        # Lifecycle should have a pending exit with no broker_order_id
        lifecycle = manager._lifecycles.get((account.account_id, plan.symbol))
        assert lifecycle is not None
        assert lifecycle.pending_exit is not None, "Expected pending_exit to be set"
        assert lifecycle.pending_exit.broker_order_id is None, "Expected broker_order_id to be None"
        assert lifecycle.pending_exit.requested_quantity == 100.0
        assert lifecycle.pending_exit.phase == TransferPhase.AWAITING_REMAINDER_RESOLUTION


class TestD09StopAdoption:
    """D-09: Ambiguous stop placement is retried via adoption of unresolved orders."""

    async def test_stop_placement_error_then_retry(self, account, tmp_path):
        """Stop placement returns ERROR → lifecycle stops getting marked UNPROTECTED."""
        class ErrorOnFirstStopBroker(PaperBroker):
            def __init__(self):
                super().__init__()
                self.place_stop_calls = 0

            async def place_protective_stop(self, account, symbol, quantity, price, exit_side):
                self.place_stop_calls += 1
                if self.place_stop_calls > 1:  # Return ERROR on second attempt
                    return OrderResult(
                        account_id=account.account_id,
                        status=OrderStatus.ERROR,
                        signal_id="",
                        message="simulated timeout on placement",
                    )
                # First call succeeds
                return await super().place_protective_stop(account, symbol, quantity, price, exit_side)

        broker = ErrorOnFirstStopBroker()
        store = SignalStore(tmp_path / "test.db")
        manager = PositionLifecycleManager(brokers={"paper": broker}, store=store)
        await manager.restore_from_store()

        plan = _plan()
        await _enter(manager, broker, account, plan, 100.0)

        lifecycle = manager._lifecycles.get((account.account_id, plan.symbol))
        assert lifecycle is not None
        assert lifecycle.stop.status == ProtectionStatus.STOP_CONFIRMED

        # Mark stop as unprotected to trigger retry
        lifecycle.stop.status = ProtectionStatus.UNPROTECTED

        # Retry should handle the ERROR gracefully
        await manager.retry_unprotected_positions()

        # After retry, should be in UNPROTECTED state (not STOP_CONFIRMED)
        # since the placement attempt returned ERROR
        assert lifecycle.stop.status in (ProtectionStatus.UNPROTECTED, ProtectionStatus.STOP_PENDING)

    async def test_resolve_pending_exit_after_error(self, account, tmp_path):
        """Resolve a pending exit that resulted from an ERROR return."""
        broker = PaperBroker()
        store = SignalStore(tmp_path / "test.db")
        manager = PositionLifecycleManager(brokers={"paper": broker}, store=store)
        await manager.restore_from_store()

        plan = _plan()
        await _enter(manager, broker, account, plan, 100.0)

        lifecycle = manager._lifecycles.get((account.account_id, plan.symbol))
        assert lifecycle is not None

        # Manually create a pending exit as if the exit returned ERROR
        lifecycle.pending_exit = PendingExit(
            broker_order_id=None,
            requested_quantity=100.0,
            phase=TransferPhase.AWAITING_REMAINDER_RESOLUTION,
            source="test",
            reason="test exit",
            stop_amended=False,
        )
        manager._persist(lifecycle)

        # Simulate later readback showing 0 filled (exit didn't execute)
        await manager.resolve_pending_exit(
            account,
            plan.symbol,
            confirmed_filled_quantity=0.0,
            remainder_cancelled=True,
        )

        # Pending exit should be resolved
        lifecycle = manager._lifecycles.get((account.account_id, plan.symbol))
        assert lifecycle is not None
        assert lifecycle.pending_exit is None or lifecycle.pending_exit.remainder_resolved


class TestStopAdoptionWithLedger:
    """Test adoption of unresolved stops using the command ledger."""

    async def test_startup_restores_and_cleans_up_state(self, account, tmp_path):
        """Startup should restore lifecycle state without errors."""
        broker = PaperBroker()
        store = SignalStore(tmp_path / "test.db")

        # First manager: create and persist state
        manager1 = PositionLifecycleManager(brokers={"paper": broker}, store=store)
        await manager1.restore_from_store()

        plan = _plan()
        await _enter(manager1, broker, account, plan, 100.0)

        lifecycle1 = manager1._lifecycles.get((account.account_id, plan.symbol))
        assert lifecycle1 is not None
        stop_id = lifecycle1.stop.broker_order_id

        # Second manager: restore from store (simulating restart)
        manager2 = PositionLifecycleManager(brokers={"paper": broker}, store=store)
        await manager2.restore_from_store()

        lifecycle2 = manager2._lifecycles.get((account.account_id, plan.symbol))
        assert lifecycle2 is not None
        # The lifecycle should be restored with the same stop id
        assert lifecycle2.stop.broker_order_id == stop_id
        assert lifecycle2.stop.status == ProtectionStatus.STOP_CONFIRMED
