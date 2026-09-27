"""E03 (bounded): app/capital_allocator.py's notional-exposure ceiling,
wired into both the plain-account and managed_lifecycle entry paths in
app/engine.py.
"""
import asyncio

import pytest

from app.brokers.paper import PaperBroker
from app.db import SignalStore
from app.engine import SignalCopierEngine
from app.lifecycle.manager import PositionLifecycleManager
from app.models import DestinationAccount, OrderResult, OrderStatus, Side, Signal
from app.routing import RoutingConfig, RoutingRule

SOURCE = "tradingview"
SYMBOL = "AAPL"


def _engine(store: SignalStore, account: DestinationAccount, broker: PaperBroker) -> SignalCopierEngine:
    routing = RoutingConfig(rules=[RoutingRule(source=SOURCE, destinations=["acct1"])], accounts={"acct1": account})
    lifecycle_manager = PositionLifecycleManager(brokers={"paper": broker}, store=store)
    return SignalCopierEngine(routing=routing, brokers={"paper": broker}, store=store, lifecycle_manager=lifecycle_manager)


@pytest.mark.asyncio
async def test_plain_account_entry_rejected_when_it_would_exceed_the_ceiling(tmp_path):
    store = SignalStore(tmp_path / "test.db")
    broker = PaperBroker()
    account = DestinationAccount(account_id="acct1", broker="paper", max_notional_exposure=500.0)
    engine = _engine(store, account, broker)

    signal = Signal(source=SOURCE, symbol=SYMBOL, side=Side.BUY, quantity=10.0, price=100.0)  # notional 1000 > 500
    results = await engine.handle_signal(signal)

    assert results[0].status == OrderStatus.REJECTED
    assert "notional exposure ceiling" in results[0].message
    assert broker.fills == []  # never reached the broker


@pytest.mark.asyncio
async def test_plain_account_entry_admitted_within_the_ceiling(tmp_path):
    store = SignalStore(tmp_path / "test.db")
    broker = PaperBroker()
    account = DestinationAccount(account_id="acct1", broker="paper", max_notional_exposure=2000.0)
    engine = _engine(store, account, broker)

    signal = Signal(source=SOURCE, symbol=SYMBOL, side=Side.BUY, quantity=10.0, price=100.0)  # notional 1000 <= 2000
    results = await engine.handle_signal(signal)

    assert results[0].status == OrderStatus.FILLED


@pytest.mark.asyncio
async def test_second_entry_rejected_once_confirmed_exposure_already_fills_the_ceiling(tmp_path):
    store = SignalStore(tmp_path / "test.db")
    broker = PaperBroker()
    account = DestinationAccount(account_id="acct1", broker="paper", max_notional_exposure=1500.0)
    engine = _engine(store, account, broker)

    first = Signal(source=SOURCE, symbol=SYMBOL, side=Side.BUY, quantity=10.0, price=100.0)  # notional 1000
    await engine.handle_signal(first)

    second = Signal(source=SOURCE, symbol="MSFT", side=Side.BUY, quantity=10.0, price=100.0)  # would add 1000 -> 2000 > 1500
    results = await engine.handle_signal(second)

    assert results[0].status == OrderStatus.REJECTED


@pytest.mark.asyncio
async def test_no_ceiling_configured_never_gates_anything(tmp_path):
    store = SignalStore(tmp_path / "test.db")
    broker = PaperBroker()
    account = DestinationAccount(account_id="acct1", broker="paper")  # max_notional_exposure=None
    engine = _engine(store, account, broker)

    signal = Signal(source=SOURCE, symbol=SYMBOL, side=Side.BUY, quantity=10_000.0, price=100.0)
    results = await engine.handle_signal(signal)

    assert results[0].status == OrderStatus.FILLED


@pytest.mark.asyncio
async def test_no_price_on_the_signal_skips_the_check_rather_than_guessing(tmp_path):
    store = SignalStore(tmp_path / "test.db")
    broker = PaperBroker()
    account = DestinationAccount(account_id="acct1", broker="paper", max_notional_exposure=1.0)
    engine = _engine(store, account, broker)

    signal = Signal(source=SOURCE, symbol=SYMBOL, side=Side.BUY, quantity=10.0)  # no price -- would obviously fail any real check
    results = await engine.handle_signal(signal)

    assert results[0].status == OrderStatus.FILLED


@pytest.mark.asyncio
async def test_managed_lifecycle_entry_rejected_when_it_would_exceed_the_ceiling(tmp_path):
    store = SignalStore(tmp_path / "test.db")
    broker = PaperBroker()
    account = DestinationAccount(account_id="acct1", broker="paper", managed_lifecycle=True, max_notional_exposure=500.0)
    engine = _engine(store, account, broker)

    signal = Signal(source=SOURCE, symbol=SYMBOL, side=Side.BUY, quantity=10.0, price=100.0, stop_loss=90.0)
    results = await engine.handle_signal(signal)

    assert results[0].status == OrderStatus.REJECTED
    assert "notional exposure ceiling" in results[0].message
    # A rejected admission must not leave a stray lifecycle plan registered.
    assert engine.lifecycle_manager.get_lifecycle("acct1", SYMBOL) is None


@pytest.mark.asyncio
async def test_concurrent_entries_for_the_same_account_cannot_both_exceed_the_ceiling(tmp_path):
    """The core E03 obligation, exercised through the real engine: two
    signals for the same account arriving concurrently, each individually
    under the ceiling but together over it, must not both be admitted."""
    store = SignalStore(tmp_path / "test.db")
    broker = PaperBroker()
    account = DestinationAccount(account_id="acct1", broker="paper", max_notional_exposure=1500.0)
    engine = _engine(store, account, broker)

    signal_a = Signal(source=SOURCE, symbol="AAPL", side=Side.BUY, quantity=10.0, price=100.0)  # notional 1000
    signal_b = Signal(source=SOURCE, symbol="MSFT", side=Side.BUY, quantity=10.0, price=100.0)  # notional 1000

    results_a, results_b = await asyncio.gather(engine.handle_signal(signal_a), engine.handle_signal(signal_b))

    statuses = sorted([results_a[0].status.value, results_b[0].status.value])
    assert statuses == [OrderStatus.FILLED.value, OrderStatus.REJECTED.value]


@pytest.mark.asyncio
async def test_known_gap_two_sequential_pending_orders_can_both_be_admitted_past_the_ceiling(tmp_path):
    """Documents a known, disclosed limitation (see capital_allocator.py's
    own docstring) rather than asserting it's fine: the provisional
    reservation is released as soon as `place_order` returns, for EVERY
    outcome including PENDING -- but a PENDING order isn't part of
    `confirmed_open_notional` yet (that only counts orders whose STORED
    status is actually FILLED). Between "broker accepted, reported
    PENDING" and "reconciliation later confirms the fill," this notional
    counts toward neither the reservation ledger nor confirmed exposure.

    Two sequential (not even concurrent -- this doesn't need a race)
    $700 orders both get admitted under a $1000 ceiling, even though both
    can still go on to fill for $1400 of real combined exposure. This
    test exists to turn into a real regression the moment this gap is
    closed (at which point the second order should be REJECTED) --
    deleting it to "make the suite pass" would hide the fix's own
    verification, not demonstrate it.
    """
    store = SignalStore(tmp_path / "test.db")
    broker = PaperBroker()

    async def pending_not_filled(signal, account, quantity, symbol):
        # A real broker's synchronous "accepted, not yet confirmed" reply
        # (e.g. AlpacaBroker/SignalStackBroker) -- PaperBroker itself
        # always fills synchronously, so this substitutes a PENDING
        # response without recording anything in the orders table as
        # FILLED, matching what confirmed_open_notional actually reads.
        return OrderResult(account_id=account.account_id, status=OrderStatus.PENDING, signal_id=signal.id)

    broker.place_order = pending_not_filled
    account = DestinationAccount(account_id="acct1", broker="paper", max_notional_exposure=1000.0)
    engine = _engine(store, account, broker)

    first = Signal(source=SOURCE, symbol="AAPL", side=Side.BUY, quantity=7.0, price=100.0)  # notional 700
    second = Signal(source=SOURCE, symbol="MSFT", side=Side.BUY, quantity=7.0, price=100.0)  # notional 700

    first_results = await engine.handle_signal(first)
    second_results = await engine.handle_signal(second)

    assert first_results[0].status == OrderStatus.PENDING
    # The known gap: this should be REJECTED once the reservation
    # correctly survives a PENDING outcome, but currently isn't.
    assert second_results[0].status == OrderStatus.PENDING
