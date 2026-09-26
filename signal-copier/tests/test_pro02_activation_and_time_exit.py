"""PRO-02: `TrailingPolicy.activate_at_price` and `PositionPlan.time_exit`
were both stored (and faithfully persisted/reloaded — see
app/lifecycle/manager.py's save/restore round-trip) but nothing ever
consumed either: a plan with a standalone `activate_at_price` and no
explicit ACTIVATE_TRAIL target would never actually turn its trail on, and
a plan with `time_exit` would never actually get closed once its deadline
passed."""
from datetime import datetime, timedelta, timezone

import pytest

from app.brokers.paper import PaperBroker
from app.lifecycle.manager import PositionLifecycleManager
from app.lifecycle.models import PositionPlan, TrailingPolicy
from app.models import DestinationAccount, Side


async def _enter(manager, broker, account, plan, filled_quantity):
    manager.start_plan(plan)
    from app.models import Signal

    entry_signal = Signal(source="test", symbol=plan.symbol, side=plan.side)
    await broker.place_order(entry_signal, account, filled_quantity, plan.symbol)
    return await manager.on_entry_fill(account, plan.symbol, filled_quantity)


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


@pytest.mark.asyncio
async def test_activate_at_price_alone_turns_the_trail_on(manager, account, broker):
    """No ACTIVATE_TRAIL target here -- only a bare activate_at_price. Before
    the fix this trail would never turn on and _update_trailing would never
    even be called."""
    plan = _plan(
        planned_quantity=10.0,
        initial_stop=48.50,
        trailing=TrailingPolicy(activate_at_price=55.0, trail_distance=2.0, active=False),
    )
    await _enter(manager, broker, account, plan, 10.0)
    lifecycle = manager.get_lifecycle("acct1", "AAPL")

    await manager.on_price_update(account, "AAPL", 52.0)  # below activation price
    assert lifecycle.plan.trailing.active is False
    assert lifecycle.stop.desired_price is None or lifecycle.stop.desired_price == 48.50

    await manager.on_price_update(account, "AAPL", 56.0)  # crosses activation price
    assert lifecycle.plan.trailing.active is True
    assert lifecycle.stop.desired_price == 54.0  # 56 - 2.0 trail distance


@pytest.mark.asyncio
async def test_activate_at_price_not_yet_reached_leaves_trail_off(manager, account, broker):
    plan = _plan(
        planned_quantity=10.0,
        initial_stop=48.50,
        trailing=TrailingPolicy(activate_at_price=100.0, trail_distance=2.0, active=False),
    )
    await _enter(manager, broker, account, plan, 10.0)
    lifecycle = manager.get_lifecycle("acct1", "AAPL")

    await manager.on_price_update(account, "AAPL", 99.0)
    assert lifecycle.plan.trailing.active is False


@pytest.mark.asyncio
async def test_short_position_activate_at_price_uses_downward_crossing(manager, account, broker):
    plan = _plan(
        side=Side.SELL,
        planned_quantity=10.0,
        initial_stop=105.0,
        trailing=TrailingPolicy(activate_at_price=90.0, trail_distance=2.0, active=False),
    )
    await _enter(manager, broker, account, plan, 10.0)
    lifecycle = manager.get_lifecycle("acct1", "AAPL")

    await manager.on_price_update(account, "AAPL", 95.0)  # not yet crossed downward
    assert lifecycle.plan.trailing.active is False

    await manager.on_price_update(account, "AAPL", 88.0)  # crossed
    assert lifecycle.plan.trailing.active is True


@pytest.mark.asyncio
async def test_time_exit_past_deadline_is_actually_closed(manager, account, broker):
    """Before the fix, nothing ever compared plan.time_exit to the clock --
    the position would sit open forever past its deadline."""
    past_deadline = datetime.now(timezone.utc) - timedelta(minutes=1)
    plan = _plan(planned_quantity=10.0, initial_stop=48.50, time_exit=past_deadline)
    lifecycle = await _enter(manager, broker, account, plan, 10.0)
    assert not lifecycle.closed

    triggered = await manager.check_time_exits()

    assert triggered == 1
    assert lifecycle.closed is True


@pytest.mark.asyncio
async def test_time_exit_in_the_future_is_left_alone(manager, account, broker):
    future_deadline = datetime.now(timezone.utc) + timedelta(hours=1)
    plan = _plan(planned_quantity=10.0, initial_stop=48.50, time_exit=future_deadline)
    lifecycle = await _enter(manager, broker, account, plan, 10.0)

    triggered = await manager.check_time_exits()

    assert triggered == 0
    assert lifecycle.closed is False


@pytest.mark.asyncio
async def test_naive_time_exit_datetime_is_treated_as_utc(manager, account, broker):
    """A naive datetime (no tzinfo) must not crash the comparison against
    an aware `datetime.now(timezone.utc)`."""
    past_deadline_naive = (datetime.now(timezone.utc) - timedelta(minutes=1)).replace(tzinfo=None)
    plan = _plan(planned_quantity=10.0, initial_stop=48.50, time_exit=past_deadline_naive)
    lifecycle = await _enter(manager, broker, account, plan, 10.0)

    triggered = await manager.check_time_exits()

    assert triggered == 1
    assert lifecycle.closed is True


@pytest.mark.asyncio
async def test_no_time_exit_set_is_a_no_op(manager, account, broker):
    plan = _plan(planned_quantity=10.0, initial_stop=48.50, time_exit=None)
    lifecycle = await _enter(manager, broker, account, plan, 10.0)

    triggered = await manager.check_time_exits()

    assert triggered == 0
    assert lifecycle.closed is False
