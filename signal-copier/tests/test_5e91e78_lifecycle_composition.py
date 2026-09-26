"""Focused diagnostic tests for commit 5e91e783f6a7fc2326d28cc7c4beab88c2c5c664.

STATUS: syntax-checked only when supplied; NOT run against the repository.
Copy to signal-copier/tests/, review, then run the actual application suite.

These tests use the real engine, SQLite store, lifecycle and reconciler.
Only the external venue is simulated. The simulated venue independently
tracks actual cumulative executions, remaining orders, and resting stops.
They are not a certification of a real broker's protocol or execution.

No app.main import, credentials, external HTTP, live orders or real database.
Do not xfail failing financial assertions. If a reviewed interface changes,
adapt the fixture while preserving the independent economic invariant.
"""
from __future__ import annotations

import asyncio
import socket
from dataclasses import dataclass, replace
from pathlib import Path

import pytest

from app.brokers.paper import PaperBroker
from app.db import SignalStore
from app.engine import SignalCopierEngine
from app.lifecycle.manager import PositionLifecycleManager
from app.lifecycle.models import ProtectionStatus
from app.models import DestinationAccount, OrderResult, OrderStatus, Side, Signal
from app.reconciliation import OrderReconciler
from app.routing import RoutingConfig, RoutingRule

ACCOUNT = "review-account"
SYMBOL = "AAPL"


@pytest.fixture(autouse=True)
def deny_network(monkeypatch):
    """Defence in depth; AF_UNIX socketpairs used by asyncio remain allowed."""
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex

    def connect(sock, address):
        if sock.family in (socket.AF_INET, socket.AF_INET6):
            raise AssertionError("Review tests forbid IP network connections")
        return original_connect(sock, address)

    def connect_ex(sock, address):
        if sock.family in (socket.AF_INET, socket.AF_INET6):
            raise AssertionError("Review tests forbid IP network connections")
        return original_connect_ex(sock, address)

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)


class ScriptedVenue(PaperBroker):
    """Orders start unfilled. report() changes this venue's book independently.

    PaperBroker supplies actual in-memory standing-stop operations. Rejection
    injections do NOT create/modify those standing stops. Multiple accepted
    independent sell commitments are observable, not silently capped by this
    test venue. That deliberately exercises the application's own controls.
    """
    def __init__(self):
        super().__init__()
        self.orders: dict[str, dict] = {}
        self.reports: dict[str, OrderResult] = {}
        self.fill_next_immediately = False
        self.new_stop_outcome: OrderStatus | None = None
        self.replacement_outcome: OrderStatus | None = None
        self.pause_first_stop = False
        self.stop_entered = asyncio.Event()
        self.release_stop = asyncio.Event()
        self.stop_attempts = 0

    async def place_order(self, signal, account, quantity, symbol):
        order_id = f"review-order-{len(self.orders) + 1}"
        self.orders[order_id] = {
            "account_id": account.account_id, "symbol": symbol,
            "side": signal.side, "quantity": quantity, "cumulative": 0.0,
            "signal_id": signal.id, "terminal": False,
        }
        if self.fill_next_immediately:
            self.fill_next_immediately = False
            self.report(order_id, quantity, OrderStatus.FILLED)
            return self.reports[order_id]
        result = OrderResult(
            account_id=account.account_id, status=OrderStatus.PENDING,
            signal_id=signal.id, broker_order_id=order_id,
            filled_quantity=None, message="accepted; no execution confirmed",
        )
        self.reports[order_id] = result
        return result

    def report(self, order_id: str, cumulative: float, status: OrderStatus):
        order = self.orders[order_id]
        if not 0 <= cumulative <= order["quantity"]:
            raise ValueError("Test venue execution outside order size")
        if cumulative < order["cumulative"]:
            raise ValueError("Use an explicit correction scenario for trade busts")
        delta = cumulative - order["cumulative"]
        book = self.positions.setdefault(order["account_id"], {})
        signed_delta = delta if order["side"] is Side.BUY else -delta
        book[order["symbol"]] = book.get(order["symbol"], 0.0) + signed_delta
        order["cumulative"] = cumulative
        order["terminal"] = status in (OrderStatus.FILLED, OrderStatus.REJECTED)
        self.reports[order_id] = OrderResult(
            account_id=order["account_id"], status=status,
            signal_id=order["signal_id"], broker_order_id=order_id,
            filled_quantity=cumulative, filled_price=100.0,
            message="scripted authoritative cumulative execution",
        )

    def report_terminal_without_quantity(self, order_id: str):
        """Incomplete terminal observation; not a reversal of known fills."""
        order = self.orders[order_id]
        order["terminal"] = True
        self.reports[order_id] = OrderResult(
            account_id=order["account_id"], status=OrderStatus.REJECTED,
            signal_id=order["signal_id"], broker_order_id=order_id,
            filled_quantity=None, message="terminal response omitted cumulative field",
        )

    async def get_order_status(self, account, broker_order_id):
        order = self.orders.get(broker_order_id)
        if order is None:
            return None
        if order["account_id"] != account.account_id:
            raise AssertionError("Observation requested for the wrong account")
        return self.reports.get(broker_order_id)

    async def place_protective_stop(self, account, symbol, quantity, stop_price, exit_side):
        self.stop_attempts += 1
        if self.pause_first_stop and self.stop_attempts == 1:
            self.stop_entered.set()
            await self.release_stop.wait()
        if self.new_stop_outcome is not None:
            return OrderResult(
                account_id=account.account_id, status=self.new_stop_outcome,
                signal_id="", broker_order_id=None, filled_quantity=0.0,
                message="test venue refused new protective stop",
            )
        return await super().place_protective_stop(account, symbol, quantity, stop_price, exit_side)

    async def replace_stop_quantity(self, account, broker_order_id, new_quantity, new_price=None):
        if self.replacement_outcome is not None:
            return OrderResult(
                account_id=account.account_id, status=self.replacement_outcome,
                signal_id="", broker_order_id=None, filled_quantity=0.0,
                message="test venue did not replace the existing stop",
            )
        return await super().replace_stop_quantity(account, broker_order_id, new_quantity, new_price)

    def owned(self):
        return self.positions.get(ACCOUNT, {}).get(SYMBOL, 0.0)

    def standing_stop_quantity(self):
        return sum(
            stop.quantity for stop in self._stop_orders.values()
            if stop.account_id == ACCOUNT and stop.symbol == SYMBOL and stop.side is Side.SELL
        )

    def outstanding_sell_quantity(self):
        return sum(
            order["quantity"] - order["cumulative"] for order in self.orders.values()
            if order["account_id"] == ACCOUNT and order["symbol"] == SYMBOL
            and order["side"] is Side.SELL and not order["terminal"]
        )


@dataclass
class World:
    store: SignalStore
    broker: ScriptedVenue
    account: DestinationAccount
    manager: PositionLifecycleManager
    engine: SignalCopierEngine
    reconciler: OrderReconciler
    database_path: Path


def make_world(path: Path, managed: bool = True) -> World:
    store = SignalStore(path)
    broker = ScriptedVenue()
    account = DestinationAccount(account_id=ACCOUNT, broker="paper", managed_lifecycle=managed)
    routing = RoutingConfig(
        rules=[RoutingRule(source="review", destinations=[ACCOUNT])], accounts={ACCOUNT: account}
    )
    manager = PositionLifecycleManager(brokers={"paper": broker}, store=store)
    engine = SignalCopierEngine(routing=routing, brokers={"paper": broker}, store=store, lifecycle_manager=manager)
    reconciler = OrderReconciler(store, {"paper": broker}, lifecycle_manager=manager)
    return World(store, broker, account, manager, engine, reconciler, path)


@pytest.fixture
def world(tmp_path):
    return make_world(tmp_path / "review.sqlite")


async def start_entry(world: World, quantity: float = 100.0):
    results = await world.engine.handle_signal(Signal(
        source="review", symbol=SYMBOL, side=Side.BUY, quantity=quantity,
        stop_loss=90.0 if world.account.managed_lifecycle else None,
    ))
    assert len(results) == 1
    assert results[0].status is OrderStatus.PENDING, results[0]
    return results[0].broker_order_id


async def progress(world: World, order_id: str, cumulative: float, status=OrderStatus.PENDING):
    world.broker.report(order_id, cumulative, status)
    await world.reconciler.reconcile_once()


def lifecycle(world: World):
    return world.manager.get_lifecycle(ACCOUNT, SYMBOL)


@pytest.mark.asyncio
async def test_control_managed_full_fill_applies_once(world):
    entry = await start_entry(world)
    await progress(world, entry, 100.0, OrderStatus.FILLED)
    await world.reconciler.reconcile_once()
    assert world.broker.owned() == world.store.get_position(ACCOUNT, SYMBOL) == 100.0
    assert lifecycle(world).confirmed_owned_quantity == 100.0
    assert lifecycle(world).stop.protected_quantity == 100.0


@pytest.mark.asyncio
async def test_control_monotonic_partial_fills_without_exits(world):
    entry = await start_entry(world)
    for quantity in (30.0, 30.0, 60.0, 100.0):
        status = OrderStatus.FILLED if quantity == 100.0 else OrderStatus.PENDING
        await progress(world, entry, quantity, status)
        assert world.broker.owned() == world.store.get_position(ACCOUNT, SYMBOL) == quantity
        assert lifecycle(world).confirmed_owned_quantity == quantity
        assert world.broker.standing_stop_quantity() == quantity


@pytest.mark.asyncio
async def test_control_plain_canceled_partial_retains_actual_fills(tmp_path):
    world = make_world(tmp_path / "plain.sqlite", managed=False)
    entry = await start_entry(world)
    await progress(world, entry, 30.0, OrderStatus.REJECTED)
    assert world.broker.owned() == world.store.get_position(ACCOUNT, SYMBOL) == 30.0


@pytest.mark.asyncio
async def test_control_completed_reconciliation_restart_is_stable(world):
    entry = await start_entry(world)
    await progress(world, entry, 100.0, OrderStatus.FILLED)
    store = SignalStore(world.database_path)
    manager = PositionLifecycleManager(brokers={"paper": world.broker}, store=store)
    manager.restore_from_store()
    reconciler = OrderReconciler(store, {"paper": world.broker}, lifecycle_manager=manager)
    await reconciler.reconcile_once()
    assert store.get_position(ACCOUNT, SYMBOL) == 100.0
    assert manager.get_lifecycle(ACCOUNT, SYMBOL).confirmed_owned_quantity == 100.0


@pytest.mark.asyncio
async def test_entry_growth_after_completed_partial_exit_does_not_recreate_sold_quantity(world):
    """F01: 30 bought - 10 sold + 30 bought = 50, not cumulative buys of 60."""
    entry = await start_entry(world)
    await progress(world, entry, 30.0)
    world.broker.fill_next_immediately = True
    result = await world.manager.request_exit(world.account, SYMBOL, 10.0, source="target")
    assert result.status is OrderStatus.FILLED
    assert world.broker.owned() == lifecycle(world).confirmed_owned_quantity == 20.0
    await progress(world, entry, 60.0)
    assert world.broker.owned() == 50.0
    assert lifecycle(world).confirmed_owned_quantity == 50.0
    assert world.broker.standing_stop_quantity() <= 50.0


@pytest.mark.asyncio
async def test_entry_growth_does_not_rearm_over_an_unresolved_exit(world):
    """F02: 60 owned may not have independent executable sells of 60 + 10."""
    entry = await start_entry(world)
    await progress(world, entry, 30.0)
    result = await world.manager.request_exit(world.account, SYMBOL, 10.0, source="target")
    assert result.status is OrderStatus.PENDING
    assert world.broker.outstanding_sell_quantity() == 10.0
    await progress(world, entry, 60.0)
    assert world.broker.owned() == 60.0
    may_sell = world.broker.standing_stop_quantity() + world.broker.outstanding_sell_quantity()
    assert may_sell <= world.broker.owned(), (may_sell, world.broker.owned())


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", [OrderStatus.ERROR, OrderStatus.REJECTED])
async def test_failed_stop_resize_is_not_reported_as_new_confirmed_coverage(world, outcome):
    """F03: status objects are not proof that a stop was successfully resized."""
    entry = await start_entry(world)
    await progress(world, entry, 30.0)
    world.broker.replacement_outcome = outcome
    await progress(world, entry, 60.0)
    assert world.broker.owned() == 60.0
    assert world.broker.standing_stop_quantity() == 30.0
    assert lifecycle(world).stop.protected_quantity <= world.broker.standing_stop_quantity()


@pytest.mark.asyncio
async def test_rejected_initial_stop_is_not_reported_confirmed(world):
    """F03: REJECTED is not a working protective order."""
    entry = await start_entry(world)
    world.broker.new_stop_outcome = OrderStatus.REJECTED
    await progress(world, entry, 30.0)
    assert world.broker.owned() == 30.0
    assert world.broker.standing_stop_quantity() == 0.0
    assert lifecycle(world).stop.status is not ProtectionStatus.STOP_CONFIRMED
    assert lifecycle(world).stop.protected_quantity == 0.0


@pytest.mark.asyncio
async def test_managed_exit_partial_cancel_reconciles_store_and_lifecycle(world):
    """F04: pending close then 40 sold/60 canceled must not leave the UI flat."""
    entry = await start_entry(world)
    await progress(world, entry, 100.0, OrderStatus.FILLED)
    exit_result = await world.engine.close_position(world.account, SYMBOL)
    assert exit_result.status is OrderStatus.PENDING
    await progress(world, exit_result.broker_order_id, 40.0, OrderStatus.REJECTED)
    assert world.broker.owned() == 60.0
    assert lifecycle(world).confirmed_owned_quantity == 60.0
    assert world.store.get_position(ACCOUNT, SYMBOL) == 60.0


@pytest.mark.asyncio
async def test_working_partial_exit_is_applied_immediately_not_at_terminal(world):
    """EXE-06: a managed close for 100 with 40 confirmed while the
    remaining 60 is still working must reduce tracked ownership to 60
    immediately -- not leave it at 100 until the whole order finishes."""
    entry = await start_entry(world)
    await progress(world, entry, 100.0, OrderStatus.FILLED)
    exit_result = await world.engine.close_position(world.account, SYMBOL)
    assert exit_result.status is OrderStatus.PENDING

    # Confirmed partial progress while still working (not terminal).
    await world.manager.resolve_pending_exit(world.account, SYMBOL, 40.0, remainder_cancelled=False)

    assert lifecycle(world).confirmed_owned_quantity == 60.0
    assert world.store.get_position(ACCOUNT, SYMBOL) == 60.0
    # The stop is deliberately NOT restored yet -- the remaining 60 is still
    # an open, uncertain commitment (see request_exit's worked example).
    assert lifecycle(world).stop.broker_order_id is None

    # More fills, then the remainder is confirmed cancelled (terminal).
    await world.manager.resolve_pending_exit(world.account, SYMBOL, 55.0, remainder_cancelled=True)

    assert lifecycle(world).confirmed_owned_quantity == 45.0
    assert world.store.get_position(ACCOUNT, SYMBOL) == 45.0
    assert world.broker.standing_stop_quantity() == 45.0


@pytest.mark.asyncio
async def test_new_same_symbol_lifecycle_cannot_swallow_an_older_plain_order(tmp_path):
    """F04: owner selection must be per order, not any current symbol match."""
    world = make_world(tmp_path / "transition.sqlite", managed=False)
    old_entry = await start_entry(world)
    world.account = replace(world.account, managed_lifecycle=True)
    world.engine.routing.accounts[ACCOUNT] = world.account
    # A fixed system may refuse this new entry until the old one resolves;
    # either way it must still reconcile the old order correctly.
    await world.engine.handle_signal(Signal(
        source="review", symbol=SYMBOL, side=Side.BUY, quantity=20.0, stop_loss=90.0,
    ))
    await progress(world, old_entry, 30.0, OrderStatus.REJECTED)
    assert world.broker.owned() == 30.0
    assert world.store.get_position(ACCOUNT, SYMBOL) == 30.0


class SimulatedProcessLoss(BaseException):
    """Abrupt interruption: not an ordinary retryable application exception."""


@pytest.mark.asyncio
async def test_crash_after_position_commit_before_fill_checkpoint_does_not_double_apply(world, monkeypatch):
    """F05: restart in the middle of resolution, not after clean completion."""
    entry = await start_entry(world)
    world.broker.report(entry, 30.0, OrderStatus.PENDING)
    original = world.store.record_fill

    def commit_then_interrupt(*args, **kwargs):
        original(*args, **kwargs)  # real SQLite commit, not a fabricated position row
        raise SimulatedProcessLoss("after position write, before outer lifecycle checkpoint")

    with monkeypatch.context() as scoped:
        scoped.setattr(world.store, "record_fill", commit_then_interrupt)
        with pytest.raises(SimulatedProcessLoss):
            await world.reconciler.reconcile_once()

    restored_store = SignalStore(world.database_path)
    restored_manager = PositionLifecycleManager(brokers={"paper": world.broker}, store=restored_store)
    restored_manager.restore_from_store()
    restored_reconciler = OrderReconciler(
        restored_store, {"paper": world.broker}, lifecycle_manager=restored_manager
    )
    await restored_reconciler.reconcile_once()
    assert world.broker.owned() == 30.0
    assert restored_store.get_position(ACCOUNT, SYMBOL) == 30.0
    assert restored_manager.get_lifecycle(ACCOUNT, SYMBOL).confirmed_owned_quantity == 30.0


@pytest.mark.asyncio
async def test_crash_during_protection_work_after_position_commit_does_not_double_apply(world, monkeypatch):
    """EXE-02: the reviewed ordering committed the checkpoint/position pair
    AFTER attempting the protective-stop placement -- an interruption during
    that placement (which had already durably advanced
    confirmed_owned_quantity/stop via its own persist) left the checkpoint
    stale, so recovery re-observed and re-applied the same fill (lifecycle/
    stop reaching 60 while the venue and position stayed at 30). The
    position+checkpoint commit must happen BEFORE any broker protection I/O,
    so an interruption during that I/O can only ever leave protection
    stale for an already-correct, already-durable position -- never a
    re-applied fill."""
    entry = await start_entry(world)
    world.broker.report(entry, 30.0, OrderStatus.PENDING)

    async def interrupt_during_protection(*args, **kwargs):
        raise SimulatedProcessLoss("crashed while placing the protective stop")

    with monkeypatch.context() as scoped:
        scoped.setattr(world.broker, "place_protective_stop", interrupt_during_protection)
        with pytest.raises(SimulatedProcessLoss):
            await world.reconciler.reconcile_once()

    # The position/checkpoint pair must already be durable even though the
    # crash happened before protection completed.
    assert world.store.get_position(ACCOUNT, SYMBOL) == 30.0

    restored_store = SignalStore(world.database_path)
    restored_manager = PositionLifecycleManager(brokers={"paper": world.broker}, store=restored_store)
    restored_manager.restore_from_store()
    restored_reconciler = OrderReconciler(restored_store, {"paper": world.broker}, lifecycle_manager=restored_manager)
    await restored_reconciler.reconcile_once()

    assert world.broker.owned() == 30.0
    assert restored_store.get_position(ACCOUNT, SYMBOL) == 30.0
    assert restored_manager.get_lifecycle(ACCOUNT, SYMBOL).confirmed_owned_quantity == 30.0


@pytest.mark.asyncio
async def test_missing_terminal_quantity_cannot_erase_an_already_owned_lifecycle(world):
    """F06: omitted cumulative data is not an authoritative reversal to zero."""
    entry = await start_entry(world)
    await progress(world, entry, 30.0)
    world.broker.report_terminal_without_quantity(entry)
    await world.reconciler.reconcile_once()
    assert world.broker.owned() == 30.0
    remaining = lifecycle(world)
    assert remaining is not None, "Known ownership lost its management record"
    assert remaining.confirmed_owned_quantity == 30.0
    assert world.store.get_position(ACCOUNT, SYMBOL) == 30.0


@pytest.mark.asyncio
async def test_explicit_terminal_zero_after_a_known_confirmed_fill_does_not_erase_it(world):
    """PRO-07: an explicit (not just missing) terminal 0 observation, even
    though it directly contradicts an already-confirmed 30, must not
    unregister the lifecycle -- a single contradictory/stale broker
    response is not proof the earlier real confirmation was wrong."""
    entry = await start_entry(world)
    await progress(world, entry, 30.0)

    await world.manager.resolve_pending_entry(world.account, SYMBOL, 0.0, remainder_cancelled=True)

    remaining = lifecycle(world)
    assert remaining is not None, "Known ownership lost its management record"
    assert remaining.confirmed_owned_quantity == 30.0
    assert world.store.get_position(ACCOUNT, SYMBOL) == 30.0


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid_quantity", [-5.0, float("inf"), float("nan")])
async def test_invalid_fill_observations_are_ignored_not_applied(world, invalid_quantity):
    """PRO-07: negative/non-finite observations are invalid input, not a
    legitimate trade-bust/correction event -- they must be ignored rather
    than corrupting the ledger."""
    entry = await start_entry(world)
    await progress(world, entry, 30.0)

    await world.manager.resolve_pending_entry(world.account, SYMBOL, invalid_quantity, remainder_cancelled=False)

    remaining = lifecycle(world)
    assert remaining is not None
    assert remaining.confirmed_owned_quantity == 30.0
    assert world.store.get_position(ACCOUNT, SYMBOL) == 30.0


@pytest.mark.asyncio
async def test_simultaneous_identical_fill_observations_do_not_duplicate_stops_or_positions(world):
    """F07: events prove overlap; no sleep-duration correctness assumption."""
    entry = await start_entry(world)
    world.broker.report(entry, 30.0, OrderStatus.PENDING)
    world.broker.pause_first_stop = True
    delivered = 0
    both_delivered = asyncio.Event()

    async def deliver():
        nonlocal delivered
        delivered += 1
        if delivered == 2:
            both_delivered.set()
        await world.manager.resolve_pending_entry(world.account, SYMBOL, 30.0, remainder_cancelled=False)

    first = asyncio.create_task(deliver())
    second = None
    try:
        await asyncio.wait_for(world.broker.stop_entered.wait(), timeout=2.0)
        second = asyncio.create_task(deliver())
        await asyncio.wait_for(both_delivered.wait(), timeout=2.0)
        world.broker.release_stop.set()
        await asyncio.wait_for(asyncio.gather(first, second), timeout=2.0)
    finally:
        world.broker.release_stop.set()
        for task in (first, second):
            if task is not None and not task.done():
                task.cancel()
        await asyncio.gather(*(t for t in (first, second) if t is not None), return_exceptions=True)

    assert world.broker.owned() == 30.0
    assert world.store.get_position(ACCOUNT, SYMBOL) == 30.0
    assert world.broker.standing_stop_quantity() == 30.0
    assert len(world.broker._stop_orders) == 1


@pytest.mark.asyncio
async def test_full_exit_of_confirmed_entry_does_not_delete_a_still_working_entry_lifecycle(world):
    """EXE-07: 30 of a 100-unit entry confirmed and fully sold must not
    delete the lifecycle -- 70 entry units are still outstanding and may
    yet fill, needing protection and visibility when they do."""
    entry = await start_entry(world, quantity=100.0)
    await progress(world, entry, 30.0)  # 30 confirmed, 70 still pending

    world.broker.fill_next_immediately = True
    result = await world.manager.request_exit(world.account, SYMBOL, 30.0, source="manual")
    assert result.status is OrderStatus.FILLED
    assert world.broker.owned() == 0.0

    lifecycle = manager_lifecycle = world.manager.get_lifecycle(ACCOUNT, SYMBOL)
    assert lifecycle is not None, "the still-working 70-unit entry lost its management record"
    assert lifecycle.closed is False
    assert lifecycle in world.manager.list_open_lifecycles()

    # The remaining 70 entry units now confirm-fill.
    await progress(world, entry, 100.0, OrderStatus.FILLED)

    assert world.broker.owned() == 70.0
    assert world.store.get_position(ACCOUNT, SYMBOL) == 70.0
    assert manager_lifecycle.confirmed_owned_quantity == 70.0
    assert world.broker.standing_stop_quantity() == 70.0  # the late fill is protected, not orphaned
