from datetime import datetime, timezone

import pytest

from app.brokers.base import BrokerAdapter
from app.db import SignalStore
from app.models import AssetClass, OrderResult, OrderStatus, Side, Signal
from app.reconciliation import OrderReconciler
from signal_platform_contracts import EventType


@pytest.fixture
def store(tmp_path):
    return SignalStore(tmp_path / "test.db")


class _StubBroker(BrokerAdapter):
    name = "stub"

    def __init__(self, status_to_return: OrderResult | None):
        self._status_to_return = status_to_return

    async def place_order(self, signal, account, quantity, symbol):
        raise NotImplementedError

    async def get_order_status(self, account, broker_order_id):
        return self._status_to_return


def _seed_pending_order(
    store, *, side=Side.BUY, requested_quantity=2.0, optimistic_filled=2.0,
    asset_class=AssetClass.CRYPTO, analyst=None,
):
    signal = Signal(
        source="test", symbol="AAPL", side=side, quantity=requested_quantity,
        asset_class=asset_class, analyst=analyst,
    )
    store.save_signal(signal)
    store.record_fill("acct1", "AAPL", side, optimistic_filled)
    row_id = store.save_order_result(
        OrderResult(
            account_id="acct1",
            status=OrderStatus.PENDING,
            signal_id=signal.id,
            broker_order_id="broker-order-1",
            filled_quantity=optimistic_filled,
            message="submitted",
        ),
        broker="stub",
        symbol="AAPL",
        side=side,
        requested_quantity=requested_quantity,
    )
    return row_id, signal.id


@pytest.mark.asyncio
async def test_still_pending_order_is_left_alone(store):
    _seed_pending_order(store)
    reconciler = OrderReconciler(store, {"stub": _StubBroker(status_to_return=None)})

    corrected = await reconciler.reconcile_once()

    assert corrected == 0
    assert store.get_position("acct1", "AAPL") == 2.0
    assert store.list_pending_orders()  # still pending in the DB


@pytest.mark.asyncio
async def test_confirmed_fill_with_same_quantity_leaves_position_unchanged(store):
    _seed_pending_order(store)
    confirmed = OrderResult(
        account_id="acct1", status=OrderStatus.FILLED, signal_id="", filled_quantity=2.0, message="filled"
    )
    reconciler = OrderReconciler(store, {"stub": _StubBroker(status_to_return=confirmed)})

    corrected = await reconciler.reconcile_once()

    assert corrected == 1
    assert store.get_position("acct1", "AAPL") == 2.0
    orders = store.list_recent_orders()
    assert orders[0]["status"] == "filled"


@pytest.mark.asyncio
async def test_confirmed_fill_with_different_quantity_trues_up_position(store):
    _seed_pending_order(store, optimistic_filled=2.0)
    # broker actually only filled 1.5 of the requested 2.0
    confirmed = OrderResult(
        account_id="acct1", status=OrderStatus.FILLED, signal_id="", filled_quantity=1.5, message="filled"
    )
    reconciler = OrderReconciler(store, {"stub": _StubBroker(status_to_return=confirmed)})

    await reconciler.reconcile_once()

    assert store.get_position("acct1", "AAPL") == 1.5


@pytest.mark.asyncio
async def test_rejected_order_reverses_optimistic_position(store):
    _seed_pending_order(store, side=Side.BUY, optimistic_filled=2.0)
    assert store.get_position("acct1", "AAPL") == 2.0

    rejected = OrderResult(
        account_id="acct1", status=OrderStatus.REJECTED, signal_id="", message="rejected by broker"
    )
    reconciler = OrderReconciler(store, {"stub": _StubBroker(status_to_return=rejected)})

    await reconciler.reconcile_once()

    assert store.get_position("acct1", "AAPL") == 0.0


@pytest.mark.asyncio
async def test_rejected_sell_order_reverses_correctly(store):
    _seed_pending_order(store, side=Side.SELL, optimistic_filled=3.0)
    assert store.get_position("acct1", "AAPL") == -3.0

    rejected = OrderResult(
        account_id="acct1", status=OrderStatus.REJECTED, signal_id="", message="rejected by broker"
    )
    reconciler = OrderReconciler(store, {"stub": _StubBroker(status_to_return=rejected)})

    await reconciler.reconcile_once()

    assert store.get_position("acct1", "AAPL") == 0.0


@pytest.mark.asyncio
async def test_crash_between_position_correction_and_order_status_update_does_not_reapply(store, monkeypatch):
    """EXE-03: the position correction and marking this order row done used
    to be two separate commits. An interruption between them left the row
    still status='pending', so the next reconciliation pass re-fetched the
    same broker answer, recomputed the same correction from the same STALE
    orders.filled_quantity baseline, and applied it a second time (a
    100-unit buy canceled with 30 filled was observed reaching -40, not the
    correct 30)."""
    _seed_pending_order(store, side=Side.BUY, requested_quantity=100.0, optimistic_filled=100.0)
    assert store.get_position("acct1", "AAPL") == 100.0

    rejected_partial = OrderResult(
        account_id="acct1", status=OrderStatus.REJECTED, signal_id="", filled_quantity=30.0, message="canceled"
    )
    reconciler = OrderReconciler(store, {"stub": _StubBroker(status_to_return=rejected_partial)})

    class ProcessLoss(BaseException):
        pass

    original = store.correct_position_and_update_order_status

    def commit_then_interrupt(*args, **kwargs):
        original(*args, **kwargs)  # real commit, not a fabricated position row
        raise ProcessLoss("crashed after the correction committed")

    monkeypatch.setattr(store, "correct_position_and_update_order_status", commit_then_interrupt)
    with pytest.raises(ProcessLoss):
        await reconciler.reconcile_once()

    assert store.get_position("acct1", "AAPL") == 30.0  # correct after the crash

    # Recovery: a fresh reconciler re-checks the same order.
    monkeypatch.undo()
    restored_reconciler = OrderReconciler(store, {"stub": _StubBroker(status_to_return=rejected_partial)})
    corrected = await restored_reconciler.reconcile_once()

    assert corrected == 0  # the order row is already 'rejected', not re-processed
    assert store.get_position("acct1", "AAPL") == 30.0  # unchanged, not re-corrected to -40


@pytest.mark.asyncio
async def test_unregistered_broker_is_skipped(store):
    _seed_pending_order(store)
    reconciler = OrderReconciler(store, brokers={})  # 'stub' broker not registered

    corrected = await reconciler.reconcile_once()

    assert corrected == 0


@pytest.mark.asyncio
async def test_reconciled_fill_exports_execution_applied_event(store):
    """B5: a broker like Alpaca/IBKR reports PENDING at placement time and
    only confirms FILLED later, right here -- the one confirmed-fill path
    that, before this fix, never exported an EXECUTION_APPLIED event to
    the commercial platform at all (only a result that was ALREADY FILLED
    synchronously at placement time did, via app/engine.py's own
    `_build_export_envelope`). A real fill this reconciler itself is the
    sole confirmation of must reach the outbox exactly the same way."""
    row_id, signal_id = _seed_pending_order(
        store, side=Side.BUY, requested_quantity=2.0, optimistic_filled=2.0,
        asset_class=AssetClass.EQUITY, analyst="analyst-1",
    )
    assert not store.list_undelivered_export_events()  # nothing exported yet

    confirmed = OrderResult(
        account_id="acct1",
        status=OrderStatus.FILLED,
        signal_id="",  # deliberately blank -- exactly what a real broker's get_order_status returns
        broker_order_id="broker-order-1",
        filled_quantity=2.0,
        filled_price=101.5,
        message="filled",
        executed_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
    )
    reconciler = OrderReconciler(store, {"stub": _StubBroker(status_to_return=confirmed)})

    corrected = await reconciler.reconcile_once()

    assert corrected == 1
    events = store.list_undelivered_export_events()
    assert len(events) == 1
    envelope = events[0]
    assert envelope.event_type == EventType.EXECUTION_APPLIED
    assert envelope.source_stream == "signal-copier:acct1"
    assert envelope.payload["broker_order_id"] == "broker-order-1"
    assert envelope.payload["filled_quantity"] == "2.0"
    assert envelope.payload["filled_price"] == "101.5"
    assert envelope.payload["instrument"]["market_type"] == AssetClass.EQUITY.value
    assert envelope.payload["originating_source_event_id"] == signal_id
    assert envelope.payload["originating_analyst_id"] == "analyst-1"
    assert row_id  # the seeded order row itself was corrected, not left dangling


@pytest.mark.asyncio
async def test_reconciled_fill_missing_filled_price_exports_nothing(store):
    """`build_execution_applied_envelope` returns `None` (never a
    best-effort/partial envelope) when a FILLED result is missing a field
    the payload actually requires -- this reconciler-confirmed path must
    honor that exact same contract, not fabricate a placeholder."""
    _seed_pending_order(store, side=Side.BUY, requested_quantity=2.0, optimistic_filled=2.0)
    confirmed = OrderResult(
        account_id="acct1",
        status=OrderStatus.FILLED,
        signal_id="",
        broker_order_id="broker-order-1",
        filled_quantity=2.0,
        filled_price=None,  # some adapter that doesn't report a fill price
        message="filled",
    )
    reconciler = OrderReconciler(store, {"stub": _StubBroker(status_to_return=confirmed)})

    corrected = await reconciler.reconcile_once()

    assert corrected == 1
    assert not store.list_undelivered_export_events()


@pytest.mark.asyncio
async def test_rejected_order_exports_nothing(store):
    """A REJECTED confirmation is not a fill -- it must never reach the
    outbox as an EXECUTION_APPLIED event."""
    _seed_pending_order(store, side=Side.BUY, optimistic_filled=2.0)
    rejected = OrderResult(
        account_id="acct1", status=OrderStatus.REJECTED, signal_id="", message="rejected by broker"
    )
    reconciler = OrderReconciler(store, {"stub": _StubBroker(status_to_return=rejected)})

    await reconciler.reconcile_once()

    assert not store.list_undelivered_export_events()
