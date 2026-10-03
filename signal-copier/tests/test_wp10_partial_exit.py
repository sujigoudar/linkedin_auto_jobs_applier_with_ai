"""WP-10 (D-05/D-11): partial exits honour the quantity and reduce_fraction.

Fix:
1. In _resolve_close (plain path): honour signal.quantity or signal.reduce_fraction;
   cap at position, reject when requested <= 0
2. In _handle_managed_close: compute requested quantity against confirmed_owned_quantity,
   cap at available, pass to request_exit
3. In on_price_update (targets): size target as reduce_fraction * confirmed_owned_quantity

Findings D-05: A CLOSE signal's explicit quantity is ignored
Findings D-11: reduce_fraction is a fraction of PLANNED, not owned, quantity
"""
import pytest

from app.brokers.paper import PaperBroker
from app.db import SignalStore
from app.engine import SignalCopierEngine
from app.lifecycle.manager import PositionLifecycleManager
from app.models import (
    DestinationAccount,
    OrderStatus,
    Side,
    Signal,
)
from app.routing import RoutingConfig, RoutingRule


@pytest.fixture
def store(tmp_path):
    return SignalStore(tmp_path / "test.db")


@pytest.fixture
def broker():
    return PaperBroker()


@pytest.fixture
def engine(store, broker):
    # WP-08: SELL on flat account requires allow_short=True
    # PaperBroker has balance capability, so no capital ceiling needed
    account = DestinationAccount(
        account_id="acct1",
        broker="paper",
        managed_lifecycle=False,  # plain account for close tests
        allow_short=True,
    )
    routing = RoutingConfig(
        rules=[RoutingRule(source="tradingview", destinations=["acct1"])],
        accounts={"acct1": account},
    )
    return SignalCopierEngine(routing=routing, brokers={"paper": broker}, store=store)


@pytest.fixture
def managed_engine(store, broker):
    """Engine with managed lifecycle enabled."""
    # WP-08: SELL on flat account requires allow_short=True
    # PaperBroker has balance capability, so no capital ceiling needed
    account = DestinationAccount(
        account_id="acct1",
        broker="paper",
        managed_lifecycle=True,
        allow_short=True,
    )
    routing = RoutingConfig(
        rules=[RoutingRule(source="tradingview", destinations=["acct1"])],
        accounts={"acct1": account},
    )
    lifecycle_manager = PositionLifecycleManager(
        brokers={"paper": broker},
        store=store,
    )
    return SignalCopierEngine(
        routing=routing,
        brokers={"paper": broker},
        store=store,
        lifecycle_manager=lifecycle_manager,
    )


@pytest.mark.asyncio
async def test_plain_close_with_explicit_quantity(store, broker, engine):
    """Plain account: CLOSE with explicit quantity should close that amount, not full position."""
    # Entry: BUY 10
    entry = Signal(source="tradingview", symbol="BTC", side=Side.BUY, quantity=10.0)
    await engine.handle_signal(entry)
    assert store.get_position("acct1", "BTC") == 10.0

    # Close with qty 4: should sell only 4
    close = Signal(source="tradingview", symbol="BTC", side=Side.CLOSE, quantity=4.0)
    result = await engine.handle_signal(close)

    assert result[0].status == OrderStatus.FILLED
    assert result[0].filled_quantity == 4.0
    assert store.get_position("acct1", "BTC") == 6.0  # 10 - 4


@pytest.mark.asyncio
async def test_plain_close_with_reduce_fraction(store, broker, engine):
    """Plain account: CLOSE with reduce_fraction should close that fraction of position."""
    # Entry: BUY 10
    entry = Signal(source="tradingview", symbol="BTC", side=Side.BUY, quantity=10.0)
    await engine.handle_signal(entry)
    assert store.get_position("acct1", "BTC") == 10.0

    # Close with reduce_fraction 0.5: should sell 50% = 5
    close = Signal(
        source="tradingview",
        symbol="BTC",
        side=Side.CLOSE,
        reduce_fraction=0.5,
    )
    result = await engine.handle_signal(close)

    assert result[0].status == OrderStatus.FILLED
    assert result[0].filled_quantity == 5.0
    assert store.get_position("acct1", "BTC") == 5.0  # 10 - 5


@pytest.mark.asyncio
async def test_plain_close_quantity_capped_at_position(store, broker, engine):
    """Plain account: CLOSE qty > position should cap at position."""
    # Entry: BUY 10
    entry = Signal(source="tradingview", symbol="BTC", side=Side.BUY, quantity=10.0)
    await engine.handle_signal(entry)

    # Close with qty 50 (more than position): should cap to 10
    close = Signal(source="tradingview", symbol="BTC", side=Side.CLOSE, quantity=50.0)
    result = await engine.handle_signal(close)

    assert result[0].status == OrderStatus.FILLED
    assert result[0].filled_quantity == 10.0  # capped at position
    assert store.get_position("acct1", "BTC") == 0.0


@pytest.mark.asyncio
async def test_plain_close_short_with_quantity(store, broker, engine):
    """Plain account: CLOSE on short position with explicit quantity."""
    # Entry: SELL 10 (short)
    entry = Signal(source="tradingview", symbol="BTC", side=Side.SELL, quantity=10.0)
    await engine.handle_signal(entry)
    assert store.get_position("acct1", "BTC") == -10.0

    # Close with qty 3: should buy back 3
    close = Signal(source="tradingview", symbol="BTC", side=Side.CLOSE, quantity=3.0)
    result = await engine.handle_signal(close)

    assert result[0].status == OrderStatus.FILLED
    assert result[0].filled_quantity == 3.0
    # Position was -10, closing 3 means: -10 + 3 = -7
    assert store.get_position("acct1", "BTC") == -7.0


@pytest.mark.asyncio
async def test_plain_close_invalid_quantity_rejected(store, broker, engine):
    """Plain account: CLOSE with qty <= 0 should be rejected."""
    # Entry: BUY 10
    entry = Signal(source="tradingview", symbol="BTC", side=Side.BUY, quantity=10.0)
    await engine.handle_signal(entry)

    # Close with qty 0: should reject
    close = Signal(source="tradingview", symbol="BTC", side=Side.CLOSE, quantity=0.0)
    result = await engine.handle_signal(close)

    assert result[0].status == OrderStatus.REJECTED
    assert "invalid" in result[0].message.lower()
    assert store.get_position("acct1", "BTC") == 10.0  # unchanged


@pytest.mark.asyncio
async def test_managed_close_with_explicit_quantity(managed_engine, broker, store):
    """Managed account: CLOSE with explicit quantity should request that amount."""
    # Entry: BUY 10 with stop-loss (managed accounts require a stop)
    entry = Signal(
        source="tradingview",
        symbol="BTC",
        side=Side.BUY,
        quantity=10.0,
        price=100.0,
        stop_loss=95.0,
    )
    result = await managed_engine.handle_signal(entry)
    assert result[0].status == OrderStatus.FILLED, f"Entry failed: {result[0].message}"

    lifecycle = managed_engine.lifecycle_manager.get_lifecycle("acct1", "BTC")
    assert lifecycle is not None
    assert lifecycle.confirmed_owned_quantity == 10.0
    # Stop should be resting at full position
    assert len(broker._stop_orders) == 1
    initial_stop_qty = list(broker._stop_orders.values())[0].quantity
    assert initial_stop_qty == 10.0

    # Close with qty 4: should request exit of 4, stop should be resized to 6
    close = Signal(source="tradingview", symbol="BTC", side=Side.CLOSE, quantity=4.0)
    result = await managed_engine.handle_signal(close)

    assert result[0].status == OrderStatus.FILLED
    assert result[0].filled_quantity == 4.0
    assert store.get_position("acct1", "BTC") == 6.0
    # Stop should be resized to 6
    assert len(broker._stop_orders) == 1
    assert list(broker._stop_orders.values())[0].quantity == 6.0


@pytest.mark.asyncio
async def test_managed_close_with_reduce_fraction(managed_engine, broker, store):
    """Managed account: CLOSE with reduce_fraction should request that fraction of owned."""
    # Entry: BUY 10 with stop-loss
    entry = Signal(
        source="tradingview",
        symbol="BTC",
        side=Side.BUY,
        quantity=10.0,
        price=100.0,
        stop_loss=95.0,
    )
    await managed_engine.handle_signal(entry)

    lifecycle = managed_engine.lifecycle_manager.get_lifecycle("acct1", "BTC")
    assert lifecycle is not None
    assert lifecycle.confirmed_owned_quantity == 10.0

    # Close with reduce_fraction 0.3: should request exit of 3
    close = Signal(
        source="tradingview",
        symbol="BTC",
        side=Side.CLOSE,
        reduce_fraction=0.3,
    )
    result = await managed_engine.handle_signal(close)

    assert result[0].status == OrderStatus.FILLED
    assert result[0].filled_quantity == 3.0
    assert store.get_position("acct1", "BTC") == 7.0
    # Stop should be resized to 7
    assert len(broker._stop_orders) == 1
    assert list(broker._stop_orders.values())[0].quantity == 7.0


@pytest.mark.asyncio
async def test_managed_close_quantity_capped_at_available(managed_engine, broker, store):
    """Managed account: CLOSE qty > available should cap at available."""
    # Entry: BUY 10 with stop-loss
    entry = Signal(
        source="tradingview",
        symbol="BTC",
        side=Side.BUY,
        quantity=10.0,
        price=100.0,
        stop_loss=95.0,
    )
    await managed_engine.handle_signal(entry)

    # Close with qty 50 (more than available): should cap to 10
    close = Signal(source="tradingview", symbol="BTC", side=Side.CLOSE, quantity=50.0)
    result = await managed_engine.handle_signal(close)

    assert result[0].status == OrderStatus.FILLED
    assert result[0].filled_quantity == 10.0  # capped at available
    assert store.get_position("acct1", "BTC") == 0.0


@pytest.mark.asyncio
async def test_plain_close_no_quantity_full_flatten(store, broker, engine):
    """Plain account: CLOSE without quantity/fraction should flatten entire position."""
    # Entry: BUY 10
    entry = Signal(source="tradingview", symbol="BTC", side=Side.BUY, quantity=10.0)
    await engine.handle_signal(entry)

    # Close with no qty/fraction: should close all
    close = Signal(source="tradingview", symbol="BTC", side=Side.CLOSE)
    result = await engine.handle_signal(close)

    assert result[0].status == OrderStatus.FILLED
    assert result[0].filled_quantity == 10.0
    assert store.get_position("acct1", "BTC") == 0.0


@pytest.mark.asyncio
async def test_managed_close_no_quantity_full_flatten(managed_engine, broker, store):
    """Managed account: CLOSE without quantity/fraction should flatten entire position."""
    # Entry: BUY 10 with stop-loss
    entry = Signal(
        source="tradingview",
        symbol="BTC",
        side=Side.BUY,
        quantity=10.0,
        price=100.0,
        stop_loss=95.0,
    )
    await managed_engine.handle_signal(entry)

    # Close with no qty/fraction: should close all
    close = Signal(source="tradingview", symbol="BTC", side=Side.CLOSE)
    result = await managed_engine.handle_signal(close)

    assert result[0].status == OrderStatus.FILLED
    assert result[0].filled_quantity == 10.0
    assert store.get_position("acct1", "BTC") == 0.0
