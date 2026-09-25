import pytest

pytest.importorskip("ccxt")

from app.brokers.ccxt_broker import CCXTBroker
from app.models import DestinationAccount, OrderStatus, Signal, Side


@pytest.fixture
def broker(monkeypatch):
    monkeypatch.setenv("CCXT_ACCT1_API_KEY", "key123")
    monkeypatch.setenv("CCXT_ACCT1_API_SECRET", "secret456")
    return CCXTBroker()


class _FakeExchange:
    def __init__(self):
        self.create_order_calls = []
        self.closed = False

    async def create_order(self, **kwargs):
        self.create_order_calls.append(kwargs)
        return {"id": "order-1", "filled": kwargs["amount"], "average": 65000.0}

    async def close(self):
        self.closed = True


@pytest.mark.asyncio
async def test_stop_loss_and_take_profit_passed_as_unified_params(broker):
    fake = _FakeExchange()
    broker._exchanges["acct1"] = fake
    account = DestinationAccount(account_id="acct1", broker="ccxt")

    await broker.place_order(
        Signal(source="test", symbol="BTCUSDT", side=Side.BUY, stop_loss=63000.0, take_profit=70000.0),
        account,
        quantity=1.0,
        symbol="BTC/USDT",
    )

    assert fake.create_order_calls[0]["params"] == {"stopLossPrice": 63000.0, "takeProfitPrice": 70000.0}


@pytest.mark.asyncio
async def test_no_sl_tp_sends_empty_params(broker):
    fake = _FakeExchange()
    broker._exchanges["acct1"] = fake
    account = DestinationAccount(account_id="acct1", broker="ccxt")

    await broker.place_order(
        Signal(source="test", symbol="BTCUSDT", side=Side.BUY), account, quantity=1.0, symbol="BTC/USDT"
    )

    assert fake.create_order_calls[0]["params"] == {}


@pytest.mark.asyncio
async def test_close_side_is_rejected(broker):
    account = DestinationAccount(account_id="acct1", broker="ccxt")

    result = await broker.place_order(
        Signal(source="test", symbol="BTCUSDT", side=Side.CLOSE), account, quantity=1.0, symbol="BTC/USDT"
    )

    assert result.status == OrderStatus.REJECTED
