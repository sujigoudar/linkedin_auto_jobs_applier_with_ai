"""WP-18 (D-01, C-07): Bracket child legs are tracked orders.

Test that native bracket orders (plain accounts with stop_loss and take_profit)
create child order rows in the database so they can be polled in reconciliation.
"""
import pytest

from app.models import AssetClass, DestinationAccount, OrderStatus, Side, Signal
from app.brokers.paper import PaperBroker
from app.db import SignalStore
from app.engine import SignalCopierEngine
from app.routing import RoutingConfig, RoutingRule


@pytest.mark.asyncio
async def test_bracket_entry_creates_child_orders(tmp_path):
    """Paper bracket entry creates three rows: parent FILLED + stop PENDING + tp PENDING."""
    # Setup
    db_path = tmp_path / "test.db"
    store = SignalStore(db_path)
    broker = PaperBroker()
    brokers = {"paper": broker}

    account = DestinationAccount(
        account_id="test_account",
        broker="paper",
        managed_lifecycle=False,  # plain account
    )
    store.upsert_config_account(account.account_id, account.broker)

    routing = RoutingConfig(
        rules=[RoutingRule(source="test", destinations=["test_account"])],
        accounts={"test_account": account}
    )
    engine = SignalCopierEngine(routing=routing, brokers=brokers, store=store)

    # Create a bracket entry signal
    signal = Signal(
        source="test",
        symbol="AAPL",
        side=Side.BUY,
        asset_class=AssetClass.EQUITY,
        quantity=10.0,
        price=100.0,
        stop_loss=95.0,
        take_profit=105.0,
    )

    # Route the signal
    results = await engine.handle_signal(signal)

    # Should have one result (the entry)
    assert len(results) == 1
    assert results[0].status == OrderStatus.FILLED
    assert results[0].child_order_ids is not None
    assert "stop" in results[0].child_order_ids
    assert "take_profit" in results[0].child_order_ids

    # Check database rows
    with store._connect() as conn:
        entry_rows = conn.execute(
            "SELECT id, status, purpose, family_id FROM orders WHERE signal_id = ? AND purpose = 'entry'",
            (signal.id,),
        ).fetchall()

    # Should have 1 entry row
    assert len(entry_rows) == 1
    entry_id, entry_status, entry_purpose, entry_family = entry_rows[0]
    assert entry_status == OrderStatus.FILLED.value
    assert entry_purpose == "entry"
    assert entry_family == signal.id

    # Check child order rows (use family_id to find them)
    with store._connect() as conn:
        child_rows = conn.execute(
            "SELECT id, status, purpose, family_id, broker_order_id FROM orders WHERE family_id = ? AND purpose IN ('stop_exit', 'target_exit')",
            (signal.id,),
        ).fetchall()

    # Should have 2 child rows (stop + take_profit)
    assert len(child_rows) == 2

    # Check that both child rows share the same family_id and have correct purposes
    purposes = set()
    for _child_id, child_status, child_purpose, child_family, broker_order_id in child_rows:
        assert child_family == signal.id
        assert child_status == OrderStatus.PENDING.value
        purposes.add(child_purpose)
        # Verify broker_order_id exists
        assert broker_order_id is not None

    assert purposes == {"stop_exit", "target_exit"}


@pytest.mark.asyncio
async def test_stop_fill_closes_position_and_cancels_sibling(tmp_path):
    """When a child stop fills, position goes to zero and take_profit is cancelled."""
    # Setup
    db_path = tmp_path / "test.db"
    store = SignalStore(db_path)
    broker = PaperBroker()
    brokers = {"paper": broker}

    from app.reconciliation import OrderReconciler
    reconciler = OrderReconciler(store, brokers)

    account = DestinationAccount(
        account_id="test_account",
        broker="paper",
        managed_lifecycle=False,
    )
    store.upsert_config_account(account.account_id, account.broker)

    routing = RoutingConfig(
        rules=[RoutingRule(source="test", destinations=["test_account"])],
        accounts={"test_account": account}
    )
    engine = SignalCopierEngine(routing=routing, brokers=brokers, store=store)

    # Create a bracket entry signal
    signal = Signal(
        source="test",
        symbol="AAPL",
        side=Side.BUY,
        asset_class=AssetClass.EQUITY,
        quantity=10.0,
        price=100.0,
        stop_loss=95.0,
        take_profit=105.0,
    )

    # Route the signal
    results = await engine.handle_signal(signal)
    assert results[0].status == OrderStatus.FILLED

    # Verify initial position
    position = store.get_position("test_account", "AAPL")
    assert position == 10.0

    # Simulate the stop order filling at 95
    broker.simulate_price("AAPL", 95.0)

    # Reconcile - should detect the stop fill
    corrected = await reconciler.reconcile_once()
    assert corrected > 0

    # Position should be corrected to 0
    position = store.get_position("test_account", "AAPL")
    assert position == 0.0

    # Check that the stop order is marked FILLED
    with store._connect() as conn:
        stop_rows = conn.execute(
            "SELECT status FROM orders WHERE purpose = 'stop_exit'",
        ).fetchall()
    assert len(stop_rows) > 0
    assert stop_rows[0][0] == OrderStatus.FILLED.value

    # Check that the take_profit order is cancelled (or rejected)
    with store._connect() as conn:
        tp_rows = conn.execute(
            "SELECT status FROM orders WHERE purpose = 'target_exit'",
        ).fetchall()
    assert len(tp_rows) > 0
    assert tp_rows[0][0] in (OrderStatus.REJECTED.value,)
