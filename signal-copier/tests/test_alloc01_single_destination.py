"""ALLOC-01: one canonical signal -> ONE selected account.

Decisive regression for the broadcast defect: with N eligible accounts
configured for a source, the real ingestion path (`engine.handle_signal`)
must produce one durable allocation decision, one execution destination,
and no orders on the other accounts -- unless a rule is explicitly
configured `delivery_mode="replicate"`.
"""
import pytest

from app.brokers.paper import PaperBroker
from app.db import SignalStore
from app.engine import SignalCopierEngine
from app.models import DestinationAccount, OrderResult, OrderStatus, Side, Signal
from app.routing import RoutingConfig, RoutingRule


def _engine(store, accounts, rules, broker=None):
    broker = broker or PaperBroker()
    routing = RoutingConfig(rules=rules, accounts={a.account_id: a for a in accounts})
    return SignalCopierEngine(routing=routing, brokers={"paper": broker}, store=store), broker


def _three():
    return [DestinationAccount(account_id=i, broker="paper") for i in ("a1", "a2", "a3")]


@pytest.fixture
def store(tmp_path):
    return SignalStore(tmp_path / "t.db")


def _entry_orders(store, signal_id):
    return [r for r in store.list_orders_for_signal(signal_id) if r["status"] != "rejected"]


@pytest.mark.asyncio
async def test_one_signal_three_eligible_accounts_produces_one_order(store):
    engine, broker = _engine(store, _three(), [RoutingRule(source="tv", destinations=["a1", "a2", "a3"])])
    signal = Signal(source="tv", symbol="BTCUSDT", side=Side.BUY, quantity=1.0)

    results = await engine.handle_signal(signal)

    assert [r.account_id for r in results] == ["a1"]
    assert results[0].status == OrderStatus.FILLED
    assert {a for a, pos in broker.positions.items() if pos.get("BTCUSDT")} == {"a1"}
    assert len(store.list_orders_for_signal(signal.id)) == 1
    intent = store.get_allocation_intent(signal.id)
    assert intent["selected_account_id"] == "a1"
    assert intent["state"] == "committed"


@pytest.mark.asyncio
async def test_duplicate_delivery_replays_without_second_order(store):
    engine, broker = _engine(store, _three(), [RoutingRule(source="tv", destinations=["a1", "a2", "a3"])])
    signal = Signal(source="tv", symbol="BTCUSDT", side=Side.BUY, quantity=1.0)
    await engine.handle_signal(signal)
    again = await engine.handle_signal(signal)

    assert [r.account_id for r in again] == ["a1"]
    assert len(store.list_orders_for_signal(signal.id)) == 1
    assert broker.positions["a1"]["BTCUSDT"] == 1.0


@pytest.mark.asyncio
async def test_independent_signals_are_independent_intents(store):
    """Two analysts, same ticker: not deduplicated merely by ticker/time."""
    engine, _ = _engine(store, _three(), [RoutingRule(source="tv", destinations=["a1", "a2", "a3"])])
    s1 = Signal(source="tv", symbol="BTCUSDT", side=Side.BUY, quantity=1.0, analyst="x")
    s2 = Signal(source="tv", symbol="BTCUSDT", side=Side.BUY, quantity=1.0, analyst="y")
    await engine.handle_signal(s1)
    await engine.handle_signal(s2)
    assert store.get_allocation_intent(s1.id) is not None
    assert store.get_allocation_intent(s2.id) is not None
    assert store.get_allocation_intent(s1.id)["intent_id"] != store.get_allocation_intent(s2.id)["intent_id"]


@pytest.mark.asyncio
async def test_selection_skips_account_without_capacity_before_submission(store):
    accounts = [
        DestinationAccount(account_id="a1", broker="paper", max_notional_exposure=10.0),
        DestinationAccount(account_id="a2", broker="paper"),
        DestinationAccount(account_id="a3", broker="paper"),
    ]
    engine, broker = _engine(store, accounts, [RoutingRule(source="tv", destinations=["a1", "a2", "a3"])])
    signal = Signal(source="tv", symbol="BTCUSDT", side=Side.BUY, quantity=1.0, price=100.0)

    results = await engine.handle_signal(signal)

    statuses = {r.account_id: r.status for r in results}
    assert statuses["a1"] == OrderStatus.REJECTED
    assert statuses["a2"] == OrderStatus.FILLED
    assert "a3" not in statuses
    assert {a for a, pos in broker.positions.items() if pos.get("BTCUSDT")} == {"a2"}
    assert store.get_allocation_intent(signal.id)["selected_account_id"] == "a2"


@pytest.mark.asyncio
async def test_no_capacity_anywhere_is_explained_skip_with_no_order(store):
    accounts = [DestinationAccount(account_id=i, broker="paper", max_notional_exposure=10.0) for i in ("a1", "a2")]
    engine, broker = _engine(store, accounts, [RoutingRule(source="tv", destinations=["a1", "a2"])])
    signal = Signal(source="tv", symbol="BTCUSDT", side=Side.BUY, quantity=1.0, price=100.0)

    results = await engine.handle_signal(signal)

    assert all(r.status == OrderStatus.REJECTED for r in results)
    assert not broker.positions
    intent = store.get_allocation_intent(signal.id)
    assert intent["state"] == "skipped"
    assert intent["selected_account_id"] is None
    assert "a1" in intent["reason"] and "a2" in intent["reason"]


class _ExplodingBroker(PaperBroker):
    async def place_order(self, signal, account, quantity, symbol) -> OrderResult:
        raise TimeoutError("response lost")


@pytest.mark.asyncio
async def test_unknown_submission_is_never_rerouted_to_another_account(store):
    engine, _ = _engine(
        store, _three(), [RoutingRule(source="tv", destinations=["a1", "a2", "a3"])], broker=_ExplodingBroker()
    )
    signal = Signal(source="tv", symbol="BTCUSDT", side=Side.BUY, quantity=1.0)

    results = await engine.handle_signal(signal)

    assert [r.account_id for r in results] == ["a1"]
    assert results[0].status == OrderStatus.ERROR
    intent = store.get_allocation_intent(signal.id)
    assert intent["state"] == "committed" and intent["selected_account_id"] == "a1"
    # redelivery must not try a2 either
    again = await engine.handle_signal(signal)
    assert {r.account_id for r in again} == {"a1"}


@pytest.mark.asyncio
async def test_restart_after_selection_resumes_same_account_only(store):
    """Intent bound to a2 but process died before any order existed: a new
    engine instance must proceed with a2 only, never re-select."""
    engine, broker = _engine(store, _three(), [RoutingRule(source="tv", destinations=["a1", "a2", "a3"])])
    signal = Signal(source="tv", symbol="BTCUSDT", side=Side.BUY, quantity=1.0)
    store.save_signal(signal)
    store.claim_allocation_intent(signal.id, strategy_key="tv", symbol="BTCUSDT", side="buy", candidates=["a1", "a2", "a3"])
    store.bind_allocation_intent(signal.id, "a2")

    results = await engine.handle_signal(signal)

    assert [r.account_id for r in results] == ["a2"]
    assert {a for a, pos in broker.positions.items() if pos.get("BTCUSDT")} == {"a2"}


@pytest.mark.asyncio
async def test_replicate_mode_is_explicit_opt_in(store):
    engine, broker = _engine(
        store, _three(), [RoutingRule(source="tv", destinations=["a1", "a2", "a3"], delivery_mode="replicate")]
    )
    signal = Signal(source="tv", symbol="BTCUSDT", side=Side.BUY, quantity=1.0)
    results = await engine.handle_signal(signal)
    assert sorted(r.account_id for r in results) == ["a1", "a2", "a3"]


@pytest.mark.asyncio
async def test_paused_primary_falls_to_secondary_and_close_still_reaches_owner(store):
    accounts = [
        DestinationAccount(account_id="a1", broker="paper", enabled=False),
        DestinationAccount(account_id="a2", broker="paper"),
    ]
    engine, broker = _engine(store, accounts, [RoutingRule(source="tv", destinations=["a1", "a2"])])
    entry = Signal(source="tv", symbol="BTCUSDT", side=Side.BUY, quantity=1.0)
    r = await engine.handle_signal(entry)
    assert [x.account_id for x in r] == ["a2"]
    close = Signal(source="tv", symbol="BTCUSDT", side=Side.CLOSE)
    rc = await engine.handle_signal(close)
    assert any(x.account_id == "a2" and x.status == OrderStatus.FILLED for x in rc)
    assert broker.positions["a2"]["BTCUSDT"] == 0
