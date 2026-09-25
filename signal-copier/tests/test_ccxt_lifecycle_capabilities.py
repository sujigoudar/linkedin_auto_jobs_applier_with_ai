import pytest

pytest.importorskip("ccxt")

from app.brokers.ccxt_broker import CCXTBroker
from app.models import DestinationAccount, OrderStatus, Side


@pytest.fixture
def broker(monkeypatch):
    monkeypatch.setenv("CCXT_ACCT1_API_KEY", "key123")
    monkeypatch.setenv("CCXT_ACCT1_API_SECRET", "secret456")
    return CCXTBroker()


@pytest.fixture
def account():
    return DestinationAccount(account_id="acct1", broker="ccxt")


class _FakeExchange:
    def __init__(self):
        self.create_order_calls = []
        self.cancel_order_calls = []
        self.fetch_positions_result = None
        self.fetch_positions_raises = None

    async def create_order(self, **kwargs):
        self.create_order_calls.append(kwargs)
        return {"id": "stop-order-1"}

    async def cancel_order(self, order_id, symbol=None):
        self.cancel_order_calls.append(order_id)
        if order_id == "does-not-exist":
            raise Exception("order not found")

    async def fetch_positions(self, symbols):
        if self.fetch_positions_raises:
            raise self.fetch_positions_raises
        return self.fetch_positions_result or []


@pytest.mark.asyncio
async def test_place_protective_stop_uses_standalone_stopLossPrice(broker, account):
    fake = _FakeExchange()
    broker._exchanges["acct1"] = fake

    result = await broker.place_protective_stop(account, "BTC/USDT", quantity=1.0, stop_price=63000.0, exit_side=Side.SELL)

    assert result.status == OrderStatus.PENDING
    assert result.broker_order_id == "stop-order-1"
    call = fake.create_order_calls[0]
    assert call["params"] == {"stopLossPrice": 63000.0}
    assert "takeProfitPrice" not in call["params"]
    assert call["side"] == "sell"


@pytest.mark.asyncio
async def test_place_protective_stop_reports_error_on_exception(broker, account):
    fake = _FakeExchange()

    async def raise_not_supported(**kwargs):
        raise Exception("stop orders not supported on this market")

    fake.create_order = raise_not_supported
    broker._exchanges["acct1"] = fake

    result = await broker.place_protective_stop(account, "BTC/USDT", quantity=1.0, stop_price=63000.0, exit_side=Side.SELL)

    assert result.status == OrderStatus.ERROR


@pytest.mark.asyncio
async def test_cancel_order_success(broker, account):
    fake = _FakeExchange()
    broker._exchanges["acct1"] = fake

    assert await broker.cancel_order(account, "stop-order-1") is True
    assert fake.cancel_order_calls == ["stop-order-1"]


@pytest.mark.asyncio
async def test_cancel_order_failure_returns_false(broker, account):
    fake = _FakeExchange()
    broker._exchanges["acct1"] = fake

    assert await broker.cancel_order(account, "does-not-exist") is False


@pytest.mark.asyncio
async def test_get_broker_position_returns_none_when_unsupported(broker, account):
    fake = _FakeExchange()
    fake.fetch_positions_raises = Exception("NotSupported: spot markets have no positions")
    broker._exchanges["acct1"] = fake

    assert await broker.get_broker_position(account, "BTC/USDT") is None


@pytest.mark.asyncio
async def test_get_broker_position_returns_long_quantity(broker, account):
    fake = _FakeExchange()
    fake.fetch_positions_result = [{"symbol": "BTC/USDT", "contracts": 1.5, "side": "long"}]
    broker._exchanges["acct1"] = fake

    assert await broker.get_broker_position(account, "BTC/USDT") == 1.5


@pytest.mark.asyncio
async def test_get_broker_position_returns_negative_for_short(broker, account):
    fake = _FakeExchange()
    fake.fetch_positions_result = [{"symbol": "BTC/USDT", "contracts": 1.5, "side": "short"}]
    broker._exchanges["acct1"] = fake

    assert await broker.get_broker_position(account, "BTC/USDT") == -1.5


@pytest.mark.asyncio
async def test_get_broker_position_returns_zero_when_flat(broker, account):
    fake = _FakeExchange()
    fake.fetch_positions_result = []
    broker._exchanges["acct1"] = fake

    assert await broker.get_broker_position(account, "BTC/USDT") == 0.0


@pytest.mark.asyncio
async def test_replace_stop_quantity_not_implemented(broker, account):
    assert await broker.replace_stop_quantity(account, "stop-order-1", new_quantity=1.0) is None
