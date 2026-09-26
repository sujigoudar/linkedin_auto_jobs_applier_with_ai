"""C23/E10: private aggregate Prometheus metrics."""
import pytest

from app.brokers.paper import PaperBroker
from app.db import SignalStore
from app.lifecycle.manager import PositionLifecycleManager
from app.lifecycle.models import PositionPlan
from app.metrics import render_metrics
from app.models import DestinationAccount, Side
from app.pricing import PriceMonitor
from app.reconciliation import OrderReconciler


@pytest.fixture
def store(tmp_path):
    return SignalStore(tmp_path / "test.db")


def test_metrics_render_without_any_activity(store):
    broker = PaperBroker()
    monitor = PriceMonitor(PositionLifecycleManager(brokers={"paper": broker}), {"paper": broker})
    reconciler = OrderReconciler(store, {"paper": broker})
    manager = PositionLifecycleManager(brokers={"paper": broker}, store=store)

    body = render_metrics(store=store, price_monitor=monitor, reconciler=reconciler, lifecycle_manager=manager)

    text = body.decode()
    assert "signal_copier_open_positions 0.0" in text
    assert "signal_copier_pending_entries 0.0" in text
    assert "signal_copier_pending_exits 0.0" in text
    assert "signal_copier_protection_deficit_positions 0.0" in text
    # No successful pass yet -- these gauges must have no sample at all,
    # never a fabricated age of zero.
    assert "signal_copier_price_observation_age_seconds" not in text
    assert "signal_copier_reconciler_cycle_age_seconds" not in text


@pytest.mark.asyncio
async def test_metrics_report_unprotected_owned_position_as_a_deficit(store):
    broker = PaperBroker()
    account = DestinationAccount(account_id="acct1", broker="paper", managed_lifecycle=True)
    manager = PositionLifecycleManager(brokers={"paper": broker}, store=store)

    plan = PositionPlan(account_id="acct1", symbol="AAPL", side=Side.BUY, planned_quantity=10, broker="paper", initial_stop=90)
    manager.start_plan(plan)

    class NoOpStop(PaperBroker):
        async def place_protective_stop(self, *a, **k):
            return None

    manager.brokers["paper"] = NoOpStop()
    await broker.place_order(__import__("app.models", fromlist=["Signal"]).Signal(source="t", symbol="AAPL", side=Side.BUY), account, 10, "AAPL")
    await manager.on_entry_fill(account, "AAPL", 10)

    monitor = PriceMonitor(manager, {"paper": broker})
    reconciler = OrderReconciler(store, {"paper": broker})

    body = render_metrics(store=store, price_monitor=monitor, reconciler=reconciler, lifecycle_manager=manager)

    assert "signal_copier_protection_deficit_positions 1.0" in body.decode()


@pytest.mark.asyncio
async def test_metrics_report_age_after_a_successful_pass(store):
    broker = PaperBroker()
    manager = PositionLifecycleManager(brokers={"paper": broker})
    monitor = PriceMonitor(manager, {"paper": broker})
    reconciler = OrderReconciler(store, {"paper": broker})

    await monitor.poll_once()  # no open positions -- 0 usable reads, still shouldn't crash
    from datetime import datetime, timezone

    monitor.last_success_at = datetime.now(timezone.utc)
    reconciler.last_success_at = datetime.now(timezone.utc)

    body = render_metrics(store=store, price_monitor=monitor, reconciler=reconciler, lifecycle_manager=manager).decode()

    assert "signal_copier_price_observation_age_seconds" in body
    assert "signal_copier_reconciler_cycle_age_seconds" in body
