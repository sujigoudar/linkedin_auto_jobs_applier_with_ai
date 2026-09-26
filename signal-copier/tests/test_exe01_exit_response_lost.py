"""EXE-01's exit-side counterpart: `request_exit`'s `_submit_exit_order`
raising after the venue may have already accepted the exit (a network
timeout/reset reading the response) used to propagate the exception with
NOTHING durable retained -- the reservation this transition already made
(excluding the requested quantity from what's available to sell again)
lived only in the in-memory arbiter, never persisted, so a restart forgot
it ever happened.

Reproduces the audit's exact case (test_state_and_input_audit.py::
test_exit_submit_exception_keeps_durable_commitment).
"""
import pytest

from app.brokers.paper import PaperBroker
from app.db import SignalStore
from app.lifecycle.manager import PositionLifecycleManager
from app.lifecycle.models import PositionPlan
from app.models import DestinationAccount, Side


@pytest.fixture
def store(tmp_path):
    return SignalStore(tmp_path / "test.db")


@pytest.mark.asyncio
async def test_audits_exact_case_exit_response_lost_keeps_durable_commitment(store):
    broker = PaperBroker()
    account = DestinationAccount(account_id="acct1", broker="paper", managed_lifecycle=True)
    manager = PositionLifecycleManager(brokers={"paper": broker}, store=store)

    plan = PositionPlan(
        account_id="acct1", symbol="AAPL", side=Side.BUY, planned_quantity=100, broker="paper", initial_stop=90
    )
    manager.start_plan(plan)
    from app.models import Signal

    await broker.place_order(Signal(source="test", symbol="AAPL", side=Side.BUY), account, 100, "AAPL")
    await manager.on_entry_fill(account, "AAPL", 100)

    original_place_order = broker.place_order

    async def lose(*args, **kwargs):
        await original_place_order(*args, **kwargs)
        raise TimeoutError("accepted exit response lost")

    broker.place_order = lose

    with pytest.raises(TimeoutError):
        await manager.request_exit(account, "AAPL", 10, "target")

    fresh_manager = PositionLifecycleManager(brokers={"paper": broker}, store=store)
    fresh_manager.restore_from_store()

    snapshot = fresh_manager.arbiter.snapshot("acct1", "AAPL")
    lifecycle = fresh_manager.get_lifecycle("acct1", "AAPL")

    assert snapshot["reserved"] == 10
    assert lifecycle.pending_exit is not None


@pytest.mark.asyncio
async def test_pending_exit_with_no_order_id_is_resolved_via_broker_position_readback(store):
    from app.reconciliation import OrderReconciler
    from app.models import Signal

    broker = PaperBroker()
    account = DestinationAccount(account_id="acct1", broker="paper", managed_lifecycle=True)
    manager = PositionLifecycleManager(brokers={"paper": broker}, store=store)

    plan = PositionPlan(
        account_id="acct1", symbol="AAPL", side=Side.BUY, planned_quantity=100, broker="paper", initial_stop=90
    )
    manager.start_plan(plan)
    await broker.place_order(Signal(source="test", symbol="AAPL", side=Side.BUY), account, 100, "AAPL")
    await manager.on_entry_fill(account, "AAPL", 100)

    original_place_order = broker.place_order

    async def lose(*args, **kwargs):
        await original_place_order(*args, **kwargs)
        raise TimeoutError("accepted exit response lost")

    broker.place_order = lose

    with pytest.raises(TimeoutError):
        await manager.request_exit(account, "AAPL", 10, "target")

    lifecycle = manager.get_lifecycle("acct1", "AAPL")
    assert lifecycle.pending_exit is not None
    assert lifecycle.pending_exit.broker_order_id is None
    # The exit really did reach the venue -- the broker's own book already
    # reflects it, even though this process never got the confirmation.
    assert broker.positions["acct1"]["AAPL"] == 90.0

    reconciler = OrderReconciler(store, {"paper": broker}, lifecycle_manager=manager)
    corrected = await reconciler.reconcile_once()

    assert corrected >= 1
    lifecycle = manager.get_lifecycle("acct1", "AAPL")
    assert lifecycle.pending_exit is None
    assert lifecycle.confirmed_owned_quantity == 90.0
    assert manager.arbiter.snapshot("acct1", "AAPL")["reserved"] == 0.0
