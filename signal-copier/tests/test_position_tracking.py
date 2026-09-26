import pytest

from app.brokers.paper import PaperBroker
from app.db import SignalStore
from app.engine import SignalCopierEngine
from app.models import DestinationAccount, OrderStatus, Signal, Side
from app.routing import RoutingConfig, RoutingRule


@pytest.fixture
def store(tmp_path):
    return SignalStore(tmp_path / "test.db")


@pytest.fixture
def engine(store):
    paper = PaperBroker()
    accounts = {"acct1": DestinationAccount(account_id="acct1", broker="paper", multiplier=1.0)}
    routing = RoutingConfig(
        rules=[RoutingRule(source="tradingview", destinations=["acct1"])], accounts=accounts
    )
    return SignalCopierEngine(routing=routing, brokers={"paper": paper}, store=store)


def test_get_position_starts_at_zero(store):
    assert store.get_position("acct1", "BTCUSDT") == 0.0


def test_record_fill_accumulates(store):
    store.record_fill("acct1", "BTCUSDT", Side.BUY, 1.0)
    assert store.get_position("acct1", "BTCUSDT") == 1.0
    store.record_fill("acct1", "BTCUSDT", Side.BUY, 0.5)
    assert store.get_position("acct1", "BTCUSDT") == 1.5
    store.record_fill("acct1", "BTCUSDT", Side.SELL, 2.0)
    assert store.get_position("acct1", "BTCUSDT") == -0.5


@pytest.mark.asyncio
async def test_close_with_no_position_is_rejected_without_calling_broker(engine):
    results = await engine.handle_signal(Signal(source="tradingview", symbol="BTCUSDT", side=Side.CLOSE))
    assert len(results) == 1
    assert results[0].status == OrderStatus.REJECTED
    assert "no open position" in results[0].message


@pytest.mark.asyncio
async def test_buy_then_close_flattens_long_position(engine, store):
    await engine.handle_signal(Signal(source="tradingview", symbol="BTCUSDT", side=Side.BUY, quantity=2.0))
    assert store.get_position("acct1", "BTCUSDT") == 2.0

    results = await engine.handle_signal(Signal(source="tradingview", symbol="BTCUSDT", side=Side.CLOSE))

    assert results[0].status == OrderStatus.FILLED
    assert results[0].filled_quantity == 2.0
    assert store.get_position("acct1", "BTCUSDT") == 0.0


@pytest.mark.asyncio
async def test_sell_then_close_flattens_short_position(engine, store):
    await engine.handle_signal(Signal(source="tradingview", symbol="BTCUSDT", side=Side.SELL, quantity=1.5))
    assert store.get_position("acct1", "BTCUSDT") == -1.5

    results = await engine.handle_signal(Signal(source="tradingview", symbol="BTCUSDT", side=Side.CLOSE))

    assert results[0].status == OrderStatus.FILLED
    assert results[0].filled_quantity == 1.5
    assert store.get_position("acct1", "BTCUSDT") == 0.0


@pytest.mark.asyncio
async def test_close_ignores_account_multiplier(store):
    # A close flattens whatever is actually open on this account, not
    # multiplier * some incoming quantity.
    paper = PaperBroker()
    accounts = {"acct_half": DestinationAccount(account_id="acct_half", broker="paper", multiplier=0.5)}
    routing = RoutingConfig(
        rules=[RoutingRule(source="tradingview", destinations=["acct_half"])], accounts=accounts
    )
    engine = SignalCopierEngine(routing=routing, brokers={"paper": paper}, store=store)

    await engine.handle_signal(Signal(source="tradingview", symbol="BTCUSDT", side=Side.BUY, quantity=4.0))
    assert store.get_position("acct_half", "BTCUSDT") == 2.0  # 4.0 * 0.5 multiplier

    results = await engine.handle_signal(Signal(source="tradingview", symbol="BTCUSDT", side=Side.CLOSE))

    assert results[0].filled_quantity == 2.0
    assert store.get_position("acct_half", "BTCUSDT") == 0.0
