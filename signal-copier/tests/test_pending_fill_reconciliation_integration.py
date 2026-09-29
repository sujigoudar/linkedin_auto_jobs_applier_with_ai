"""End-to-end tests (through the real engine/reconciler path, not a
hand-constructed shortcut) for how a PENDING order's tracked position
effect gets reconciled against its later-confirmed outcome.

AUD-01 (this pass): plain accounts used to apply an OPTIMISTIC quantity to
`SignalStore.positions` (via `record_fill`) at placement time -- the full
requested quantity, guessed before the broker confirmed anything -- while
persisting the broker's raw `filled_quantity` (often `None` for a genuine
PENDING response) into the `orders` row. That is no longer this codebase's
behavior: `SignalCopierEngine` now leaves `positions.net_quantity` (i.e.
`actual_remaining_ownership`) UNCHANGED for a PENDING order with nothing
confirmed yet, and tracks the full requested quantity only as
`outstanding_possible_fill` -- genuine uncertain exposure, never applied to
the position. `OrderReconciler._correct_position` still applies the
confirmed delta once the broker's real answer is known; with nothing
optimistically pre-applied, that delta is simply the full confirmed
quantity (not a "true-up" of an inflated guess) -- see this file's own
tests below for the corrected shape of "not double-counted" and "trues up
correctly."

Managed-lifecycle accounts: a PENDING entry called `record_fill` with the
FULL requested quantity but never called `on_entry_fill`, so the position
was recorded as owned while genuinely having NO protective stop -- and
nothing ever resolved that gap. `PendingEntry` +
`PositionLifecycleManager.register_pending_entry`/`resolve_pending_entry`
(mirroring the existing `PendingExit` machinery) close it: the optimistic
guess isn't applied until the broker's real answer is known, and only then
is the CONFIRMED quantity (which may be less than requested) protected.
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
    """A PaperBroker whose place_order reports PENDING with a controllable
    (possibly None) filled_quantity, and whose get_order_status returns a
    scripted terminal result once told to -- the exact shape of an
    async-confirming broker (Alpaca/IBKR/SignalStack/NinjaTrader/Rithmic)."""

    def __init__(self):
        super().__init__()
        self.next_broker_order_id = "order-1"
        self._status_by_order_id: dict[str, OrderResult] = {}

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

    async def get_broker_position(self, account, symbol):
        # This stub models the PENDING/confirm dance only -- it never
        # populates PaperBroker's own position book, so the inherited
        # get_broker_position would falsely report "flat" regardless of
        # what's actually been confirmed. None = honestly unknown (see
        # OPS-03's reconciliation gate in app/reconciliation.py, which
        # never treats None as confirming zero).
        return None

    def script_terminal_result(self, broker_order_id: str, *, status: OrderStatus, filled_quantity: float | None):
        self._status_by_order_id[broker_order_id] = OrderResult(
            account_id="doesn't matter", status=status, signal_id="", filled_quantity=filled_quantity, message="done"
        )


@pytest.mark.asyncio
async def test_plain_account_pending_entry_confirmed_at_same_quantity_is_not_double_counted(store):
    broker = _ControllablePendingBroker()
    account = DestinationAccount(account_id="acct1", broker="paper")
    routing = RoutingConfig(rules=[RoutingRule(source="tv", destinations=["acct1"])], accounts={"acct1": account})
    engine = SignalCopierEngine(routing=routing, brokers={"paper": broker}, store=store)

    await engine.handle_signal(Signal(source="tv", symbol="AAPL", side=Side.BUY, quantity=10.0))

    # AUD-01: nothing confirmed yet -- actual_remaining_ownership must not
    # move for a merely-PENDING order.
    assert store.get_position("acct1", "AAPL") == 0.0
    pending_rows = store.list_pending_orders()
    assert len(pending_rows) == 1
    # The stored row's filled_quantity must reflect what was actually
    # APPLIED (nothing -- None), not an optimistic guess.
    assert pending_rows[0]["filled_quantity"] is None
    # The full requested quantity is tracked as outstanding_possible_fill.
    assert store.get_outstanding_possible_fill("acct1") == {"AAPL": 10.0}

    broker.script_terminal_result("order-1", status=OrderStatus.FILLED, filled_quantity=10.0)
    reconciler = OrderReconciler(store, {"paper": broker})
    corrected = await reconciler.reconcile_once()

    assert corrected == 1
    # The confirmed fill is applied exactly once, from a zero baseline (no
    # optimistic pre-application to double up on).
    assert store.get_position("acct1", "AAPL") == 10.0
    # And the outstanding exposure clears now that the order is terminal.
    assert store.get_outstanding_possible_fill("acct1") == {}


@pytest.mark.asyncio
async def test_plain_account_pending_entry_confirmed_at_lower_quantity_trues_up_correctly(store):
    broker = _ControllablePendingBroker()
    account = DestinationAccount(account_id="acct1", broker="paper")
    routing = RoutingConfig(rules=[RoutingRule(source="tv", destinations=["acct1"])], accounts={"acct1": account})
    engine = SignalCopierEngine(routing=routing, brokers={"paper": broker}, store=store)

    await engine.handle_signal(Signal(source="tv", symbol="AAPL", side=Side.BUY, quantity=10.0))
    # AUD-01: nothing confirmed yet -- position stays flat, not an
    # optimistic 10.0 guess.
    assert store.get_position("acct1", "AAPL") == 0.0

    broker.script_terminal_result("order-1", status=OrderStatus.FILLED, filled_quantity=6.0)
    reconciler = OrderReconciler(store, {"paper": broker})
    await reconciler.reconcile_once()

    # Only actually filled 6 of the requested 10 -- position must land at
    # exactly the confirmed 6.0 (applied once, from a zero baseline).
    assert store.get_position("acct1", "AAPL") == 6.0


@pytest.mark.asyncio
async def test_managed_pending_entry_has_no_protection_until_resolved(store):
    broker = _ControllablePendingBroker()
    account = DestinationAccount(account_id="acct1", broker="paper", managed_lifecycle=True)
    routing = RoutingConfig(rules=[RoutingRule(source="tv", destinations=["acct1"])], accounts={"acct1": account})
    lifecycle_manager = PositionLifecycleManager(brokers={"paper": broker}, store=store)
    engine = SignalCopierEngine(
        routing=routing, brokers={"paper": broker}, store=store, lifecycle_manager=lifecycle_manager
    )

    await engine.handle_signal(
        Signal(source="tv", symbol="AAPL", side=Side.BUY, quantity=100.0, stop_loss=90.0)
    )

    lifecycle = lifecycle_manager.get_lifecycle("acct1", "AAPL")
    assert lifecycle is not None
    assert lifecycle.pending_entry is not None
    assert lifecycle.pending_entry.requested_quantity == 100.0
    # Not protected yet -- on_entry_fill hasn't been called.
    assert lifecycle.stop.status == ProtectionStatus.UNPROTECTED
    assert lifecycle.confirmed_owned_quantity == 0.0
    # And SignalStore was never optimistically bumped for it either.
    assert store.get_position("acct1", "AAPL") == 0.0


@pytest.mark.asyncio
async def test_managed_pending_entry_partial_fill_protects_exactly_the_confirmed_amount(store):
    broker = _ControllablePendingBroker()
    account = DestinationAccount(account_id="acct1", broker="paper", managed_lifecycle=True)
    routing = RoutingConfig(rules=[RoutingRule(source="tv", destinations=["acct1"])], accounts={"acct1": account})
    lifecycle_manager = PositionLifecycleManager(brokers={"paper": broker}, store=store)
    engine = SignalCopierEngine(
        routing=routing, brokers={"paper": broker}, store=store, lifecycle_manager=lifecycle_manager
    )

    await engine.handle_signal(
        Signal(source="tv", symbol="AAPL", side=Side.BUY, quantity=100.0, stop_loss=90.0)
    )

    # 30 of the 100 actually filled; the rest was cancelled.
    broker.script_terminal_result("order-1", status=OrderStatus.REJECTED, filled_quantity=30.0)
    reconciler = OrderReconciler(store, {"paper": broker}, lifecycle_manager=lifecycle_manager)
    await reconciler.reconcile_once()

    lifecycle = lifecycle_manager.get_lifecycle("acct1", "AAPL")
    assert lifecycle.pending_entry is None
    assert lifecycle.confirmed_owned_quantity == 30.0
    assert lifecycle.stop.status == ProtectionStatus.STOP_CONFIRMED
    assert lifecycle.stop.protected_quantity == 30.0
    assert store.get_position("acct1", "AAPL") == 30.0


@pytest.mark.asyncio
async def test_managed_pending_entry_that_never_fills_unregisters_cleanly(store):
    broker = _ControllablePendingBroker()
    account = DestinationAccount(account_id="acct1", broker="paper", managed_lifecycle=True)
    routing = RoutingConfig(rules=[RoutingRule(source="tv", destinations=["acct1"])], accounts={"acct1": account})
    lifecycle_manager = PositionLifecycleManager(brokers={"paper": broker}, store=store)
    engine = SignalCopierEngine(
        routing=routing, brokers={"paper": broker}, store=store, lifecycle_manager=lifecycle_manager
    )

    await engine.handle_signal(
        Signal(source="tv", symbol="AAPL", side=Side.BUY, quantity=100.0, stop_loss=90.0)
    )

    broker.script_terminal_result("order-1", status=OrderStatus.REJECTED, filled_quantity=0.0)
    reconciler = OrderReconciler(store, {"paper": broker}, lifecycle_manager=lifecycle_manager)
    await reconciler.reconcile_once()

    assert lifecycle_manager.get_lifecycle("acct1", "AAPL") is None
    assert store.get_position("acct1", "AAPL") == 0.0
