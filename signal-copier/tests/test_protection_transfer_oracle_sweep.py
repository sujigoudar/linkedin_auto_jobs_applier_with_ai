"""Exhaustively sweeps the small integer fill-geometry domain
(0 < target <= owned, 0 <= first <= target, 0 <= late <= target-first) and
checks PositionLifecycleManager's protection-transfer math against an
independent, hand-derived oracle for every combination.

This generalizes tests/test_protection_transfer.py's single worked example
(62 owned / 15 target / 8 first / 3 late) to every small fill geometry, to
catch edge cases (first=0, late=0, target==owned, first==target) the one
worked example doesn't exercise. The oracle formula and the domain-sweep
idea come from an externally supplied verification pack
(signal_copier_verification_v7/auditlib/oracles.py's transfer_oracle) whose
own offline self-tests were run and passed before any of its logic was
adapted here; the oracle itself was reimplemented locally rather than
imported, since it is independent-verification math, not application code.
"""
import pytest

from app.brokers.paper import PaperBroker
from app.lifecycle.manager import PositionLifecycleManager
from app.lifecycle.models import PositionPlan
from app.models import DestinationAccount, OrderResult, OrderStatus, Side, Signal


def _oracle_after_late(owned: int, target: int, first: int, late: int) -> int:
    """Independent restore-quantity formula: whatever actually filled
    (first + late) comes out of owned; the stop must never restore to
    anything else, regardless of what was originally requested (target)."""
    assert 0 < target <= owned
    assert 0 <= first <= target
    assert 0 <= late <= target - first
    return owned - first - late


def _fill_geometries():
    cases = []
    for owned in range(1, 7):
        for target in range(1, owned + 1):
            for first in range(0, target + 1):
                for late in range(0, target - first + 1):
                    cases.append((owned, target, first, late))
    return cases


def _pending_exit_place_order(broker_order_id="tp-order-1"):
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
@pytest.mark.parametrize("owned,target,first,late", _fill_geometries())
async def test_restore_quantity_matches_independent_oracle_across_fill_geometry(owned, target, first, late, monkeypatch):
    account = DestinationAccount(account_id="acct1", broker="paper")
    broker = PaperBroker()
    manager = PositionLifecycleManager(brokers={"paper": broker})

    plan = PositionPlan(account_id="acct1", symbol="AAPL", side=Side.BUY, planned_quantity=float(owned), initial_stop=1.0)
    manager.start_plan(plan)
    await broker.place_order(Signal(source="test", symbol="AAPL", side=Side.BUY), account, float(owned), "AAPL")
    await manager.on_entry_fill(account, "AAPL", float(owned))

    monkeypatch.setattr(broker, "place_order", _pending_exit_place_order())
    exit_result = await manager.request_exit(account, "AAPL", float(target), source="target")
    assert exit_result.status == OrderStatus.PENDING

    confirmed_filled = float(first + late)
    await manager.resolve_pending_exit(account, "AAPL", confirmed_filled_quantity=confirmed_filled, remainder_cancelled=True)

    expected_remaining = _oracle_after_late(owned, target, first, late)
    lifecycle = manager.get_lifecycle("acct1", "AAPL")

    assert manager.arbiter.available_to_sell("acct1", "AAPL") == pytest.approx(expected_remaining)
    if expected_remaining <= 0:
        assert lifecycle.closed is True
    else:
        assert lifecycle.closed is False
        assert lifecycle.confirmed_owned_quantity == pytest.approx(expected_remaining)
        assert lifecycle.stop.protected_quantity == pytest.approx(expected_remaining)
