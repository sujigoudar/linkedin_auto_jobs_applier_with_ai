import pytest

from app.brokers.paper import PaperBroker
from app.db import SignalStore
from app.engine import SignalCopierEngine
from app.models import DestinationAccount, Side, Signal
from app.routing import RoutingConfig, RoutingRule


@pytest.fixture
def store(tmp_path):
    return SignalStore(tmp_path / "test.db")


def test_list_open_positions_excludes_flat(store):
    store.record_fill("acct1", "BTCUSDT", Side.BUY, 1.0)
    store.record_fill("acct1", "ETHUSDT", Side.BUY, 2.0)
    store.record_fill("acct1", "ETHUSDT", Side.SELL, 2.0)  # flattened back to zero

    positions = store.list_open_positions()

    symbols = {p["symbol"] for p in positions}
    assert "BTCUSDT" in symbols
    assert "ETHUSDT" not in symbols


def test_list_recent_signals_orders_newest_first(store):
    store.save_signal(Signal(source="a", symbol="BTCUSDT", side=Side.BUY))
    store.save_signal(Signal(source="b", symbol="ETHUSDT", side=Side.SELL))

    signals = store.list_recent_signals(limit=10)

    assert len(signals) == 2
    assert signals[0]["source"] == "b"  # most recently inserted first
    assert signals[1]["source"] == "a"


def test_list_recent_orders_filters_by_account(store):
    from app.models import OrderResult, OrderStatus

    store.save_order_result(
        OrderResult(account_id="acct1", status=OrderStatus.FILLED, signal_id="sig1")
    )
    store.save_order_result(
        OrderResult(account_id="acct2", status=OrderStatus.FILLED, signal_id="sig2")
    )

    all_orders = store.list_recent_orders()
    acct1_orders = store.list_recent_orders(account_id="acct1")

    assert len(all_orders) == 2
    assert len(acct1_orders) == 1
    assert acct1_orders[0]["account_id"] == "acct1"


@pytest.mark.asyncio
async def test_positions_endpoint_reflects_engine_activity(store):
    paper = PaperBroker()
    accounts = {"acct1": DestinationAccount(account_id="acct1", broker="paper")}
    routing = RoutingConfig(
        rules=[RoutingRule(source="tradingview", destinations=["acct1"])], accounts=accounts
    )
    engine = SignalCopierEngine(routing=routing, brokers={"paper": paper}, store=store)

    await engine.handle_signal(Signal(source="tradingview", symbol="BTCUSDT", side=Side.BUY, quantity=1.5))

    positions = store.list_open_positions()
    assert positions == [
        {"account_id": "acct1", "symbol": "BTCUSDT", "net_quantity": 1.5, "updated_at": positions[0]["updated_at"]}
    ]
