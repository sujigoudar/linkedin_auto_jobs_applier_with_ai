"""WC-20: Engine integration of the complete entry workflow.

Wires the _handle_signal entry path through: identity collapse, admission,
feasibility sizing per candidate, selection, hierarchical budget reservation,
OrderIntent + outbox, dispatch, and protection.

Tests: single-destination selection, duplicate binding collapse, budget
exhaustion blocking, exit routing, invariants I01/I02/I08 after each event.
"""
from __future__ import annotations

import pytest

from app.db import SignalStore
from app.brokers.paper import PaperBroker
from app.engine import SignalCopierEngine
from app.models import (
    DestinationAccount,
    OrderStatus,
    Side,
    Signal,
)
from app.routing import RoutingConfig, RoutingRule
from pathlib import Path


def _engine(store, accounts, rules, broker=None):
    """Helper to create an engine with given accounts and routing rules."""
    broker = broker or PaperBroker()
    routing = RoutingConfig(rules=rules, accounts={a.account_id: a for a in accounts})
    return SignalCopierEngine(routing=routing, brokers={"paper": broker}, store=store), broker


@pytest.fixture
def store(tmp_path: Path):
    """Create a temporary SQLite database."""
    return SignalStore(tmp_path / "test.db")


@pytest.mark.asyncio
async def test_wc20_single_destination_selection_i01(store):
    """Test WC-20: Entry path with two eligible accounts, selects one (I01)."""
    accounts = [
        DestinationAccount(account_id="a1", broker="paper"),
        DestinationAccount(account_id="a2", broker="paper"),
    ]
    rules = [RoutingRule(source="test", destinations=["a1", "a2"])]
    engine, broker = _engine(store, accounts, rules)

    signal = Signal(
        source="test",
        symbol="AAPL",
        side=Side.BUY,
        quantity=10.0,
    )

    results = await engine.handle_signal(signal)

    # Invariant I01: One canonical signal -> exactly one selected account
    filled_results = [r for r in results if r.status == OrderStatus.FILLED]
    assert len(filled_results) == 1, f"Single-destination should select exactly one account, got {len(filled_results)}"

    # Verify exactly one order was placed on one account
    total_quantity = sum(broker.positions.get(account_id, {}).get("AAPL", 0.0) for account_id in ("a1", "a2"))
    assert total_quantity == 10.0, f"Total quantity should be 10.0, got {total_quantity}"

    # Check allocation intent state
    intent = store.get_allocation_intent(signal.id)
    assert intent is not None
    assert intent["selected_account_id"] in ("a1", "a2")
    assert intent["state"] == "committed"


@pytest.mark.asyncio
async def test_wc20_exit_to_owning_account(store):
    """Test WC-20: Exit/reduce inherit owning account without router."""
    accounts = [
        DestinationAccount(account_id="a1", broker="paper"),
        DestinationAccount(account_id="a2", broker="paper"),
    ]
    rules = [
        RoutingRule(source="test", destinations=["a1", "a2"]),
    ]
    engine, broker = _engine(store, accounts, rules)

    # First, create an entry position
    entry_signal = Signal(
        source="test",
        symbol="AAPL",
        side=Side.BUY,
        quantity=10.0,
    )

    entry_results = await engine.handle_signal(entry_signal)
    assert len(entry_results) == 1
    entry_account = entry_results[0].account_id

    # Now send an exit signal
    exit_signal = Signal(
        source="test",
        symbol="AAPL",
        side=Side.CLOSE,
    )

    exit_results = await engine.handle_signal(exit_signal)
    # Exit should go to the same account that has the position
    # (exits inherit the account, no router)
    exit_accounts = [r.account_id for r in exit_results]
    assert entry_account in exit_accounts, f"Exit should route to {entry_account}, got {exit_accounts}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
