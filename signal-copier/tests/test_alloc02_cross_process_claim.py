"""ALLOC-02: allocation-intent claim/bind semantics hold across SEPARATE
OS processes and connections on one SQLite file (real BEGIN IMMEDIATE
contention), not just across asyncio tasks in one process."""
import multiprocessing as mp

import pytest

from app.db import SignalStore
from app.models import Side, Signal


def _worker(db_path: str, signal_id: str, account_id: str, start, out) -> None:
    store = SignalStore.__new__(SignalStore)
    store.db_path = db_path  # reuse schema already created by the parent
    start.wait()
    store.claim_allocation_intent(signal_id, strategy_key="tv", symbol="X", side="buy", candidates=["a1", "a2", "a3", "a4"])
    out.put((account_id, store.bind_allocation_intent(signal_id, account_id)))


def test_many_processes_race_exactly_one_account_wins(tmp_path):
    db = tmp_path / "race.db"
    store = SignalStore(db)
    signal = Signal(source="tv", symbol="X", side=Side.BUY)
    store.save_signal(signal)

    ctx = mp.get_context("spawn")
    start, out = ctx.Event(), ctx.Queue()
    procs = [
        ctx.Process(target=_worker, args=(str(db), signal.id, f"a{i}", start, out)) for i in range(1, 5)
    ]
    for p in procs:
        p.start()
    start.set()
    outcomes = [out.get(timeout=60) for _ in procs]
    for p in procs:
        p.join(timeout=30)

    winners = [account for account, ok in outcomes if ok]
    assert len(winners) == 1
    intent = store.get_allocation_intent(signal.id)
    assert intent["selected_account_id"] == winners[0]
    assert intent["state"] == "selected"
    assert len(store.list_allocation_intents()) == 1


def test_bind_is_idempotent_and_never_rebinds(tmp_path):
    store = SignalStore(tmp_path / "t.db")
    signal = Signal(source="tv", symbol="X", side=Side.BUY)
    store.save_signal(signal)
    store.claim_allocation_intent(signal.id, strategy_key="tv", symbol="X", side="buy", candidates=["a1", "a2"])
    assert store.bind_allocation_intent(signal.id, "a1") is True
    assert store.bind_allocation_intent(signal.id, "a1") is True
    assert store.bind_allocation_intent(signal.id, "a2") is False
    store.commit_allocation_intent(signal.id, "a1")
    assert store.release_allocation_binding(signal.id, "a1") is False  # committed: never released
    assert store.bind_allocation_intent(signal.id, "a2") is False


def test_skip_cannot_override_a_bound_intent(tmp_path):
    store = SignalStore(tmp_path / "t.db")
    signal = Signal(source="tv", symbol="X", side=Side.BUY)
    store.save_signal(signal)
    store.claim_allocation_intent(signal.id, strategy_key="tv", symbol="X", side="buy", candidates=["a1"])
    store.bind_allocation_intent(signal.id, "a1")
    store.skip_allocation_intent(signal.id, reason="late skip")
    assert store.get_allocation_intent(signal.id)["state"] == "selected"


def test_release_returns_intent_to_claimed_and_allows_other_account(tmp_path):
    store = SignalStore(tmp_path / "t.db")
    signal = Signal(source="tv", symbol="X", side=Side.BUY)
    store.save_signal(signal)
    store.claim_allocation_intent(signal.id, strategy_key="tv", symbol="X", side="buy", candidates=["a1", "a2"])
    store.bind_allocation_intent(signal.id, "a1")
    assert store.release_allocation_binding(signal.id, "a1") is True
    assert store.bind_allocation_intent(signal.id, "a2") is True


@pytest.mark.parametrize("bad", ["", "nope"])
def test_rule_rejects_unknown_delivery_mode(bad):
    from app.routing import RoutingRule

    with pytest.raises(ValueError):
        RoutingRule(source="tv", destinations=["a1"], delivery_mode=bad)
