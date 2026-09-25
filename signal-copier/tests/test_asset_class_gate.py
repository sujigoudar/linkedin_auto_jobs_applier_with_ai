"""Answers the exact question raised about a provider that mixes asset
classes in one channel (e.g. options + stocks + crypto alerts from the
same source): each signal must land on a broker that can actually trade
its asset_class, or be refused rather than silently misrouted."""
import pytest

from app.brokers.alpaca import AlpacaBroker
from app.brokers.ibkr import IBKRBroker
from app.brokers.paper import PaperBroker
from app.db import SignalStore
from app.engine import SignalCopierEngine
from app.models import AssetClass, DestinationAccount, OrderStatus, Signal, Side
from app.routing import RoutingConfig, RoutingRule


@pytest.fixture
def store(tmp_path):
    return SignalStore(tmp_path / "test.db")


def test_alpaca_declares_equity_only():
    broker = AlpacaBroker()
    assert broker.can_trade_asset_class(AssetClass.EQUITY) is True
    assert broker.can_trade_asset_class(AssetClass.OPTION) is False
    assert broker.can_trade_asset_class(AssetClass.CRYPTO) is False


def test_ibkr_declares_equity_only():
    broker = IBKRBroker()
    assert broker.can_trade_asset_class(AssetClass.EQUITY) is True
    assert broker.can_trade_asset_class(AssetClass.FUTURE) is False


def test_undeclared_broker_is_unrestricted_by_default():
    """PaperBroker doesn't declare supported_asset_classes -- an undeclared
    adapter's existing behavior must not change (see BrokerAdapter's
    docstring: None means "not verified," not "verified compatible")."""
    broker = PaperBroker()
    assert broker.supported_asset_classes is None
    for asset_class in AssetClass:
        assert broker.can_trade_asset_class(asset_class) is True


@pytest.mark.asyncio
async def test_option_signal_from_a_mixed_provider_is_refused_on_equity_only_broker(store, monkeypatch):
    monkeypatch.setenv("ALPACA_ACCT1_API_KEY", "key")
    monkeypatch.setenv("ALPACA_ACCT1_API_SECRET", "secret")
    broker = AlpacaBroker()
    account = DestinationAccount(account_id="acct1", broker="alpaca")
    routing = RoutingConfig(
        rules=[RoutingRule(source="buyalerts", destinations=["acct1"])], accounts={"acct1": account}
    )
    engine = SignalCopierEngine(routing=routing, brokers={"alpaca": broker}, store=store)

    option_signal = Signal(source="buyalerts", symbol="AAPL240119C00150000", side=Side.BUY, asset_class=AssetClass.OPTION, quantity=1.0)
    results = await engine.handle_signal(option_signal)

    assert results[0].status == OrderStatus.REJECTED
    assert "cannot trade asset_class" in results[0].message
    await broker.close()


@pytest.mark.asyncio
async def test_equity_signal_from_the_same_mixed_provider_still_routes_correctly(store, monkeypatch):
    """The other half of the same scenario: a stock alert from that SAME
    mixed-asset-class provider must still go through normally."""
    monkeypatch.setenv("ALPACA_ACCT1_API_KEY", "key")
    monkeypatch.setenv("ALPACA_ACCT1_API_SECRET", "secret")
    broker = AlpacaBroker()

    async def fake_post(url, headers, json):
        class _Resp:
            status_code = 200

            def raise_for_status(self):
                return None

            def json(self):
                return {"id": "order-1", "status": "accepted"}

        return _Resp()

    monkeypatch.setattr(broker._client, "post", fake_post)
    account = DestinationAccount(account_id="acct1", broker="alpaca")
    routing = RoutingConfig(
        rules=[RoutingRule(source="buyalerts", destinations=["acct1"])], accounts={"acct1": account}
    )
    engine = SignalCopierEngine(routing=routing, brokers={"alpaca": broker}, store=store)

    equity_signal = Signal(source="buyalerts", symbol="AAPL", side=Side.BUY, asset_class=AssetClass.EQUITY, quantity=1.0)
    results = await engine.handle_signal(equity_signal)

    assert results[0].status == OrderStatus.PENDING
    await broker.close()


@pytest.mark.asyncio
async def test_crypto_signal_from_the_same_mixed_provider_is_also_refused_on_equity_broker(store, monkeypatch):
    monkeypatch.setenv("ALPACA_ACCT1_API_KEY", "key")
    monkeypatch.setenv("ALPACA_ACCT1_API_SECRET", "secret")
    broker = AlpacaBroker()
    account = DestinationAccount(account_id="acct1", broker="alpaca")
    routing = RoutingConfig(
        rules=[RoutingRule(source="buyalerts", destinations=["acct1"])], accounts={"acct1": account}
    )
    engine = SignalCopierEngine(routing=routing, brokers={"alpaca": broker}, store=store)

    crypto_signal = Signal(source="buyalerts", symbol="BTCUSDT", side=Side.BUY, asset_class=AssetClass.CRYPTO, quantity=0.1)
    results = await engine.handle_signal(crypto_signal)

    assert results[0].status == OrderStatus.REJECTED
    await broker.close()
