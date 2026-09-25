import pytest

from app.brokers.base import BrokerAdapter
from app.db import SignalStore
from app.models import DestinationAccount, OrderResult, OrderStatus, Side, Signal
from app.reconciliation import OrderReconciler


@pytest.fixture
def store(tmp_path):
    return SignalStore(tmp_path / "test.db")


class _StubBroker(BrokerAdapter):
    name = "stub"

    def __init__(self, status_to_return: OrderResult | None):
        self._status_to_return = status_to_return

    async def place_order(self, signal, account, quantity, symbol):
        raise NotImplementedError

    async def get_order_status(self, account, broker_order_id):
        return self._status_to_return


def _seed_pending_order(store, *, side=Side.BUY, requested_quantity=2.0, optimistic_filled=2.0):
    signal = Signal(source="test", symbol="AAPL", side=side, quantity=requested_quantity)
    store.save_signal(signal)
    store.record_fill("acct1", "AAPL", side, optimistic_filled)
    row_id = store.save_order_result(
        OrderResult(
            account_id="acct1",
            status=OrderStatus.PENDING,
            signal_id=signal.id,
            broker_order_id="broker-order-1",
            filled_quantity=optimistic_filled,
            message="submitted",
        ),
        broker="stub",
        symbol="AAPL",
        side=side,
        requested_quantity=requested_quantity,
    )
    return row_id


@pytest.mark.asyncio
async def test_still_pending_order_is_left_alone(store):
    _seed_pending_order(store)
    reconciler = OrderReconciler(store, {"stub": _StubBroker(status_to_return=None)})

    corrected = await reconciler.reconcile_once()

    assert corrected == 0
    assert store.get_position("acct1", "AAPL") == 2.0
    assert store.list_pending_orders()  # still pending in the DB


@pytest.mark.asyncio
async def test_confirmed_fill_with_same_quantity_leaves_position_unchanged(store):
    row_id = _seed_pending_order(store)
    confirmed = OrderResult(
        account_id="acct1", status=OrderStatus.FILLED, signal_id="", filled_quantity=2.0, message="filled"
    )
    reconciler = OrderReconciler(store, {"stub": _StubBroker(status_to_return=confirmed)})

    corrected = await reconciler.reconcile_once()

    assert corrected == 1
    assert store.get_position("acct1", "AAPL") == 2.0
    orders = store.list_recent_orders()
    assert orders[0]["status"] == "filled"


@pytest.mark.asyncio
async def test_confirmed_fill_with_different_quantity_trues_up_position(store):
    _seed_pending_order(store, optimistic_filled=2.0)
    # broker actually only filled 1.5 of the requested 2.0
    confirmed = OrderResult(
        account_id="acct1", status=OrderStatus.FILLED, signal_id="", filled_quantity=1.5, message="filled"
    )
    reconciler = OrderReconciler(store, {"stub": _StubBroker(status_to_return=confirmed)})

    await reconciler.reconcile_once()

    assert store.get_position("acct1", "AAPL") == 1.5


@pytest.mark.asyncio
async def test_rejected_order_reverses_optimistic_position(store):
    _seed_pending_order(store, side=Side.BUY, optimistic_filled=2.0)
    assert store.get_position("acct1", "AAPL") == 2.0

    rejected = OrderResult(
        account_id="acct1", status=OrderStatus.REJECTED, signal_id="", message="rejected by broker"
    )
    reconciler = OrderReconciler(store, {"stub": _StubBroker(status_to_return=rejected)})

    await reconciler.reconcile_once()

    assert store.get_position("acct1", "AAPL") == 0.0


@pytest.mark.asyncio
async def test_rejected_sell_order_reverses_correctly(store):
    _seed_pending_order(store, side=Side.SELL, optimistic_filled=3.0)
    assert store.get_position("acct1", "AAPL") == -3.0

    rejected = OrderResult(
        account_id="acct1", status=OrderStatus.REJECTED, signal_id="", message="rejected by broker"
    )
    reconciler = OrderReconciler(store, {"stub": _StubBroker(status_to_return=rejected)})

    await reconciler.reconcile_once()

    assert store.get_position("acct1", "AAPL") == 0.0


@pytest.mark.asyncio
async def test_unregistered_broker_is_skipped(store):
    _seed_pending_order(store)
    reconciler = OrderReconciler(store, brokers={})  # 'stub' broker not registered

    corrected = await reconciler.reconcile_once()

    assert corrected == 0
