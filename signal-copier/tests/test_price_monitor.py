"""Answers "does the system constantly monitor an open position and move
its stop, including cancel/replace when trailing isn't atomically
possible" -- for real, end to end: a live price update reaching
PositionLifecycleManager.on_price_update() through PriceMonitor.poll_once(),
exactly as production's background loop would deliver it."""
import pytest

from app.brokers.paper import PaperBroker
from app.lifecycle.manager import PositionLifecycleManager
from app.lifecycle.models import PositionPlan, TrailingPolicy
from app.models import DestinationAccount, Side, Signal
from app.pricing import PriceMonitor


@pytest.fixture
def account():
    return DestinationAccount(account_id="acct1", broker="paper")


@pytest.fixture
def broker():
    return PaperBroker()


async def _enter(manager, broker, account, plan, filled_quantity):
    manager.start_plan(plan)
    entry_signal = Signal(source="test", symbol=plan.symbol, side=plan.side)
    await broker.place_order(entry_signal, account, filled_quantity, plan.symbol)
    return await manager.on_entry_fill(account, plan.symbol, filled_quantity)


@pytest.mark.asyncio
async def test_broker_without_last_price_capability_is_skipped_not_errored(account, broker):
    """PaperBroker doesn't implement get_last_price (it's a mock with no
    real market) -- the monitor must skip it cleanly, not raise or crash
    the whole poll."""
    manager = PositionLifecycleManager(brokers={"paper": broker})
    plan = PositionPlan(account_id="acct1", symbol="AAPL", side=Side.BUY, planned_quantity=10.0, broker="paper", initial_stop=48.50)
    await _enter(manager, broker, account, plan, 10.0)
    monitor = PriceMonitor(lifecycle_manager=manager, brokers={"paper": broker})

    updated = await monitor.poll_once()

    assert updated == 0
    assert broker.has_last_price_capability is False


@pytest.mark.asyncio
async def test_poll_once_drives_trailing_stop_replacement_through_a_real_feed(account, broker, monkeypatch):
    """The exact scenario the user described: after entry, price moves,
    and the system cancels/replaces the stop to trail it -- driven purely
    by PriceMonitor.poll_once() the way a production background loop
    would, not called directly by a test."""

    class _FakeFeedBroker(PaperBroker):
        def __init__(self):
            super().__init__()
            self.name = "paper"
            self._price = 48.50

        async def get_last_price(self, account, symbol):
            return self._price

    fed_broker = _FakeFeedBroker()
    manager = PositionLifecycleManager(brokers={"paper": fed_broker})
    plan = PositionPlan(
        account_id="acct1",
        symbol="AAPL",
        side=Side.BUY,
        planned_quantity=62.0,
        broker="paper",
        initial_stop=48.50,
        trailing=TrailingPolicy(trail_distance=1.50, active=True),
    )
    await _enter(manager, fed_broker, account, plan, 62.0)
    monitor = PriceMonitor(lifecycle_manager=manager, brokers={"paper": fed_broker})

    assert fed_broker.has_last_price_capability is True
    lifecycle = manager.get_lifecycle("acct1", "AAPL")
    assert lifecycle.stop.desired_price == 48.50

    fed_broker._price = 52.00  # price rallies -- floor should trail to 52.00 - 1.50 = 50.50
    updated = await monitor.poll_once()

    assert updated == 1
    assert lifecycle.stop.desired_price == 50.50
    # the stop is genuinely resting at the new level, not just recorded as "desired"
    fills = fed_broker.simulate_price("AAPL", 50.40)
    assert len(fills) == 1


@pytest.mark.asyncio
async def test_poll_once_skips_price_none_without_dropping_the_position(account, broker, monkeypatch):
    class _FlakyFeedBroker(PaperBroker):
        async def get_last_price(self, account, symbol):
            return None  # feed temporarily unavailable

    flaky_broker = _FlakyFeedBroker()
    manager = PositionLifecycleManager(brokers={"paper": flaky_broker})
    plan = PositionPlan(account_id="acct1", symbol="AAPL", side=Side.BUY, planned_quantity=10.0, broker="paper", initial_stop=48.50)
    await _enter(manager, flaky_broker, account, plan, 10.0)
    monitor = PriceMonitor(lifecycle_manager=manager, brokers={"paper": flaky_broker})

    updated = await monitor.poll_once()

    assert updated == 0
    # the position is still tracked and still protected -- a missing price
    # this poll didn't cause it to be dropped or unprotected
    lifecycle = manager.get_lifecycle("acct1", "AAPL")
    assert lifecycle is not None
    assert lifecycle.stop.protected_quantity == 10.0


@pytest.mark.asyncio
async def test_closed_lifecycles_are_never_polled(account, broker):
    manager = PositionLifecycleManager(brokers={"paper": broker})
    plan = PositionPlan(account_id="acct1", symbol="AAPL", side=Side.BUY, planned_quantity=10.0, broker="paper", initial_stop=48.50)
    await _enter(manager, broker, account, plan, 10.0)
    await manager.request_exit(account, "AAPL", 10.0, source="test")
    assert manager.get_lifecycle("acct1", "AAPL").closed is True

    monitor = PriceMonitor(lifecycle_manager=manager, brokers={"paper": broker})
    updated = await monitor.poll_once()

    assert updated == 0
