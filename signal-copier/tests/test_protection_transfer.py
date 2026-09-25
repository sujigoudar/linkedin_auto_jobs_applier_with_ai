"""Reproduces the audit's "most important partial-profit gap" table exactly:
a 62-share position, a 15-share target that only confirms asynchronously
(PENDING), 8 shares actually fill, and the stop must NOT be restored to 54
until the remaining 7-share order is confirmed done — and once it is, the
restore target reflects whatever ACTUALLY happened (8 filled -> 54; 8+3
filled during cancellation -> 51), never the originally requested 15.
"""
import pytest

from app.brokers.paper import PaperBroker
from app.db import SignalStore
from app.lifecycle.manager import PositionLifecycleManager
from app.lifecycle.models import PositionPlan, ProtectionStatus, TransferPhase
from app.models import DestinationAccount, OrderResult, OrderStatus, Side, Signal


async def _enter(manager, broker, account, plan, filled_quantity):
    manager.start_plan(plan)
    entry_signal = Signal(source="test", symbol=plan.symbol, side=plan.side)
    await broker.place_order(entry_signal, account, filled_quantity, plan.symbol)
    return await manager.on_entry_fill(account, plan.symbol, filled_quantity)


@pytest.fixture
def account():
    return DestinationAccount(account_id="acct1", broker="paper")


@pytest.fixture
def broker():
    return PaperBroker()


def _plan(**overrides) -> PositionPlan:
    defaults = dict(account_id="acct1", symbol="AAPL", side=Side.BUY, planned_quantity=62.0, initial_stop=48.50)
    defaults.update(overrides)
    return PositionPlan(**defaults)


def _pending_exit_place_order(broker_order_id="tp-order-1"):
    """A broker.place_order stand-in mirroring an async-confirming broker
    (e.g. Alpaca): reports PENDING immediately, no fill known yet."""

    async def place_order(signal, account, quantity, symbol):
        return OrderResult(
            account_id=account.account_id,
            status=OrderStatus.PENDING,
            signal_id=signal.id,
            broker_order_id=broker_order_id,
            message="submitted, awaiting fill",
        )

    return place_order


@pytest.mark.asyncio
async def test_pending_exit_does_not_restore_stop_and_marks_coverage_deficit(account, broker, monkeypatch):
    manager = PositionLifecycleManager(brokers={"paper": broker})
    await _enter(manager, broker, account, _plan(), 62.0)
    monkeypatch.setattr(broker, "place_order", _pending_exit_place_order())

    result = await manager.request_exit(account, "AAPL", 15.0, source="target", reason="target @ 51.50")

    assert result.status == OrderStatus.PENDING
    lifecycle = manager.get_lifecycle("acct1", "AAPL")
    assert lifecycle.pending_exit is not None
    assert lifecycle.pending_exit.requested_quantity == 15.0
    assert lifecycle.pending_exit.remainder_resolved is False
    assert lifecycle.pending_exit.phase == TransferPhase.AWAITING_REMAINDER_RESOLUTION
    # the old 62-share stop was cancelled to free these shares, but nothing
    # re-armed yet — those 15 shares are genuinely uncovered right now
    assert lifecycle.stop.status == ProtectionStatus.UNPROTECTED
    assert lifecycle.covered_quantity == 0.0
    assert lifecycle.uncovered_quantity == 62.0
    # the 15 requested shares are still excluded from what's sellable, so a
    # second target can't also claim them
    assert manager.arbiter.available_to_sell("acct1", "AAPL") == 47.0


@pytest.mark.asyncio
async def test_second_exit_is_refused_while_first_remains_unresolved(account, broker, monkeypatch):
    manager = PositionLifecycleManager(brokers={"paper": broker})
    await _enter(manager, broker, account, _plan(), 62.0)
    monkeypatch.setattr(broker, "place_order", _pending_exit_place_order())
    await manager.request_exit(account, "AAPL", 15.0, source="target")

    result = await manager.request_exit(account, "AAPL", 10.0, source="trailing")

    assert result.status == OrderStatus.REJECTED
    assert "hasn't resolved yet" in result.message


@pytest.mark.asyncio
async def test_resolving_a_partial_fill_before_remainder_is_cancelled_does_not_restore_early(account, broker, monkeypatch):
    manager = PositionLifecycleManager(brokers={"paper": broker})
    await _enter(manager, broker, account, _plan(), 62.0)
    monkeypatch.setattr(broker, "place_order", _pending_exit_place_order())
    await manager.request_exit(account, "AAPL", 15.0, source="target")

    # 8 of the 15 have filled so far, but the remaining 7 are still live —
    # this must NOT bump the stop to 54 yet (design's exact "do not increase
    # the stop to 54 yet" step).
    await manager.resolve_pending_exit(account, "AAPL", confirmed_filled_quantity=8.0, remainder_cancelled=False)

    lifecycle = manager.get_lifecycle("acct1", "AAPL")
    assert lifecycle.pending_exit is not None
    assert lifecycle.pending_exit.remainder_resolved is False
    assert lifecycle.pending_exit.confirmed_filled_quantity == 8.0
    assert lifecycle.stop.status == ProtectionStatus.UNPROTECTED
    assert manager.arbiter.available_to_sell("acct1", "AAPL") == 47.0  # unchanged — still not settled


@pytest.mark.asyncio
async def test_remainder_cancelled_after_8_of_15_fill_restores_stop_to_54_not_47(account, broker, monkeypatch):
    """The audit's primary table: 62 owned, stop reduced to protect 47,
    15-share target accepted, 8 fill, remaining 7 confirmed cancelled ->
    restore target is 54 (62 - 8), never 47 (62 - 15) and never the naive 54
    computed too early."""
    manager = PositionLifecycleManager(brokers={"paper": broker})
    await _enter(manager, broker, account, _plan(), 62.0)
    monkeypatch.setattr(broker, "place_order", _pending_exit_place_order())
    await manager.request_exit(account, "AAPL", 15.0, source="target")

    await manager.resolve_pending_exit(account, "AAPL", confirmed_filled_quantity=8.0, remainder_cancelled=True)

    lifecycle = manager.get_lifecycle("acct1", "AAPL")
    assert lifecycle.pending_exit is None
    assert lifecycle.confirmed_owned_quantity == 54.0
    assert lifecycle.stop.status == ProtectionStatus.STOP_CONFIRMED
    assert lifecycle.stop.protected_quantity == 54.0
    assert manager.arbiter.available_to_sell("acct1", "AAPL") == 54.0
    fills = broker.simulate_price("AAPL", 48.00)
    assert fills[0].filled_quantity == 54.0


@pytest.mark.asyncio
async def test_more_fills_during_cancellation_restore_to_51_not_54(account, broker, monkeypatch):
    """The audit's alternative row: 3 more shares fill while cancellation is
    in flight (total 11 of 15) -> restore target is 51 (62 - 11), not 54."""
    manager = PositionLifecycleManager(brokers={"paper": broker})
    await _enter(manager, broker, account, _plan(), 62.0)
    monkeypatch.setattr(broker, "place_order", _pending_exit_place_order())
    await manager.request_exit(account, "AAPL", 15.0, source="target")

    await manager.resolve_pending_exit(account, "AAPL", confirmed_filled_quantity=11.0, remainder_cancelled=True)

    lifecycle = manager.get_lifecycle("acct1", "AAPL")
    assert lifecycle.confirmed_owned_quantity == 51.0
    assert lifecycle.stop.protected_quantity == 51.0
    assert manager.arbiter.available_to_sell("acct1", "AAPL") == 51.0


@pytest.mark.asyncio
async def test_full_fill_of_requested_quantity_resolves_immediately(account, broker, monkeypatch):
    """If the broker eventually reports the full 15 filled, that alone is a
    resolution — no separate cancellation confirmation is needed."""
    manager = PositionLifecycleManager(brokers={"paper": broker})
    await _enter(manager, broker, account, _plan(), 62.0)
    monkeypatch.setattr(broker, "place_order", _pending_exit_place_order())
    await manager.request_exit(account, "AAPL", 15.0, source="target")

    await manager.resolve_pending_exit(account, "AAPL", confirmed_filled_quantity=15.0, remainder_cancelled=False)

    lifecycle = manager.get_lifecycle("acct1", "AAPL")
    assert lifecycle.pending_exit is None
    assert lifecycle.confirmed_owned_quantity == 47.0
    assert lifecycle.stop.protected_quantity == 47.0


@pytest.mark.asyncio
async def test_trailing_update_does_not_touch_stop_while_pending_exit_unresolved(account, broker, monkeypatch):
    """A trailing ratchet firing while a target's remainder is still
    unresolved must not re-arm a stop sized off `tx.owned`, which still
    includes shares that may leave via the pending order."""
    from app.lifecycle.models import TrailingPolicy

    plan = _plan(trailing=TrailingPolicy(trail_distance=1.0, active=True))
    manager = PositionLifecycleManager(brokers={"paper": broker})
    await _enter(manager, broker, account, plan, 62.0)
    monkeypatch.setattr(broker, "place_order", _pending_exit_place_order())
    await manager.request_exit(account, "AAPL", 15.0, source="target")

    results = await manager.on_price_update(account, "AAPL", 60.0)  # would otherwise trail the stop up

    assert results == []
    lifecycle = manager.get_lifecycle("acct1", "AAPL")
    assert lifecycle.stop.status == ProtectionStatus.UNPROTECTED  # still not re-armed
    assert lifecycle.pending_exit is not None


@pytest.mark.asyncio
async def test_persisted_lifecycle_resumes_after_restart_with_deficit_intact(account, broker, monkeypatch, tmp_path):
    store = SignalStore(tmp_path / "test.db")
    manager1 = PositionLifecycleManager(brokers={"paper": broker}, store=store)
    await _enter(manager1, broker, account, _plan(), 62.0)
    monkeypatch.setattr(broker, "place_order", _pending_exit_place_order())
    await manager1.request_exit(account, "AAPL", 15.0, source="target")

    # simulate a process restart: a brand new manager, seeded only from the store
    manager2 = PositionLifecycleManager(brokers={"paper": broker}, store=store)
    manager2.restore_from_store()

    lifecycle = manager2.get_lifecycle("acct1", "AAPL")
    assert lifecycle is not None
    assert lifecycle.confirmed_owned_quantity == 62.0
    assert lifecycle.pending_exit is not None
    assert lifecycle.pending_exit.requested_quantity == 15.0
    assert lifecycle.pending_exit.broker_order_id == "tp-order-1"
    # the resumed arbiter still correctly excludes the in-flight 15 shares
    assert manager2.arbiter.available_to_sell("acct1", "AAPL") == 47.0

    # and the resumed manager can finish the transfer exactly like the original would have
    await manager2.resolve_pending_exit(account, "AAPL", confirmed_filled_quantity=8.0, remainder_cancelled=True)
    resumed_lifecycle = manager2.get_lifecycle("acct1", "AAPL")
    assert resumed_lifecycle.confirmed_owned_quantity == 54.0
    assert resumed_lifecycle.stop.protected_quantity == 54.0


@pytest.mark.asyncio
async def test_closed_lifecycle_is_not_persisted_after_full_exit(account, broker, tmp_path):
    store = SignalStore(tmp_path / "test.db")
    manager = PositionLifecycleManager(brokers={"paper": broker}, store=store)
    await _enter(manager, broker, account, _plan(planned_quantity=62.0), 62.0)

    result = await manager.request_exit(account, "AAPL", 62.0, source="provider_exit")

    assert result.status == OrderStatus.FILLED
    assert store.load_lifecycle_states() == []
