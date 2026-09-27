"""EXE-01b: `_handle_managed_entry` retained a plan when `place_order`
RAISED after possible acceptance (see test_exe01_submission_response_lost.py),
but still unconditionally unregistered the plan when a broker instead
CAUGHT its own transport/timeout error internally and RETURNED
`OrderStatus.ERROR` -- exactly what AlpacaBroker does (`except
httpx.HTTPError as exc: return OrderResult(status=OrderStatus.ERROR, ...)`
in app/brokers/alpaca.py, which also catches httpx.TimeoutException since
it's an HTTPError subclass). A request that reached Alpaca and was
accepted before the client gave up waiting on the response looked, from
here, identical to one that failed validation and never left this
process -- both surfaced as a returned ERROR, and both used to silently
drop lifecycle protection for what may already be a real position.

This is the same ambiguity EXE-01 already handles for the raised-exception
path, just via the returned-result path instead. Only a broker-confirmed
`OrderStatus.REJECTED` (or a client-side validation failure that never
reached the network) is treated as "definitely never happened."
"""
from __future__ import annotations

import pytest

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


def _engine(store, paper, account):
    routing = RoutingConfig(
        rules=[RoutingRule(source="tradingview", destinations=[account.account_id])],
        accounts={account.account_id: account},
    )
    lifecycle_manager = PositionLifecycleManager(brokers={"paper": paper}, store=store)
    engine = SignalCopierEngine(
        routing=routing, brokers={"paper": paper}, store=store, lifecycle_manager=lifecycle_manager
    )
    return engine, lifecycle_manager


@pytest.mark.asyncio
async def test_error_result_after_real_acceptance_retains_recovery_intent(store):
    paper = PaperBroker()
    account = DestinationAccount(account_id="acct1", broker="paper", managed_lifecycle=True)
    engine, lifecycle_manager = _engine(store, paper, account)

    original_place_order = paper.place_order

    async def accepted_then_returns_error(signal, account, quantity, symbol):
        # The venue (paper's own book) really did accept it -- mirrors
        # AlpacaBroker's own internal `except httpx.HTTPError` catching a
        # timeout that happened AFTER the venue already processed the
        # request, and returning ERROR rather than letting it propagate.
        await original_place_order(signal, account, quantity, symbol)
        return OrderResult(
            account_id=account.account_id,
            status=OrderStatus.ERROR,
            signal_id=signal.id,
            message="timed out waiting for the response (order may have been accepted)",
        )

    paper.place_order = accepted_then_returns_error

    signal = Signal(source="tradingview", symbol="AAPL", side=Side.BUY, quantity=10.0, stop_loss=90.0)
    results = await engine.handle_signal(signal)

    assert results[0].status == OrderStatus.ERROR
    assert paper.positions["acct1"]["AAPL"] == 10.0  # the venue really does hold it

    lifecycle = lifecycle_manager.get_lifecycle("acct1", "AAPL")
    assert lifecycle is not None, "the plan must not be unregistered -- a real position may exist to protect"
    assert lifecycle.pending_entry is not None
    assert lifecycle.pending_entry.broker_order_id is None


@pytest.mark.asyncio
async def test_error_result_pending_entry_is_resolved_via_broker_position_readback(store):
    """Same discovery path as the raised-exception case: OrderReconciler's
    broker-position readback (OPS-03) finds and protects the real fill."""
    paper = PaperBroker()
    account = DestinationAccount(account_id="acct1", broker="paper", managed_lifecycle=True)
    engine, lifecycle_manager = _engine(store, paper, account)

    original_place_order = paper.place_order

    async def accepted_then_returns_error(signal, account, quantity, symbol):
        await original_place_order(signal, account, quantity, symbol)
        return OrderResult(
            account_id=account.account_id, status=OrderStatus.ERROR, signal_id=signal.id, message="response lost"
        )

    paper.place_order = accepted_then_returns_error

    signal = Signal(source="tradingview", symbol="AAPL", side=Side.BUY, quantity=10.0, stop_loss=90.0)
    await engine.handle_signal(signal)

    reconciler = OrderReconciler(store, {"paper": paper}, lifecycle_manager=lifecycle_manager)
    corrected = await reconciler.reconcile_once()

    assert corrected >= 1
    lifecycle = lifecycle_manager.get_lifecycle("acct1", "AAPL")
    assert lifecycle.confirmed_owned_quantity == 10.0
    assert lifecycle.stop.broker_order_id is not None


@pytest.mark.asyncio
async def test_broker_confirmed_rejection_still_unregisters_the_plan(store):
    """Contrast case: a genuine REJECTED (the broker confirms it never
    accepted the order) is the one ERROR-adjacent outcome that really is
    definitive -- nothing to protect, so nothing to keep registered."""

    class RejectsAtSubmission(PaperBroker):
        async def place_order(self, signal, account, quantity, symbol):
            return OrderResult(
                account_id=account.account_id,
                status=OrderStatus.REJECTED,
                signal_id=signal.id,
                message="broker confirmed: order rejected, nothing accepted",
            )

    broker = RejectsAtSubmission()
    account = DestinationAccount(account_id="acct1", broker="paper", managed_lifecycle=True)
    engine, lifecycle_manager = _engine(store, broker, account)

    signal = Signal(source="tradingview", symbol="AAPL", side=Side.BUY, quantity=10.0, stop_loss=90.0)
    results = await engine.handle_signal(signal)

    assert results[0].status == OrderStatus.REJECTED
    assert lifecycle_manager.get_lifecycle("acct1", "AAPL") is None
