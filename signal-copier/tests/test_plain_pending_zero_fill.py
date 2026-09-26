"""EXE-04: `result.filled_quantity or quantity` treats an explicit, reported
zero fill (0.0, falsy in Python) the same as "nothing reported yet" (None),
silently applying the full requested quantity to a position that's actually
still flat. A genuinely unknown fill (None) is the only case that should
fall back to the optimistic full-quantity guess."""
import pytest

from app.brokers.base import BrokerAdapter
from app.db import SignalStore
from app.engine import SignalCopierEngine
from app.models import DestinationAccount, OrderResult, OrderStatus, Signal, Side
from app.routing import RoutingConfig, RoutingRule


class _ZeroFillPendingBroker(BrokerAdapter):
    name = "zero-fill"

    async def place_order(self, signal, account, quantity, symbol):
        return OrderResult(
            account_id=account.account_id,
            status=OrderStatus.PENDING,
            signal_id=signal.id,
            broker_order_id="broker-order-1",
            filled_quantity=0.0,  # explicit, reported zero -- not "unknown"
            message="accepted, nothing filled yet",
        )


@pytest.fixture
def store(tmp_path):
    return SignalStore(tmp_path / "test.db")


@pytest.mark.asyncio
async def test_explicit_zero_fill_on_a_pending_entry_does_not_apply_the_full_quantity(store):
    broker = _ZeroFillPendingBroker()
    account = DestinationAccount(account_id="acct1", broker="zero-fill")
    routing = RoutingConfig(rules=[RoutingRule(source="test", destinations=["acct1"])], accounts={"acct1": account})
    engine = SignalCopierEngine(routing=routing, brokers={"zero-fill": broker}, store=store)

    results = await engine.handle_signal(Signal(source="test", symbol="AAPL", side=Side.BUY, quantity=10.0))

    assert results[0].status == OrderStatus.PENDING
    assert store.get_position("acct1", "AAPL") == 0.0


@pytest.mark.asyncio
async def test_explicit_zero_fill_on_a_plain_close_does_not_apply_the_full_quantity(store):
    broker = _ZeroFillPendingBroker()
    account = DestinationAccount(account_id="acct1", broker="zero-fill")
    routing = RoutingConfig(rules=[RoutingRule(source="test", destinations=["acct1"])], accounts={"acct1": account})
    engine = SignalCopierEngine(routing=routing, brokers={"zero-fill": broker}, store=store)
    store.record_fill("acct1", "AAPL", Side.BUY, 10.0)  # pretend a prior fill left us long 10

    result = await engine.close_position(account, "AAPL")

    assert result.status == OrderStatus.PENDING
    # A confirmed zero fill on the closing order must not be treated as if
    # the whole 10 sold -- position should still show the original 10.
    assert store.get_position("acct1", "AAPL") == 10.0
