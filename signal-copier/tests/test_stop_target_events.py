"""PU-A4: the real, append-only stop/target lifecycle event log --
`SignalStore.stop_target_events`, written by
`PositionLifecycleManager`'s own real state-changing call sites (see
`app/lifecycle/models.py`'s `StopTargetEventType` for exactly which event
types exist and why the catalog's breakeven/trailing-activation event
types are a documented gap rather than fabricated here)."""
import pytest

from app.brokers.paper import PaperBroker
from app.db import SignalStore
from app.lifecycle.manager import PositionLifecycleManager
from app.lifecycle.models import (
    PositionPlan,
    StopTargetEventType,
    Target,
    TargetAction,
    TrailingPolicy,
)
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


@pytest.fixture
def store(tmp_path):
    return SignalStore(tmp_path / "test.db")


@pytest.fixture
def manager(broker, store):
    return PositionLifecycleManager(brokers={"paper": broker}, store=store)


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
async def test_initial_stop_placement_logs_stop_placed_with_no_previous_price(manager, account, broker, store):
    await _enter(manager, broker, account, _plan(initial_stop=48.50), 62.0)

    events = store.list_stop_target_events("acct1", "AAPL")

    assert len(events) == 1
    event = events[0]
    assert event["event_type"] == StopTargetEventType.STOP_PLACED.value
    assert event["price"] == 48.50
    assert event["previous_price"] is None
    assert event["source"] == "signal"
    assert event["account_id"] == "acct1"
    assert event["symbol"] == "AAPL"


@pytest.mark.asyncio
async def test_stop_tightening_logs_previous_and_new_price_in_the_correct_order(manager, account, broker, store):
    """LOAD-BEARING: a stop-tightened event with `previous_price`/`price`
    swapped would misrepresent real risk behavior -- reporting a stop that
    tightened from 48.50 to 49.00 as if it had gone from 49.00 down to
    48.50 (i.e. loosened) is exactly backwards from what really happened."""
    lifecycle = await _enter(manager, broker, account, _plan(initial_stop=48.50), 62.0)

    await manager._tighten_stop_to(lifecycle, account, 49.00)

    events = store.list_stop_target_events("acct1", "AAPL")
    assert len(events) == 2  # the initial placement, then this tightening
    tighten_event = events[1]
    assert tighten_event["event_type"] == StopTargetEventType.STOP_TIGHTENED.value
    assert tighten_event["previous_price"] == 48.50
    assert tighten_event["price"] == 49.00
    # The literal risk-behavior assertion: the new price must be tighter
    # (higher, for a long) than the previous one, not the reverse.
    assert tighten_event["price"] > tighten_event["previous_price"]


@pytest.mark.asyncio
async def test_loosening_attempt_is_a_no_op_and_logs_no_event(manager, account, broker, store):
    lifecycle = await _enter(manager, broker, account, _plan(initial_stop=48.50), 62.0)

    await manager._tighten_stop_to(lifecycle, account, 47.00)  # looser than 48.50 -- must be rejected

    events = store.list_stop_target_events("acct1", "AAPL")
    assert len(events) == 1  # only the initial placement -- no fabricated tighten event
    assert lifecycle.stop.desired_price == 48.50


@pytest.mark.asyncio
async def test_trailing_ratchet_logs_stop_tightened_event(manager, account, broker, store):
    """TrailingPolicy/`_update_trailing` are real, tested code, but (per
    this branch's own documented gap) nothing in the live signal path
    constructs a non-null TrailingPolicy today -- so this exercises the
    manager's real trailing logic directly, the same way this codebase's
    own existing trailing-ratchet tests do, rather than fabricating an
    engine-level signal path that doesn't exist here."""
    plan = _plan(initial_stop=48.50, trailing=TrailingPolicy(trail_distance=2.0, active=True))
    lifecycle = await _enter(manager, broker, account, plan, 62.0)

    await manager._update_trailing(lifecycle, account, 55.0)  # floor = 55 - 2 = 53, tighter than 48.50

    events = store.list_stop_target_events("acct1", "AAPL")
    assert len(events) == 2
    trail_event = events[1]
    assert trail_event["event_type"] == StopTargetEventType.STOP_TIGHTENED.value
    assert trail_event["previous_price"] == 48.50
    assert trail_event["price"] == 53.0


@pytest.mark.asyncio
async def test_target_hit_logs_target_hit_event_with_trigger_price(manager, account, broker, store):
    """The single take-profit target app/engine.py's real live signal path
    constructs (`Target(action=SELL)` from `signal.take_profit`) actually
    firing IS a real, already-reachable moment -- not a fabricated one."""
    plan = _plan(
        initial_stop=48.50,
        targets=[Target(trigger_price=55.0, action=TargetAction.SELL, reduce_fraction=1.0)],
    )
    await _enter(manager, broker, account, plan, 62.0)

    results = await manager.on_price_update(account, "AAPL", 55.0)

    assert len(results) == 1
    assert results[0].status == OrderStatus.FILLED
    events = store.list_stop_target_events("acct1", "AAPL")
    event_types = [e["event_type"] for e in events]
    assert StopTargetEventType.TARGET_HIT.value in event_types
    hit_event = next(e for e in events if e["event_type"] == StopTargetEventType.TARGET_HIT.value)
    assert hit_event["price"] == 55.0
    assert hit_event["previous_price"] is None
    assert hit_event["source"] == "signal"


@pytest.mark.asyncio
async def test_protection_failure_logs_protection_failed_event(manager, account, broker, store, monkeypatch):
    async def rejected_stop(*args, **kwargs):
        return None

    monkeypatch.setattr(broker, "place_protective_stop", rejected_stop)

    await _enter(manager, broker, account, _plan(initial_stop=48.50), 62.0)

    events = store.list_stop_target_events("acct1", "AAPL")
    assert len(events) == 1
    event = events[0]
    assert event["event_type"] == StopTargetEventType.PROTECTION_FAILED.value
    assert event["price"] == 48.50
    assert event["previous_price"] is None


@pytest.mark.asyncio
async def test_ambiguous_stop_result_also_logs_protection_failed(manager, account, broker, store, monkeypatch):
    """A FILLED-on-submission or no-broker_order_id result is treated as
    unprotected (PRO-05), not confirmed coverage -- and must log the same
    honest PROTECTION_FAILED event, not a fabricated STOP_PLACED."""

    async def ambiguous_stop(*args, **kwargs):
        return OrderResult(account_id="acct1", status=OrderStatus.FILLED, signal_id="", message="ambiguous")

    monkeypatch.setattr(broker, "place_protective_stop", ambiguous_stop)

    await _enter(manager, broker, account, _plan(initial_stop=48.50), 62.0)

    events = store.list_stop_target_events("acct1", "AAPL")
    assert len(events) == 1
    assert events[0]["event_type"] == StopTargetEventType.PROTECTION_FAILED.value


@pytest.mark.asyncio
async def test_retry_of_same_price_after_failure_logs_stop_placed_not_tightened(
    manager, account, broker, store, monkeypatch
):
    """retry_unprotected_positions re-submits the SAME desired price after
    an earlier failure -- that's a fresh confirmation (STOP_PLACED), never
    a fabricated STOP_TIGHTENED (nothing tightened; the price is
    unchanged)."""
    call_count = {"n": 0}
    real_place = broker.place_protective_stop

    async def fail_once(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return None
        return await real_place(*args, **kwargs)

    monkeypatch.setattr(broker, "place_protective_stop", fail_once)

    await _enter(manager, broker, account, _plan(initial_stop=48.50), 62.0)
    retried = await manager.retry_unprotected_positions()

    assert retried == 1
    events = store.list_stop_target_events("acct1", "AAPL")
    event_types = [e["event_type"] for e in events]
    assert event_types == [
        StopTargetEventType.PROTECTION_FAILED.value,
        StopTargetEventType.STOP_PLACED.value,
    ]
    placed_event = events[1]
    assert placed_event["source"] == "reconciliation"
    assert placed_event["price"] == 48.50


@pytest.mark.asyncio
async def test_log_is_append_only_never_mutates_earlier_rows(manager, account, broker, store):
    lifecycle = await _enter(manager, broker, account, _plan(initial_stop=48.50), 62.0)
    first_snapshot = store.list_stop_target_events("acct1", "AAPL")
    assert len(first_snapshot) == 1

    await manager._tighten_stop_to(lifecycle, account, 49.00)
    await manager._tighten_stop_to(lifecycle, account, 49.50)

    final = store.list_stop_target_events("acct1", "AAPL")
    assert len(final) == 3
    # The very first row is byte-for-byte unchanged by later appends.
    assert final[0] == first_snapshot[0]
    # Ordered oldest-first, ids strictly increasing (never rewritten in place).
    ids = [e["id"] for e in final]
    assert ids == sorted(ids)
    assert len(set(ids)) == len(ids)


def test_no_fabricated_breakeven_or_trailing_activation_event_type():
    """This branch's own documented gap: `app/signal_commands.py`'s
    MOVE_STOP/"breakeven" handling and `app/protection_auditor.py`'s
    ProtectionAuditor exist only on the sibling
    `claude/signal-copier-safety-features` branch -- neither file exists
    here, and nothing in this branch's live signal path ever activates a
    trailing policy either (see `app/engine.py`'s `_handle_managed_entry`,
    which never constructs one). `StopTargetEventType` must not invent an
    event type for a trigger this branch's code can't actually reach."""
    values = {member.value for member in StopTargetEventType}
    assert "breakeven" not in values
    assert "breakeven_activated" not in values
    assert "move_to_breakeven" not in values
    assert "trail_activated" not in values
    assert "trailing_activated" not in values
    assert values == {"stop_placed", "stop_tightened", "protection_failed", "target_hit"}
