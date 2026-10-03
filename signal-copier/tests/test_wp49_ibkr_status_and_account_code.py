"""WP-49 tests for IBKR adapter gaps C-14 and C-18.

C-14: IBKR order status is process-memory only -- after a restart,
      get_order_status must recover from the venue via reqOpenOrders/
      reqCompletedOrders, or return ERROR rather than None.

C-18: IBKR uses the local account_id as the IB account code -- must
      use an explicit IBKR_{ID}_ACCOUNT environment variable and fail
      closed with a clear error when it is missing.
"""
import pytest

pytest.importorskip("ib_async")

from app.brokers.ibkr import IBKRBroker
from app.models import DestinationAccount, OrderStatus, Signal, Side


class _FakeOrderStatus:
    """Fake IB order status."""

    def __init__(self, status, filled=0.0, avgFillPrice=0.0):
        self.status = status
        self.filled = filled
        self.avgFillPrice = avgFillPrice


class _FakeOrder:
    """Fake IB order object."""

    def __init__(self, order_id):
        self.orderId = order_id
        self.account = None


class _FakeTrade:
    """Fake IB Trade object."""

    def __init__(self, order_id, status="Submitted", filled=0.0, avgFillPrice=0.0):
        self.order = _FakeOrder(order_id)
        self.orderStatus = _FakeOrderStatus(status, filled, avgFillPrice)


class _FakeIB:
    """Fake IB connection with support for open/completed orders."""

    def __init__(self):
        self._next_id = 1
        self.placed = []
        self._all_trades = {}  # order_id -> Trade

        class _Client:
            def getReqId(inner_self):
                req_id = self._next_id
                self._next_id += 1
                return req_id

        self.client = _Client()

    def placeOrder(self, contract, order):
        trade = _FakeTrade(order.orderId or self.client.getReqId())
        self.placed.append(order)
        self._all_trades[trade.order.orderId] = trade
        return trade

    async def reqAllOpenOrdersAsync(self):
        """C-14: Return only open (non-terminal) orders."""
        return [
            trade
            for trade in self._all_trades.values()
            if trade.orderStatus.status not in ("Filled", "Cancelled", "ApiCancelled", "Inactive")
        ]

    async def reqCompletedOrdersAsync(self, apiOnly=False):
        """C-14: Return only completed (terminal) orders."""
        return [
            trade
            for trade in self._all_trades.values()
            if trade.orderStatus.status in ("Filled", "Cancelled", "ApiCancelled", "Inactive")
        ]


@pytest.fixture
def broker():
    return IBKRBroker()


# C-18 Tests: IBKR Account Code Requirement


@pytest.mark.asyncio
async def test_c18_place_order_fails_without_broker_account_code_env(broker, monkeypatch):
    """C-18: place_order must fail closed when IBKR_{ID}_ACCOUNT is missing."""
    monkeypatch.delenv("IBKR_MAIN_ACCOUNT", raising=False)
    fake_ib = _FakeIB()
    monkeypatch.setattr(broker, "_connected_ib", lambda: _async_return(fake_ib))

    account = DestinationAccount(account_id="main", broker="ibkr")
    result = await broker.place_order(
        Signal(source="test", symbol="AAPL", side=Side.BUY), account, quantity=10.0, symbol="AAPL"
    )

    assert result.status == OrderStatus.ERROR
    assert "IBKR_MAIN_ACCOUNT" in result.message
    assert "missing" in result.message.lower()


@pytest.mark.asyncio
async def test_c18_place_order_uses_explicit_broker_account_code(broker, monkeypatch):
    """C-18: place_order uses IBKR_{ID}_ACCOUNT, not the local account_id."""
    monkeypatch.setenv("IBKR_MAIN_ACCOUNT", "DU123456")
    fake_ib = _FakeIB()
    monkeypatch.setattr(broker, "_connected_ib", lambda: _async_return(fake_ib))

    account = DestinationAccount(account_id="main", broker="ibkr")
    await broker.place_order(
        Signal(source="test", symbol="AAPL", side=Side.BUY), account, quantity=10.0, symbol="AAPL"
    )

    # Verify that the order's account field has the IBKR account code, not the local id
    assert len(fake_ib.placed) == 1
    assert fake_ib.placed[0].account == "DU123456"
    assert fake_ib.placed[0].account != "main"


@pytest.mark.asyncio
async def test_c18_bracket_orders_use_explicit_broker_account_code(broker, monkeypatch):
    """C-18: Bracket orders also use IBKR_{ID}_ACCOUNT."""
    monkeypatch.setenv("IBKR_ACCT2_ACCOUNT", "DU789012")
    fake_ib = _FakeIB()
    monkeypatch.setattr(broker, "_connected_ib", lambda: _async_return(fake_ib))

    account = DestinationAccount(account_id="acct2", broker="ibkr")
    await broker.place_order(
        Signal(source="test", symbol="AAPL", side=Side.BUY, stop_loss=185.0, take_profit=200.0),
        account,
        quantity=10.0,
        symbol="AAPL",
    )

    # All three orders (parent + 2 children) should have the explicit broker account code
    assert len(fake_ib.placed) == 3
    assert all(order.account == "DU789012" for order in fake_ib.placed)


# C-14 Tests: Order Status Recovery After Restart


@pytest.mark.asyncio
async def test_c14_get_order_status_returns_cached_order(broker):
    """C-14 baseline: Cached orders still work."""
    broker._trades["order-1"] = _FakeTrade(1, status="Filled", filled=100.0)
    account = DestinationAccount(account_id="main", broker="ibkr")

    result = await broker.get_order_status(account, "order-1")

    assert result is not None
    assert result.status == OrderStatus.FILLED
    assert result.filled_quantity == 100.0


@pytest.mark.asyncio
async def test_c14_get_order_status_recovers_from_open_orders(broker, monkeypatch):
    """C-14: When order is not cached, recover from reqAllOpenOrdersAsync."""
    fake_ib = _FakeIB()

    # Simulate an order that was placed before the restart
    # (it's in the open orders list but not in the cache)
    open_order = _FakeTrade(42, status="Submitted", filled=50.0)
    fake_ib._all_trades[42] = open_order

    monkeypatch.setattr(broker, "_connected_ib", lambda: _async_return(fake_ib))

    account = DestinationAccount(account_id="main", broker="ibkr")

    # get_order_status with a broker_order_id not in the cache
    result = await broker.get_order_status(account, "42")

    assert result is not None
    assert result.status == OrderStatus.PENDING
    assert result.filled_quantity == 50.0
    # After recovery, it should be cached
    assert "42" in broker._trades


@pytest.mark.asyncio
async def test_c14_get_order_status_recovers_from_completed_orders(broker, monkeypatch):
    """C-14: When order is not cached, recover from reqCompletedOrdersAsync."""
    fake_ib = _FakeIB()

    # Simulate an order that completed before the restart
    completed_order = _FakeTrade(99, status="Filled", filled=100.0, avgFillPrice=150.0)
    fake_ib._all_trades[99] = completed_order

    monkeypatch.setattr(broker, "_connected_ib", lambda: _async_return(fake_ib))

    account = DestinationAccount(account_id="main", broker="ibkr")

    # get_order_status with a broker_order_id not in the cache
    result = await broker.get_order_status(account, "99")

    assert result is not None
    assert result.status == OrderStatus.FILLED
    assert result.filled_quantity == 100.0
    assert result.filled_price == 150.0
    # After recovery, it should be cached
    assert "99" in broker._trades


@pytest.mark.asyncio
async def test_c14_get_order_status_returns_error_when_not_found(broker, monkeypatch):
    """C-14: When order cannot be recovered, return ERROR (not None)."""
    fake_ib = _FakeIB()
    # No orders in the fake IB instance

    monkeypatch.setattr(broker, "_connected_ib", lambda: _async_return(fake_ib))

    account = DestinationAccount(account_id="main", broker="ibkr")

    # get_order_status with a broker_order_id not in cache and not in broker
    result = await broker.get_order_status(account, "999")

    # Should return ERROR, not None
    assert result is not None
    assert result.status == OrderStatus.ERROR
    assert "not found" in result.message.lower()
    assert "999" in result.message


@pytest.mark.asyncio
async def test_c14_get_order_status_handles_broker_connection_error(broker, monkeypatch):
    """C-14: When broker connection fails during recovery, return ERROR."""

    async def failing_connected_ib():
        raise RuntimeError("Connection failed")

    monkeypatch.setattr(broker, "_connected_ib", failing_connected_ib)

    account = DestinationAccount(account_id="main", broker="ibkr")

    # get_order_status with order not in cache
    result = await broker.get_order_status(account, "orphaned-123")

    assert result is not None
    assert result.status == OrderStatus.ERROR
    assert "not found" in result.message.lower()


@pytest.mark.asyncio
async def test_c14_recovery_does_not_affect_cached_orders(broker, monkeypatch):
    """C-14: Recovery lookup doesn't overwrite or interfere with cached orders."""
    fake_ib = _FakeIB()

    # Add an order to both the broker and the cache
    cached_trade = _FakeTrade(111, status="Filled", filled=75.0, avgFillPrice=160.0)
    broker._trades["111"] = cached_trade

    broker_trade = _FakeTrade(111, status="Submitted", filled=0.0)  # Different status
    fake_ib._all_trades[111] = broker_trade

    monkeypatch.setattr(broker, "_connected_ib", lambda: _async_return(fake_ib))

    account = DestinationAccount(account_id="main", broker="ibkr")
    result = await broker.get_order_status(account, "111")

    # Should use the cached version, not the broker version
    assert result is not None
    assert result.status == OrderStatus.FILLED
    assert result.filled_quantity == 75.0


# Helper


async def _async_return(value):
    return value
