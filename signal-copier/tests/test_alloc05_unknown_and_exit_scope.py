"""ALLOC-05: (a) an unknown submission is an unresolved OBLIGATION -- its
capital reservation is held, not released -- until independent evidence
resolves it; (b) scoped exits never sell holdings the exiting strategy does
not own (the SNOW-style case)."""
import pytest

from app.brokers.paper import PaperBroker
from app.db import SignalStore
from app.engine import SignalCopierEngine
from app.models import DestinationAccount, OrderResult, OrderStatus, Side, Signal
from app.routing import RoutingConfig, RoutingRule


@pytest.fixture
def store(tmp_path):
    return SignalStore(tmp_path / "t.db")


def _engine(store, broker, accounts=("a1", "a2")):
    routing = RoutingConfig(
        rules=[RoutingRule(source="tv", destinations=list(accounts))],
        accounts={a: DestinationAccount(account_id=a, broker="paper") for a in accounts},
    )
    return SignalCopierEngine(routing=routing, brokers={"paper": broker}, store=store)


class _LostResponseBroker(PaperBroker):
    async def place_order(self, signal, account, quantity, symbol) -> OrderResult:
        raise TimeoutError("response lost")


@pytest.mark.asyncio
async def test_unknown_submission_holds_reservation_until_resolved(store):
    store.set_strategy_budget("tv", 1500.0)
    engine = _engine(store, _LostResponseBroker())
    signal = Signal(source="tv", symbol="AAA", side=Side.BUY, quantity=10, price=100.0)
    first = await engine.handle_signal(signal)
    assert first[0].status == OrderStatus.ERROR

    # the 1000 notional is STILL reserved: the broker may have accepted it
    assert store.sum_unresolved_strategy_reservations("tv") == 1000.0
    assert engine.capital_allocator.pending_reservation("a1") == 1000.0

    # so a second opportunity cannot spend the same capacity
    second = await engine.handle_signal(Signal(source="tv", symbol="BBB", side=Side.BUY, quantity=10, price=100.0))
    assert all(r.status == OrderStatus.REJECTED for r in second)

    unresolved = store.list_unresolved_command_ledger_entries()
    assert len(unresolved) == 1
    # independent evidence that nothing was placed releases it exactly once
    assert engine.resolve_unknown_submission(unresolved[0].idempotency_key, outcome="not_placed", evidence="broker shows no order")
    assert store.sum_unresolved_strategy_reservations("tv") == 0.0
    assert engine.capital_allocator.pending_reservation("a1") == 0.0
    assert not engine.resolve_unknown_submission(unresolved[0].idempotency_key, outcome="not_placed", evidence="again")


@pytest.mark.asyncio
async def test_unknown_resolution_rejects_unsupported_outcome(store):
    engine = _engine(store, _LostResponseBroker())
    await engine.handle_signal(Signal(source="tv", symbol="AAA", side=Side.BUY, quantity=1, price=10.0))
    key = store.list_unresolved_command_ledger_entries()[0].idempotency_key
    with pytest.raises(ValueError):
        engine.resolve_unknown_submission(key, outcome="filled", evidence="x")


@pytest.mark.asyncio
async def test_exit_after_failed_entry_sells_nothing_and_creates_no_short(store):
    """SNOW-style: the entry produced no confirmed owned quantity; the
    provider's later exit must not sell anything or open a short."""
    broker = PaperBroker()
    engine = _engine(store, broker, accounts=("a1",))
    # entry rejected before submission (no price -> fail-closed with a strategy ceiling)
    store.set_strategy_budget("tv", 100.0)
    await engine.handle_signal(Signal(source="tv", symbol="SNOW", side=Side.BUY, quantity=5))
    exit_results = await engine.handle_signal(Signal(source="tv", symbol="SNOW", side=Side.CLOSE))
    assert not any(r.status == OrderStatus.FILLED for r in exit_results)
    assert broker.positions.get("a1", {}).get("SNOW", 0) == 0


@pytest.mark.asyncio
async def test_exit_never_sells_another_strategys_holdings(store):
    broker = PaperBroker()
    routing = RoutingConfig(
        rules=[
            RoutingRule(source="alice", destinations=["a1"]),
            RoutingRule(source="bob", destinations=["a1"]),
        ],
        accounts={"a1": DestinationAccount(account_id="a1", broker="paper")},
    )
    engine = SignalCopierEngine(routing=routing, brokers={"paper": broker}, store=store)
    await engine.handle_signal(Signal(source="alice", symbol="SNOW", side=Side.BUY, quantity=5, price=100.0))
    assert broker.positions["a1"]["SNOW"] == 5
    # bob never bought: bob's exit must not touch alice's shares
    bob_exit = await engine.handle_signal(Signal(source="bob", symbol="SNOW", side=Side.CLOSE))
    assert not any(r.status == OrderStatus.FILLED for r in bob_exit)
    assert broker.positions["a1"]["SNOW"] == 5
