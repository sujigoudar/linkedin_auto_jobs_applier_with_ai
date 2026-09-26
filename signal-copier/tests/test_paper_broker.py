import pytest

from app.brokers.paper import PaperBroker
from app.models import AssetClass, DestinationAccount, OrderStatus, Signal, Side


@pytest.mark.asyncio
async def test_buy_then_sell_nets_position():
    broker = PaperBroker()
    account = DestinationAccount(account_id="acct1", broker="paper", multiplier=1.0)

    buy_signal = Signal(source="test", symbol="BTCUSDT", side=Side.BUY, asset_class=AssetClass.CRYPTO, price=100)
    result = await broker.place_order(buy_signal, account, quantity=1.0, symbol="BTC/USDT")
    assert result.status == OrderStatus.FILLED
    assert broker.positions["acct1"]["BTC/USDT"] == 1.0

    sell_signal = Signal(source="test", symbol="BTCUSDT", side=Side.SELL, asset_class=AssetClass.CRYPTO, price=110)
    await broker.place_order(sell_signal, account, quantity=0.4, symbol="BTC/USDT")
    assert broker.positions["acct1"]["BTC/USDT"] == pytest.approx(0.6)


@pytest.mark.asyncio
async def test_close_zeroes_position():
    broker = PaperBroker()
    account = DestinationAccount(account_id="acct1", broker="paper")

    await broker.place_order(
        Signal(source="test", symbol="ETHUSDT", side=Side.BUY), account, quantity=2.0, symbol="ETH/USDT"
    )
    await broker.place_order(
        Signal(source="test", symbol="ETHUSDT", side=Side.CLOSE), account, quantity=0, symbol="ETH/USDT"
    )
    assert broker.positions["acct1"]["ETH/USDT"] == 0.0
