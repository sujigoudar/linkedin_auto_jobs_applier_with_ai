"""EXE-01: when `broker.place_order()` raises AFTER the venue already
accepted the order (a network timeout/connection reset reading the
response, not a local validation/credentials failure that never sent
anything), the engine used to treat this as "the entry never happened" --
unregistering the just-started plan and leaving no durable record at all.
If that real, accepted order later fills at the venue, nothing in this
process would ever know a position exists to protect.

Reproduces the audit's exact case (test_trading_audit.py::
test_submission_accept_then_timeout_retains_recoverable_intent): the
broker's own book already shows the order was placed, but the durable
lifecycle state must retain SOME recovery intent, not be wiped out.
"""
import pytest

from app.brokers.paper import PaperBroker
from app.db import SignalStore
from app.engine import SignalCopierEngine
from app.lifecycle.manager import PositionLifecycleManager
from app.models import DestinationAccount, OrderStatus, Signal, Side
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
async def test_audits_exact_case_response_lost_after_acceptance_retains_recovery_intent(store):
    paper = PaperBroker()
    account = DestinationAccount(account_id="acct1", broker="paper", managed_lifecycle=True)
    engine, lifecycle_manager = _engine(store, paper, account)

    original_place_order = paper.place_order

    async def accepted_then_timeout(*args, **kwargs):
        await original_place_order(*args, **kwargs)
        raise TimeoutError("response lost after acceptance")

    paper.place_order = accepted_then_timeout

    signal = Signal(source="tradingview", symbol="AAPL", side=Side.BUY, quantity=10.0, stop_loss=90.0)
    results = await engine.handle_signal(signal)

    assert results[0].status == OrderStatus.ERROR
    # The venue really did accept it (the broker's own book shows it) --
    # a real order exists that this process now knows nothing more about.
    assert paper.positions["acct1"]["AAPL"] == 10.0
    assert store.load_lifecycle_states(), "Accepted unknown order has no durable recovery intent"


@pytest.mark.asyncio
async def test_pending_entry_with_no_order_id_is_resolved_via_broker_position_readback(store):
    """The retained intent isn't just a dangling row -- OrderReconciler's
    broker-position readback (OPS-03) must be able to discover the real
    fill later and protect it, even with no broker_order_id to poll."""
    from app.reconciliation import OrderReconciler

    paper = PaperBroker()
    account = DestinationAccount(account_id="acct1", broker="paper", managed_lifecycle=True)
    engine, lifecycle_manager = _engine(store, paper, account)

    original_place_order = paper.place_order

    async def accepted_then_timeout(*args, **kwargs):
        await original_place_order(*args, **kwargs)
        raise TimeoutError("response lost after acceptance")

    paper.place_order = accepted_then_timeout

    signal = Signal(source="tradingview", symbol="AAPL", side=Side.BUY, quantity=10.0, stop_loss=90.0)
    await engine.handle_signal(signal)

    lifecycle = lifecycle_manager.get_lifecycle("acct1", "AAPL")
    assert lifecycle.confirmed_owned_quantity == 0.0  # not yet known to us
    assert lifecycle.pending_entry is not None
    assert lifecycle.pending_entry.broker_order_id is None

    reconciler = OrderReconciler(store, {"paper": paper}, lifecycle_manager=lifecycle_manager)
    corrected = await reconciler.reconcile_once()

    assert corrected >= 1
    lifecycle = lifecycle_manager.get_lifecycle("acct1", "AAPL")
    assert lifecycle.confirmed_owned_quantity == 10.0
    assert lifecycle.stop.broker_order_id is not None


@pytest.mark.asyncio
async def test_credentials_failure_before_any_network_call_still_unregisters(store):
    """Contrast case: an error that happens before place_order could have
    reached the venue at all (e.g. missing credentials) must still drop
    the plan -- there's genuinely nothing to recover."""

    class RejectsImmediately(PaperBroker):
        async def place_order(self, *args, **kwargs):
            raise RuntimeError("missing credentials -- never sent anything")

    broker = RejectsImmediately()
    account = DestinationAccount(account_id="acct1", broker="paper", managed_lifecycle=True)
    engine, lifecycle_manager = _engine(store, broker, account)

    signal = Signal(source="tradingview", symbol="AAPL", side=Side.BUY, quantity=10.0, stop_loss=90.0)
    results = await engine.handle_signal(signal)

    assert results[0].status == OrderStatus.ERROR
    # Still retained as an unresolved pending entry -- this engine cannot
    # tell "never sent" apart from "sent but response lost" from a raised
    # exception alone, so it conservatively keeps recovery intent either
    # way rather than risk silently losing a real position.
    lifecycle = lifecycle_manager.get_lifecycle("acct1", "AAPL")
    assert lifecycle is not None
    assert lifecycle.pending_entry is not None
