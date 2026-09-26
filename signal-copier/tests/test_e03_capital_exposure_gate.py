"""E03 (bounded): app/capital_allocator.py's notional-exposure ceiling,
wired into both the plain-account and managed_lifecycle entry paths in
app/engine.py.
"""
import asyncio

import pytest

from app.brokers.paper import PaperBroker
from app.db import SignalStore
from app.engine import SignalCopierEngine
from app.lifecycle.manager import PositionLifecycleManager
from app.models import DestinationAccount, OrderStatus, Side, Signal
from app.routing import RoutingConfig, RoutingRule

SOURCE = "tradingview"
SYMBOL = "AAPL"


def _engine(store: SignalStore, account: DestinationAccount, broker: PaperBroker) -> SignalCopierEngine:
    routing = RoutingConfig(rules=[RoutingRule(source=SOURCE, destinations=["acct1"])], accounts={"acct1": account})
    lifecycle_manager = PositionLifecycleManager(brokers={"paper": broker}, store=store)
    return SignalCopierEngine(routing=routing, brokers={"paper": broker}, store=store, lifecycle_manager=lifecycle_manager)


@pytest.mark.asyncio
async def test_plain_account_entry_rejected_when_it_would_exceed_the_ceiling(tmp_path):
    store = SignalStore(tmp_path / "test.db")
    broker = PaperBroker()
    account = DestinationAccount(account_id="acct1", broker="paper", max_notional_exposure=500.0)
    engine = _engine(store, account, broker)

    signal = Signal(source=SOURCE, symbol=SYMBOL, side=Side.BUY, quantity=10.0, price=100.0)  # notional 1000 > 500
    results = await engine.handle_signal(signal)

    assert results[0].status == OrderStatus.REJECTED
    assert "notional exposure ceiling" in results[0].message
    assert broker.fills == []  # never reached the broker


@pytest.mark.asyncio
async def test_plain_account_entry_admitted_within_the_ceiling(tmp_path):
    store = SignalStore(tmp_path / "test.db")
    broker = PaperBroker()
    account = DestinationAccount(account_id="acct1", broker="paper", max_notional_exposure=2000.0)
    engine = _engine(store, account, broker)

    signal = Signal(source=SOURCE, symbol=SYMBOL, side=Side.BUY, quantity=10.0, price=100.0)  # notional 1000 <= 2000
    results = await engine.handle_signal(signal)

    assert results[0].status == OrderStatus.FILLED


@pytest.mark.asyncio
async def test_second_entry_rejected_once_confirmed_exposure_already_fills_the_ceiling(tmp_path):
    store = SignalStore(tmp_path / "test.db")
    broker = PaperBroker()
    account = DestinationAccount(account_id="acct1", broker="paper", max_notional_exposure=1500.0)
    engine = _engine(store, account, broker)

    first = Signal(source=SOURCE, symbol=SYMBOL, side=Side.BUY, quantity=10.0, price=100.0)  # notional 1000
    await engine.handle_signal(first)

    second = Signal(source=SOURCE, symbol="MSFT", side=Side.BUY, quantity=10.0, price=100.0)  # would add 1000 -> 2000 > 1500
    results = await engine.handle_signal(second)

    assert results[0].status == OrderStatus.REJECTED


@pytest.mark.asyncio
async def test_no_ceiling_configured_never_gates_anything(tmp_path):
    store = SignalStore(tmp_path / "test.db")
    broker = PaperBroker()
    account = DestinationAccount(account_id="acct1", broker="paper")  # max_notional_exposure=None
    engine = _engine(store, account, broker)

    signal = Signal(source=SOURCE, symbol=SYMBOL, side=Side.BUY, quantity=10_000.0, price=100.0)
    results = await engine.handle_signal(signal)

    assert results[0].status == OrderStatus.FILLED


@pytest.mark.asyncio
async def test_no_price_on_the_signal_skips_the_check_rather_than_guessing(tmp_path):
    store = SignalStore(tmp_path / "test.db")
    broker = PaperBroker()
    account = DestinationAccount(account_id="acct1", broker="paper", max_notional_exposure=1.0)
    engine = _engine(store, account, broker)

    signal = Signal(source=SOURCE, symbol=SYMBOL, side=Side.BUY, quantity=10.0)  # no price -- would obviously fail any real check
    results = await engine.handle_signal(signal)

    assert results[0].status == OrderStatus.FILLED


@pytest.mark.asyncio
async def test_managed_lifecycle_entry_rejected_when_it_would_exceed_the_ceiling(tmp_path):
    store = SignalStore(tmp_path / "test.db")
    broker = PaperBroker()
    account = DestinationAccount(account_id="acct1", broker="paper", managed_lifecycle=True, max_notional_exposure=500.0)
    engine = _engine(store, account, broker)

    signal = Signal(source=SOURCE, symbol=SYMBOL, side=Side.BUY, quantity=10.0, price=100.0, stop_loss=90.0)
    results = await engine.handle_signal(signal)

    assert results[0].status == OrderStatus.REJECTED
    assert "notional exposure ceiling" in results[0].message
    # A rejected admission must not leave a stray lifecycle plan registered.
    assert engine.lifecycle_manager.get_lifecycle("acct1", SYMBOL) is None


@pytest.mark.asyncio
async def test_concurrent_entries_for_the_same_account_cannot_both_exceed_the_ceiling(tmp_path):
    """The core E03 obligation, exercised through the real engine: two
    signals for the same account arriving concurrently, each individually
    under the ceiling but together over it, must not both be admitted."""
    store = SignalStore(tmp_path / "test.db")
    broker = PaperBroker()
    account = DestinationAccount(account_id="acct1", broker="paper", max_notional_exposure=1500.0)
    engine = _engine(store, account, broker)

    signal_a = Signal(source=SOURCE, symbol="AAPL", side=Side.BUY, quantity=10.0, price=100.0)  # notional 1000
    signal_b = Signal(source=SOURCE, symbol="MSFT", side=Side.BUY, quantity=10.0, price=100.0)  # notional 1000

    results_a, results_b = await asyncio.gather(engine.handle_signal(signal_a), engine.handle_signal(signal_b))

    statuses = sorted([results_a[0].status.value, results_b[0].status.value])
    assert statuses == [OrderStatus.FILLED.value, OrderStatus.REJECTED.value]
