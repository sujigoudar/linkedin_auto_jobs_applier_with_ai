import pytest

pytest.importorskip("ib_insync")

from app.brokers.ibkr import IBKRBroker
from app.models import DestinationAccount, OrderStatus, Signal, Side


class _FakeOrderStatus:
    status = "Submitted"


class _FakeTrade:
    def __init__(self, order):
        self.order = order
        self.orderStatus = _FakeOrderStatus()


class _FakeIB:
    def __init__(self):
        self._next_id = 1
        self.placed = []

        class _Client:
            def getReqId(inner_self):
                req_id = self._next_id
                self._next_id += 1
                return req_id

        self.client = _Client()

    def placeOrder(self, contract, order):
        self.placed.append(order)
        return _FakeTrade(order)


@pytest.fixture
def broker():
    return IBKRBroker()


@pytest.mark.asyncio
async def test_plain_order_has_no_bracket(broker, monkeypatch):
    fake_ib = _FakeIB()
    monkeypatch.setattr(broker, "_connected_ib", lambda: _async_return(fake_ib))

    account = DestinationAccount(account_id="acct1", broker="ibkr")
    result = await broker.place_order(
        Signal(source="test", symbol="AAPL", side=Side.BUY), account, quantity=10.0, symbol="AAPL"
    )

    assert result.status == OrderStatus.PENDING
    assert len(fake_ib.placed) == 1
    assert fake_ib.placed[0].orderType == "MKT"


@pytest.mark.asyncio
async def test_both_sl_and_tp_builds_three_order_bracket(broker, monkeypatch):
    fake_ib = _FakeIB()
    monkeypatch.setattr(broker, "_connected_ib", lambda: _async_return(fake_ib))

    account = DestinationAccount(account_id="acct1", broker="ibkr")
    await broker.place_order(
        Signal(source="test", symbol="AAPL", side=Side.BUY, stop_loss=185.0, take_profit=200.0),
        account,
        quantity=10.0,
        symbol="AAPL",
    )

    assert len(fake_ib.placed) == 3
    parent, take_profit, stop_loss = fake_ib.placed

    assert parent.orderType == "MKT"
    assert parent.action == "BUY"
    assert parent.transmit is False

    assert take_profit.orderType == "LMT"
    assert take_profit.action == "SELL"  # opposite of entry
    assert take_profit.lmtPrice == 200.0
    assert take_profit.parentId == parent.orderId
    assert take_profit.transmit is False

    assert stop_loss.orderType == "STP"
    assert stop_loss.action == "SELL"
    assert stop_loss.auxPrice == 185.0
    assert stop_loss.parentId == parent.orderId
    assert stop_loss.transmit is True  # last order in the group submits it


@pytest.mark.asyncio
async def test_only_stop_loss_builds_two_order_bracket(broker, monkeypatch):
    fake_ib = _FakeIB()
    monkeypatch.setattr(broker, "_connected_ib", lambda: _async_return(fake_ib))

    account = DestinationAccount(account_id="acct1", broker="ibkr")
    await broker.place_order(
        Signal(source="test", symbol="AAPL", side=Side.SELL, stop_loss=195.0),
        account,
        quantity=5.0,
        symbol="AAPL",
    )

    assert len(fake_ib.placed) == 2
    parent, stop_loss = fake_ib.placed
    assert parent.transmit is False
    assert stop_loss.action == "BUY"  # opposite of a SELL entry
    assert stop_loss.transmit is True


async def _async_return(value):
    return value


class _FakeOrderStatusWithFill:
    def __init__(self, status, filled=0.0, avgFillPrice=0.0):
        self.status = status
        self.filled = filled
        self.avgFillPrice = avgFillPrice


class _FakeTradeWithStatus:
    def __init__(self, order_status):
        self.orderStatus = order_status


@pytest.mark.asyncio
async def test_get_order_status_partially_filled_reports_pending_with_progress(broker):
    """A still-open, partially-filled order must surface its real fill
    progress (so a managed-lifecycle entry can be protected for what's
    actually confirmed owned so far -- see
    PositionLifecycleManager.resolve_pending_entry) rather than being
    discarded the same as a fully-unfilled "nothing new" order."""
    broker._trades["order-1"] = _FakeTradeWithStatus(_FakeOrderStatusWithFill("Submitted", filled=30.0))
    account = DestinationAccount(account_id="acct1", broker="ibkr")

    result = await broker.get_order_status(account, "order-1")

    assert result is not None
    assert result.status == OrderStatus.PENDING
    assert result.filled_quantity == 30.0


@pytest.mark.asyncio
async def test_get_order_status_new_unfilled_order_reports_nothing_new(broker):
    broker._trades["order-1"] = _FakeTradeWithStatus(_FakeOrderStatusWithFill("Submitted", filled=0.0))
    account = DestinationAccount(account_id="acct1", broker="ibkr")

    result = await broker.get_order_status(account, "order-1")

    assert result is None


@pytest.mark.asyncio
async def test_get_order_status_filled_reports_terminal(broker):
    broker._trades["order-1"] = _FakeTradeWithStatus(_FakeOrderStatusWithFill("Filled", filled=100.0))
    account = DestinationAccount(account_id="acct1", broker="ibkr")

    result = await broker.get_order_status(account, "order-1")

    assert result is not None
    assert result.status == OrderStatus.FILLED
    assert result.filled_quantity == 100.0


@pytest.mark.asyncio
async def test_get_order_status_cancelled_with_partial_fill_reports_it(broker):
    broker._trades["order-1"] = _FakeTradeWithStatus(_FakeOrderStatusWithFill("Cancelled", filled=30.0))
    account = DestinationAccount(account_id="acct1", broker="ibkr")

    result = await broker.get_order_status(account, "order-1")

    assert result is not None
    assert result.status == OrderStatus.REJECTED
    assert result.filled_quantity == 30.0
