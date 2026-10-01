"""B2: POST /reconciliation/run-now (a real, owner-gated on-demand
reconciliation trigger backed by app/reconciliation.py's OrderReconciler).

B6: read-only preview computations for a partial reduction / a trailing
stop-price change, backed by the exact real pure functions
app/lifecycle/manager.py's `request_exit`/`_update_trailing` themselves use
(`_compute_reduction_plan`/`_compute_trailing_candidate`) -- never a second,
separately maintained implementation.

The trailing-stop-preview test at the bottom of this file is the STANDING
RULES' load-bearing verification: it is written to fail if
`preview_stop_change` ever stops calling the real `_compute_trailing_candidate`
(e.g. a future edit reimplements the math separately/divergently) by
comparing the preview's own output against a real, independent invocation of
`_update_trailing`'s live trailing-stop-adjustment path.
"""
import asyncio

import pytest

from app.brokers.paper import PaperBroker
from app.lifecycle.manager import (
    PositionLifecycleManager,
    _compute_reduction_plan,
    _compute_trailing_candidate,
)
from app.lifecycle.models import PositionPlan, TrailingPolicy
from app.models import DestinationAccount, Side, Signal
from app.reconciliation import OrderReconciler


@pytest.fixture
def account():
    return DestinationAccount(account_id="acct1", broker="paper")


@pytest.fixture
def broker():
    return PaperBroker()


@pytest.fixture
def manager(broker):
    return PositionLifecycleManager(brokers={"paper": broker})


def _plan(**overrides) -> PositionPlan:
    defaults = dict(
        account_id="acct1",
        symbol="AAPL",
        side=Side.BUY,
        planned_quantity=100.0,
        broker="paper",
        initial_stop=48.50,
    )
    defaults.update(overrides)
    return PositionPlan(**defaults)


async def _enter(manager, broker, account, plan, filled_quantity):
    manager.start_plan(plan)
    entry_signal = Signal(source="test", symbol=plan.symbol, side=plan.side)
    await broker.place_order(entry_signal, account, filled_quantity, plan.symbol)
    return await manager.on_entry_fill(account, plan.symbol, filled_quantity)


# --------------------------------------------------------------------------
# B6: preview_reduction
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_preview_reduction_no_lifecycle_is_honestly_unsupported(manager, account):
    result = await manager.preview_reduction(account, "AAPL", 10.0)
    assert result["supported"] is False
    assert "no active managed lifecycle" in result["reason"]


@pytest.mark.asyncio
async def test_preview_reduction_matches_real_plan_and_never_mutates(manager, broker, account):
    plan = _plan(planned_quantity=100.0)
    await _enter(manager, broker, account, plan, 100.0)

    result = await manager.preview_reduction(account, "AAPL", 30.0)
    assert result["supported"] is True
    assert result["requested_quantity"] == 30.0
    assert result["owned_before"] == 100.0
    assert result["remaining_after_request"] == 70.0
    assert result["had_stop"] is True

    # Never mutates real state: available-to-sell and the lifecycle's owned
    # quantity are unchanged after a preview call.
    assert manager.arbiter.available_to_sell(account.account_id, "AAPL") == 100.0
    lifecycle = manager.get_lifecycle(account.account_id, "AAPL")
    assert lifecycle.confirmed_owned_quantity == 100.0
    assert lifecycle.pending_exit is None


@pytest.mark.asyncio
async def test_preview_reduction_reuses_same_pure_function_request_exit_uses(manager, broker, account):
    """Verifies `preview_reduction` doesn't drift from `request_exit`'s own
    planning: both must derive identical fields from the same ledger/
    lifecycle/broker state via `_compute_reduction_plan`."""
    plan = _plan(planned_quantity=62.0)
    await _enter(manager, broker, account, plan, 62.0)

    preview = await manager.preview_reduction(account, "AAPL", 15.0)

    async with manager.arbiter.transition(account.account_id, "AAPL") as tx:
        lifecycle = manager.get_lifecycle(account.account_id, "AAPL")
        direct_plan = _compute_reduction_plan(tx, lifecycle, broker, 15.0)

    assert preview["requested_quantity"] == direct_plan.requested_quantity
    assert preview["remaining_after_request"] == direct_plan.remaining_after_request
    assert preview["can_amend_stop_in_place"] == direct_plan.can_amend_stop_in_place


@pytest.mark.asyncio
async def test_preview_reduction_honors_unresolved_pending_exit_refusal(manager, broker, account):
    from app.lifecycle.models import PendingExit, TransferPhase

    plan = _plan(planned_quantity=50.0)
    await _enter(manager, broker, account, plan, 50.0)
    lifecycle = manager.get_lifecycle(account.account_id, "AAPL")
    lifecycle.pending_exit = PendingExit(
        broker_order_id="order-123",
        requested_quantity=10.0,
        phase=TransferPhase.AWAITING_REMAINDER_RESOLUTION,
        source="target",
        reason="target @ 55.0",
    )

    result = await manager.preview_reduction(account, "AAPL", 5.0)
    assert result["supported"] is False
    assert "hasn't resolved" in result["reason"]


# --------------------------------------------------------------------------
# B6: preview_stop_change
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_preview_stop_change_no_trailing_policy_is_honestly_unsupported(manager, broker, account):
    plan = _plan(planned_quantity=10.0)
    await _enter(manager, broker, account, plan, 10.0)
    result = manager.preview_stop_change(account.account_id, "AAPL", 55.0)
    assert result["supported"] is False
    assert "no ACTIVE trailing-stop policy" in result["reason"]


@pytest.mark.asyncio
async def test_preview_stop_change_matches_real_trailing_update_and_never_mutates(manager, broker, account):
    trailing = TrailingPolicy(trail_distance=2.0, active=True)
    plan = _plan(planned_quantity=10.0, trailing=trailing)
    await _enter(manager, broker, account, plan, 10.0)
    lifecycle = manager.get_lifecycle(account.account_id, "AAPL")
    desired_before = lifecycle.stop.desired_price

    preview = manager.preview_stop_change(account.account_id, "AAPL", 60.0)
    assert preview["supported"] is True
    assert preview["candidate_stop_price"] == pytest.approx(58.0)
    assert preview["would_change"] is True

    # Never mutates: calling the preview doesn't move the real stop.
    assert lifecycle.stop.desired_price == desired_before
    assert trailing.floor_price is None

    # Now drive the REAL trailing update to the same price and confirm the
    # real outcome matches exactly what was previewed -- this is the
    # load-bearing check: it fails if the preview ever diverges from the
    # real `_update_trailing` computation.
    await manager.on_price_update(account, "AAPL", 60.0)
    lifecycle = manager.get_lifecycle(account.account_id, "AAPL")
    assert lifecycle.stop.desired_price == pytest.approx(preview["candidate_stop_price"])


def test_compute_trailing_candidate_matches_load_bearing_expectation():
    """A narrow, deterministic unit check on the extracted pure function
    itself (BUY side, 2.0 trail distance, price 100 -> floor 98)."""
    trailing = TrailingPolicy(trail_distance=2.0, active=True)
    candidate, improved = _compute_trailing_candidate(trailing, None, Side.BUY, 100.0)
    assert candidate == pytest.approx(98.0)
    assert improved is True


# --------------------------------------------------------------------------
# B2: OrderReconciler.run_now
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_now_reports_real_examined_and_corrected_counts(tmp_path):
    from app.db import SignalStore

    store = SignalStore(str(tmp_path / "test.db"))
    reconciler = OrderReconciler(store=store, brokers={})
    result = await reconciler.run_now()
    assert result["already_running"] is False
    assert result["corrected"] == 0
    assert result["orders_examined"] == 0
    assert reconciler.last_success_at is not None


@pytest.mark.asyncio
async def test_run_now_reports_real_pending_exit_and_entry_breakdown_counts(tmp_path):
    """Track 45: no existing test ever gave `run_now` a real
    `lifecycle_manager` with actual pending exits/entries queued -- the
    `pending_exits_examined`/`pending_entries_examined` breakdown fields
    (and the `orders_examined` sum they feed) were only ever exercised
    against an empty store, so a mutation that hard-codes the
    lifecycle_manager's contribution to `[]` regardless of its real state
    (or flips `+`/`-` in the sum) survived undetected."""
    from app.db import SignalStore
    from app.lifecycle.manager import PositionLifecycleManager
    from app.lifecycle.models import PositionPlan

    store = SignalStore(str(tmp_path / "test3.db"))
    broker = PaperBroker()
    account = DestinationAccount(account_id="acct1", broker="paper", managed_lifecycle=True)
    manager = PositionLifecycleManager(brokers={"paper": broker}, store=store)
    plan = PositionPlan(
        account_id="acct1", symbol="AAPL", side=Side.BUY, planned_quantity=100, broker="paper", initial_stop=90
    )
    manager.start_plan(plan)
    # A pending entry with no resolved fill yet -- list_pending_entries()
    # must report it.
    manager.register_pending_entry(account, "AAPL", broker_order_id="order-1", requested_quantity=100.0)

    reconciler = OrderReconciler(store=store, brokers={"paper": broker}, lifecycle_manager=manager)
    result = await reconciler.run_now()

    assert result["already_running"] is False
    assert result["pending_entries_examined"] == 1
    assert result["pending_exits_examined"] == 0
    # The sum must be the real total, not silently forced to 0 or
    # miscounted by a +/- sign flip.
    assert result["orders_examined"] == 1


@pytest.mark.asyncio
async def test_run_now_guards_against_concurrent_manual_runs(tmp_path):
    from app.db import SignalStore

    store = SignalStore(str(tmp_path / "test2.db"))
    reconciler = OrderReconciler(store=store, brokers={})

    started = asyncio.Event()
    release = asyncio.Event()

    async def _slow_reconcile_once():
        started.set()
        await release.wait()
        return 0

    reconciler.reconcile_once = _slow_reconcile_once  # type: ignore[method-assign]

    first = asyncio.ensure_future(reconciler.run_now())
    await started.wait()
    second = await reconciler.run_now()
    assert second["already_running"] is True

    release.set()
    first_result = await first
    assert first_result["already_running"] is False
