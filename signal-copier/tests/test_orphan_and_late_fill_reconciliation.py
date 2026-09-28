"""Real-condition tests for app/reconciliation.py's multi-angle orphan-fill
reconciliation: orphan-position detection (a broker position with no local
record at all) and late-fill reconstruction (a local order recorded
REJECTED whose broker trade history shows a real fill). See
app/reconciliation.py's module docstring for the design these exercise.
"""
from __future__ import annotations

import pytest

from app.brokers.base import BrokerAdapter
from app.db import SignalStore
from app.models import AssetClass, DestinationAccount, OrderResult, OrderStatus, Side, Signal
from app.protection_auditor import ProtectionAuditor
from app.reconciliation import OrderReconciler


@pytest.fixture
def store(tmp_path):
    return SignalStore(tmp_path / "test.db")


class _BulkPositionBroker(BrokerAdapter):
    """A broker whose full holdings this service has never been told
    about, independent of anything it submitted."""

    name = "bulk"

    def __init__(self, positions: dict[str, float]):
        self._positions = positions

    async def place_order(self, signal, account, quantity, symbol):
        raise NotImplementedError

    async def list_broker_positions(self, account):
        return dict(self._positions)


# --- orphan-position detection ---


@pytest.mark.asyncio
async def test_broker_position_with_no_local_record_is_orphan(store):
    account = DestinationAccount(account_id="acct1", broker="bulk")
    broker = _BulkPositionBroker({"MSFT": 50.0})  # this service has never heard of MSFT on this account
    reconciler = OrderReconciler(store, {"bulk": broker}, accounts={"acct1": account})

    await reconciler.reconcile_once()

    orphans = reconciler.list_orphan_positions()
    assert len(orphans) == 1
    assert orphans[0].account_id == "acct1"
    assert orphans[0].symbol == "MSFT"
    assert orphans[0].quantity == 50.0
    # Never fabricates a signal/provider attribution for it.
    assert not hasattr(orphans[0], "signal_id")
    assert not hasattr(orphans[0], "analyst")


@pytest.mark.asyncio
async def test_broker_position_with_local_order_history_is_not_orphan(store):
    signal = Signal(source="test", symbol="AAPL", side=Side.BUY, quantity=10.0, asset_class=AssetClass.EQUITY)
    store.save_signal(signal)
    store.save_order_result(
        OrderResult(account_id="acct1", status=OrderStatus.FILLED, signal_id=signal.id, filled_quantity=10.0),
        broker="bulk", symbol="AAPL", side=Side.BUY, requested_quantity=10.0,
    )
    account = DestinationAccount(account_id="acct1", broker="bulk")
    broker = _BulkPositionBroker({"AAPL": 10.0})
    reconciler = OrderReconciler(store, {"bulk": broker}, accounts={"acct1": account})

    await reconciler.reconcile_once()

    assert reconciler.list_orphan_positions() == []


@pytest.mark.asyncio
async def test_broker_without_bulk_capability_is_skipped_not_a_false_negative(store):
    class _NoCapabilityBroker(BrokerAdapter):
        name = "limited"

        async def place_order(self, signal, account, quantity, symbol):
            raise NotImplementedError

    account = DestinationAccount(account_id="acct1", broker="limited")
    reconciler = OrderReconciler(store, {"limited": _NoCapabilityBroker()}, accounts={"acct1": account})

    corrected = await reconciler.reconcile_once()

    assert corrected == 0
    assert reconciler.list_orphan_positions() == []  # not fabricated as "no orphans" -- genuinely never checked


# --- late-fill reconstruction ---


class _TradeHistoryBroker(BrokerAdapter):
    name = "history"

    def __init__(self, history: list[OrderResult]):
        self._history = history

    async def place_order(self, signal, account, quantity, symbol):
        raise NotImplementedError

    async def get_trade_history(self, account, symbol, since=None):
        return self._history


def _seed_rejected_order(store, *, filled_quantity=0.0):
    signal = Signal(source="test", symbol="TSLA", side=Side.BUY, quantity=20.0, asset_class=AssetClass.EQUITY)
    store.save_signal(signal)
    row_id = store.save_order_result(
        OrderResult(
            account_id="acct1", status=OrderStatus.REJECTED, signal_id=signal.id,
            broker_order_id="broker-order-9", filled_quantity=filled_quantity, message="canceled",
        ),
        broker="history", symbol="TSLA", side=Side.BUY, requested_quantity=20.0,
    )
    return row_id, signal.id


@pytest.mark.asyncio
async def test_broker_history_shows_real_fill_reconstructs_position(store):
    _seed_rejected_order(store)
    assert store.get_position("acct1", "TSLA") == 0.0

    real_fill = OrderResult(
        account_id="acct1", status=OrderStatus.FILLED, signal_id="", broker_order_id="broker-order-9",
        filled_quantity=20.0, filled_price=250.0, message="actually filled",
    )
    broker = _TradeHistoryBroker([real_fill])
    reconciler = OrderReconciler(store, {"history": broker})

    reconstructed = await reconciler.reconcile_once()

    assert reconstructed >= 1
    assert store.get_position("acct1", "TSLA") == 20.0
    orders = store.list_recent_orders()
    assert orders[0]["status"] == "filled"
    assert orders[0]["filled_quantity"] == 20.0


@pytest.mark.asyncio
async def test_late_fill_triggers_immediate_protection_audit(store):
    _seed_rejected_order(store)
    real_fill = OrderResult(
        account_id="acct1", status=OrderStatus.FILLED, signal_id="", broker_order_id="broker-order-9",
        filled_quantity=20.0, filled_price=250.0, message="actually filled",
    )
    broker = _TradeHistoryBroker([real_fill])
    auditor = ProtectionAuditor({"history": broker})
    reconciler = OrderReconciler(store, {"history": broker}, protection_auditor=auditor)

    await reconciler.reconcile_once()

    # The reconstructed position was independently audited right away --
    # the broker in this test has no position-readback capability at all,
    # so the honest, non-fabricated answer is UNKNOWN, but a finding must
    # exist (proving the audit actually ran) rather than nothing at all.
    finding = auditor.get_finding("acct1", "TSLA")
    assert finding is not None


@pytest.mark.asyncio
async def test_matching_broker_history_is_not_reconstructed_again(store):
    """Already-correct bookkeeping (broker history agrees with what was
    already applied) must not be treated as a late fill and re-applied."""
    _seed_rejected_order(store, filled_quantity=20.0)
    matching_fill = OrderResult(
        account_id="acct1", status=OrderStatus.FILLED, signal_id="", broker_order_id="broker-order-9",
        filled_quantity=20.0, filled_price=250.0, message="matches what was already applied",
    )
    broker = _TradeHistoryBroker([matching_fill])
    reconciler = OrderReconciler(store, {"history": broker})

    reconstructed = await reconciler.reconcile_once()

    assert reconstructed == 0


@pytest.mark.asyncio
async def test_broker_without_trade_history_capability_is_skipped(store):
    _seed_rejected_order(store)

    class _NoHistoryBroker(BrokerAdapter):
        name = "history"

        async def place_order(self, signal, account, quantity, symbol):
            raise NotImplementedError

    reconciler = OrderReconciler(store, {"history": _NoHistoryBroker()})

    reconstructed = await reconciler.reconcile_once()

    assert reconstructed == 0
    assert store.get_position("acct1", "TSLA") == 0.0  # left exactly as REJECTED, no guess
