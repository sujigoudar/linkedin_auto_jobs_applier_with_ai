import pytest

from app.brokers.paper import PaperBroker
from app.db import SignalStore
from app.engine import SignalCopierEngine
from app.models import DestinationAccount, OrderStatus, Signal, Side
from app.routing import RoutingConfig, RoutingRule


@pytest.fixture
def store(tmp_path):
    return SignalStore(tmp_path / "test.db")


@pytest.mark.asyncio
async def test_signal_routes_and_sizes_to_multiple_accounts(store):
    paper = PaperBroker()
    accounts = {
        "acct_full": DestinationAccount(account_id="acct_full", broker="paper", multiplier=1.0),
        "acct_half": DestinationAccount(account_id="acct_half", broker="paper", multiplier=0.5),
        "acct_flat": DestinationAccount(account_id="acct_flat", broker="paper", fixed_quantity=5.0),
    }
    routing = RoutingConfig(
        rules=[RoutingRule(source="tradingview", destinations=["acct_full", "acct_half", "acct_flat"])],
        accounts=accounts,
    )
    engine = SignalCopierEngine(routing=routing, brokers={"paper": paper}, store=store)

    signal = Signal(source="tradingview", symbol="BTCUSDT", side=Side.BUY, quantity=2.0)
    results = await engine.handle_signal(signal)

    by_account = {r.account_id: r for r in results}
    assert by_account["acct_full"].filled_quantity == 2.0
    assert by_account["acct_half"].filled_quantity == 1.0
    assert by_account["acct_flat"].filled_quantity == 5.0
    assert all(r.status == OrderStatus.FILLED for r in results)


@pytest.mark.asyncio
async def test_symbol_filter_excludes_non_matching_signal(store):
    paper = PaperBroker()
    accounts = {"acct1": DestinationAccount(account_id="acct1", broker="paper")}
    routing = RoutingConfig(
        rules=[RoutingRule(source="tradingview", destinations=["acct1"], symbol_filter=["ETHUSDT"])],
        accounts=accounts,
    )
    engine = SignalCopierEngine(routing=routing, brokers={"paper": paper}, store=store)

    signal = Signal(source="tradingview", symbol="BTCUSDT", side=Side.BUY, quantity=1.0)
    results = await engine.handle_signal(signal)

    assert results == []


@pytest.mark.asyncio
async def test_missing_broker_reports_error_without_blocking_others(store):
    paper = PaperBroker()
    accounts = {
        "acct_missing_broker": DestinationAccount(account_id="acct_missing_broker", broker="nonexistent"),
        "acct_ok": DestinationAccount(account_id="acct_ok", broker="paper"),
    }
    routing = RoutingConfig(
        rules=[RoutingRule(source="tradingview", destinations=["acct_missing_broker", "acct_ok"])],
        accounts=accounts,
    )
    engine = SignalCopierEngine(routing=routing, brokers={"paper": paper}, store=store)

    signal = Signal(source="tradingview", symbol="BTCUSDT", side=Side.BUY, quantity=1.0)
    results = await engine.handle_signal(signal)

    by_account = {r.account_id: r for r in results}
    assert by_account["acct_missing_broker"].status == OrderStatus.ERROR
    assert by_account["acct_ok"].status == OrderStatus.FILLED


@pytest.mark.asyncio
async def test_disabled_account_is_not_routed_to(store):
    paper = PaperBroker()
    accounts = {"acct1": DestinationAccount(account_id="acct1", broker="paper", enabled=False)}
    routing = RoutingConfig(
        rules=[RoutingRule(source="tradingview", destinations=["acct1"])], accounts=accounts
    )
    engine = SignalCopierEngine(routing=routing, brokers={"paper": paper}, store=store)

    results = await engine.handle_signal(Signal(source="tradingview", symbol="BTCUSDT", side=Side.BUY))
    assert results == []
