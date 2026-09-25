import pytest

from app.brokers.paper import PaperBroker
from app.db import SignalStore
from app.engine import SignalCopierEngine
from app.lifecycle.manager import PositionLifecycleManager
from app.models import DestinationAccount, OrderStatus, Signal, Side
from app.routing import RoutingConfig, RoutingRule


@pytest.fixture
def store(tmp_path):
    return SignalStore(tmp_path / "test.db")


def _engine(store, paper, account):
    routing = RoutingConfig(
        rules=[RoutingRule(source="tradingview", destinations=[account.account_id])],
        accounts={account.account_id: account},
    )
    lifecycle_manager = PositionLifecycleManager(brokers={"paper": paper})
    engine = SignalCopierEngine(
        routing=routing, brokers={"paper": paper}, store=store, lifecycle_manager=lifecycle_manager
    )
    return engine, lifecycle_manager


@pytest.mark.asyncio
async def test_managed_entry_without_stop_is_rejected(store):
    paper = PaperBroker()
    account = DestinationAccount(account_id="acct1", broker="paper", managed_lifecycle=True)
    engine, _ = _engine(store, paper, account)

    signal = Signal(source="tradingview", symbol="AAPL", side=Side.BUY, quantity=10.0)
    results = await engine.handle_signal(signal)

    assert results[0].status == OrderStatus.REJECTED
    assert "refusing to enter unprotected" in results[0].message
    # never reached the broker
    assert paper.positions.get("acct1", {}).get("AAPL", 0.0) == 0.0


@pytest.mark.asyncio
async def test_managed_entry_places_protective_stop_after_fill(store):
    paper = PaperBroker()
    account = DestinationAccount(account_id="acct1", broker="paper", managed_lifecycle=True)
    engine, lifecycle_manager = _engine(store, paper, account)

    signal = Signal(
        source="tradingview", symbol="AAPL", side=Side.BUY, quantity=10.0, stop_loss=48.50, take_profit=55.0
    )
    results = await engine.handle_signal(signal)

    assert results[0].status == OrderStatus.FILLED
    lifecycle = lifecycle_manager.get_lifecycle("acct1", "AAPL")
    assert lifecycle is not None
    assert lifecycle.stop.broker_order_id is not None
    assert lifecycle.stop.protected_quantity == 10.0

    # the stop is a resting order on the broker, not filled yet
    assert paper.simulate_price("AAPL", 49.00) == []
    fills = paper.simulate_price("AAPL", 48.00)
    assert len(fills) == 1
    assert store.get_position("acct1", "AAPL") == 10.0  # store isn't updated by broker-side stop fills directly


@pytest.mark.asyncio
async def test_managed_close_sells_the_tracked_open_quantity(store):
    paper = PaperBroker()
    account = DestinationAccount(account_id="acct1", broker="paper", managed_lifecycle=True)
    engine, lifecycle_manager = _engine(store, paper, account)

    entry = Signal(source="tradingview", symbol="AAPL", side=Side.BUY, quantity=10.0, stop_loss=48.50)
    await engine.handle_signal(entry)

    close = Signal(source="tradingview", symbol="AAPL", side=Side.CLOSE)
    results = await engine.handle_signal(close)

    assert results[0].status == OrderStatus.FILLED
    assert results[0].filled_quantity == 10.0
    lifecycle = lifecycle_manager.get_lifecycle("acct1", "AAPL")
    assert lifecycle.closed is True
    assert store.get_position("acct1", "AAPL") == 0.0


@pytest.mark.asyncio
async def test_managed_close_with_no_open_position_is_rejected(store):
    paper = PaperBroker()
    account = DestinationAccount(account_id="acct1", broker="paper", managed_lifecycle=True)
    engine, _ = _engine(store, paper, account)

    close = Signal(source="tradingview", symbol="AAPL", side=Side.CLOSE)
    results = await engine.handle_signal(close)

    assert results[0].status == OrderStatus.REJECTED
    assert "no open position to close" in results[0].message
