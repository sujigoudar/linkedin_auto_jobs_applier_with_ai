"""AUD-01: the distinct-field quantity model replacing the old plain-path
optimistic PENDING-order accounting (see app/engine.py's module docstring
section "Position tracking's distinct-field quantity model" and
app/db.py's `orders` table SCHEMA comment for the full contract).

Five distinct quantities are tracked for every order/fill event:

    - requested_quantity          -- what was asked for
    - confirmed_cumulative_fill   -- what the broker has actually confirmed
    - applied_execution_delta     -- what this event actually applied to
                                      the tracked position
    - outstanding_possible_fill   -- requested - confirmed, genuine
                                      uncertain exposure while PENDING
    - actual_remaining_ownership  -- positions.net_quantity itself, updated
                                      ONLY from confirmed fills

These tests exercise the plain (non-managed_lifecycle) path end-to-end
through the real engine/reconciler, not a hand-constructed shortcut.
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
    """A PaperBroker whose place_order reports PENDING with a controllable
    (possibly None, possibly a real partial) filled_quantity, and whose
    get_order_status returns a scripted terminal result once told to."""

    def __init__(self, *, initial_filled_quantity: float | None = None):
        super().__init__()
        self.next_broker_order_id = "order-1"
        self.initial_filled_quantity = initial_filled_quantity
        self._status_by_order_id: dict[str, OrderResult] = {}

    async def place_order(self, signal, account, quantity, symbol):
        return OrderResult(
            account_id=account.account_id,
            status=OrderStatus.PENDING,
            signal_id=signal.id,
            broker_order_id=self.next_broker_order_id,
            filled_quantity=self.initial_filled_quantity,
            message="submitted, awaiting fill",
        )

    async def get_order_status(self, account, broker_order_id):
        return self._status_by_order_id.get(broker_order_id)

    def script_terminal_result(self, broker_order_id: str, *, status: OrderStatus, filled_quantity: float | None):
        self._status_by_order_id[broker_order_id] = OrderResult(
            account_id="doesn't matter", status=status, signal_id="", filled_quantity=filled_quantity, message="done"
        )


def _build(store, broker):
    account = DestinationAccount(account_id="acct1", broker="paper")
    routing = RoutingConfig(rules=[RoutingRule(source="tv", destinations=["acct1"])], accounts={"acct1": account})
    return SignalCopierEngine(routing=routing, brokers={"paper": broker}, store=store)


@pytest.mark.asyncio
async def test_partial_fill_leaves_actual_remaining_ownership_at_confirmed_not_requested(store):
    """A broker that reports PENDING with a REAL partial-fill quantity
    (not None) has confirmed that much -- it must be applied exactly, not
    the full requested amount."""
    broker = _ControllablePendingBroker(initial_filled_quantity=6.0)
    engine = _build(store, broker)

    await engine.handle_signal(Signal(source="tv", symbol="AAPL", side=Side.BUY, quantity=10.0))

    assert store.get_position("acct1", "AAPL") == 6.0  # confirmed, not the requested 10.0
    row = store.list_pending_orders()[0]
    assert row["filled_quantity"] == 6.0
    # The remaining 4.0 could still fill -- genuine uncertain exposure.
    assert store.get_outstanding_possible_fill("acct1") == {"AAPL": 4.0}


@pytest.mark.asyncio
async def test_pending_order_later_rejected_leaves_zero_position_impact_and_clears_outstanding(store):
    broker = _ControllablePendingBroker(initial_filled_quantity=None)
    engine = _build(store, broker)

    await engine.handle_signal(Signal(source="tv", symbol="AAPL", side=Side.BUY, quantity=10.0))

    # Nothing confirmed yet -- no position impact, full requested quantity
    # is outstanding exposure.
    assert store.get_position("acct1", "AAPL") == 0.0
    assert store.get_outstanding_possible_fill("acct1") == {"AAPL": 10.0}

    broker.script_terminal_result("order-1", status=OrderStatus.REJECTED, filled_quantity=0.0)
    reconciler = OrderReconciler(store, {"paper": broker})
    corrected = await reconciler.reconcile_once()

    assert corrected == 1
    # Rejected with a confirmed zero fill -- zero position impact, exactly
    # as if this order had never been sent.
    assert store.get_position("acct1", "AAPL") == 0.0
    # And the uncertain exposure is now resolved -- nothing left to fill.
    assert store.get_outstanding_possible_fill("acct1") == {}


@pytest.mark.asyncio
async def test_pending_order_later_fully_filled_updates_all_fields_correctly(store):
    broker = _ControllablePendingBroker(initial_filled_quantity=None)
    engine = _build(store, broker)

    await engine.handle_signal(Signal(source="tv", symbol="AAPL", side=Side.BUY, quantity=10.0))
    assert store.get_position("acct1", "AAPL") == 0.0
    assert store.get_outstanding_possible_fill("acct1") == {"AAPL": 10.0}

    broker.script_terminal_result("order-1", status=OrderStatus.FILLED, filled_quantity=10.0)
    reconciler = OrderReconciler(store, {"paper": broker})
    corrected = await reconciler.reconcile_once()

    assert corrected == 1
    # actual_remaining_ownership now reflects the full confirmed fill.
    assert store.get_position("acct1", "AAPL") == 10.0
    # Nothing left outstanding -- the order is terminal.
    assert store.get_outstanding_possible_fill("acct1") == {}
    orders = store.list_pending_orders()
    assert orders == []  # no longer pending


@pytest.mark.asyncio
async def test_rapid_double_reconciliation_pass_does_not_double_count(store):
    """A duplicate/rapid-retry reconciliation pass observing the SAME
    already-terminal order a second time (e.g. a webhook or scheduler
    firing twice in quick succession) must not apply the confirmed fill a
    second time -- the oversell-prevention pattern this repo already uses
    elsewhere (per-(account,symbol) claims / idempotent terminal-status
    transitions), applied here to the new distinct-field bookkeeping."""
    broker = _ControllablePendingBroker(initial_filled_quantity=None)
    engine = _build(store, broker)

    await engine.handle_signal(Signal(source="tv", symbol="AAPL", side=Side.BUY, quantity=10.0))
    broker.script_terminal_result("order-1", status=OrderStatus.FILLED, filled_quantity=10.0)

    reconciler = OrderReconciler(store, {"paper": broker})
    first = await reconciler.reconcile_once()
    second = await reconciler.reconcile_once()

    assert first == 1
    # The order is no longer PENDING after the first pass, so the second
    # pass has nothing left to (re)correct.
    assert second == 0
    assert store.get_position("acct1", "AAPL") == 10.0  # not 20.0
    assert store.get_outstanding_possible_fill("acct1") == {}


# -- Mutation-testing follow-ups (track39) -----------------------------------
#
# These two exercise `SignalStore.get_outstanding_possible_fill` directly
# (bypassing the engine) for two scenarios every other test in this file
# happens to never construct: a PENDING SELL order (every scenario above
# only ever uses BUY), and a PENDING order whose remainder is exactly
# zero. Both are real, reachable states this method's own docstring
# describes, and both were genuine mutation-testing survivors before
# these were added.


def test_outstanding_possible_fill_signs_a_pending_sell_negative(store):
    """A pending SELL's outstanding exposure must be NEGATIVE (it would
    reduce net exposure if it lands) -- the opposite sign from a pending
    BUY's. No other test in this suite ever exercises the SELL branch of
    `get_outstanding_possible_fill`'s `side == Side.BUY.value` check."""
    signal = Signal(source="tv", symbol="AAPL", side=Side.SELL)
    store.save_signal(signal)
    store.save_order_result(
        OrderResult(account_id="acct1", status=OrderStatus.PENDING, signal_id=signal.id, broker_order_id="order-1"),
        broker="paper",
        symbol="AAPL",
        side=Side.SELL,
        requested_quantity=10.0,
        confirmed_cumulative_fill=4.0,
    )
    assert store.get_outstanding_possible_fill("acct1") == {"AAPL": -6.0}


def test_outstanding_possible_fill_excludes_a_symbol_with_zero_remainder(store):
    """A still-PENDING order whose confirmed fill already equals its full
    requested quantity has a remainder of exactly 0.0 -- it must be
    excluded entirely (never contribute a spurious 0.0 entry, and the
    `remainder <= 0` boundary must include the equal-to-zero case, not
    just strictly negative)."""
    signal = Signal(source="tv", symbol="AAPL", side=Side.BUY)
    store.save_signal(signal)
    store.save_order_result(
        OrderResult(account_id="acct1", status=OrderStatus.PENDING, signal_id=signal.id, broker_order_id="order-1"),
        broker="paper",
        symbol="AAPL",
        side=Side.BUY,
        requested_quantity=10.0,
        confirmed_cumulative_fill=10.0,  # fully confirmed already, still technically PENDING
    )
    assert store.get_outstanding_possible_fill("acct1") == {}
