"""Real-condition tests for app/protection_auditor.py's independent SL/TP
verification -- see that module's docstring for the design this exercises:
`ProtectionAuditor` never reads `PositionLifecycleManager`'s own
`StopRecord`/`CloseArbiter` bookkeeping to decide whether a position is
protected, only what a `BrokerAdapter` reports directly.
"""
from __future__ import annotations

import pytest

from app.brokers.base import BrokerAdapter
from app.brokers.paper import PaperBroker
from app.lifecycle.manager import PositionLifecycleManager
from app.lifecycle.models import PositionPlan, ProtectionStatus
from app.models import BrokerOpenOrder, DestinationAccount, Side, Signal
from app.protection_auditor import AuditStatus, ProtectionAuditor


@pytest.fixture
def account():
    return DestinationAccount(account_id="acct1", broker="paper")


@pytest.fixture
def broker():
    return PaperBroker()


@pytest.fixture
def manager(broker):
    return PositionLifecycleManager(brokers={"paper": broker})


def _plan(**overrides) -> PositionPlan:
    defaults = dict(
        account_id="acct1", symbol="AAPL", side=Side.BUY, planned_quantity=100.0, broker="paper", initial_stop=48.50
    )
    defaults.update(overrides)
    return PositionPlan(**defaults)


async def _enter(manager, broker, account, plan, filled_quantity):
    manager.start_plan(plan)
    entry_signal = Signal(source="test", symbol=plan.symbol, side=plan.side)
    await broker.place_order(entry_signal, account, filled_quantity, plan.symbol)
    return await manager.on_entry_fill(account, plan.symbol, filled_quantity)


# --- classification against a mocked-but-realistic broker response ---


@pytest.mark.asyncio
async def test_broker_flat_is_fully_protected(account, broker, manager):
    auditor = ProtectionAuditor({"paper": broker}, lifecycle_manager=manager)
    finding = await auditor.audit_position(account, "AAPL")
    assert finding.status == AuditStatus.FULLY_PROTECTED
    assert finding.broker_position_quantity == 0.0


@pytest.mark.asyncio
async def test_real_stop_matching_quantity_is_fully_protected(account, broker, manager):
    await _enter(manager, broker, account, _plan(), 100.0)
    auditor = ProtectionAuditor({"paper": broker}, lifecycle_manager=manager)

    finding = await auditor.audit_position(account, "AAPL")

    assert finding.status == AuditStatus.FULLY_PROTECTED
    assert finding.stop_orders_found == 1
    assert finding.broker_position_quantity == 100.0


@pytest.mark.asyncio
async def test_no_resting_stop_is_classified_no_stop_and_repaired(account, broker, manager):
    """The manager's own `on_entry_fill` already placed a real stop via
    PaperBroker -- cancel it directly at the broker (bypassing the
    manager entirely, exactly like a broker-side cancellation this
    service never observed) so the broker's own order book now
    genuinely has zero stop orders, while the manager's internal
    `StopRecord.status` still says STOP_CONFIRMED. That mismatch is
    the whole reason this module has to read the broker directly."""
    lifecycle = await _enter(manager, broker, account, _plan(), 100.0)
    assert lifecycle.stop.status == ProtectionStatus.STOP_CONFIRMED
    broker._stop_orders.clear()  # simulate an out-of-band cancellation the manager never saw

    auditor = ProtectionAuditor({"paper": broker}, lifecycle_manager=manager)
    finding = await auditor.audit_position(account, "AAPL")

    assert finding.status == AuditStatus.FULLY_PROTECTED  # repaired, then re-verified
    assert finding.repaired is True
    assert "post-repair" in finding.detail
    # A real stop is resting at the broker again, sized to the true quantity.
    open_orders = await broker.list_open_orders(account, "AAPL")
    assert len(open_orders) == 1
    assert open_orders[0].quantity == 100.0


@pytest.mark.asyncio
async def test_under_protected_quantity_is_repaired_to_true_quantity(account, broker, manager):
    lifecycle = await _enter(manager, broker, account, _plan(), 100.0)
    stop_order_id = lifecycle.stop.broker_order_id
    # Independently corrupt the resting stop's quantity at the broker --
    # the manager's own StopRecord still believes 100.0 is covered.
    broker._stop_orders[stop_order_id].quantity = 40.0

    auditor = ProtectionAuditor({"paper": broker}, lifecycle_manager=manager)
    finding = await auditor.audit_position(account, "AAPL")

    assert finding.repaired is True
    assert finding.status == AuditStatus.FULLY_PROTECTED
    open_orders = await broker.list_open_orders(account, "AAPL")
    assert open_orders[0].quantity == 100.0


@pytest.mark.asyncio
async def test_over_protected_quantity_is_detected(account, broker, manager):
    lifecycle = await _enter(manager, broker, account, _plan(), 100.0)
    stop_order_id = lifecycle.stop.broker_order_id
    broker._stop_orders[stop_order_id].quantity = 250.0  # more than actually owned -- oversell risk

    auditor = ProtectionAuditor({"paper": broker}, lifecycle_manager=manager)
    finding = await auditor.audit_position(account, "AAPL")

    assert finding.repaired is True
    assert finding.status == AuditStatus.FULLY_PROTECTED
    open_orders = await broker.list_open_orders(account, "AAPL")
    assert open_orders[0].quantity == 100.0


@pytest.mark.asyncio
async def test_wrong_side_stop_is_flagged_not_repaired(account, broker, manager):
    lifecycle = await _enter(manager, broker, account, _plan(), 100.0)
    stop_order_id = lifecycle.stop.broker_order_id
    broker._stop_orders[stop_order_id].side = Side.BUY  # should be SELL to protect a long

    auditor = ProtectionAuditor({"paper": broker}, lifecycle_manager=manager)
    finding = await auditor.audit_position(account, "AAPL")

    assert finding.status == AuditStatus.WRONG_SIDE
    assert finding.repaired is False


@pytest.mark.asyncio
async def test_flat_position_with_leftover_order_is_stale(account, broker, manager):
    await _enter(manager, broker, account, _plan(), 100.0)
    # Broker later reports flat (e.g. the stop already filled and closed
    # the position) but its resting-order list wasn't cleaned up.
    broker.positions["acct1"]["AAPL"] = 0.0

    auditor = ProtectionAuditor({"paper": broker}, lifecycle_manager=manager)
    finding = await auditor.audit_position(account, "AAPL")

    assert finding.status == AuditStatus.STALE_ORDER
    assert finding.broker_position_quantity == 0.0


@pytest.mark.asyncio
async def test_broker_without_position_readback_is_unknown_not_fabricated():
    class _BlindBroker(BrokerAdapter):
        name = "blind"

        async def place_order(self, signal, account, quantity, symbol):
            raise NotImplementedError

    account = DestinationAccount(account_id="acct1", broker="blind")
    auditor = ProtectionAuditor({"blind": _BlindBroker()})

    finding = await auditor.audit_position(account, "AAPL")

    assert finding.status == AuditStatus.UNKNOWN
    assert finding.repaired is False


# --- the load-bearing invariant: an ambiguous/UNKNOWN finding is NEVER
# silently repaired, and the position is halted rather than guessed at ---


class _MultiStopBroker(BrokerAdapter):
    """A broker whose own order book genuinely has two resting stop orders
    for the same symbol -- an ambiguous state no independent verifier
    should ever resolve by guessing which one is authoritative."""

    name = "multistop"

    async def place_order(self, signal, account, quantity, symbol):
        raise NotImplementedError

    async def get_broker_position(self, account, symbol):
        return 100.0

    async def list_open_orders(self, account, symbol):
        return [
            BrokerOpenOrder(
                account_id=account.account_id, symbol=symbol, broker_order_id="a", side=Side.SELL,
                quantity=100.0, price=48.0, role="stop",
            ),
            BrokerOpenOrder(
                account_id=account.account_id, symbol=symbol, broker_order_id="b", side=Side.SELL,
                quantity=100.0, price=47.5, role="stop",
            ),
        ]


@pytest.mark.asyncio
async def test_ambiguous_multiple_stops_is_unknown_and_never_repaired(account, manager):
    broker = _MultiStopBroker()
    account = DestinationAccount(account_id="acct1", broker="multistop")
    auditor = ProtectionAuditor({"multistop": broker}, lifecycle_manager=manager)

    finding = await auditor.audit_position(account, "AAPL")

    assert finding.status == AuditStatus.UNKNOWN
    assert finding.repaired is False
    assert finding.repair_detail == ""
    # The ambiguous position is halted rather than left to a guess.
    assert manager.arbiter.is_halted("acct1", "AAPL") is True


@pytest.mark.asyncio
async def test_unrecognized_resting_order_is_unknown_not_no_stop(account, manager):
    """A resting order this adapter can't classify as a stop must not be
    silently treated as "there is no stop" (which would trigger a repair
    placing a SECOND order on top of one that might already be the real
    stop, just unrecognized)."""

    class _AmbiguousRoleBroker(BrokerAdapter):
        name = "ambiguous"

        async def place_order(self, signal, account, quantity, symbol):
            raise NotImplementedError

        async def get_broker_position(self, account, symbol):
            return 100.0

        async def list_open_orders(self, account, symbol):
            return [
                BrokerOpenOrder(
                    account_id=account.account_id, symbol=symbol, broker_order_id="x", side=Side.SELL,
                    quantity=100.0, price=48.0, role="other",
                )
            ]

    account = DestinationAccount(account_id="acct1", broker="ambiguous")
    auditor = ProtectionAuditor({"ambiguous": _AmbiguousRoleBroker()}, lifecycle_manager=manager)

    finding = await auditor.audit_position(account, "AAPL")

    assert finding.status == AuditStatus.UNKNOWN
    assert finding.repaired is False


@pytest.mark.asyncio
async def test_no_stop_without_lifecycle_is_reported_not_repaired():
    """An unmanaged/native-bracket account (no PositionLifecycleManager
    entry) has no known intended stop price and no repair path -- this
    must be reported as NO_STOP, never silently left unrepaired-but-
    claimed-fixed, and never guessed at."""
    broker = PaperBroker()
    account = DestinationAccount(account_id="acct1", broker="paper")
    broker.positions["acct1"] = {"AAPL": 100.0}  # broker owns it; nothing local ever tracked/placed a stop

    auditor = ProtectionAuditor({"paper": broker})  # no lifecycle_manager wired in at all

    finding = await auditor.audit_position(account, "AAPL")

    assert finding.status == AuditStatus.NO_STOP
    assert finding.repaired is False
    assert "no repair path" in finding.detail
