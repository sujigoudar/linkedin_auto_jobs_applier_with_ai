"""OPS-01: /health reported status="ok" unconditionally regardless of the
database_ok/price_monitor_ok/reconciler_ok flags right next to it, and
PriceMonitor updated last_success_at whenever a pass completed without
crashing, even one where every single price lookup failed.

OPS-02: OrderReconciler.start() (and PriceMonitor.start()) spawned a brand
new background task on every call, orphaning any already-running one.

OPS-03: restarting with a stale persisted lifecycle state had no way to
discover that a native stop had actually already filled at the broker
while the process was down -- only PENDING orders already known to the
store were ever re-checked, never the broker's own reported position.

Reproduces the audit's exact cases (test_http_audit.py,
test_trading_audit.py, test_state_and_input_audit.py).
"""
import asyncio

import pytest

from app.brokers.paper import PaperBroker
from app.db import SignalStore
from app.lifecycle.manager import PositionLifecycleManager
from app.models import DestinationAccount, OrderStatus, Side, Signal
from app.pricing import PriceMonitor
from app.reconciliation import OrderReconciler


@pytest.mark.asyncio
async def test_audits_exact_case_all_failed_reads_not_usable_observation_success():
    broker = PaperBroker()
    account = DestinationAccount(account_id="acct1", broker="paper", managed_lifecycle=True)
    manager = PositionLifecycleManager(brokers={"paper": broker})
    manager.start_plan.__self__  # no-op, keep import used

    from app.lifecycle.models import PositionPlan

    plan = PositionPlan(account_id="acct1", symbol="AAPL", side=Side.BUY, planned_quantity=10, broker="paper", initial_stop=90)
    manager.start_plan(plan)
    await manager.on_entry_fill(account, "AAPL", 10)

    class _AlwaysFailingBroker(PaperBroker):
        async def get_last_price(self, account, symbol):
            raise RuntimeError("all quotes fail")

    failing = _AlwaysFailingBroker()
    manager.brokers["paper"] = failing
    monitor = PriceMonitor(manager, {"paper": failing}, interval_seconds=0.001)

    await monitor.start()
    await asyncio.sleep(0.02)
    await monitor.stop()

    assert monitor.last_success_at is None


@pytest.mark.asyncio
async def test_audits_exact_case_start_idempotent_for_background_worker(tmp_path):
    store = SignalStore(tmp_path / "test.db")
    reconciler = OrderReconciler(store, {})

    await reconciler.start()
    first = reconciler._task
    await reconciler.start()
    second = reconciler._task

    try:
        assert first is second
    finally:
        first.cancel()
        await asyncio.gather(first, return_exceptions=True)


@pytest.mark.asyncio
async def test_price_monitor_start_is_also_idempotent():
    manager = PositionLifecycleManager(brokers={})
    monitor = PriceMonitor(manager, {}, interval_seconds=10)

    await monitor.start()
    first = monitor._task
    await monitor.start()
    second = monitor._task

    try:
        assert first is second
    finally:
        first.cancel()
        await asyncio.gather(first, return_exceptions=True)


@pytest.mark.asyncio
async def test_audits_exact_case_startup_must_reconcile_native_stop_execution(tmp_path):
    store = SignalStore(tmp_path / "test.db")
    broker = PaperBroker()
    account = DestinationAccount(account_id="acct1", broker="paper", managed_lifecycle=True)

    manager = PositionLifecycleManager(brokers={"paper": broker}, store=store)
    from app.lifecycle.models import PositionPlan

    manager.start_plan(
        PositionPlan(account_id="acct1", symbol="AAPL", side=Side.BUY, planned_quantity=30, broker="paper", initial_stop=90)
    )
    await broker.place_order(Signal(source="test", symbol="AAPL", side=Side.BUY), account, 30, "AAPL")
    await manager.on_entry_fill(account, "AAPL", 30)

    # The protective stop fires on the venue -- broker.positions now shows 0,
    # but nothing in this process is watching, so the persisted lifecycle
    # state still says 30.
    broker.simulate_price("AAPL", 89)
    assert broker.positions["acct1"]["AAPL"] == 0

    # Simulate a full restart: a brand new manager, seeded only from the
    # store (which still reflects the pre-stop-fill state).
    fresh_manager = PositionLifecycleManager(brokers={"paper": broker}, store=store)
    fresh_manager.restore_from_store()
    assert fresh_manager.get_lifecycle("acct1", "AAPL").confirmed_owned_quantity == 30

    reconciler = OrderReconciler(store, {"paper": broker}, lifecycle_manager=fresh_manager)
    await reconciler.reconcile_once()

    lifecycle = fresh_manager.get_lifecycle("acct1", "AAPL")
    assert lifecycle.confirmed_owned_quantity == broker.positions["acct1"]["AAPL"] == 0


@pytest.mark.asyncio
async def test_broker_position_readback_never_corrects_on_an_unknown_read(tmp_path):
    """A broker with no verified position-readback (get_broker_position
    returns None) must never be treated as confirming zero."""
    store = SignalStore(tmp_path / "test.db")
    broker = PaperBroker()
    account = DestinationAccount(account_id="acct1", broker="paper", managed_lifecycle=True)
    manager = PositionLifecycleManager(brokers={"paper": broker}, store=store)
    from app.lifecycle.models import PositionPlan

    manager.start_plan(
        PositionPlan(account_id="acct1", symbol="AAPL", side=Side.BUY, planned_quantity=10, broker="paper", initial_stop=90)
    )
    await broker.place_order(Signal(source="test", symbol="AAPL", side=Side.BUY), account, 10, "AAPL")
    await manager.on_entry_fill(account, "AAPL", 10)

    async def unknown_position(account, symbol):
        return None

    broker.get_broker_position = unknown_position

    reconciler = OrderReconciler(store, {"paper": broker}, lifecycle_manager=manager)
    corrected = await reconciler.reconcile_once()

    assert manager.get_lifecycle("acct1", "AAPL").confirmed_owned_quantity == 10
