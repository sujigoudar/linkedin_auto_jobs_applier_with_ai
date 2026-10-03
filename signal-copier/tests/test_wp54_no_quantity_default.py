"""WP-54: Remove 1.0 default quantity (R0-B02).

Test that entries without quantity are rejected unless the account has
fixed_quantity configured.

Audit finding B-02: "A signal with no quantity trades 1.0 unit of whatever
the instrument is: 1 share, 1 BTC (~$65k), 1 lot (100k EUR), 1 ES contract
(~$250k)."

Fix: Reject entries without quantity unless account has fixed_quantity set.
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


def _engine(store, broker, account):
    routing = RoutingConfig(
        rules=[RoutingRule(source="tradingview", destinations=[account.account_id])],
        accounts={account.account_id: account},
    )
    lifecycle_manager = PositionLifecycleManager(
        brokers={account.broker: broker}, store=store
    )
    engine = SignalCopierEngine(
        routing=routing,
        brokers={account.broker: broker},
        store=store,
        lifecycle_manager=lifecycle_manager,
    )
    return engine


@pytest.mark.asyncio
async def test_no_quantity_without_fixed_quantity_rejected(store):
    """Entry with no quantity is rejected when account has no fixed_quantity."""
    broker = PaperBroker()
    account = DestinationAccount(
        account_id="no_fixed_qty",
        broker="paper",
        # No fixed_quantity set (defaults to None)
    )
    engine = _engine(store, broker, account)

    # Signal with no quantity
    signal = Signal(
        source="tradingview",
        symbol="AAPL",
        side=Side.BUY,
        quantity=None,  # No quantity provided
        price=150.0,
    )
    results = await engine.handle_signal(signal)

    # Should be rejected
    assert results[0].status == OrderStatus.REJECTED
    assert "quantity" in results[0].message.lower()


@pytest.mark.asyncio
async def test_no_quantity_with_fixed_quantity_accepted(store):
    """Entry with no quantity is accepted when account has fixed_quantity."""
    broker = PaperBroker()
    account = DestinationAccount(
        account_id="with_fixed_qty",
        broker="paper",
        fixed_quantity=10.0,  # Fixed quantity set
    )
    engine = _engine(store, broker, account)

    # Signal with no quantity
    signal = Signal(
        source="tradingview",
        symbol="AAPL",
        side=Side.BUY,
        quantity=None,  # No quantity provided
        price=150.0,
    )
    results = await engine.handle_signal(signal)

    # Should succeed using fixed_quantity
    assert results[0].status == OrderStatus.FILLED


@pytest.mark.asyncio
async def test_explicit_quantity_accepted(store):
    """Entry with explicit quantity is always accepted."""
    broker = PaperBroker()
    account = DestinationAccount(
        account_id="any_account",
        broker="paper",
        # No fixed_quantity
    )
    engine = _engine(store, broker, account)

    # Signal with explicit quantity
    signal = Signal(
        source="tradingview",
        symbol="AAPL",
        side=Side.BUY,
        quantity=5.0,  # Explicit quantity
        price=150.0,
    )
    results = await engine.handle_signal(signal)

    # Should succeed
    assert results[0].status == OrderStatus.FILLED
