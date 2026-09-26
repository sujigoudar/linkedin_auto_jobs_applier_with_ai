import asyncio

import pytest

from app.brokers.paper import PaperBroker
from app.lifecycle.manager import PositionLifecycleManager
from app.lifecycle.models import PositionPlan, ProtectionStatus, Target, TargetAction, TrailingPolicy
from app.models import DestinationAccount, OrderResult, OrderStatus, Side, Signal


async def _enter(manager, broker, account, plan, filled_quantity):
    """Mirrors the real flow: the caller submits the entry via the broker
    itself (the manager never does), *then* tells the manager what filled."""
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


@pytest.fixture
def manager(broker):
    return PositionLifecycleManager(brokers={"paper": broker})


def _plan(**overrides) -> PositionPlan:
    defaults = dict(
        account_id="acct1",
        symbol="AAPL",
        side=Side.BUY,
        planned_quantity=100.0,
        broker="paper",
        initial_stop=48.50,
    )
    defaults.update(overrides)
    return PositionPlan(**defaults)


@pytest.mark.asyncio
async def test_no_stop_resolved_refuses_entry(manager):
    plan = _plan(initial_stop=None)
    error = manager.validate_plan(plan)
    assert error is not None
    assert "refusing to enter unprotected" in error


@pytest.mark.asyncio
async def test_valid_plan_passes_validation(manager):
    assert manager.validate_plan(_plan()) is None


@pytest.mark.asyncio
async def test_plan_refused_when_broker_cannot_protect_position():
    from app.brokers.signalstack import SignalStackBroker

    unprotectable_broker = SignalStackBroker()
    manager_without_protection = PositionLifecycleManager(brokers={"signalstack": unprotectable_broker})

    error = manager_without_protection.validate_plan(_plan(broker="signalstack"))

    assert error is not None
    assert "no verified way to keep this position protected" in error


@pytest.mark.asyncio
async def test_plan_refused_when_broker_is_not_registered(manager):
    error = manager.validate_plan(_plan(broker="nonexistent"))
    assert error is not None
    assert "no broker adapter registered" in error


@pytest.mark.asyncio
async def test_second_same_symbol_entry_is_refused_not_silently_merged(manager, account, broker):
    """EXE-09: a second entry for the same (account, symbol) used to
    silently overwrite the first lifecycle's dict entry via start_plan --
    discarding its confirmed_owned_quantity/stop tracking entirely while
    the first entry's real broker-side exposure and resting stop kept
    existing, now completely untracked."""
    await _enter(manager, broker, account, _plan(planned_quantity=20.0), 20.0)

    error = manager.validate_plan(_plan(planned_quantity=10.0))

    assert error is not None
    assert "already exists" in error
    # The original lifecycle must be untouched.
    lifecycle = manager.get_lifecycle("acct1", "AAPL")
    assert lifecycle.confirmed_owned_quantity == 20.0


@pytest.mark.asyncio
async def test_partial_fill_sets_confirmed_owned_not_planned_quantity(manager, account, broker):
    plan = _plan(planned_quantity=100.0)
    lifecycle = await _enter(manager, broker, account, plan, 62.0)

    assert lifecycle.confirmed_owned_quantity == 62.0
    assert manager.arbiter.available_to_sell("acct1", "AAPL") == 62.0


@pytest.mark.asyncio
async def test_protection_submitted_and_confirmed_after_fill(manager, account, broker):
    lifecycle = await _enter(manager, broker, account, _plan(initial_stop=48.50), 62.0)

    assert lifecycle.stop.status == ProtectionStatus.STOP_CONFIRMED
    assert lifecycle.stop.broker_confirmed_price == 48.50
    assert lifecycle.stop.protected_quantity == 62.0
    assert lifecycle.stop.broker_order_id is not None
    # actually resting at the broker, not just a DB row
    fills = broker.simulate_price("AAPL", 48.00)
    assert len(fills) == 1


@pytest.mark.asyncio
async def test_target_resize_matches_worked_example_full_fill(manager, account, broker):
    """Design's worked example: 62 owned, 62-share stop, target sells 15 (fully
    filled) -> remaining 47, stop resized to 47."""
    await _enter(manager, broker, account, _plan(planned_quantity=100.0, initial_stop=48.50), 62.0)

    result = await manager.request_exit(account, "AAPL", 15.0, source="target")

    assert result.status == OrderStatus.FILLED
    assert result.filled_quantity == 15.0
    lifecycle = manager.get_lifecycle("acct1", "AAPL")
    assert lifecycle.stop.protected_quantity == 47.0
    assert manager.arbiter.available_to_sell("acct1", "AAPL") == 47.0
    # new stop actually resting for 47, not the old 62
    fills = broker.simulate_price("AAPL", 47.00)
    assert fills[0].filled_quantity == 47.0


@pytest.mark.asyncio
async def test_target_resize_with_partial_fill_matches_worked_example(manager, account, broker, monkeypatch):
    """Design section 6: requested 15, only 8 actually fill -> remaining stop
    quantity must be 54 (62 - 8), not the planned 47 (62 - 15)."""
    await _enter(manager, broker, account, _plan(planned_quantity=100.0, initial_stop=48.50), 62.0)

    async def partial_fill_place_order(signal, acct, quantity, symbol):
        return OrderResult(
            account_id=acct.account_id,
            status=OrderStatus.FILLED,
            signal_id=signal.id,
            filled_quantity=8.0,  # broker only filled 8 of the requested 15
            filled_price=51.50,
            message="partial fill",
        )

    monkeypatch.setattr(broker, "place_order", partial_fill_place_order)

    await manager.request_exit(account, "AAPL", 15.0, source="target")

    lifecycle = manager.get_lifecycle("acct1", "AAPL")
    assert lifecycle.stop.protected_quantity == 54.0
    assert manager.arbiter.available_to_sell("acct1", "AAPL") == 54.0


@pytest.mark.asyncio
async def test_close_arbiter_prevents_overselling_from_two_targets(manager, account, broker):
    await _enter(manager, broker, account, _plan(planned_quantity=100.0, initial_stop=48.50), 62.0)

    results = await asyncio.gather(
        manager.request_exit(account, "AAPL", 40.0, source="target1"),
        manager.request_exit(account, "AAPL", 40.0, source="target2"),
    )

    total_sold = sum(r.filled_quantity or 0 for r in results if r.status == OrderStatus.FILLED)
    assert total_sold <= 62.0
    assert manager.arbiter.available_to_sell("acct1", "AAPL") >= 0


@pytest.mark.asyncio
async def test_stop_fill_closes_lifecycle(manager, account, broker):
    await _enter(manager, broker, account, _plan(planned_quantity=62.0, initial_stop=48.50), 62.0)

    fills = broker.simulate_price("AAPL", 48.00)
    assert len(fills) == 1
    await manager.on_stop_filled(account, "AAPL", filled_quantity=fills[0].filled_quantity, filled_price=48.00)

    lifecycle = manager.get_lifecycle("acct1", "AAPL")
    assert lifecycle.closed is True
    assert lifecycle.stop.status == ProtectionStatus.UNPROTECTED
    assert manager.arbiter.available_to_sell("acct1", "AAPL") == 0.0


@pytest.mark.asyncio
async def test_stop_fill_applies_to_the_tracked_position(account, broker, tmp_path):
    """EXE-05: on_stop_filled used to only update the in-memory/persisted
    lifecycle ledger, never SignalStore.positions -- local holdings could
    stay open after the venue was actually flat."""
    from app.db import SignalStore

    store = SignalStore(tmp_path / "test.db")
    manager = PositionLifecycleManager(brokers={"paper": broker}, store=store)
    await _enter(manager, broker, account, _plan(planned_quantity=62.0, initial_stop=48.50), 62.0)
    # _enter() mirrors on_entry_fill directly, bypassing the engine's own
    # store.record_fill() call for the entry -- seed the store the way the
    # real engine would have, so this test isolates on_stop_filled's own
    # store-application behavior.
    store.record_fill("acct1", "AAPL", Side.BUY, 62.0)

    fills = broker.simulate_price("AAPL", 48.00)
    await manager.on_stop_filled(account, "AAPL", filled_quantity=fills[0].filled_quantity, filled_price=48.00)

    assert store.get_position("acct1", "AAPL") == 0.0


@pytest.mark.asyncio
async def test_partial_stop_fill_preserves_remaining_coverage_and_order_id(account, broker, tmp_path, monkeypatch):
    """EXE-05: a stop notification can itself be a partial fill -- the
    remainder may still be resting at the broker under the same order id.
    Unconditionally clearing broker_order_id/marking UNPROTECTED would lose
    track of that still-working order and report zero coverage even though
    some protection remains."""
    from app.db import SignalStore

    store = SignalStore(tmp_path / "test.db")
    manager = PositionLifecycleManager(brokers={"paper": broker}, store=store)
    await _enter(manager, broker, account, _plan(planned_quantity=62.0, initial_stop=48.50), 62.0)
    store.record_fill("acct1", "AAPL", Side.BUY, 62.0)  # see the full-fill test above for why
    lifecycle = manager.get_lifecycle("acct1", "AAPL")
    original_stop_id = lifecycle.stop.broker_order_id

    await manager.on_stop_filled(account, "AAPL", filled_quantity=20.0, filled_price=48.00)

    assert lifecycle.closed is False
    assert lifecycle.confirmed_owned_quantity == 42.0
    assert lifecycle.stop.protected_quantity == 42.0  # 62 - 20, remainder still covered
    assert lifecycle.stop.broker_order_id == original_stop_id  # still the same resting order
    assert lifecycle.stop.status == ProtectionStatus.STOP_CONFIRMED
    assert store.get_position("acct1", "AAPL") == 42.0


@pytest.mark.asyncio
async def test_exit_after_stop_already_filled_is_rejected_not_oversold(manager, account, broker):
    """Design section 7: a target firing after the stop already filled must not
    also execute — the arbiter must have nothing left to sell."""
    await _enter(manager, broker, account, _plan(planned_quantity=62.0, initial_stop=48.50), 62.0)

    fills = broker.simulate_price("AAPL", 48.00)
    await manager.on_stop_filled(account, "AAPL", filled_quantity=fills[0].filled_quantity, filled_price=48.00)

    result = await manager.request_exit(account, "AAPL", 15.0, source="target")

    # the stop fully closed the lifecycle, so this is correctly refused either
    # as "nothing left to sell" or "no active lifecycle" — the point is REJECTED,
    # not a specific wording
    assert result.status == OrderStatus.REJECTED


@pytest.mark.asyncio
async def test_price_update_fires_target_and_sells(manager, account, broker):
    plan = _plan(
        planned_quantity=100.0,
        initial_stop=48.50,
        targets=[Target(trigger_price=51.50, action=TargetAction.SELL, reduce_fraction=0.25)],
    )
    await _enter(manager, broker, account, plan, 100.0)

    results = await manager.on_price_update(account, "AAPL", 51.60)

    assert len(results) == 1
    assert results[0].status == OrderStatus.FILLED
    assert results[0].filled_quantity == 25.0  # 25% of the planned 100
    assert manager.arbiter.available_to_sell("acct1", "AAPL") == 75.0


@pytest.mark.asyncio
async def test_target_does_not_fire_twice(manager, account, broker):
    plan = _plan(
        planned_quantity=100.0,
        initial_stop=48.50,
        targets=[Target(trigger_price=51.50, action=TargetAction.SELL, reduce_fraction=0.25)],
    )
    await _enter(manager, broker, account, plan, 100.0)

    await manager.on_price_update(account, "AAPL", 51.60)
    second_results = await manager.on_price_update(account, "AAPL", 52.00)

    assert second_results == []


@pytest.mark.asyncio
async def test_trailing_stop_ratchets_up_and_never_loosens(manager, account, broker):
    plan = _plan(
        planned_quantity=62.0,
        initial_stop=48.50,
        trailing=TrailingPolicy(trail_distance=1.50, active=True),
    )
    await _enter(manager, broker, account, plan, 62.0)

    await manager.on_price_update(account, "AAPL", 52.00)  # floor -> 50.50
    lifecycle = manager.get_lifecycle("acct1", "AAPL")
    assert lifecycle.stop.desired_price == 50.50

    await manager.on_price_update(account, "AAPL", 53.00)  # floor -> 51.50, tighter
    assert lifecycle.stop.desired_price == 51.50

    await manager.on_price_update(account, "AAPL", 52.50)  # price dropped, floor must NOT loosen
    assert lifecycle.stop.desired_price == 51.50

    # the stop must now be resting at 51.50, not the original 48.50: a price just
    # above 51.50 must NOT trigger it, but anything at/below 51.50 (e.g. 51.40) must
    assert broker.simulate_price("AAPL", 51.60) == []
    fills = broker.simulate_price("AAPL", 51.40)
    assert len(fills) == 1
    assert fills[0].filled_quantity == 62.0


@pytest.mark.asyncio
async def test_first_trailing_activation_cannot_loosen_an_existing_stop(manager, account, broker):
    """PRO-01: on the FIRST trail update, trailing.floor_price is still
    None, so comparing only against it (not the existing stop) treated any
    candidate as 'improved'. Existing stop 95, price 100, trail distance 20
    -> candidate 80, which is a LOOSER stop and must be refused."""
    plan = _plan(
        planned_quantity=10.0,
        initial_stop=95.0,
        trailing=TrailingPolicy(trail_distance=20.0, active=True),
    )
    await _enter(manager, broker, account, plan, 10.0)
    lifecycle = manager.get_lifecycle("acct1", "AAPL")
    assert lifecycle.stop.desired_price == 95.0

    await manager.on_price_update(account, "AAPL", 100.0)  # candidate floor = 80, worse than 95

    assert lifecycle.stop.desired_price == 95.0

    # Mirrored short case: existing stop 105, price 100, trail distance 20 -> candidate 120 (worse).
    short_plan = _plan(
        symbol="MSFT",
        side=Side.SELL,
        planned_quantity=10.0,
        initial_stop=105.0,
        trailing=TrailingPolicy(trail_distance=20.0, active=True),
    )
    await _enter(manager, broker, account, short_plan, 10.0)
    short_lifecycle = manager.get_lifecycle("acct1", "MSFT")
    assert short_lifecycle.stop.desired_price == 105.0

    await manager.on_price_update(account, "MSFT", 100.0)

    assert short_lifecycle.stop.desired_price == 105.0


@pytest.mark.asyncio
async def test_target_can_tighten_stop_instead_of_selling(manager, account, broker):
    """Design section 9: a provider TP can mean 'lock in profit' rather than 'sell'."""
    plan = _plan(
        planned_quantity=62.0,
        initial_stop=95.0,
        side=Side.BUY,
        targets=[Target(trigger_price=105.0, action=TargetAction.TIGHTEN_STOP)],
    )
    await _enter(manager, broker, account, plan, 62.0)

    results = await manager.on_price_update(account, "AAPL", 105.0)

    assert results == []  # no sell happened
    lifecycle = manager.get_lifecycle("acct1", "AAPL")
    assert lifecycle.stop.desired_price == 105.0
    assert manager.arbiter.available_to_sell("acct1", "AAPL") == 62.0  # still fully in the trade


@pytest.mark.asyncio
async def test_replace_that_returns_a_new_order_id_updates_tracked_id(manager, account, broker, monkeypatch):
    """A broker (e.g. Alpaca) whose replace cancels-and-recreates gives back a
    NEW order id — the manager must track that id, not keep the stale one, or
    a later cancel/replace would target a dead order."""
    plan = _plan(
        planned_quantity=62.0,
        initial_stop=48.50,
        trailing=TrailingPolicy(trail_distance=1.50, active=True),
    )
    await _enter(manager, broker, account, plan, 62.0)
    original_stop_id = manager.get_lifecycle("acct1", "AAPL").stop.broker_order_id

    async def replace_with_new_id(account, broker_order_id, new_quantity, new_price=None):
        return OrderResult(
            account_id=account.account_id,
            status=OrderStatus.PENDING,
            signal_id="",
            broker_order_id="a-brand-new-order-id",
            message="replaced with a new id",
        )

    monkeypatch.setattr(broker, "replace_stop_quantity", replace_with_new_id)

    await manager.on_price_update(account, "AAPL", 52.00)  # triggers a trailing replace

    lifecycle = manager.get_lifecycle("acct1", "AAPL")
    assert lifecycle.stop.broker_order_id == "a-brand-new-order-id"
    assert lifecycle.stop.broker_order_id != original_stop_id


@pytest.mark.asyncio
async def test_halted_position_rejects_new_exits(manager, account, broker):
    await _enter(manager, broker, account, _plan(planned_quantity=62.0, initial_stop=48.50), 62.0)
    await manager.arbiter.halt("acct1", "AAPL", "incident: manual halt")

    result = await manager.request_exit(account, "AAPL", 10.0, source="target")

    assert result.status == OrderStatus.REJECTED
    assert "halted" in result.message
