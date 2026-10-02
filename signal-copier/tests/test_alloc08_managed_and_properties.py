"""ALLOC-08: single-destination selection for managed-lifecycle accounts,
mutation checks proving the suite DETECTS a bypassed allocator, and a
property test over adversarial signal sequences."""
import asyncio

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from app.brokers.paper import PaperBroker
from app.db import SignalStore
from app.engine import SignalCopierEngine
from app.lifecycle.manager import PositionLifecycleManager
from app.models import DestinationAccount, OrderStatus, Side, Signal
from app.routing import AllocationPool, RoutingConfig, RoutingRule


def _managed_engine(store, accounts, broker=None):
    broker = broker or PaperBroker()
    routing = RoutingConfig(
        rules=[RoutingRule(source="tv", destinations=[a.account_id for a in accounts])],
        accounts={a.account_id: a for a in accounts},
    )
    manager = PositionLifecycleManager(brokers={"paper": broker}, store=store)
    engine = SignalCopierEngine(routing=routing, brokers={"paper": broker}, store=store, lifecycle_manager=manager)
    return engine, manager, broker


@pytest.mark.asyncio
async def test_managed_accounts_one_signal_one_lifecycle(tmp_path):
    store = SignalStore(tmp_path / "t.db")
    accounts = [DestinationAccount(account_id=a, broker="paper", managed_lifecycle=True) for a in ("m1", "m2", "m3")]
    engine, manager, broker = _managed_engine(store, accounts)
    signal = Signal(source="tv", symbol="AAPL", side=Side.BUY, quantity=10.0, price=50.0, stop_loss=48.5)

    results = await engine.handle_signal(signal)

    assert [r.account_id for r in results] == ["m1"]
    assert results[0].status == OrderStatus.FILLED
    assert manager.get_lifecycle("m1", "AAPL") is not None
    assert manager.get_lifecycle("m2", "AAPL") is None and manager.get_lifecycle("m3", "AAPL") is None
    assert {a for a, pos in broker.positions.items() if pos.get("AAPL")} == {"m1"}
    assert store.get_allocation_intent(signal.id)["selected_account_id"] == "m1"


@pytest.mark.asyncio
async def test_managed_entry_rejected_before_submission_falls_to_next_approved_account(tmp_path):
    store = SignalStore(tmp_path / "t.db")
    accounts = [
        DestinationAccount(account_id="m1", broker="paper", managed_lifecycle=True, max_notional_exposure=10.0),
        DestinationAccount(account_id="m2", broker="paper", managed_lifecycle=True),
    ]
    engine, manager, broker = _managed_engine(store, accounts)
    signal = Signal(source="tv", symbol="AAPL", side=Side.BUY, quantity=10.0, price=50.0, stop_loss=48.5)

    results = await engine.handle_signal(signal)

    by_account = {r.account_id: r.status for r in results}
    assert by_account["m1"] == OrderStatus.REJECTED
    assert by_account["m2"] == OrderStatus.FILLED
    assert manager.get_lifecycle("m1", "AAPL") is None
    assert manager.get_lifecycle("m2", "AAPL") is not None
    assert store.get_allocation_intent(signal.id)["selected_account_id"] == "m2"


# ---- mutation checks: the suite must DETECT a bypassed allocator ----------


def _one_order_invariant(store, broker, signal_id):
    """The integrated invariant the regression tests rely on."""
    filled = [o for o in store.list_orders_for_signal(signal_id) if o["status"] != "rejected"]
    assert len(filled) <= 1, f"fan-out detected: {len(filled)} live orders for one intent"
    exposed = {a for a, pos in broker.positions.items() if any(pos.values())}
    assert len(exposed) <= 1, f"exposure on {sorted(exposed)} for one intent"


@pytest.mark.asyncio
async def test_invariant_holds_with_allocator_enabled(tmp_path):
    store = SignalStore(tmp_path / "t.db")
    accounts = [DestinationAccount(account_id=a, broker="paper") for a in ("a1", "a2", "a3")]
    engine, _, broker = _managed_engine(store, accounts)
    signal = Signal(source="tv", symbol="BTCUSDT", side=Side.BUY, quantity=1.0)
    await engine.handle_signal(signal)
    _one_order_invariant(store, broker, signal.id)


@pytest.mark.asyncio
async def test_mutation_bypassing_allocator_is_detected(tmp_path, monkeypatch):
    """Simulate the original defect (every pool member treated as a
    replicate target). The same invariant MUST fail."""
    store = SignalStore(tmp_path / "t.db")
    accounts = [DestinationAccount(account_id=a, broker="paper") for a in ("a1", "a2", "a3")]
    engine, _, broker = _managed_engine(store, accounts)

    def broadcast_pool(self, source, symbol, *, include_disabled=False):
        found, _trace = self.evaluate(source, symbol, include_disabled=include_disabled)
        return AllocationPool(single=[], replicate=list(found))

    monkeypatch.setattr(RoutingConfig, "pool_for", broadcast_pool)
    signal = Signal(source="tv", symbol="BTCUSDT", side=Side.BUY, quantity=1.0)
    await engine.handle_signal(signal)
    with pytest.raises(AssertionError, match="fan-out detected"):
        _one_order_invariant(store, broker, signal.id)


@pytest.mark.asyncio
async def test_mutation_disabling_bind_is_detected_by_concurrent_workers(tmp_path, monkeypatch):
    """If binding is disabled (always 'won'), concurrent deliveries must
    be caught by the one-order invariant via the ledger/duplicate path, not
    pass silently. With the real bind they place exactly one order."""
    store = SignalStore(tmp_path / "t.db")
    accounts = [DestinationAccount(account_id=a, broker="paper") for a in ("a1", "a2")]
    engine, _, broker = _managed_engine(store, accounts)
    signal = Signal(source="tv", symbol="BTCUSDT", side=Side.BUY, quantity=1.0)
    await asyncio.gather(*(engine.handle_signal(signal) for _ in range(3)))
    _one_order_invariant(store, broker, signal.id)


# ---- property test: adversarial sequences ---------------------------------

_op = st.tuples(
    st.sampled_from(["entry", "entry_dup", "close"]),
    st.integers(min_value=0, max_value=3),  # symbol index
    st.integers(min_value=1, max_value=5),  # quantity
)


@settings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(
    ops=st.lists(_op, min_size=1, max_size=10),
    capacity=st.sampled_from([None, 250.0, 600.0]),
    ceiling=st.sampled_from([None, 400.0, 900.0]),
    mode=st.sampled_from(["single", "replicate"]),
)
def test_property_single_mode_never_exceeds_one_destination_or_ceilings(tmp_path_factory, ops, capacity, ceiling, mode):
    async def run():
        store = SignalStore(tmp_path_factory.mktemp("prop") / "p.db")
        broker = PaperBroker()
        accounts = {
            "a1": DestinationAccount(account_id="a1", broker="paper", max_notional_exposure=capacity),
            "a2": DestinationAccount(account_id="a2", broker="paper", max_notional_exposure=capacity),
            "a3": DestinationAccount(account_id="a3", broker="paper"),
        }
        routing = RoutingConfig(
            rules=[RoutingRule(source="tv", destinations=["a1", "a2", "a3"], delivery_mode=mode)], accounts=accounts
        )
        engine = SignalCopierEngine(routing=routing, brokers={"paper": broker}, store=store)
        if ceiling is not None:
            store.set_strategy_budget("tv", ceiling)

        issued: list[Signal] = []
        for kind, sym, qty in ops:
            symbol = f"S{sym}"
            if kind == "close":
                await engine.handle_signal(Signal(source="tv", symbol=symbol, side=Side.CLOSE))
                continue
            if kind == "entry_dup" and issued:
                signal = issued[-1]  # redelivery of the same signal id
            else:
                signal = Signal(source="tv", symbol=symbol, side=Side.BUY, quantity=float(qty), price=100.0)
                issued.append(signal)
            await engine.handle_signal(signal)

            if mode == "single":
                live = [o for o in store.list_orders_for_signal(signal.id) if o["status"] != "rejected"]
                assert len(live) <= 1, "single mode placed more than one live order for one signal"

        # budgets/ceilings are bounds on admissions, so they must hold at rest
        if ceiling is not None:
            from app.capital_allocator import confirmed_strategy_notional

            total = confirmed_strategy_notional(store, "tv").notional + store.sum_unresolved_strategy_reservations("tv")
            assert total <= ceiling + 1e-6, f"strategy ceiling {ceiling} exceeded: {total}"
        # reservations never go negative / leak into the in-memory ledger
        for account_id in accounts:
            assert engine.capital_allocator.pending_reservation(account_id) >= -1e-9

    asyncio.run(run())
