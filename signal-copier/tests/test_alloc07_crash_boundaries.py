"""ALLOC-07: process termination at every durable boundary of an entry.

A crash is simulated with a BaseException (not caught by the engine's
`except Exception`) raised at one persistence/external-call boundary, then
the SAME signal is re-delivered to a brand-new engine on the same database
(a restart). Whatever the crash point, the intended trade must never be
placed more than once and never on a second account.
"""
import pytest

from app.brokers.paper import PaperBroker
from app.db import SignalStore
from app.engine import SignalCopierEngine
from app.models import DestinationAccount, OrderStatus, Side, Signal
from app.routing import RoutingConfig, RoutingRule


class _Crash(BaseException):
    pass


def _engine(store, broker):
    routing = RoutingConfig(
        rules=[RoutingRule(source="tv", destinations=["a1", "a2", "a3"])],
        accounts={a: DestinationAccount(account_id=a, broker="paper") for a in ("a1", "a2", "a3")},
    )
    return SignalCopierEngine(routing=routing, brokers={"paper": broker}, store=store)


def _units(broker):
    return {a: p.get("AAA", 0) for a, p in broker.positions.items() if p.get("AAA", 0)}


def _signal():
    return Signal(source="tv", symbol="AAA", side=Side.BUY, quantity=1.0, price=100.0)


@pytest.mark.asyncio
async def test_crash_after_binding_before_ledger_intent_resumes_same_account_once(tmp_path):
    store = SignalStore(tmp_path / "t.db")
    broker = PaperBroker()
    signal = _signal()
    engine1 = _engine(store, broker)

    def boom(**kwargs):
        raise _Crash()

    engine1.store.open_command_ledger_entry = boom  # type: ignore[method-assign]
    with pytest.raises(_Crash):
        await engine1.handle_signal(signal)
    assert store.get_allocation_intent(signal.id)["selected_account_id"] == "a1"
    assert not broker.positions

    results = await _engine(SignalStore(tmp_path / "t.db"), broker).handle_signal(signal)
    assert [r.account_id for r in results] == ["a1"]
    assert _units(broker) == {"a1": 1.0}


@pytest.mark.asyncio
async def test_crash_after_ledger_intent_before_broker_call_never_resubmits(tmp_path):
    """The broker call may or may not have happened: ambiguous, so the
    restart must NOT submit; the reservation is held as an obligation."""
    store = SignalStore(tmp_path / "t.db")
    broker = PaperBroker()
    signal = _signal()
    engine1 = _engine(store, broker)

    async def boom(*a, **k):
        raise _Crash()

    broker.place_order = boom  # type: ignore[method-assign]
    with pytest.raises(_Crash):
        await engine1.handle_signal(signal)

    broker2 = PaperBroker()
    engine2 = _engine(SignalStore(tmp_path / "t.db"), broker2)
    results = await engine2.handle_signal(signal)
    assert [r.account_id for r in results] == ["a1"]
    assert not broker2.positions and not broker.positions
    unresolved = store.list_unresolved_command_ledger_entries()
    assert len(unresolved) == 1 and unresolved[0].uncertainty_state.value == "unknown_ambiguous"


@pytest.mark.asyncio
async def test_crash_after_broker_fill_before_ledger_outcome_never_resubmits(tmp_path):
    store = SignalStore(tmp_path / "t.db")
    broker = PaperBroker()
    signal = _signal()
    engine1 = _engine(store, broker)

    def boom(*a, **k):
        raise _Crash()

    engine1.store.mark_command_ledger_outcome = boom  # type: ignore[method-assign]
    with pytest.raises(_Crash):
        await engine1.handle_signal(signal)
    assert _units(broker) == {"a1": 1.0}  # the broker really filled

    results = await _engine(SignalStore(tmp_path / "t.db"), broker).handle_signal(signal)
    assert [r.account_id for r in results] == ["a1"]
    assert _units(broker) == {"a1": 1.0}  # still exactly one unit, one account


@pytest.mark.asyncio
async def test_crash_after_ledger_outcome_before_order_saved_never_resubmits(tmp_path):
    store = SignalStore(tmp_path / "t.db")
    broker = PaperBroker()
    signal = _signal()
    engine1 = _engine(store, broker)

    def boom(*a, **k):
        raise _Crash()

    engine1.store.save_order_result = boom  # type: ignore[method-assign]
    with pytest.raises(_Crash):
        await engine1.handle_signal(signal)
    assert _units(broker) == {"a1": 1.0}

    results = await _engine(SignalStore(tmp_path / "t.db"), broker).handle_signal(signal)
    assert {r.account_id for r in results} == {"a1"}
    assert _units(broker) == {"a1": 1.0}
    assert all(r.status in (OrderStatus.PENDING, OrderStatus.FILLED) for r in results)


@pytest.mark.asyncio
async def test_concurrent_deliveries_of_one_signal_place_one_order(tmp_path):
    import asyncio

    store = SignalStore(tmp_path / "t.db")
    broker = PaperBroker()
    engine = _engine(store, broker)
    signal = _signal()
    await asyncio.gather(*(engine.handle_signal(signal) for _ in range(4)))
    assert _units(broker) == {"a1": 1.0}
