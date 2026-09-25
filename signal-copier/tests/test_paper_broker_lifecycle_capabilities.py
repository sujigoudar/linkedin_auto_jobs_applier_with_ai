import pytest

from app.brokers.paper import PaperBroker
from app.models import DestinationAccount, OrderStatus, Side, Signal


@pytest.fixture
async def broker_with_long_position():
    broker = PaperBroker()
    account = DestinationAccount(account_id="acct1", broker="paper")
    await broker.place_order(
        Signal(source="test", symbol="AAPL", side=Side.BUY), account, quantity=62.0, symbol="AAPL"
    )
    return broker, account


@pytest.mark.asyncio
async def test_place_protective_stop_rests_pending(broker_with_long_position):
    broker, account = broker_with_long_position

    result = await broker.place_protective_stop(account, "AAPL", quantity=62.0, stop_price=48.50, exit_side=Side.SELL)

    assert result.status == OrderStatus.PENDING
    assert result.broker_order_id is not None
    # doesn't fill until the price actually trades through it
    assert broker.simulate_price("AAPL", 49.00) == []


@pytest.mark.asyncio
async def test_simulate_price_fills_stop_when_touched(broker_with_long_position):
    broker, account = broker_with_long_position
    stop_result = await broker.place_protective_stop(account, "AAPL", quantity=62.0, stop_price=48.50, exit_side=Side.SELL)

    fills = broker.simulate_price("AAPL", 48.40)

    assert len(fills) == 1
    assert fills[0].broker_order_id == stop_result.broker_order_id
    assert fills[0].filled_quantity == 62.0
    assert fills[0].filled_price == 48.40
    assert broker.positions["acct1"]["AAPL"] == 0.0  # long fully closed by the stop


@pytest.mark.asyncio
async def test_cancel_order_removes_resting_stop(broker_with_long_position):
    broker, account = broker_with_long_position
    stop_result = await broker.place_protective_stop(account, "AAPL", quantity=62.0, stop_price=48.50, exit_side=Side.SELL)

    cancelled = await broker.cancel_order(account, stop_result.broker_order_id)

    assert cancelled is True
    assert broker.simulate_price("AAPL", 40.00) == []  # nothing left to trigger


@pytest.mark.asyncio
async def test_cancel_order_on_unknown_id_returns_false(broker_with_long_position):
    broker, account = broker_with_long_position
    assert await broker.cancel_order(account, "does-not-exist") is False


@pytest.mark.asyncio
async def test_replace_stop_quantity_resizes_in_place(broker_with_long_position):
    broker, account = broker_with_long_position
    stop_result = await broker.place_protective_stop(account, "AAPL", quantity=62.0, stop_price=48.50, exit_side=Side.SELL)

    await broker.replace_stop_quantity(account, stop_result.broker_order_id, new_quantity=47.0)

    fills = broker.simulate_price("AAPL", 48.00)
    assert fills[0].filled_quantity == 47.0


@pytest.mark.asyncio
async def test_get_broker_position_reflects_fills(broker_with_long_position):
    broker, account = broker_with_long_position
    assert await broker.get_broker_position(account, "AAPL") == 62.0


@pytest.mark.asyncio
async def test_short_position_gets_buy_stop():
    broker = PaperBroker()
    account = DestinationAccount(account_id="acct1", broker="paper")
    await broker.place_order(
        Signal(source="test", symbol="AAPL", side=Side.SELL), account, quantity=30.0, symbol="AAPL"
    )

    await broker.place_protective_stop(account, "AAPL", quantity=30.0, stop_price=52.00, exit_side=Side.BUY)

    assert broker.simulate_price("AAPL", 51.99) == []  # doesn't trigger a BUY-side stop
    fills = broker.simulate_price("AAPL", 52.01)
    assert len(fills) == 1
    assert broker.positions["acct1"]["AAPL"] == 0.0
