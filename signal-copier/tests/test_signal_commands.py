"""app/signal_commands.py's canonical command vocabulary: free-text
classification, and end-to-end execution of the backed commands
(CLOSE_PERCENT, MOVE_STOP incl. breakeven, CANCEL_ENTRY, CLOSE_REMAINDER)
through the REAL engine/PositionLifecycleManager/CloseArbiter path -- not a
hand-constructed shortcut. Also confirms the unbacked commands
(ADD_TARGET, REMOVE_TARGET, ADD_ENTRY) are classified, recorded via an
explicit rejected result, and never silently dropped or approximated as a
real order.
"""
from __future__ import annotations

import pytest

from app.brokers.paper import PaperBroker
from app.db import SignalStore
from app.engine import SignalCopierEngine
from app.lifecycle.manager import PositionLifecycleManager
from app.models import DestinationAccount, OrderStatus, Signal, Side
from app.routing import RoutingConfig, RoutingRule
from app.signal_commands import CanonicalCommand, CommandType, classify_command_text


@pytest.fixture
def store(tmp_path):
    return SignalStore(tmp_path / "test.db")


def _engine(store, paper, account):
    routing = RoutingConfig(
        rules=[RoutingRule(source="telegram", destinations=[account.account_id])],
        accounts={account.account_id: account},
    )
    lifecycle_manager = PositionLifecycleManager(brokers={"paper": paper}, store=store)
    engine = SignalCopierEngine(
        routing=routing, brokers={"paper": paper}, store=store, lifecycle_manager=lifecycle_manager
    )
    return engine, lifecycle_manager


async def _enter(engine, symbol="AAPL", quantity=10.0, stop_loss=90.0, price=100.0):
    signal = Signal(
        source="telegram", symbol=symbol, side=Side.BUY, quantity=quantity, stop_loss=stop_loss, price=price
    )
    results = await engine.handle_signal(signal)
    assert results[0].status == OrderStatus.FILLED
    return results[0]


# --- classify_command_text ------------------------------------------------------


def test_classifies_close_half():
    command = classify_command_text("close half", source="telegram", symbol="AAPL", analyst="a")
    assert command.command_type is CommandType.CLOSE_PERCENT
    assert command.fraction == 0.5


def test_classifies_move_sl_to_breakeven():
    command = classify_command_text("move sl to breakeven", source="telegram", symbol="AAPL", analyst="a")
    assert command.command_type is CommandType.MOVE_STOP
    assert command.stop_target == "breakeven"


def test_classifies_cancel():
    command = classify_command_text("cancel", source="telegram", symbol="AAPL", analyst="a")
    assert command.command_type is CommandType.CANCEL_ENTRY


def test_classifies_close_all_as_close_remainder():
    command = classify_command_text("close all", source="telegram", symbol="AAPL", analyst="a")
    assert command.command_type is CommandType.CLOSE_REMAINDER


def test_classifies_add_target():
    command = classify_command_text("add tp 120", source="telegram", symbol="AAPL", analyst="a")
    assert command.command_type is CommandType.ADD_TARGET
    assert command.price == 120.0


def test_unrecognized_text_returns_none():
    assert classify_command_text("looking strong today", source="telegram", symbol="AAPL", analyst="a") is None


# --- CLOSE_PERCENT: wired end-to-end ---------------------------------------------


@pytest.mark.asyncio
async def test_close_percent_wired_end_to_end(store):
    paper = PaperBroker()
    account = DestinationAccount(account_id="acct1", broker="paper", managed_lifecycle=True)
    engine, lifecycle_manager = _engine(store, paper, account)
    await _enter(engine, quantity=10.0)

    command = CanonicalCommand(command_type=CommandType.CLOSE_PERCENT, source="telegram", symbol="AAPL", fraction=0.5)
    result = await engine.apply_canonical_command(command, account)

    assert result.status == OrderStatus.FILLED
    assert result.filled_quantity == 5.0
    lifecycle = lifecycle_manager.get_lifecycle("acct1", "AAPL")
    assert lifecycle.confirmed_owned_quantity == 5.0
    assert lifecycle.closed is False
    assert paper.positions["acct1"]["AAPL"] == 5.0


@pytest.mark.asyncio
async def test_close_percent_never_oversells_past_owned_quantity(store):
    paper = PaperBroker()
    account = DestinationAccount(account_id="acct1", broker="paper", managed_lifecycle=True)
    engine, lifecycle_manager = _engine(store, paper, account)
    await _enter(engine, quantity=10.0)

    # 150% is invalid outright -- refused, not clamped-and-executed.
    command = CanonicalCommand(command_type=CommandType.CLOSE_PERCENT, source="telegram", symbol="AAPL", fraction=1.5)
    result = await engine.apply_canonical_command(command, account)
    assert result.status == OrderStatus.REJECTED
    assert paper.positions["acct1"]["AAPL"] == 10.0  # untouched


# --- MOVE_STOP (explicit price and breakeven): wired end-to-end -----------------


@pytest.mark.asyncio
async def test_move_stop_explicit_price_wired_end_to_end(store):
    paper = PaperBroker()
    account = DestinationAccount(account_id="acct1", broker="paper", managed_lifecycle=True)
    engine, lifecycle_manager = _engine(store, paper, account)
    await _enter(engine, quantity=10.0, stop_loss=90.0)

    command = CanonicalCommand(command_type=CommandType.MOVE_STOP, source="telegram", symbol="AAPL", stop_target=95.0)
    result = await engine.apply_canonical_command(command, account)

    assert result.status == OrderStatus.FILLED
    lifecycle = lifecycle_manager.get_lifecycle("acct1", "AAPL")
    assert lifecycle.stop.desired_price == 95.0
    assert lifecycle.stop.broker_confirmed_price == 95.0

    # the OLD stop is gone from the broker's book -- there's exactly one
    # resting stop order, at the new price, not two.
    open_orders = await paper.list_open_orders(account, "AAPL")
    assert len(open_orders) == 1
    assert open_orders[0].price == 95.0


@pytest.mark.asyncio
async def test_move_stop_breakeven_wired_end_to_end(store):
    paper = PaperBroker()
    account = DestinationAccount(account_id="acct1", broker="paper", managed_lifecycle=True)
    engine, lifecycle_manager = _engine(store, paper, account)
    await _enter(engine, quantity=10.0, stop_loss=90.0, price=100.0)

    command = CanonicalCommand(
        command_type=CommandType.MOVE_STOP, source="telegram", symbol="AAPL", stop_target="breakeven"
    )
    result = await engine.apply_canonical_command(command, account)

    assert result.status == OrderStatus.FILLED
    lifecycle = lifecycle_manager.get_lifecycle("acct1", "AAPL")
    assert lifecycle.stop.desired_price == 100.0  # the entry signal's stated price


@pytest.mark.asyncio
async def test_move_stop_breakeven_refused_when_entry_price_unknown(store):
    paper = PaperBroker()
    account = DestinationAccount(account_id="acct1", broker="paper", managed_lifecycle=True)
    engine, lifecycle_manager = _engine(store, paper, account)
    await _enter(engine, quantity=10.0, stop_loss=90.0, price=None)

    command = CanonicalCommand(
        command_type=CommandType.MOVE_STOP, source="telegram", symbol="AAPL", stop_target="breakeven"
    )
    result = await engine.apply_canonical_command(command, account)

    assert result.status == OrderStatus.REJECTED
    assert "no known entry price" in result.message
    lifecycle = lifecycle_manager.get_lifecycle("acct1", "AAPL")
    assert lifecycle.stop.desired_price == 90.0  # untouched -- never guessed at 0/None


@pytest.mark.asyncio
async def test_move_stop_never_loosens_protection(store):
    paper = PaperBroker()
    account = DestinationAccount(account_id="acct1", broker="paper", managed_lifecycle=True)
    engine, lifecycle_manager = _engine(store, paper, account)
    await _enter(engine, quantity=10.0, stop_loss=95.0)

    command = CanonicalCommand(command_type=CommandType.MOVE_STOP, source="telegram", symbol="AAPL", stop_target=90.0)
    result = await engine.apply_canonical_command(command, account)

    assert result.status == OrderStatus.REJECTED
    lifecycle = lifecycle_manager.get_lifecycle("acct1", "AAPL")
    assert lifecycle.stop.desired_price == 95.0  # unchanged


# --- CANCEL_ENTRY: wired end-to-end ----------------------------------------------


@pytest.mark.asyncio
async def test_cancel_entry_before_any_fill_wired_end_to_end(store):
    paper = PaperBroker()
    account = DestinationAccount(account_id="acct1", broker="paper", managed_lifecycle=True)
    engine, lifecycle_manager = _engine(store, paper, account)

    from app.lifecycle.models import PositionPlan

    plan = PositionPlan(account_id="acct1", symbol="AAPL", side=Side.BUY, planned_quantity=10.0, broker="paper", initial_stop=90.0)
    lifecycle_manager.start_plan(plan)
    assert lifecycle_manager.get_lifecycle("acct1", "AAPL") is not None

    command = CanonicalCommand(command_type=CommandType.CANCEL_ENTRY, source="telegram", symbol="AAPL")
    result = await engine.apply_canonical_command(command, account)

    assert result.status == OrderStatus.FILLED
    assert lifecycle_manager.get_lifecycle("acct1", "AAPL") is None


@pytest.mark.asyncio
async def test_cancel_entry_already_filled_is_rejected_not_a_close(store):
    paper = PaperBroker()
    account = DestinationAccount(account_id="acct1", broker="paper", managed_lifecycle=True)
    engine, lifecycle_manager = _engine(store, paper, account)
    await _enter(engine, quantity=10.0)

    command = CanonicalCommand(command_type=CommandType.CANCEL_ENTRY, source="telegram", symbol="AAPL")
    result = await engine.apply_canonical_command(command, account)

    assert result.status == OrderStatus.REJECTED
    assert "already filled" in result.message
    lifecycle = lifecycle_manager.get_lifecycle("acct1", "AAPL")
    assert lifecycle is not None and lifecycle.confirmed_owned_quantity == 10.0  # untouched


@pytest.mark.asyncio
async def test_cancel_entry_with_pending_broker_order_cancels_it(store):
    from app.models import OrderResult

    class _PendingEntryBroker(PaperBroker):
        async def place_order(self, signal, account, quantity, symbol):
            return OrderResult(
                account_id=account.account_id, status=OrderStatus.PENDING, signal_id=signal.id,
                broker_order_id="entry-1", filled_quantity=None, message="submitted",
            )

        async def cancel_order(self, account, broker_order_id):
            if broker_order_id == "entry-1":
                return True
            return await super().cancel_order(account, broker_order_id)

    broker = _PendingEntryBroker()
    account = DestinationAccount(account_id="acct1", broker="paper", managed_lifecycle=True)
    engine, lifecycle_manager = _engine(store, broker, account)

    signal = Signal(source="telegram", symbol="AAPL", side=Side.BUY, quantity=10.0, stop_loss=90.0)
    result = await engine.handle_signal(signal)
    assert result[0].status == OrderStatus.PENDING
    lifecycle = lifecycle_manager.get_lifecycle("acct1", "AAPL")
    assert lifecycle.pending_entry is not None
    assert lifecycle.pending_entry.broker_order_id == "entry-1"

    command = CanonicalCommand(command_type=CommandType.CANCEL_ENTRY, source="telegram", symbol="AAPL")
    cancel_result = await engine.apply_canonical_command(command, account)

    assert cancel_result.status == OrderStatus.FILLED
    lifecycle = lifecycle_manager.get_lifecycle("acct1", "AAPL")
    assert lifecycle is None  # nothing was ever confirmed filled -- fully unregistered


# --- CLOSE_REMAINDER: wired end-to-end -------------------------------------------


@pytest.mark.asyncio
async def test_close_remainder_wired_end_to_end(store):
    paper = PaperBroker()
    account = DestinationAccount(account_id="acct1", broker="paper", managed_lifecycle=True)
    engine, lifecycle_manager = _engine(store, paper, account)
    await _enter(engine, quantity=10.0)

    command = CanonicalCommand(command_type=CommandType.CLOSE_REMAINDER, source="telegram", symbol="AAPL")
    result = await engine.apply_canonical_command(command, account)

    assert result.status == OrderStatus.FILLED
    lifecycle = lifecycle_manager.get_lifecycle("acct1", "AAPL")
    assert lifecycle.closed is True


# --- unbacked commands: recorded, never silently dropped or auto-executed -------


@pytest.mark.asyncio
@pytest.mark.parametrize("command_type", [CommandType.ADD_TARGET, CommandType.REMOVE_TARGET, CommandType.ADD_ENTRY])
async def test_unbacked_commands_are_recorded_never_silently_executed(store, command_type):
    paper = PaperBroker()
    account = DestinationAccount(account_id="acct1", broker="paper", managed_lifecycle=True)
    engine, lifecycle_manager = _engine(store, paper, account)
    await _enter(engine, quantity=10.0)

    command = CanonicalCommand(command_type=command_type, source="telegram", symbol="AAPL", price=999.0, fraction=0.5)
    result = await engine.apply_canonical_command(command, account)

    # Never silently dropped: a real, descriptive result comes back.
    assert result.status == OrderStatus.REJECTED
    assert "not yet actionable" in result.message
    assert "no backing execution capability" in result.message

    # Never silently approximated as something else: nothing about the
    # broker-side position/targets/stop changed as a side effect.
    lifecycle = lifecycle_manager.get_lifecycle("acct1", "AAPL")
    assert lifecycle.confirmed_owned_quantity == 10.0
    assert lifecycle.plan.targets == []
    assert paper.positions["acct1"]["AAPL"] == 10.0


@pytest.mark.asyncio
async def test_command_on_non_managed_lifecycle_account_is_rejected_not_executed(store):
    paper = PaperBroker()
    account = DestinationAccount(account_id="acct1", broker="paper", managed_lifecycle=False)
    engine, _ = _engine(store, paper, account)

    command = CanonicalCommand(command_type=CommandType.CLOSE_PERCENT, source="telegram", symbol="AAPL", fraction=0.5)
    result = await engine.apply_canonical_command(command, account)

    assert result.status == OrderStatus.REJECTED
    assert "not managed_lifecycle" in result.message
