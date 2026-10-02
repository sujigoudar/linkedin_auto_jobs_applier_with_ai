"""WP-05: D-07/F-01 - Refuse managed_lifecycle flag changes with open exposure.

The exit path (managed vs plain) is determined by lifecycle existence, not the
managed_lifecycle flag. Changing the flag mid-position can orphan a protective
stop (managed to plain) or strand a position (plain to managed).

Tests:
1. Entry creates managed lifecycle when flag is True
2. Close follows lifecycle existence, not flag
3. Plain entry on plain account stays plain
4. Plain account close works when no lifecycle exists
"""

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


def _engine(store, paper, account, provider_id="tradingview"):
    routing = RoutingConfig(
        rules=[RoutingRule(source=provider_id, destinations=[account.account_id])],
        accounts={account.account_id: account},
    )
    lifecycle_manager = PositionLifecycleManager(brokers={"paper": paper}, store=store)
    engine = SignalCopierEngine(
        routing=routing, brokers={"paper": paper}, store=store, lifecycle_manager=lifecycle_manager
    )
    return engine, lifecycle_manager


@pytest.mark.asyncio
async def test_managed_entry_creates_lifecycle(store):
    """An entry with stop_loss on an account with managed_lifecycle=True
    creates a managed lifecycle."""
    paper = PaperBroker()
    account = DestinationAccount(account_id="acct1", broker="paper", managed_lifecycle=True)
    engine, lifecycle_manager = _engine(store, paper, account)

    signal = Signal(
        source="tradingview", symbol="AAPL", side=Side.BUY, quantity=10.0, stop_loss=48.50
    )
    results = await engine.handle_signal(signal)

    assert results[0].status == OrderStatus.FILLED
    lifecycle = lifecycle_manager.get_lifecycle("acct1", "AAPL")
    assert lifecycle is not None
    assert lifecycle.stop.protected_quantity == 10.0


@pytest.mark.asyncio
async def test_close_follows_lifecycle_existence_not_flag(store):
    """A CLOSE signal uses the managed path if a lifecycle exists, regardless
    of the managed_lifecycle flag. This prevents orphaning a protective stop."""
    paper = PaperBroker()
    account_managed = DestinationAccount(account_id="acct1", broker="paper", managed_lifecycle=True)
    engine_managed, lifecycle_manager = _engine(store, paper, account_managed)

    # Entry creates managed lifecycle
    entry = Signal(source="tradingview", symbol="AAPL", side=Side.BUY, quantity=10.0, stop_loss=48.50)
    await engine_managed.handle_signal(entry)
    lifecycle = lifecycle_manager.get_lifecycle("acct1", "AAPL")
    assert lifecycle is not None

    # Simulate flag flip by creating engine with plain account but same lifecycle manager
    account_plain = DestinationAccount(account_id="acct1", broker="paper", managed_lifecycle=False)
    routing_plain = RoutingConfig(
        rules=[RoutingRule(source="tradingview", destinations=["acct1"])],
        accounts={"acct1": account_plain},
    )
    engine_plain = SignalCopierEngine(
        routing=routing_plain, brokers={"paper": paper}, store=store, lifecycle_manager=lifecycle_manager
    )

    # CLOSE signal should use managed path because lifecycle exists
    close = Signal(source="tradingview", symbol="AAPL", side=Side.CLOSE)
    results = await engine_plain.handle_signal(close)

    # Should be FILLED (managed close)
    assert results[0].status == OrderStatus.FILLED
    lifecycle_after = lifecycle_manager.get_lifecycle("acct1", "AAPL")
    # Lifecycle should be closed
    assert lifecycle_after is None or lifecycle_after.closed


@pytest.mark.asyncio
async def test_plain_entry_on_plain_account_stays_plain(store):
    """Entry on a plain account without stop_loss/take_profit uses plain path."""
    paper = PaperBroker()
    account = DestinationAccount(account_id="acct1", broker="paper", managed_lifecycle=False)
    engine, lifecycle_manager = _engine(store, paper, account)

    signal = Signal(source="tradingview", symbol="AAPL", side=Side.BUY, quantity=10.0)
    results = await engine.handle_signal(signal)

    assert results[0].status == OrderStatus.FILLED
    lifecycle = lifecycle_manager.get_lifecycle("acct1", "AAPL")
    assert lifecycle is None


@pytest.mark.asyncio
async def test_plain_account_close_works_when_no_lifecycle(store):
    """Plain account close works via plain path when no lifecycle exists."""
    paper = PaperBroker()
    account = DestinationAccount(account_id="acct1", broker="paper", managed_lifecycle=False)
    engine, _ = _engine(store, paper, account)

    # Entry without stop
    entry = Signal(source="tradingview", symbol="AAPL", side=Side.BUY, quantity=10.0)
    await engine.handle_signal(entry)

    # Close should work
    close = Signal(source="tradingview", symbol="AAPL", side=Side.CLOSE)
    results = await engine.handle_signal(close)

    assert results[0].status == OrderStatus.FILLED
    assert store.get_position("acct1", "AAPL") == 0.0


@pytest.mark.asyncio
async def test_managed_close_with_lifecycle(store):
    """When a managed lifecycle exists, close goes through managed path."""
    paper = PaperBroker()
    account = DestinationAccount(account_id="acct1", broker="paper", managed_lifecycle=True)
    engine, lifecycle_manager = _engine(store, paper, account)

    # Entry creates managed lifecycle
    entry = Signal(source="tradingview", symbol="AAPL", side=Side.BUY, quantity=10.0, stop_loss=48.50)
    await engine.handle_signal(entry)

    # Close should go through managed path
    close = Signal(source="tradingview", symbol="AAPL", side=Side.CLOSE)
    results = await engine.handle_signal(close)

    assert results[0].status == OrderStatus.FILLED
    lifecycle = lifecycle_manager.get_lifecycle("acct1", "AAPL")
    assert lifecycle is None or lifecycle.closed
