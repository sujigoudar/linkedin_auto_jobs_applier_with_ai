"""EXE-08: some brokers report PENDING for a still-open order but already
carry a confirmed partial fill in that SAME synchronous response (e.g.
filled_quantity=30 on an order for 100). Discarding that and waiting for
the next reconciliation poll to "discover" it leaves a real, already-known
fill unprotected for however long that poll interval is."""
import pytest

from app.brokers.base import BrokerAdapter
from app.db import SignalStore
from app.engine import SignalCopierEngine
from app.lifecycle.manager import PositionLifecycleManager
from app.models import DestinationAccount, OrderResult, OrderStatus, Signal, Side
from app.routing import RoutingConfig, RoutingRule


class _ImmediatePartialFillBroker(BrokerAdapter):
    name = "partial-broker"

    def __init__(self):
        self.stop_calls = []

    async def place_order(self, signal, account, quantity, symbol):
        return OrderResult(
            account_id=account.account_id,
            status=OrderStatus.PENDING,
            signal_id=signal.id,
            broker_order_id="broker-order-1",
            filled_quantity=30.0,  # confirmed partial, in the SAME response
            message="accepted, 30 of 100 filled so far",
        )

    async def place_protective_stop(self, account, symbol, quantity, stop_price, exit_side):
        self.stop_calls.append(quantity)
        return OrderResult(
            account_id=account.account_id, status=OrderStatus.PENDING, signal_id="", broker_order_id="stop-1"
        )

    def can_protect_a_managed_position(self) -> bool:
        return True


@pytest.mark.asyncio
async def test_a_confirmed_partial_fill_in_the_initial_pending_response_is_protected_immediately(tmp_path):
    store = SignalStore(tmp_path / "test.db")
    broker = _ImmediatePartialFillBroker()
    account = DestinationAccount(account_id="acct1", broker="partial-broker", managed_lifecycle=True)
    routing = RoutingConfig(rules=[RoutingRule(source="test", destinations=["acct1"])], accounts={"acct1": account})
    manager = PositionLifecycleManager(brokers={"partial-broker": broker}, store=store)
    engine = SignalCopierEngine(routing=routing, brokers={"partial-broker": broker}, store=store, lifecycle_manager=manager)

    results = await engine.handle_signal(
        Signal(source="test", symbol="AAPL", side=Side.BUY, quantity=100.0, stop_loss=90.0)
    )

    assert results[0].status == OrderStatus.PENDING
    # Protected immediately -- not left waiting for the next reconciliation poll.
    assert broker.stop_calls == [30.0]
    assert store.get_position("acct1", "AAPL") == 30.0
    lifecycle = manager.get_lifecycle("acct1", "AAPL")
    assert lifecycle.confirmed_owned_quantity == 30.0
    assert lifecycle.stop.protected_quantity == 30.0
