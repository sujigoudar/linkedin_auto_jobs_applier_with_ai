"""A follow-up review of commit edd2b70 (which fixed the original
optimistic-fill double-count and the missing managed-entry protection)
found that the fix itself introduced a NEW double-count for managed
full-fills, still left a working partial fill completely unprotected
while its remainder stayed open, and left a separate pre-existing bug
(REJECTED wiping a confirmed partial fill to zero) unaddressed. This file
reproduces each finding through the real engine/store/lifecycle/reconciler
path and proves the fix for each.
"""
import pytest

from app.brokers.paper import PaperBroker
from app.db import SignalStore
from app.engine import SignalCopierEngine
from app.lifecycle.manager import PositionLifecycleManager
from app.lifecycle.models import ProtectionStatus
from app.models import DestinationAccount, OrderResult, OrderStatus, Side, Signal
from app.reconciliation import OrderReconciler
from app.routing import RoutingConfig, RoutingRule


@pytest.fixture
def store(tmp_path):
    return SignalStore(tmp_path / "test.db")


class _ControllablePendingBroker(PaperBroker):
    """Same shape as the broker in test_pending_fill_reconciliation_
    integration.py: place_order always reports PENDING with no fill yet;
    get_order_status returns whatever's been scripted for that order id,
    including a NON-terminal PENDING carrying partial-fill progress (the
    shape AlpacaBroker/IBKRBroker now report for a still-open,
    partially-filled order)."""

    def __init__(self):
        super().__init__()
        self.next_broker_order_id = "order-1"
        self._status_by_order_id: dict[str, OrderResult] = {}
        self.stop_order_ids_seen: list[str] = []

    async def place_order(self, signal, account, quantity, symbol):
        return OrderResult(
            account_id=account.account_id,
            status=OrderStatus.PENDING,
            signal_id=signal.id,
            broker_order_id=self.next_broker_order_id,
            filled_quantity=None,
            message="submitted, awaiting fill",
        )

    async def get_order_status(self, account, broker_order_id):
        return self._status_by_order_id.get(broker_order_id)

    def script(self, broker_order_id: str, *, status: OrderStatus, filled_quantity: float | None):
        self._status_by_order_id[broker_order_id] = OrderResult(
            account_id="doesn't matter", status=status, signal_id="", filled_quantity=filled_quantity, message="done"
        )

    async def place_protective_stop(self, account, symbol, quantity, stop_price, exit_side):
        result = await super().place_protective_stop(account, symbol, quantity, stop_price, exit_side)
        self.stop_order_ids_seen.append(result.broker_order_id)
        return result


def _managed_setup(store):
    broker = _ControllablePendingBroker()
    account = DestinationAccount(account_id="acct1", broker="paper", managed_lifecycle=True)
    routing = RoutingConfig(rules=[RoutingRule(source="tv", destinations=["acct1"])], accounts={"acct1": account})
    lifecycle_manager = PositionLifecycleManager(brokers={"paper": broker}, store=store)
    engine = SignalCopierEngine(
        routing=routing, brokers={"paper": broker}, store=store, lifecycle_manager=lifecycle_manager
    )
    reconciler = OrderReconciler(store, {"paper": broker}, lifecycle_manager=lifecycle_manager)
    return broker, lifecycle_manager, engine, reconciler


# --- Finding 1: managed full-fill was applied twice ---


@pytest.mark.asyncio
async def test_managed_full_fill_is_applied_exactly_once_not_twice(store):
    broker, lifecycle_manager, engine, reconciler = _managed_setup(store)

    await engine.handle_signal(Signal(source="tv", symbol="AAPL", side=Side.BUY, quantity=100.0, stop_loss=90.0))
    assert store.get_position("acct1", "AAPL") == 0.0  # nothing optimistic yet

    broker.script("order-1", status=OrderStatus.FILLED, filled_quantity=100.0)
    corrected = await reconciler.reconcile_once()
    assert corrected >= 1

    lifecycle = lifecycle_manager.get_lifecycle("acct1", "AAPL")
    assert store.get_position("acct1", "AAPL") == 100.0  # NOT 200
    assert lifecycle.confirmed_owned_quantity == 100.0
    assert lifecycle.stop.protected_quantity == 100.0

    # Repeating the observation must not re-apply anything -- the order row
    # is already terminal in the DB, and the pending_entry is already
    # resolved/cleared.
    corrected_again = await reconciler.reconcile_once()
    assert corrected_again == 0
    assert store.get_position("acct1", "AAPL") == 100.0
    assert lifecycle.confirmed_owned_quantity == 100.0


@pytest.mark.asyncio
async def test_managed_full_fill_survives_a_restart_without_duplicating(store):
    broker, lifecycle_manager, engine, reconciler = _managed_setup(store)
    await engine.handle_signal(Signal(source="tv", symbol="AAPL", side=Side.BUY, quantity=100.0, stop_loss=90.0))
    broker.script("order-1", status=OrderStatus.FILLED, filled_quantity=100.0)
    await reconciler.reconcile_once()
    assert store.get_position("acct1", "AAPL") == 100.0

    # Simulate a process restart: fresh manager + reconciler, seeded only
    # from persisted state.
    new_lifecycle_manager = PositionLifecycleManager(brokers={"paper": broker}, store=store)
    new_lifecycle_manager.restore_from_store()
    new_reconciler = OrderReconciler(store, {"paper": broker}, lifecycle_manager=new_lifecycle_manager)

    restored = new_lifecycle_manager.get_lifecycle("acct1", "AAPL")
    assert restored.confirmed_owned_quantity == 100.0
    assert restored.pending_entry is None

    corrected = await new_reconciler.reconcile_once()
    assert corrected == 0  # already resolved before the restart -- nothing left to do
    assert store.get_position("acct1", "AAPL") == 100.0


# --- Finding 2: working partial fills got no protection until terminal ---


@pytest.mark.asyncio
async def test_working_partial_fill_is_protected_while_remainder_stays_open(store):
    broker, lifecycle_manager, engine, reconciler = _managed_setup(store)
    await engine.handle_signal(Signal(source="tv", symbol="AAPL", side=Side.BUY, quantity=100.0, stop_loss=90.0))

    # 30 of 100 filled so far; order still open (PENDING, not terminal).
    broker.script("order-1", status=OrderStatus.PENDING, filled_quantity=30.0)
    await reconciler.reconcile_once()

    lifecycle = lifecycle_manager.get_lifecycle("acct1", "AAPL")
    assert lifecycle.pending_entry is not None  # remainder still open
    assert lifecycle.pending_entry.requested_quantity == 100.0
    assert lifecycle.confirmed_owned_quantity == 30.0
    assert lifecycle.stop.status == ProtectionStatus.STOP_CONFIRMED
    assert lifecycle.stop.protected_quantity == 30.0
    assert store.get_position("acct1", "AAPL") == 30.0
    first_stop_order_id = lifecycle.stop.broker_order_id

    # A repeated observation of the SAME 30 must be a no-op -- no duplicate
    # stop, no double-application to the tracked position.
    await reconciler.reconcile_once()
    assert len(broker.stop_order_ids_seen) == 1  # still only one stop ever placed
    assert store.get_position("acct1", "AAPL") == 30.0

    # 30 more fill (60 of 100 total, still open).
    broker.script("order-1", status=OrderStatus.PENDING, filled_quantity=60.0)
    await reconciler.reconcile_once()
    assert lifecycle.confirmed_owned_quantity == 60.0
    assert lifecycle.stop.protected_quantity == 60.0
    assert store.get_position("acct1", "AAPL") == 60.0
    # PaperBroker's replace_stop_quantity resizes the SAME resting stop
    # order rather than creating a second one alongside it.
    assert lifecycle.stop.broker_order_id == first_stop_order_id
    assert len(broker._stop_orders) == 1

    # Remainder (40) confirmed fully filled -- terminal.
    broker.script("order-1", status=OrderStatus.FILLED, filled_quantity=100.0)
    await reconciler.reconcile_once()
    assert lifecycle.pending_entry is None
    assert lifecycle.confirmed_owned_quantity == 100.0
    assert lifecycle.stop.protected_quantity == 100.0
    assert store.get_position("acct1", "AAPL") == 100.0
    assert len(broker._stop_orders) == 1  # still one stop, just resized again


@pytest.mark.asyncio
async def test_working_partial_fill_then_remainder_cancelled_protects_only_the_partial(store):
    broker, lifecycle_manager, engine, reconciler = _managed_setup(store)
    await engine.handle_signal(Signal(source="tv", symbol="AAPL", side=Side.BUY, quantity=100.0, stop_loss=90.0))

    broker.script("order-1", status=OrderStatus.PENDING, filled_quantity=30.0)
    await reconciler.reconcile_once()
    assert store.get_position("acct1", "AAPL") == 30.0

    # Remainder confirmed cancelled -- terminal, same 30 filled.
    broker.script("order-1", status=OrderStatus.REJECTED, filled_quantity=30.0)
    await reconciler.reconcile_once()

    lifecycle = lifecycle_manager.get_lifecycle("acct1", "AAPL")
    assert lifecycle.pending_entry is None
    assert lifecycle.confirmed_owned_quantity == 30.0
    assert lifecycle.stop.protected_quantity == 30.0
    assert store.get_position("acct1", "AAPL") == 30.0  # NOT re-applied a second time


# --- Finding 3: plain-account REJECTED wiped a confirmed partial fill ---


@pytest.fixture
def plain_setup(store):
    broker = _ControllablePendingBroker()
    account = DestinationAccount(account_id="acct1", broker="paper")
    routing = RoutingConfig(rules=[RoutingRule(source="tv", destinations=["acct1"])], accounts={"acct1": account})
    engine = SignalCopierEngine(routing=routing, brokers={"paper": broker}, store=store)
    reconciler = OrderReconciler(store, {"paper": broker})
    return broker, account, engine, reconciler


@pytest.mark.asyncio
async def test_plain_buy_partially_filled_then_canceled_leaves_the_confirmed_partial(store, plain_setup):
    broker, account, engine, reconciler = plain_setup

    await engine.handle_signal(Signal(source="tv", symbol="AAPL", side=Side.BUY, quantity=100.0))
    assert store.get_position("acct1", "AAPL") == 100.0  # optimistic

    # Only 30 actually filled; the remaining 70 was canceled (Alpaca reports
    # this as REJECTED, with the real partial fill quantity attached).
    broker.script("order-1", status=OrderStatus.REJECTED, filled_quantity=30.0)
    await reconciler.reconcile_once()

    assert store.get_position("acct1", "AAPL") == 30.0  # NOT 0.0


@pytest.mark.asyncio
async def test_plain_close_partially_filled_then_canceled_leaves_the_correct_remainder(store, plain_setup):
    broker, account, engine, reconciler = plain_setup

    # Start with 100 already held (as if a prior BUY had fully confirmed).
    await broker.place_order(Signal(source="test", symbol="AAPL", side=Side.BUY), account, 100.0, "AAPL")
    store.record_fill("acct1", "AAPL", Side.BUY, 100.0)

    close_signal = Signal(source="tv", symbol="AAPL", side=Side.CLOSE)
    broker.next_broker_order_id = "close-order-1"
    await engine.handle_signal(close_signal)
    assert store.get_position("acct1", "AAPL") == 0.0  # optimistic full close applied

    # Only 40 of the 100-share close actually sold; the remaining 60 was
    # canceled -- true remaining holding is 60, not 100.
    broker.script("close-order-1", status=OrderStatus.REJECTED, filled_quantity=40.0)
    await reconciler.reconcile_once()

    assert store.get_position("acct1", "AAPL") == 60.0
