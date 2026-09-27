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
from app.reconciliation import OrderReconciler
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
async def test_managed_lifecycle_pending_entry_with_a_broker_order_id_keeps_its_reservation(tmp_path):
    """Same reservation-timing fix as the plain-account test above, for a
    managed_lifecycle account -- here the reservation lives on
    app/lifecycle/manager.py's PendingEntry (`reserved_notional`), released
    by `resolve_pending_entry` once app/reconciliation.py's
    `_reconcile_pending_entries` confirms a terminal outcome, not by the
    orders-table column app/reconciliation.py's `_correct_position` uses
    for a plain account."""
    store = SignalStore(tmp_path / "test.db")
    broker = PaperBroker()

    async def pending_not_filled(signal, account, quantity, symbol):
        return OrderResult(
            account_id=account.account_id, status=OrderStatus.PENDING, signal_id=signal.id, broker_order_id="order-1"
        )

    broker.place_order = pending_not_filled
    account = DestinationAccount(
        account_id="acct1", broker="paper", managed_lifecycle=True, max_notional_exposure=1000.0
    )
    engine = _engine(store, account, broker)

    first = Signal(source=SOURCE, symbol="AAPL", side=Side.BUY, quantity=7.0, price=100.0, stop_loss=90.0)
    second = Signal(source=SOURCE, symbol="MSFT", side=Side.BUY, quantity=7.0, price=100.0, stop_loss=90.0)

    first_results = await engine.handle_signal(first)
    second_results = await engine.handle_signal(second)

    assert first_results[0].status == OrderStatus.PENDING
    assert second_results[0].status == OrderStatus.REJECTED
    assert "notional exposure ceiling" in second_results[0].message

    lifecycle = engine.lifecycle_manager.get_lifecycle("acct1", "AAPL")
    assert lifecycle is not None and lifecycle.pending_entry is not None
    assert lifecycle.pending_entry.reserved_notional == 700.0

    async def confirmed_rejected(account, broker_order_id):
        return OrderResult(account_id=account.account_id, status=OrderStatus.REJECTED, signal_id=first.id)

    broker.get_order_status = confirmed_rejected
    reconciler = OrderReconciler(
        store=store,
        brokers={"paper": broker},
        lifecycle_manager=engine.lifecycle_manager,
        capital_allocator=engine.capital_allocator,
    )
    corrected = await reconciler.reconcile_once()
    assert corrected >= 1

    third = Signal(source=SOURCE, symbol="MSFT", side=Side.BUY, quantity=7.0, price=100.0, stop_loss=90.0)
    admitted_results = await engine.handle_signal(third)
    assert admitted_results[0].status == OrderStatus.PENDING  # capacity freed, admitted again


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
async def test_pending_with_a_broker_order_id_keeps_its_reservation_and_blocks_a_second_entry(tmp_path):
    """The reservation-timing gap this used to document (see git history /
    capital_allocator.py's docstring): a PENDING order isn't part of
    `confirmed_open_notional` yet (that only counts orders whose STORED
    status is actually FILLED), so releasing its reservation immediately
    -- the same as REJECTED/ERROR/FILLED -- briefly counted it toward
    neither the reservation ledger nor confirmed exposure. Now closed for
    the pollable case: a PENDING result with a real broker_order_id keeps
    its reservation until app/reconciliation.py resolves it (see
    app/engine.py's `_try_reserve_capital` docstring).

    Two sequential (not even concurrent -- this doesn't need a race) $700
    orders under a $1000 ceiling: the first is admitted and stays PENDING
    with a broker_order_id, so its reservation is still held when the
    second arrives -- 700 (reservation) + 700 (requested) > 1000, REJECTED.
    """
    store = SignalStore(tmp_path / "test.db")
    broker = PaperBroker()

    async def pending_not_filled(signal, account, quantity, symbol):
        # A real broker's synchronous "accepted, not yet confirmed" reply
        # (e.g. AlpacaBroker/SignalStackBroker) -- PaperBroker itself
        # always fills synchronously, so this substitutes a PENDING
        # response (with a broker_order_id to poll, like those real
        # adapters return) without recording anything in the orders table
        # as FILLED, matching what confirmed_open_notional actually reads.
        return OrderResult(
            account_id=account.account_id, status=OrderStatus.PENDING, signal_id=signal.id, broker_order_id="order-1"
        )

    broker.place_order = pending_not_filled
    account = DestinationAccount(account_id="acct1", broker="paper", max_notional_exposure=1000.0)
    engine = _engine(store, account, broker)

    first = Signal(source=SOURCE, symbol="AAPL", side=Side.BUY, quantity=7.0, price=100.0)  # notional 700
    second = Signal(source=SOURCE, symbol="MSFT", side=Side.BUY, quantity=7.0, price=100.0)  # notional 700

    first_results = await engine.handle_signal(first)
    second_results = await engine.handle_signal(second)

    assert first_results[0].status == OrderStatus.PENDING
    assert second_results[0].status == OrderStatus.REJECTED
    assert "notional exposure ceiling" in second_results[0].message


@pytest.mark.asyncio
async def test_reservation_releases_once_reconciliation_confirms_the_pending_order_is_rejected(tmp_path):
    """The other half of the fix: a held reservation must not leak forever
    -- once app/reconciliation.py confirms the PENDING order's real
    terminal outcome, capacity is freed for a later entry."""
    store = SignalStore(tmp_path / "test.db")
    broker = PaperBroker()

    async def pending_not_filled(signal, account, quantity, symbol):
        return OrderResult(
            account_id=account.account_id, status=OrderStatus.PENDING, signal_id=signal.id, broker_order_id="order-1"
        )

    broker.place_order = pending_not_filled
    account = DestinationAccount(account_id="acct1", broker="paper", max_notional_exposure=1000.0)
    engine = _engine(store, account, broker)

    first = Signal(source=SOURCE, symbol="AAPL", side=Side.BUY, quantity=7.0, price=100.0)  # notional 700
    first_results = await engine.handle_signal(first)
    assert first_results[0].status == OrderStatus.PENDING

    second = Signal(source=SOURCE, symbol="MSFT", side=Side.BUY, quantity=7.0, price=100.0)
    blocked_results = await engine.handle_signal(second)
    assert blocked_results[0].status == OrderStatus.REJECTED  # reservation still held

    async def confirmed_rejected(account, broker_order_id):
        return OrderResult(account_id=account.account_id, status=OrderStatus.REJECTED, signal_id=first.id)

    broker.get_order_status = confirmed_rejected
    reconciler = OrderReconciler(store=store, brokers={"paper": broker}, capital_allocator=engine.capital_allocator)
    corrected = await reconciler.reconcile_once()
    assert corrected >= 1

    third = Signal(source=SOURCE, symbol="MSFT", side=Side.BUY, quantity=7.0, price=100.0)
    admitted_results = await engine.handle_signal(third)
    assert admitted_results[0].status == OrderStatus.PENDING  # capacity freed, admitted again


@pytest.mark.asyncio
async def test_pending_with_no_broker_order_id_still_releases_immediately_a_narrower_remaining_gap(tmp_path):
    """A PENDING result with no broker_order_id at all (the same ambiguous
    situation as EXE-01/EXE-01b's raised-or-returned ERROR) has nothing
    app/reconciliation.py could ever poll to release it later -- deferring
    release here would risk a reservation that's never released, which
    capital_allocator.py's docstring calls out as worse than the timing
    gap it would close. So this specific, narrower case still releases
    immediately, same as before the fix -- documented here, not silently
    left to be "discovered" as a surprise regression."""
    store = SignalStore(tmp_path / "test.db")
    broker = PaperBroker()

    async def pending_no_order_id(signal, account, quantity, symbol):
        return OrderResult(account_id=account.account_id, status=OrderStatus.PENDING, signal_id=signal.id)

    broker.place_order = pending_no_order_id
    account = DestinationAccount(account_id="acct1", broker="paper", max_notional_exposure=1000.0)
    engine = _engine(store, account, broker)

    first = Signal(source=SOURCE, symbol="AAPL", side=Side.BUY, quantity=7.0, price=100.0)  # notional 700
    second = Signal(source=SOURCE, symbol="MSFT", side=Side.BUY, quantity=7.0, price=100.0)  # notional 700

    first_results = await engine.handle_signal(first)
    second_results = await engine.handle_signal(second)

    assert first_results[0].status == OrderStatus.PENDING
    assert second_results[0].status == OrderStatus.PENDING
