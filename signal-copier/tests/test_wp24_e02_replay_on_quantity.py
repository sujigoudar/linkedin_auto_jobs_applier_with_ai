"""WP-24 / E-02: replay on quantity, not status.

When a partially-filled order is then cancelled (REJECTED status), the
filled portion is applied to positions but the row is invisible to all
replay queries because they filter by status = 'filled'. This fix changes
all replay queries to select on filled_quantity > 0 (or applied_execution_delta
!= 0) regardless of terminal status.

The scenario: BUY 100 shares → PENDING; broker later reports canceled with
filled_quantity=30. After reconciliation:
- positions.net_quantity = 30.0 (correct)
- BUT order.status = 'REJECTED' (not 'filled')
- Before fix: list_filled_orders_chronological() returns empty (invisible)
- After fix: list_filled_orders_chronological() returns the partial fill

This affects:
- app/provider_value.py (provider attribution)
- app/trade_episode.py (episode grouping)
- app/economics.py (P&L calculation)
- app/export_events.py (commercial export)
"""
import pytest

from app.brokers.paper import PaperBroker
from app.db import SignalStore
from app.engine import SignalCopierEngine
from app.models import DestinationAccount, OrderResult, OrderStatus, Side, Signal
from app.reconciliation import OrderReconciler
from app.routing import RoutingConfig, RoutingRule


@pytest.fixture
def store(tmp_path):
    return SignalStore(tmp_path / "test.db")


class _ControllablePendingBroker(PaperBroker):
    """A PaperBroker whose place_order reports PENDING, and whose
    get_order_status returns a scripted terminal result once told to."""

    def __init__(self):
        super().__init__()
        self.next_broker_order_id = "order-1"
        self._status_by_order_id: dict[str, OrderResult] = {}

    async def place_order(self, signal, account, quantity, symbol):
        broker_order_id = self.next_broker_order_id
        self.next_broker_order_id = f"order-{int(broker_order_id.split('-')[1]) + 1}"
        return OrderResult(
            account_id=account.account_id,
            status=OrderStatus.PENDING,
            signal_id=signal.id,
            broker_order_id=broker_order_id,
            message="submitted, awaiting fill",
        )

    async def get_order_status(self, account, broker_order_id):
        return self._status_by_order_id.get(broker_order_id)

    def script_terminal_result(
        self, broker_order_id: str, *, status: OrderStatus, filled_quantity: float
    ):
        """Simulate a later status poll returning this result."""
        self._status_by_order_id[broker_order_id] = OrderResult(
            account_id="test-account",
            status=status,
            signal_id="",
            broker_order_id=broker_order_id,
            filled_quantity=filled_quantity,
            filled_price=150.0,
            message="done",
        )


def _build_engine_and_account(store, broker):
    """Create engine with routing config for testing."""
    account = DestinationAccount(account_id="test-account", broker="paper")
    routing = RoutingConfig(
        rules=[RoutingRule(source="test_source", destinations=["test-account"])],
        accounts={"test-account": account},
    )
    engine = SignalCopierEngine(routing=routing, brokers={"paper": broker}, store=store)
    return engine, account


@pytest.mark.asyncio
async def test_e02_partially_filled_cancelled_order_visible_in_replay(store):
    """E-02: A partially-filled order that is then cancelled should be
    visible in list_filled_orders_chronological (and other replay queries)
    even though its status is REJECTED, because filled_quantity > 0."""

    broker = _ControllablePendingBroker()
    engine, account = _build_engine_and_account(store, broker)

    # Place order via engine (reports PENDING initially)
    await engine.handle_signal(
        Signal(
            id="entry-1",
            source="test_source",
            asset_class="equity",
            symbol="AAPL",
            side=Side.BUY,
            quantity=100.0,
            price=150.0,
        )
    )

    # Verify order is PENDING with no fill yet
    orders = store.list_pending_orders()
    assert len(orders) == 1
    order = orders[0]
    broker_order_id = order["broker_order_id"]

    # Simulate broker confirming: REJECTED with 30 shares filled (partial fill)
    broker.script_terminal_result(
        broker_order_id, status=OrderStatus.REJECTED, filled_quantity=30.0
    )

    # Reconciliation picks up the REJECTED status with partial fill
    reconciler = OrderReconciler(store, {"paper": broker})
    corrected = await reconciler.reconcile_once()
    assert corrected == 1

    # After reconciliation:
    # - Order status should be REJECTED
    # - filled_quantity should be 30
    recent_orders = store.list_recent_orders(account_id=account.account_id)
    assert len(recent_orders) == 1
    order = recent_orders[0]
    assert order["status"] == "rejected"
    assert order["filled_quantity"] == 30.0

    # Position should reflect the 30 filled shares
    pos = store.get_position(account.account_id, "AAPL")
    assert pos == 30.0

    # BEFORE FIX: list_filled_orders_chronological would return empty list
    # AFTER FIX: should include the partial fill despite status = 'rejected'
    filled_orders = store.list_filled_orders_chronological(account.account_id)
    assert len(filled_orders) == 1, (
        "E-02: Partially-filled rejected order should be visible in replay "
        "queries (checking filled_quantity > 0, not just status = 'filled')"
    )
    assert filled_orders[0]["filled_quantity"] == 30.0
    assert filled_orders[0]["symbol"] == "AAPL"


@pytest.mark.asyncio
async def test_e02_with_signal_chronological_replay(store):
    """E-02: Partially-filled rejected orders should also be visible in
    list_filled_orders_with_signal_chronological (used by provider_value.py)."""

    broker = _ControllablePendingBroker()
    engine, account = _build_engine_and_account(store, broker)

    # Place order
    await engine.handle_signal(
        Signal(
            id="entry-2",
            source="test_source",
            asset_class="equity",
            symbol="MSFT",
            side=Side.BUY,
            quantity=50.0,
            price=300.0,
            analyst="test_analyst",
        )
    )

    orders = store.list_pending_orders()
    broker_order_id = orders[0]["broker_order_id"]

    # Simulate partial fill then cancellation
    broker.script_terminal_result(
        broker_order_id, status=OrderStatus.REJECTED, filled_quantity=20.0
    )

    reconciler = OrderReconciler(store, {"paper": broker})
    await reconciler.reconcile_once()

    # Check replay with signal info (used for provider attribution)
    filled_orders = store.list_filled_orders_with_signal_chronological()
    assert len(filled_orders) == 1
    order = filled_orders[0]
    assert order["filled_quantity"] == 20.0
    assert order["symbol"] == "MSFT"
    assert order["source"] == "test_source"
    assert order["analyst"] == "test_analyst"


@pytest.mark.asyncio
async def test_e02_zero_fill_rejected_order_excluded(store):
    """E-02: A REJECTED order with zero fill should NOT be included in
    replay queries (only those with filled_quantity > 0)."""

    broker = _ControllablePendingBroker()
    engine, account = _build_engine_and_account(store, broker)

    await engine.handle_signal(
        Signal(
            id="entry-3",
            source="test_source",
            asset_class="equity",
            symbol="GOOGL",
            side=Side.BUY,
            quantity=100.0,
        )
    )

    orders = store.list_pending_orders()
    broker_order_id = orders[0]["broker_order_id"]

    # Rejected with NO fill
    broker.script_terminal_result(
        broker_order_id, status=OrderStatus.REJECTED, filled_quantity=0.0
    )

    reconciler = OrderReconciler(store, {"paper": broker})
    await reconciler.reconcile_once()

    # Should NOT appear in filled orders
    filled_orders = store.list_filled_orders_chronological(account.account_id)
    assert len(filled_orders) == 0, (
        "E-02: REJECTED orders with zero fill should not appear in replay queries"
    )

    # But the order row should still exist with status = rejected
    recent = store.list_recent_orders(account_id=account.account_id)
    assert len(recent) == 1
    assert recent[0]["status"] == "rejected"
    assert recent[0]["filled_quantity"] == 0.0


@pytest.mark.asyncio
async def test_e02_with_timing_replay(store):
    """E-02: Partially-filled rejected orders should be visible in
    list_filled_orders_with_signal_timing (used by execution_quality.py)."""

    broker = _ControllablePendingBroker()
    engine, account = _build_engine_and_account(store, broker)

    await engine.handle_signal(
        Signal(
            id="entry-4",
            source="test_source",
            asset_class="equity",
            symbol="TSLA",
            side=Side.BUY,
            quantity=75.0,
        )
    )

    orders = store.list_pending_orders()
    broker_order_id = orders[0]["broker_order_id"]

    # Partial fill before cancellation
    broker.script_terminal_result(
        broker_order_id, status=OrderStatus.REJECTED, filled_quantity=45.0
    )

    reconciler = OrderReconciler(store, {"paper": broker})
    await reconciler.reconcile_once()

    # Check timing replay
    timing_orders = store.list_filled_orders_with_signal_timing(account.account_id)
    assert len(timing_orders) == 1
    assert timing_orders[0]["symbol"] == "TSLA"


@pytest.mark.asyncio
async def test_e02_with_reference_price_replay(store):
    """E-02: Partially-filled rejected orders should be visible in
    list_filled_orders_with_signal_reference_price (used for slippage
    calculation in account_economics_v2.py)."""

    broker = _ControllablePendingBroker()
    engine, account = _build_engine_and_account(store, broker)

    await engine.handle_signal(
        Signal(
            id="entry-5",
            source="test_source",
            asset_class="equity",
            symbol="AMZN",
            side=Side.BUY,
            quantity=60.0,
            price=180.0,
        )
    )

    orders = store.list_pending_orders()
    broker_order_id = orders[0]["broker_order_id"]

    # Partial fill before cancellation
    broker.script_terminal_result(
        broker_order_id, status=OrderStatus.REJECTED, filled_quantity=40.0
    )

    reconciler = OrderReconciler(store, {"paper": broker})
    await reconciler.reconcile_once()

    # Check reference price replay
    ref_price_orders = store.list_filled_orders_with_signal_reference_price(
        account.account_id
    )
    assert len(ref_price_orders) == 1
    order = ref_price_orders[0]
    assert order["filled_quantity"] == 40.0
    assert order["symbol"] == "AMZN"
    assert order["signal_price"] == 180.0  # Should include reference price
