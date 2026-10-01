"""TRK-Q1: the explicit multi-field quantity model -- app/models.py's
`QuantityBreakdown` plus app/quantity.py's builders, formalizing (never
renaming) the seven distinct quantities a release review named as the
single most important architectural principle for this system:

    requested quantity != reserved quantity != acknowledged quantity !=
    cumulative executed quantity != currently owned quantity != quantity
    still executable != protected quantity

These tests construct scenarios where all seven values genuinely diverge
from each other and assert the model reports each one correctly -- not
just "some number came back."
"""
import pytest

from app.brokers.paper import PaperBroker
from app.db import SignalStore
from app.engine import SignalCopierEngine
from app.lifecycle.models import (
    PendingEntry,
    PositionLifecycle,
    PositionPlan,
    ProtectionStatus,
    StopRecord,
)
from app.lifecycle.manager import _lifecycle_from_state, _lifecycle_to_state
from app.models import AssetClass, DestinationAccount, OrderResult, OrderStatus, Side, Signal
from app.quantity import (
    acknowledged_quantity_for,
    build_quantity_breakdown,
    quantity_breakdown_for_lifecycle,
    quantity_still_executable_for,
)
from app.routing import RoutingConfig, RoutingRule


# --- Pure unit tests: the seven fields genuinely diverge in one event ---


def test_acknowledged_quantity_distinguishes_accepted_from_executed():
    """A PENDING order with a real broker_order_id has been ACCEPTED at
    the full requested size even though nothing has executed yet --
    acknowledged_quantity must report the full size, not 0.0 (which would
    conflate "not yet executed" with "never accepted")."""
    result = OrderResult(
        account_id="a1", status=OrderStatus.PENDING, signal_id="s1", broker_order_id="order-1", filled_quantity=None
    )
    assert acknowledged_quantity_for(result, requested_quantity=100.0) == 100.0


def test_acknowledged_quantity_is_zero_for_rejected():
    result = OrderResult(account_id="a1", status=OrderStatus.REJECTED, signal_id="s1")
    assert acknowledged_quantity_for(result, requested_quantity=100.0) == 0.0


def test_acknowledged_quantity_is_zero_for_pending_with_no_broker_order_id():
    """The ambiguous case app/command_ledger.py classifies UNKNOWN_AMBIGUOUS
    -- nothing the venue is on record as having accepted."""
    result = OrderResult(account_id="a1", status=OrderStatus.PENDING, signal_id="s1", broker_order_id=None)
    assert acknowledged_quantity_for(result, requested_quantity=100.0) == 0.0


def test_quantity_still_executable_is_zero_once_terminal():
    result = OrderResult(account_id="a1", status=OrderStatus.FILLED, signal_id="s1", filled_quantity=100.0)
    assert quantity_still_executable_for(result, requested_quantity=100.0, confirmed_cumulative_fill=100.0) == 0.0


def test_quantity_still_executable_treats_no_confirmed_fill_as_zero_confirmed():
    """Mutation-testing follow-up (track39): kills the `confirmed_cumulative_fill
    or 1.0` mutant. A still-PENDING order with NOTHING confirmed yet
    (`confirmed_cumulative_fill=None`) must report the FULL requested
    quantity as still executable, not one unit less -- `None` means
    "nothing confirmed", i.e. 0.0 confirmed, never a fabricated 1.0."""
    result = OrderResult(account_id="a1", status=OrderStatus.PENDING, signal_id="s1", broker_order_id="order-1")
    assert quantity_still_executable_for(result, requested_quantity=100.0, confirmed_cumulative_fill=None) == 100.0


def test_quantity_still_executable_floors_at_zero_not_one_when_fully_confirmed_but_pending():
    """Mutation-testing follow-up (track39): kills the `max(1.0, ...)`
    mutant. A PENDING order whose confirmed fill already equals (or
    exceeds) the requested quantity must report exactly 0.0 still
    executable, never a fabricated floor of 1.0 unit."""
    result = OrderResult(account_id="a1", status=OrderStatus.PENDING, signal_id="s1", broker_order_id="order-1")
    assert quantity_still_executable_for(result, requested_quantity=100.0, confirmed_cumulative_fill=100.0) == 0.0


def test_all_seven_fields_diverge_for_one_partial_fill_event():
    """The scenario the review's principle names: an order for 100 units,
    partially filled for 30, with capital reserved for the full 100, an
    existing position of 20 already owned before this fill, and a stop
    only covering 20 of them -- every one of the seven values differs."""
    result = OrderResult(
        account_id="a1", status=OrderStatus.PENDING, signal_id="s1", broker_order_id="order-1", filled_quantity=30.0
    )
    breakdown = build_quantity_breakdown(
        requested_quantity=100.0,
        result=result,
        reserved_quantity=95.0,  # already narrowed slightly from an earlier partial resolve
        confirmed_cumulative_fill=30.0,
        currently_owned_quantity=50.0,  # 20 already owned + 30 just confirmed
        protected_quantity=20.0,  # the stop hasn't been resized to the new fill yet
    )
    assert breakdown.requested_quantity == 100.0
    assert breakdown.reserved_quantity == 95.0
    assert breakdown.acknowledged_quantity == 100.0  # accepted in full, even though only 30 executed
    assert breakdown.cumulative_executed_quantity == 30.0
    assert breakdown.currently_owned_quantity == 50.0
    assert breakdown.quantity_still_executable == 70.0  # 100 requested - 30 confirmed
    assert breakdown.protected_quantity == 20.0  # the stop deficit the review calls out by name

    # Every pair of fields the review's principle names as distinct really
    # is distinct here (never collapsed into one generic "quantity") --
    # the one exception (acknowledged == requested) is itself a documented,
    # deliberate fact of this codebase's brokers (see
    # `acknowledged_quantity_for`'s own docstring: no broker here reports a
    # genuine partial-acknowledgment size), not an accidental collapse.
    assert breakdown.reserved_quantity != breakdown.requested_quantity
    assert breakdown.reserved_quantity != breakdown.acknowledged_quantity
    assert breakdown.acknowledged_quantity != breakdown.cumulative_executed_quantity
    assert breakdown.cumulative_executed_quantity != breakdown.currently_owned_quantity
    assert breakdown.currently_owned_quantity != breakdown.quantity_still_executable
    assert breakdown.quantity_still_executable != breakdown.protected_quantity
    assert breakdown.protected_quantity != breakdown.currently_owned_quantity


def test_quantity_breakdown_never_fabricates_when_nothing_reached_the_broker():
    """No `result` at all (a pure pre-submission/capital-only report) must
    leave acknowledged_quantity/quantity_still_executable None -- never a
    fabricated 0.0 or the requested quantity."""
    breakdown = build_quantity_breakdown(requested_quantity=50.0, reserved_quantity=50.0)
    assert breakdown.acknowledged_quantity is None
    assert breakdown.quantity_still_executable is None
    assert breakdown.cumulative_executed_quantity is None


# --- Managed-lifecycle breakdown: protected_quantity vs currently_owned_quantity ---


def _lifecycle(*, planned_quantity, confirmed_owned_quantity, protected_quantity, protection_confirmed):
    plan = PositionPlan(
        account_id="acct1", symbol="AAPL", side=Side.BUY, planned_quantity=planned_quantity, asset_class=AssetClass.EQUITY
    )
    stop = StopRecord(
        protected_quantity=protected_quantity,
        status=ProtectionStatus.STOP_CONFIRMED if protection_confirmed else ProtectionStatus.STOP_PENDING,
    )
    return PositionLifecycle(plan=plan, confirmed_owned_quantity=confirmed_owned_quantity, stop=stop)


def test_lifecycle_breakdown_protected_quantity_lags_currently_owned_after_partial_exit_restore_gap():
    """A position owns 80 units but the stop is still confirmed for the
    OLD size of 100 (a resize hasn't landed yet) -- protected_quantity
    must report 100 (what's actually covered right now), genuinely
    different from currently_owned_quantity (80). This is exactly the
    "protection deficit" the review names: correctly tracking the fill
    must not paper over the stop still needing repair."""
    lifecycle = _lifecycle(
        planned_quantity=100.0, confirmed_owned_quantity=80.0, protected_quantity=100.0, protection_confirmed=True
    )
    breakdown = quantity_breakdown_for_lifecycle(lifecycle)
    assert breakdown.currently_owned_quantity == 80.0
    assert breakdown.protected_quantity == 100.0
    assert breakdown.currently_owned_quantity != breakdown.protected_quantity


def test_lifecycle_breakdown_protected_quantity_is_zero_when_stop_not_confirmed():
    """An unconfirmed/pending stop protects nothing yet, even though a
    `protected_quantity` value is staged on the StopRecord -- covered_quantity
    (and so this breakdown) must report 0.0, never the staged value."""
    lifecycle = _lifecycle(
        planned_quantity=100.0, confirmed_owned_quantity=100.0, protected_quantity=100.0, protection_confirmed=False
    )
    breakdown = quantity_breakdown_for_lifecycle(lifecycle)
    assert breakdown.protected_quantity == 0.0
    assert breakdown.currently_owned_quantity == 100.0


def test_lifecycle_breakdown_reserved_quantity_reflects_open_pending_entry():
    lifecycle = _lifecycle(
        planned_quantity=100.0, confirmed_owned_quantity=0.0, protected_quantity=0.0, protection_confirmed=False
    )
    lifecycle.pending_entry = PendingEntry(
        broker_order_id="order-1", requested_quantity=100.0, confirmed_filled_quantity=0.0, reserved_quantity=100.0
    )
    breakdown = quantity_breakdown_for_lifecycle(lifecycle)
    assert breakdown.reserved_quantity == 100.0
    assert breakdown.quantity_still_executable == 100.0  # unresolved_remainder: nothing confirmed yet


def test_lifecycle_breakdown_reserved_quantity_none_with_no_pending_entry():
    lifecycle = _lifecycle(
        planned_quantity=100.0, confirmed_owned_quantity=100.0, protected_quantity=100.0, protection_confirmed=True
    )
    breakdown = quantity_breakdown_for_lifecycle(lifecycle)
    assert breakdown.reserved_quantity is None
    assert breakdown.quantity_still_executable == 0.0


def test_lifecycle_breakdown_requested_quantity_is_the_plans_planned_quantity():
    """Mutation-testing follow-up (track39): kills the
    `requested_quantity=None` mutant in `quantity_breakdown_for_lifecycle`
    -- no existing test in this file asserted this field at all. A
    lifecycle's breakdown must report the plan's real
    `planned_quantity`, never a fabricated/omitted `None` (which would
    also be indistinguishable from "this event never reached the
    broker", a different and much more significant fact elsewhere in
    this model)."""
    lifecycle = _lifecycle(
        planned_quantity=137.0, confirmed_owned_quantity=50.0, protected_quantity=0.0, protection_confirmed=False
    )
    breakdown = quantity_breakdown_for_lifecycle(lifecycle)
    assert breakdown.requested_quantity == 137.0


# --- PendingEntry.reserved_quantity round-trips through persistence ---


def test_pending_entry_reserved_quantity_survives_state_serialization_round_trip():
    """A restart must not silently drop the QUANTITY-form reservation --
    `_lifecycle_to_state`/`_lifecycle_from_state` (the exact functions
    app/db.py persists/restores a managed-lifecycle position through) must
    round-trip `PendingEntry.reserved_quantity` the same way they already
    round-trip `reserved_notional`."""
    plan = PositionPlan(account_id="acct1", symbol="AAPL", side=Side.BUY, planned_quantity=100.0)
    lifecycle = PositionLifecycle(plan=plan)
    lifecycle.pending_entry = PendingEntry(
        broker_order_id="order-1",
        requested_quantity=100.0,
        confirmed_filled_quantity=0.0,
        reserved_notional=5000.0,
        reserved_quantity=100.0,
    )

    state = _lifecycle_to_state(lifecycle, ledger={})
    restored = _lifecycle_from_state(state)

    assert restored.pending_entry is not None
    assert restored.pending_entry.reserved_notional == 5000.0
    assert restored.pending_entry.reserved_quantity == 100.0


def test_pending_entry_restore_defaults_reserved_quantity_for_pre_existing_rows():
    """A lifecycle persisted before this field existed (a real, pre-existing
    deployment's history) must restore safely with 0.0, not raise -- same
    backward-compatibility contract every other additive field on this
    dataclass already gets (see `entry_signal_id`'s own precedent)."""
    plan = PositionPlan(account_id="acct1", symbol="AAPL", side=Side.BUY, planned_quantity=100.0)
    lifecycle = PositionLifecycle(plan=plan)
    lifecycle.pending_entry = PendingEntry(broker_order_id="order-1", requested_quantity=100.0)
    state = _lifecycle_to_state(lifecycle, ledger={})
    del state["pending_entry"]["reserved_quantity"]  # simulate a pre-TRK-Q1 persisted row

    restored = _lifecycle_from_state(state)
    assert restored.pending_entry.reserved_quantity == 0.0


# --- End-to-end through the real engine + database: persisted columns ---


@pytest.fixture
def store(tmp_path):
    return SignalStore(tmp_path / "test.db")


def _raw_order_row(store, order_id):
    with store._connect() as conn:
        row = conn.execute(
            "SELECT reserved_quantity, acknowledged_quantity, requested_quantity, status "
            "FROM orders WHERE id = ?",
            (order_id,),
        ).fetchone()
    return row


class _PendingBroker(PaperBroker):
    async def place_order(self, signal, account, quantity, symbol):
        return OrderResult(
            account_id=account.account_id,
            status=OrderStatus.PENDING,
            signal_id=signal.id,
            broker_order_id="order-1",
            filled_quantity=None,
            message="submitted, awaiting fill",
        )


@pytest.mark.asyncio
async def test_engine_persists_reserved_and_acknowledged_quantity_for_a_gated_pending_entry(store):
    """A real end-to-end signal through the engine, for an account with a
    real notional gate configured: the persisted `orders` row must carry
    a non-fabricated `reserved_quantity` (capital genuinely held) AND a
    non-fabricated `acknowledged_quantity` (the broker accepted the order)
    even though NOTHING has executed yet."""
    broker = _PendingBroker()
    account = DestinationAccount(account_id="acct1", broker="paper", max_notional_exposure=10_000.0)
    routing = RoutingConfig(rules=[RoutingRule(source="tv", destinations=["acct1"])], accounts={"acct1": account})
    engine = SignalCopierEngine(routing=routing, brokers={"paper": broker}, store=store)

    await engine.handle_signal(Signal(source="tv", symbol="AAPL", side=Side.BUY, quantity=10.0, price=100.0))

    order_id = store.list_pending_orders()[0]["id"]
    reserved_quantity, acknowledged_quantity, requested_quantity, status = _raw_order_row(store, order_id)
    assert status == "pending"
    assert requested_quantity == 10.0
    assert reserved_quantity == 10.0  # capital genuinely reserved for the full ask
    assert acknowledged_quantity == 10.0  # broker accepted it, even though nothing has executed
    # And, per AUD-01, still 0 executed / 10 still outstanding at this point.
    assert store.get_position("acct1", "AAPL") == 0.0
    assert store.get_outstanding_possible_fill("acct1") == {"AAPL": 10.0}


@pytest.mark.asyncio
async def test_engine_reports_zero_reserved_quantity_with_no_gate_configured(store):
    """With NO notional/risk gate configured for the account, nothing is
    actually reserved -- `reserved_quantity` must be 0.0, never the full
    requested quantity (which would fabricate a reservation that was never
    made -- see `_try_reserve_capital`'s own `has_gate` branch)."""
    broker = _PendingBroker()
    account = DestinationAccount(account_id="acct1", broker="paper")  # no gate at all
    routing = RoutingConfig(rules=[RoutingRule(source="tv", destinations=["acct1"])], accounts={"acct1": account})
    engine = SignalCopierEngine(routing=routing, brokers={"paper": broker}, store=store)

    await engine.handle_signal(Signal(source="tv", symbol="AAPL", side=Side.BUY, quantity=10.0, price=100.0))

    order_id = store.list_pending_orders()[0]["id"]
    reserved_quantity, acknowledged_quantity, requested_quantity, status = _raw_order_row(store, order_id)
    assert reserved_quantity == 0.0
    assert acknowledged_quantity == 10.0  # still genuinely acknowledged by the broker


@pytest.mark.asyncio
async def test_engine_persists_zero_acknowledged_quantity_for_a_rejected_order(store):
    class _RejectingBroker(PaperBroker):
        async def place_order(self, signal, account, quantity, symbol):
            return OrderResult(
                account_id=account.account_id, status=OrderStatus.REJECTED, signal_id=signal.id, message="no"
            )

    broker = _RejectingBroker()
    account = DestinationAccount(account_id="acct1", broker="paper")
    routing = RoutingConfig(rules=[RoutingRule(source="tv", destinations=["acct1"])], accounts={"acct1": account})
    engine = SignalCopierEngine(routing=routing, brokers={"paper": broker}, store=store)

    await engine.handle_signal(Signal(source="tv", symbol="AAPL", side=Side.BUY, quantity=10.0, price=100.0))

    with store._connect() as conn:
        row = conn.execute(
            "SELECT reserved_quantity, acknowledged_quantity FROM orders WHERE status = 'rejected'"
        ).fetchone()
    assert row[1] == 0.0  # never acknowledged -- definitely rejected
