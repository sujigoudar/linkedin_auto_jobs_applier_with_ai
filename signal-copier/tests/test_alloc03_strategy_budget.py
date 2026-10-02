"""ALLOC-03: a strategy's global ceiling is counted ONCE across accounts,
reservations are attributed to the right strategy, and admission is atomic
across separate OS processes."""
import multiprocessing as mp

import pytest

from app.brokers.paper import PaperBroker
from app.db import SignalStore
from app.engine import SignalCopierEngine
from app.models import DestinationAccount, OrderResult, OrderStatus, Side, Signal
from app.routing import RoutingConfig, RoutingRule


@pytest.fixture
def store(tmp_path):
    return SignalStore(tmp_path / "t.db")


def _engine(store, broker=None, accounts=("a1", "a2")):
    broker = broker or PaperBroker()
    routing = RoutingConfig(
        rules=[RoutingRule(source="tv", destinations=list(accounts))],
        accounts={a: DestinationAccount(account_id=a, broker="paper") for a in accounts},
    )
    return SignalCopierEngine(routing=routing, brokers={"paper": broker}, store=store), broker


def _sig(qty, price=100.0, source="tv", symbol="AAA"):
    return Signal(source=source, symbol=symbol, side=Side.BUY, quantity=qty, price=price)


@pytest.mark.asyncio
async def test_strategy_ceiling_is_global_not_per_account(store):
    store.set_strategy_budget("tv", 1500.0)
    engine, broker = _engine(store)

    first = await engine.handle_signal(_sig(10))  # notional 1000
    assert [r.status for r in first] == [OrderStatus.FILLED]

    second = await engine.handle_signal(_sig(10, symbol="BBB"))  # would be 2000 > 1500, on EITHER account
    assert all(r.status == OrderStatus.REJECTED for r in second)
    assert {r.account_id for r in second} == {"a1", "a2"}  # both tried, both refused: ceiling not multiplied
    assert store.get_allocation_intent(second[0].signal_id)["state"] == "skipped"

    third = await engine.handle_signal(_sig(4, symbol="CCC"))  # 400 fits (1400 <= 1500)
    assert third[0].status == OrderStatus.FILLED
    assert sum(1 for a in broker.positions.values() for q in a.values() if q) == 2


@pytest.mark.asyncio
async def test_unset_budget_blocks_nothing(store):
    engine, _ = _engine(store)
    results = await engine.handle_signal(_sig(500))  # 50k, inside paper buying power
    assert results[0].status == OrderStatus.FILLED


@pytest.mark.asyncio
async def test_missing_price_fails_closed_when_strategy_ceiling_set(store):
    store.set_strategy_budget("tv", 1500.0)
    engine, _ = _engine(store)
    results = await engine.handle_signal(Signal(source="tv", symbol="AAA", side=Side.BUY, quantity=1.0))
    assert all(r.status == OrderStatus.REJECTED for r in results)


class _PendingBroker(PaperBroker):
    async def place_order(self, signal, account, quantity, symbol) -> OrderResult:
        return OrderResult(
            account_id=account.account_id,
            status=OrderStatus.PENDING,
            signal_id=signal.id,
            broker_order_id=f"b-{signal.id[:6]}",
        )


@pytest.mark.asyncio
async def test_pending_order_reservation_counts_against_strategy(store):
    store.set_strategy_budget("tv", 1500.0)
    engine, _ = _engine(store, broker=_PendingBroker())
    first = await engine.handle_signal(_sig(10))
    assert first[0].status == OrderStatus.PENDING
    assert store.sum_unresolved_strategy_reservations("tv") == 1000.0
    second = await engine.handle_signal(_sig(10, symbol="BBB"))
    assert all(r.status == OrderStatus.REJECTED for r in second)


def test_release_resolves_the_matching_strategy_reservation_not_a_lookalike(store):
    from app.capital_allocator import CapitalAllocator

    alloc = CapitalAllocator(store=store)
    alloc.reserve_locked("a1", 500.0, signal_id="s-A", strategy_key="A")
    alloc.reserve_locked("a1", 500.0, signal_id="s-B", strategy_key="B")
    alloc.release("a1", 500.0, signal_id="s-B")
    assert store.sum_unresolved_strategy_reservations("A") == 500.0
    assert store.sum_unresolved_strategy_reservations("B") == 0.0


def test_strategy_budget_validation(store):
    with pytest.raises(ValueError):
        store.set_strategy_budget("tv", 0)
    with pytest.raises(ValueError):
        store.set_strategy_budget("tv", -5)
    store.set_strategy_budget("tv", 10.0)
    store.set_strategy_budget("tv", None)  # explicit unset
    assert store.get_strategy_budget("tv")["max_notional"] is None


def _reserve_worker(db_path, i, start, out):
    store = SignalStore.__new__(SignalStore)
    store.db_path = db_path
    start.wait()
    ok, _c, _p = store.reserve_strategy_checked(
        f"r-{i}", f"acct{i}", 400.0, signal_id=f"sig-{i}", strategy_key="tv", ceiling=1000.0,
        confirmed_notional=lambda: 0.0,
    )
    out.put(ok)


def test_cross_process_strategy_admission_is_atomic(tmp_path):
    """Six processes on six different ACCOUNTS race for a 1000 strategy
    ceiling with 400 each: exactly two may be admitted."""
    db = tmp_path / "race.db"
    store = SignalStore(db)
    ctx = mp.get_context("spawn")
    start, out = ctx.Event(), ctx.Queue()
    procs = [ctx.Process(target=_reserve_worker, args=(str(db), i, start, out)) for i in range(6)]
    for p in procs:
        p.start()
    start.set()
    results = [out.get(timeout=60) for _ in procs]
    for p in procs:
        p.join(timeout=30)
    assert sum(results) == 2
    assert store.sum_unresolved_strategy_reservations("tv") == 800.0
