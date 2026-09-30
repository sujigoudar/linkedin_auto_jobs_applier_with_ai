"""TRK-23: the cross-repo audit's HIGH-severity gap -- a managed_lifecycle
fill (CHANGELOG's own words: "the majority of real trading activity")
recorded AUD-01's quantity fields into this service's OWN `orders` table
(TRK-22) but never built or exported a real EXECUTION_APPLIED envelope,
leaving signal-portfolio-commercial's `Book.PLATFORM` ledger silently
missing it, with no error and no failed test. This directly asserts the
fix at every real confirmed-fill point a managed-lifecycle position can
reach:

- a managed entry that fills SYNCHRONOUSLY (`_handle_signal`'s managed
  branch, app/engine.py)
- a managed entry that reports PENDING and is later confirmed FILLED by
  `OrderReconciler` (app/reconciliation.py)
- a managed CLOSE (a provider EXIT signal) that fills synchronously
- a managed CLOSE that reports PENDING and is later confirmed FILLED by
  `OrderReconciler`
- a manual "Exit now"/"Flatten" close (`SignalCopierEngine.close_position`)
- a protective stop filling on its own, with no engine/reconciler call
  site of its own at all (`PositionLifecycleManager.on_stop_filled`)

and that none of these double-exports the same fill (the plain-account
reconciler path, `_correct_position`/`_export_reconciled_fill`, is
structurally never reached for a lifecycle's own pending order -- see
`OrderReconciler.reconcile_once`'s own comment)."""
from __future__ import annotations

from dataclasses import replace

import pytest
from signal_platform_contracts import EventType

from app.brokers.paper import PaperBroker
from app.db import SignalStore
from app.engine import SignalCopierEngine
from app.lifecycle.manager import PositionLifecycleManager
from app.models import DestinationAccount, OrderResult, OrderStatus, Side, Signal
from app.reconciliation import OrderReconciler
from app.routing import RoutingConfig, RoutingRule


@pytest.fixture
def store(tmp_path):
    return SignalStore(tmp_path / "test.db")


def _by_type(envelopes, event_type):
    return [e for e in envelopes if e.event_type == event_type]


def _executions(store):
    return _by_type(store.list_undelivered_export_events(), EventType.EXECUTION_APPLIED)


class _ScriptedBroker(PaperBroker):
    """A PaperBroker whose `place_order` pops from a scripted response
    queue (one call = one response) instead of always filling instantly --
    lets a test drive an entry through one outcome and a later exit
    through another. Falls back to PaperBroker's own synchronous-FILLED
    behavior once the queue is empty, same as every other test in this
    codebase relies on for a plain synchronous fill. `get_order_status`
    replays whatever terminal result the test scripted for a given
    broker_order_id -- the exact shape OrderReconciler polls."""

    def __init__(self) -> None:
        super().__init__()
        self._queued: list[OrderResult] = []
        self._status_by_order_id: dict[str, OrderResult] = {}

    def queue_place_order_result(self, result: OrderResult) -> None:
        self._queued.append(result)

    async def place_order(self, signal, account, quantity, symbol):
        if self._queued:
            # `orders.signal_id` is a real `NOT NULL REFERENCES signals(id)`
            # foreign key -- the queued result must carry THIS call's own
            # real signal id (`app/engine.py` always saves it before
            # calling the broker), never the placeholder used to build it.
            return replace(self._queued.pop(0), signal_id=signal.id)
        return await super().place_order(signal, account, quantity, symbol)

    async def get_order_status(self, account, broker_order_id):
        return self._status_by_order_id.get(broker_order_id)

    async def get_broker_position(self, account, symbol):
        # A queued PENDING result never touches PaperBroker's own
        # `self.positions` book (unlike its inherited synchronous-fill
        # behavior) -- returning that stale/zero book here would make
        # OrderReconciler's broker-position readback (OPS-03) see a false
        # deficit once `resolve_pending_entry`/`resolve_pending_exit`
        # apply the confirmed fill to `SignalStore`, and misfire
        # `on_stop_filled` for a stop that never actually filled. None
        # (honestly unknown) is exactly what `_reconcile_broker_positions`
        # already treats as "never treated as confirming zero" -- see its
        # own docstring, and `_ControllablePendingBroker`'s identical
        # override in tests/test_pending_fill_reconciliation_integration.py.
        return None

    def script_terminal_result(
        self, broker_order_id: str, *, status: OrderStatus, filled_quantity: float | None, filled_price: float | None
    ) -> None:
        self._status_by_order_id[broker_order_id] = OrderResult(
            account_id="doesn't matter",
            status=status,
            signal_id="",
            # `build_execution_applied_envelope` requires a real
            # `broker_order_id` (see its own docstring) -- exactly what a
            # real broker's `get_order_status` response carries.
            broker_order_id=broker_order_id,
            filled_quantity=filled_quantity,
            filled_price=filled_price,
            message="done",
        )


def _managed_engine(store, broker, account_id="acct1"):
    account = DestinationAccount(account_id=account_id, broker="paper", managed_lifecycle=True)
    routing = RoutingConfig(
        rules=[RoutingRule(source="tradingview", destinations=[account_id])], accounts={account_id: account}
    )
    lifecycle_manager = PositionLifecycleManager(brokers={"paper": broker}, store=store)
    engine = SignalCopierEngine(
        routing=routing, brokers={"paper": broker}, store=store, lifecycle_manager=lifecycle_manager
    )
    return engine, account, lifecycle_manager


# --- 1. Synchronous managed entry ---


@pytest.mark.asyncio
async def test_synchronous_managed_entry_fill_exports_exactly_one_execution_applied(store):
    broker = PaperBroker()
    engine, account, _ = _managed_engine(store, broker)

    signal = Signal(source="tradingview", symbol="AAPL", side=Side.BUY, quantity=10.0, price=50.0, stop_loss=45.0)
    results = await engine.handle_signal(signal)
    assert results[0].status == OrderStatus.FILLED

    executions = _executions(store)
    assert len(executions) == 1
    envelope = executions[0]
    assert envelope.source_stream == "signal-copier:acct1"
    assert envelope.payload["side"] == "buy"
    assert envelope.payload["filled_quantity"] == "10.0"
    assert envelope.payload["broker_order_id"] == results[0].broker_order_id
    assert envelope.payload["account"]["account_id"] == "acct1"
    assert envelope.payload["fee"] is None  # never fabricated


@pytest.mark.asyncio
async def test_rejected_managed_entry_exports_no_execution_applied(store):
    broker = PaperBroker()
    engine, account, _ = _managed_engine(store, broker)

    # No stop_loss -- validate_plan rejects this before any broker call.
    signal = Signal(source="tradingview", symbol="AAPL", side=Side.BUY, quantity=10.0)
    results = await engine.handle_signal(signal)
    assert results[0].status == OrderStatus.REJECTED
    assert _executions(store) == []


# --- 2. Synchronous managed close (provider EXIT) ---


@pytest.mark.asyncio
async def test_synchronous_managed_close_fill_exports_exactly_one_new_execution_applied(store):
    broker = PaperBroker()
    engine, account, lifecycle_manager = _managed_engine(store, broker)

    entry = Signal(source="tradingview", symbol="AAPL", side=Side.BUY, quantity=10.0, price=50.0, stop_loss=45.0)
    await engine.handle_signal(entry)
    assert len(_executions(store)) == 1  # the entry's own export

    close = Signal(source="tradingview", symbol="AAPL", side=Side.CLOSE, price=55.0)
    results = await engine.handle_signal(close)
    assert results[0].status == OrderStatus.FILLED

    executions = _executions(store)
    assert len(executions) == 2  # entry + close, never double-counted
    close_envelope = executions[-1]
    assert close_envelope.payload["side"] == "sell"  # the resolved exit side, never "close"
    assert close_envelope.payload["filled_quantity"] == "10.0"
    assert close_envelope.payload["broker_order_id"] == results[0].broker_order_id


# --- 3. Manual "Exit now" close ---


@pytest.mark.asyncio
async def test_manual_close_position_fill_exports_execution_applied_with_resolved_side(store):
    broker = PaperBroker()
    engine, account, lifecycle_manager = _managed_engine(store, broker)

    entry = Signal(source="tradingview", symbol="AAPL", side=Side.BUY, quantity=10.0, price=50.0, stop_loss=45.0)
    await engine.handle_signal(entry)
    assert len(_executions(store)) == 1

    result = await engine.close_position(account, "AAPL", reason="manual_exit")
    assert result.status == OrderStatus.FILLED

    executions = _executions(store)
    assert len(executions) == 2
    manual_envelope = executions[-1]
    assert manual_envelope.payload["side"] == "sell"
    assert manual_envelope.payload["broker_order_id"] == result.broker_order_id


# --- 4. Protective stop filling on its own (no engine/reconciler call site) ---


@pytest.mark.asyncio
async def test_on_stop_filled_exports_execution_applied_with_the_real_stop_broker_order_id(store):
    broker = PaperBroker()
    engine, account, lifecycle_manager = _managed_engine(store, broker)

    entry = Signal(source="tradingview", symbol="AAPL", side=Side.BUY, quantity=10.0, price=50.0, stop_loss=45.0)
    await engine.handle_signal(entry)
    assert len(_executions(store)) == 1  # the entry's own export

    lifecycle = lifecycle_manager.get_lifecycle("acct1", "AAPL")
    stop_broker_order_id = lifecycle.stop.broker_order_id
    assert stop_broker_order_id is not None

    fills = broker.simulate_price("AAPL", 44.0)  # triggers the resting SELL stop
    assert len(fills) == 1
    await lifecycle_manager.on_stop_filled(account, "AAPL", filled_quantity=10.0, filled_price=44.0)

    executions = _executions(store)
    assert len(executions) == 2  # entry + the stop's own exit, never double-counted
    stop_envelope = executions[-1]
    assert stop_envelope.payload["side"] == "sell"
    assert stop_envelope.payload["filled_quantity"] == "10.0"
    assert stop_envelope.payload["filled_price"] == "44.0"
    assert stop_envelope.payload["broker_order_id"] == stop_broker_order_id


# --- 5. Async-resolved managed entry (OrderReconciler) ---


@pytest.mark.asyncio
async def test_async_resolved_managed_entry_exports_execution_applied_exactly_once(store):
    broker = _ScriptedBroker()
    engine, account, lifecycle_manager = _managed_engine(store, broker)

    broker.queue_place_order_result(
        OrderResult(
            account_id="acct1",
            status=OrderStatus.PENDING,
            signal_id="",
            broker_order_id="entry-order-1",
            filled_quantity=None,
            message="submitted, awaiting fill",
        )
    )
    signal = Signal(source="tradingview", symbol="AAPL", side=Side.BUY, quantity=10.0, price=50.0, stop_loss=45.0)
    results = await engine.handle_signal(signal)
    assert results[0].status == OrderStatus.PENDING
    assert _executions(store) == []  # not exported yet -- nothing confirmed

    broker.script_terminal_result(
        "entry-order-1", status=OrderStatus.FILLED, filled_quantity=10.0, filled_price=50.0
    )
    reconciler = OrderReconciler(store, {"paper": broker}, lifecycle_manager=lifecycle_manager)
    corrected = await reconciler.reconcile_once()
    assert corrected >= 1

    executions = _executions(store)
    assert len(executions) == 1
    envelope = executions[0]
    assert envelope.payload["side"] == "buy"
    assert envelope.payload["filled_quantity"] == "10.0"
    assert envelope.payload["broker_order_id"] == "entry-order-1"

    # Confirm it's never exported a second time on a later, idle pass.
    await reconciler.reconcile_once()
    assert len(_executions(store)) == 1


# --- 6. Async-resolved managed close (provider EXIT, OrderReconciler) ---


@pytest.mark.asyncio
async def test_async_resolved_managed_close_exports_execution_applied_with_resolved_side(store):
    broker = _ScriptedBroker()
    engine, account, lifecycle_manager = _managed_engine(store, broker)

    entry = Signal(source="tradingview", symbol="AAPL", side=Side.BUY, quantity=10.0, price=50.0, stop_loss=45.0)
    await engine.handle_signal(entry)
    assert len(_executions(store)) == 1  # the entry's own synchronous export

    broker.queue_place_order_result(
        OrderResult(
            account_id="acct1",
            status=OrderStatus.PENDING,
            signal_id="",
            broker_order_id="exit-order-1",
            filled_quantity=None,
            message="submitted, awaiting fill",
        )
    )
    close = Signal(source="tradingview", symbol="AAPL", side=Side.CLOSE, price=55.0)
    results = await engine.handle_signal(close)
    assert results[0].status == OrderStatus.PENDING
    assert len(_executions(store)) == 1  # still just the entry -- the close isn't confirmed yet

    broker.script_terminal_result(
        "exit-order-1", status=OrderStatus.FILLED, filled_quantity=10.0, filled_price=55.0
    )
    reconciler = OrderReconciler(store, {"paper": broker}, lifecycle_manager=lifecycle_manager)
    await reconciler.reconcile_once()

    executions = _executions(store)
    assert len(executions) == 2
    close_envelope = executions[-1]
    # The stored `orders.side` for this row is literally Side.CLOSE (see
    # app/engine.py's managed branch) -- this is the exact case the
    # lifecycle-derived `exit_side` resolution exists for for exactly this
    # reason: build_execution_applied_envelope must never see "close".
    assert close_envelope.payload["side"] == "sell"
    assert close_envelope.payload["filled_quantity"] == "10.0"
    assert close_envelope.payload["broker_order_id"] == "exit-order-1"

    lifecycle = lifecycle_manager.get_lifecycle("acct1", "AAPL")
    assert lifecycle.closed is True
