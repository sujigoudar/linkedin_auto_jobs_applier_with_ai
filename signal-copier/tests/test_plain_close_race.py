"""A plain (non-managed_lifecycle) account's close path had no equivalent of
CloseArbiter's per-(account, symbol) serialization: two near-simultaneous
close attempts (a duplicate dashboard click, a retried HTTP request, a
provider EXIT signal racing a manual "Exit now") could both read the same
tracked position via SignalStore.get_position, both resolve to a
full-quantity opposing order, and both submit it -- a double-sell.
SignalCopierEngine._resolve_and_submit_plain_close now serializes that
read-resolve-submit-record sequence per (account_id, symbol), the same
guarantee managed_lifecycle accounts already had via CloseArbiter.
"""
import asyncio

import pytest

from app.brokers.paper import PaperBroker
from app.db import SignalStore
from app.engine import SignalCopierEngine
from app.models import DestinationAccount, OrderResult, OrderStatus, Side, Signal
from app.routing import RoutingConfig, RoutingRule


class _SlowBroker(PaperBroker):
    """A PaperBroker whose place_order takes a moment to "confirm" -- wide
    enough to make two concurrent close attempts actually overlap instead
    of trivially serializing through Python's cooperative scheduling."""

    def __init__(self, delay: float = 0.05):
        super().__init__()
        self.delay = delay
        self.submitted_sells: list[float] = []

    async def place_order(self, signal, account, quantity, symbol):
        if signal.side in (Side.SELL, Side.BUY) and quantity > 0:
            await asyncio.sleep(self.delay)
        result = await super().place_order(signal, account, quantity, symbol)
        if signal.side == Side.SELL:
            self.submitted_sells.append(quantity)
        return result


@pytest.fixture
def store(tmp_path):
    return SignalStore(tmp_path / "test.db")


@pytest.fixture
def broker():
    return _SlowBroker()


@pytest.fixture
def engine(store, broker):
    account = DestinationAccount(account_id="acct1", broker="paper")
    # A real routing rule is required for test_provider_close_signal_races_
    # manual_close_without_double_selling below: without one,
    # destinations_for() returns [] and engine.handle_signal(close_signal)
    # is a silent no-op, which would make that test "pass" without the
    # provider-side close ever reaching the shared lock it's meant to test.
    routing = RoutingConfig(
        rules=[RoutingRule(source="tradingview", destinations=["acct1"])], accounts={"acct1": account}
    )
    return SignalCopierEngine(routing=routing, brokers={"paper": broker}, store=store)


@pytest.mark.asyncio
async def test_two_concurrent_manual_closes_do_not_double_sell(store, broker, engine):
    account = engine.routing.accounts["acct1"]
    await broker.place_order(Signal(source="test", symbol="AAPL", side=Side.BUY), account, 10.0, "AAPL")
    store.record_fill("acct1", "AAPL", Side.BUY, 10.0)

    results = await asyncio.gather(
        engine.close_position(account, "AAPL", reason="dashboard_manual_exit"),
        engine.close_position(account, "AAPL", reason="dashboard_manual_exit"),
    )

    filled = [r for r in results if r.status == OrderStatus.FILLED]
    rejected = [r for r in results if r.status == OrderStatus.REJECTED]
    assert len(filled) == 1
    assert len(rejected) == 1
    assert rejected[0].message == "no open position to close"
    # Exactly one 10-share sell reached the broker -- not two.
    assert broker.submitted_sells == [10.0]
    assert store.get_position("acct1", "AAPL") == 0.0


@pytest.mark.asyncio
async def test_provider_close_signal_races_manual_close_without_double_selling(store, broker, engine):
    account = engine.routing.accounts["acct1"]
    await broker.place_order(Signal(source="test", symbol="AAPL", side=Side.BUY), account, 6.0, "AAPL")
    store.record_fill("acct1", "AAPL", Side.BUY, 6.0)

    close_signal = Signal(source="tradingview", symbol="AAPL", side=Side.CLOSE)
    results = await asyncio.gather(
        engine.handle_signal(close_signal),
        engine.close_position(account, "AAPL", reason="dashboard_manual_exit"),
    )
    manual_result: OrderResult = results[1]
    provider_results: list[OrderResult] = results[0]

    # Confirms the provider-side close actually reached _resolve_and_submit_
    # plain_close (and so the same lock as the manual close) rather than
    # destinations_for() silently returning no destinations.
    assert len(provider_results) == 1

    all_results = provider_results + [manual_result]
    filled = [r for r in all_results if r.status == OrderStatus.FILLED]
    rejected = [r for r in all_results if r.status == OrderStatus.REJECTED]
    assert len(filled) == 1
    assert len(rejected) == 1
    assert rejected[0].message == "no open position to close"
    assert broker.submitted_sells == [6.0]
    assert store.get_position("acct1", "AAPL") == 0.0
