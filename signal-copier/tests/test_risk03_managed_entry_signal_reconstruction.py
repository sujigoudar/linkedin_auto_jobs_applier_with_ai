"""RISK-03: a managed-lifecycle entry's `entry_signal` reconstruction in
app/engine.py's _handle_managed_entry deliberately drops stop_loss/
take_profit (correct -- those are managed by the lifecycle manager, not
embedded in the broker order), but was ALSO dropping price/quantity/
analyst with no such reason. Some broker adapters read signal.price
directly (PaperBroker's simulated fill price, NinjaTraderBroker's limit
price), so a managed-lifecycle entry always executed at price 0
regardless of what the source actually specified."""
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
    lifecycle_manager = PositionLifecycleManager(brokers={"paper": paper}, store=store)
    engine = SignalCopierEngine(
        routing=routing, brokers={"paper": paper}, store=store, lifecycle_manager=lifecycle_manager
    )
    return engine, lifecycle_manager


@pytest.mark.asyncio
async def test_managed_entry_fills_at_the_signals_specified_price_not_zero(store):
    paper = PaperBroker()
    account = DestinationAccount(account_id="acct1", broker="paper", managed_lifecycle=True)
    engine, _ = _engine(store, paper, account)

    signal = Signal(
        source="tradingview", symbol="AAPL", side=Side.BUY, quantity=10.0, price=190.25, stop_loss=180.0
    )
    results = await engine.handle_signal(signal)

    assert results[0].status == OrderStatus.FILLED
    assert results[0].filled_price == 190.25


@pytest.mark.asyncio
async def test_managed_entry_still_drops_stop_loss_and_take_profit_from_the_broker_order(store, monkeypatch):
    """The lifecycle manager, not the broker order, must own protection --
    confirms the fix didn't accidentally restore the fields that are
    deliberately excluded."""
    paper = PaperBroker()
    account = DestinationAccount(account_id="acct1", broker="paper", managed_lifecycle=True)
    engine, _ = _engine(store, paper, account)

    seen_signals = []
    original_place_order = paper.place_order

    async def tracking_place_order(signal, account, quantity, symbol):
        seen_signals.append(signal)
        return await original_place_order(signal, account, quantity, symbol)

    monkeypatch.setattr(paper, "place_order", tracking_place_order)

    signal = Signal(
        source="tradingview",
        symbol="AAPL",
        side=Side.BUY,
        quantity=10.0,
        price=190.25,
        stop_loss=180.0,
        take_profit=220.0,
        analyst="alice",
    )
    await engine.handle_signal(signal)

    assert len(seen_signals) == 1
    entry_signal = seen_signals[0]
    assert entry_signal.stop_loss is None
    assert entry_signal.take_profit is None
    assert entry_signal.price == 190.25
    assert entry_signal.quantity == 10.0
    assert entry_signal.analyst == "alice"
