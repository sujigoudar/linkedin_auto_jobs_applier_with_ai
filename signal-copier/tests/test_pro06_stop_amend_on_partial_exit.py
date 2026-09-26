"""PRO-06: a partial profit-taking exit (a target selling a fraction of the
position) used to ALWAYS cancel the entire existing stop outright before
submitting the sell, even on a broker that can amend a resting order's
quantity in place -- leaving the WHOLE position momentarily uncovered for
a partial reduce, not just the fraction being sold, with no way to
distinguish "this broker can amend" from "this broker can only
cancel/resubmit". When the broker supports `replace_stop_quantity`, the
existing stop is now shrunk in place instead: the shares NOT part of the
exit stay continuously covered by the same resting order throughout."""
import pytest

from app.brokers.base import BrokerAdapter
from app.brokers.paper import PaperBroker
from app.lifecycle.manager import PositionLifecycleManager
from app.lifecycle.models import PositionPlan, ProtectionStatus
from app.models import DestinationAccount, OrderResult, OrderStatus, Side, Signal


async def _enter(manager, broker, account, plan, filled_quantity):
    manager.start_plan(plan)
    entry_signal = Signal(source="test", symbol=plan.symbol, side=plan.side)
    await broker.place_order(entry_signal, account, filled_quantity, plan.symbol)
    return await manager.on_entry_fill(account, plan.symbol, filled_quantity)


@pytest.fixture
def account():
    return DestinationAccount(account_id="acct1", broker="paper")


def _plan(**overrides) -> PositionPlan:
    defaults = dict(account_id="acct1", symbol="AAPL", side=Side.BUY, planned_quantity=62.0, broker="paper", initial_stop=48.50)
    defaults.update(overrides)
    return PositionPlan(**defaults)


@pytest.mark.asyncio
async def test_partial_exit_amends_stop_down_instead_of_cancelling(account):
    """PaperBroker supports replace_stop_quantity -- request_exit must use it
    to shrink the stop, never call cancel_order at all for a partial
    reduce."""
    broker = PaperBroker()
    manager = PositionLifecycleManager(brokers={"paper": broker})
    await _enter(manager, broker, account, _plan(), 62.0)
    lifecycle = manager.get_lifecycle("acct1", "AAPL")
    original_stop_order_id = lifecycle.stop.broker_order_id
    assert original_stop_order_id is not None

    cancel_calls = []
    original_cancel = broker.cancel_order

    async def tracking_cancel(account, broker_order_id):
        cancel_calls.append(broker_order_id)
        return await original_cancel(account, broker_order_id)

    broker.cancel_order = tracking_cancel

    result = await manager.request_exit(account, "AAPL", 15.0, source="target", reason="target @ 51.50")

    assert result.status == OrderStatus.FILLED
    assert cancel_calls == []  # never cancelled -- amended instead
    # the same resting stop order (PaperBroker's replace keeps the id) still
    # covers the 47 shares that were not part of this exit
    assert lifecycle.stop.status == ProtectionStatus.STOP_CONFIRMED
    assert lifecycle.stop.protected_quantity == 47.0
    assert lifecycle.confirmed_owned_quantity == 47.0


@pytest.mark.asyncio
async def test_full_close_still_uses_cancel_not_amend(account):
    """A full close has nothing left to amend down to (remaining would be
    0) -- must still go through the ordinary cancel path, not try to
    'amend to zero'."""
    broker = PaperBroker()
    manager = PositionLifecycleManager(brokers={"paper": broker})
    await _enter(manager, broker, account, _plan(planned_quantity=62.0), 62.0)

    result = await manager.request_exit(account, "AAPL", 62.0, source="provider_exit")

    assert result.status == OrderStatus.FILLED
    lifecycle = manager.get_lifecycle("acct1", "AAPL")
    assert lifecycle.closed is True


class _CancelOnlyBroker(BrokerAdapter):
    """A stand-in for a broker with NO amend capability at all (e.g. one
    only overriding cancel_order, not replace_stop_quantity) -- must fall
    back to the pre-existing cancel-then-resubmit behavior exactly as
    before this fix."""

    name = "cancel_only"

    def __init__(self):
        self._next_stop_id = 0
        self.cancel_calls: list[str] = []

    async def place_order(self, signal, account, quantity, symbol):
        return OrderResult(account_id=account.account_id, status=OrderStatus.FILLED, signal_id=signal.id, filled_quantity=quantity, filled_price=100.0)

    async def place_protective_stop(self, account, symbol, quantity, stop_price, exit_side):
        self._next_stop_id += 1
        return OrderResult(
            account_id=account.account_id,
            status=OrderStatus.PENDING,
            signal_id="",
            broker_order_id=f"stop-{self._next_stop_id}",
        )

    async def cancel_order(self, account, broker_order_id):
        self.cancel_calls.append(broker_order_id)
        return True


@pytest.mark.asyncio
async def test_broker_without_amend_capability_falls_back_to_cancel(account):
    broker = _CancelOnlyBroker()
    account = DestinationAccount(account_id="acct1", broker="cancel_only")
    manager = PositionLifecycleManager(brokers={"cancel_only": broker})
    await _enter(manager, broker, account, _plan(broker="cancel_only"), 62.0)
    lifecycle = manager.get_lifecycle("acct1", "AAPL")
    original_stop_order_id = lifecycle.stop.broker_order_id

    result = await manager.request_exit(account, "AAPL", 15.0, source="target")

    assert result.status == OrderStatus.FILLED
    assert broker.cancel_calls == [original_stop_order_id]
    # a brand new stop was placed for the true remainder
    assert lifecycle.stop.protected_quantity == 47.0
    assert lifecycle.stop.broker_order_id != original_stop_order_id


@pytest.mark.asyncio
async def test_amend_correction_after_partial_fill_uses_replace_not_a_new_stop(account, monkeypatch):
    """If the actual fill differs from what was requested (PENDING outcome,
    then a partial fill resolves), the correction to the TRUE remaining
    must still go through replace_stop_quantity on the SAME resting order
    -- never place a second, brand new stop alongside the amended one."""
    broker = PaperBroker()
    manager = PositionLifecycleManager(brokers={"paper": broker})
    await _enter(manager, broker, account, _plan(), 62.0)
    lifecycle = manager.get_lifecycle("acct1", "AAPL")
    original_stop_order_id = lifecycle.stop.broker_order_id

    async def pending_place_order(signal, account, quantity, symbol):
        return OrderResult(account_id=account.account_id, status=OrderStatus.PENDING, signal_id=signal.id, broker_order_id="tp-order-1")

    monkeypatch.setattr(broker, "place_order", pending_place_order)

    place_protective_stop_calls = []
    original_place_stop = broker.place_protective_stop

    async def tracking_place_stop(*args, **kwargs):
        place_protective_stop_calls.append(args)
        return await original_place_stop(*args, **kwargs)

    broker.place_protective_stop = tracking_place_stop

    result = await manager.request_exit(account, "AAPL", 15.0, source="target")
    assert result.status == OrderStatus.PENDING
    assert lifecycle.pending_exit.stop_amended is True

    # only 8 of the 15 actually fill; the rest is confirmed cancelled
    await manager.resolve_pending_exit(account, "AAPL", confirmed_filled_quantity=8.0, remainder_cancelled=True)

    assert place_protective_stop_calls == []  # corrected via amend, never a brand-new stop
    assert lifecycle.confirmed_owned_quantity == 54.0
    assert lifecycle.stop.protected_quantity == 54.0
    assert lifecycle.stop.status == ProtectionStatus.STOP_CONFIRMED
