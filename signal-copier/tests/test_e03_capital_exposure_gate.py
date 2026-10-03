"""E03: app/capital_allocator.py's notional-exposure, owner-wide-exposure
and risk-basis admission gates, wired into both the plain-account and
managed_lifecycle entry paths in app/engine.py.
"""
import asyncio

import pytest

from app.brokers.paper import PaperBroker
from app.db import SignalStore
from app.engine import SignalCopierEngine
from app.lifecycle.manager import PositionLifecycleManager
from app.models import AccountBalance, DestinationAccount, OrderResult, OrderStatus, Side, Signal
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
    """Track 1b: this account has neither of this module's own opt-in
    ceilings configured (`max_notional_exposure`/`risk_percent_of_equity`)
    -- but it's still subject to the separate, always-on buying-power
    gate (`_check_buying_power`), which is real and intentional (see
    app/engine.py's own module docstring on that check and
    tests/test_track1b_buying_power_gate.py for its own dedicated
    coverage). Quantity here is sized to stay within PaperBroker's
    documented STARTING_CASH (100_000) so this test keeps demonstrating
    exactly what it says: no *ceiling* gates anything, without being
    confused with the separate buying-power gate this same admission
    path now also enforces."""
    store = SignalStore(tmp_path / "test.db")
    broker = PaperBroker()
    account = DestinationAccount(account_id="acct1", broker="paper")  # max_notional_exposure=None
    engine = _engine(store, account, broker)

    signal = Signal(source=SOURCE, symbol=SYMBOL, side=Side.BUY, quantity=500.0, price=100.0)  # notional 50,000
    results = await engine.handle_signal(signal)

    assert results[0].status == OrderStatus.FILLED


@pytest.mark.asyncio
async def test_no_price_on_the_signal_is_rejected_when_a_gate_is_configured(tmp_path):
    """Release-audit fix: a signal with no price used to silently SKIP the
    whole admission check whenever a gate (here, max_notional_exposure) was
    configured -- an unbounded admission, economically identical to having
    no ceiling at all. It must now be REJECTED, never silently passed
    through, since this build has no independent way to resolve a price
    for the instrument."""
    store = SignalStore(tmp_path / "test.db")
    broker = PaperBroker()
    account = DestinationAccount(account_id="acct1", broker="paper", max_notional_exposure=1.0)
    engine = _engine(store, account, broker)

    signal = Signal(source=SOURCE, symbol=SYMBOL, side=Side.BUY, quantity=10.0)  # no price
    results = await engine.handle_signal(signal)

    assert results[0].status == OrderStatus.REJECTED
    assert "no price" in results[0].message
    assert broker.fills == []  # never reached the broker


@pytest.mark.asyncio
async def test_no_price_on_the_signal_still_admits_when_no_gate_is_configured(tmp_path):
    """The complementary case: with NO gate configured at all for this
    account, a priceless signal is still admitted unconditionally -- there
    is genuinely nothing to check, so this is not a regression of the
    'no ceiling configured' default behavior."""
    store = SignalStore(tmp_path / "test.db")
    broker = PaperBroker()
    account = DestinationAccount(account_id="acct1", broker="paper")  # no gate at all
    engine = _engine(store, account, broker)

    signal = Signal(source=SOURCE, symbol=SYMBOL, side=Side.BUY, quantity=10.0)
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
async def test_pending_with_no_broker_order_id_holds_reservation_until_resolved(tmp_path):
    """ALLOC-05 (supersedes the earlier "still releases immediately, a
    narrower remaining gap" behavior this test used to document).

    A PENDING result with no broker_order_id is the same ambiguous
    situation as a raised or returned-ERROR submission: the order may have
    been accepted at the venue and nothing can be polled. Releasing the
    reservation would let the same capacity be spent twice, so it is held as
    an unresolved obligation until independent evidence settles it
    (`SignalCopierEngine.resolve_unknown_submission`)."""
    store = SignalStore(tmp_path / "test.db")
    broker = PaperBroker()

    async def pending_no_order_id(signal, account, quantity, symbol):
        return OrderResult(account_id=account.account_id, status=OrderStatus.PENDING, signal_id=signal.id)

    broker.place_order = pending_no_order_id
    account = DestinationAccount(account_id="acct1", broker="paper", max_notional_exposure=1000.0)
    engine = _engine(store, account, broker)

    first = Signal(source=SOURCE, symbol="AAPL", side=Side.BUY, quantity=7.0, price=100.0)  # notional 700
    second = Signal(source=SOURCE, symbol="MSFT", side=Side.BUY, quantity=7.0, price=100.0)  # would be 1400 > 1000

    first_results = await engine.handle_signal(first)
    assert first_results[0].status == OrderStatus.PENDING
    assert engine.capital_allocator.pending_reservation("acct1") == 700.0

    second_results = await engine.handle_signal(second)
    assert second_results[0].status == OrderStatus.REJECTED  # the held 700 still counts

    key = store.list_unresolved_command_ledger_entries()[0].idempotency_key
    assert engine.resolve_unknown_submission(key, outcome="not_placed", evidence="broker shows no order")
    assert engine.capital_allocator.pending_reservation("acct1") == 0.0


# --- Unresolved exposure: audit's second named bug ---


@pytest.mark.asyncio
async def test_unresolved_exposure_blocks_new_admissions_rather_than_counting_as_zero(tmp_path):
    """Release-audit fix: a position this replay can't resolve an
    average_cost for (here, a fill with a missing filled_price) used to
    contribute exactly 0.0 to confirmed_open_notional -- treated as if it
    were no exposure at all. It must now block new admissions for this
    account until it resolves, never silently behave like zero."""
    store = SignalStore(tmp_path / "test.db")
    broker = PaperBroker()
    account = DestinationAccount(account_id="acct1", broker="paper", max_notional_exposure=100_000.0)
    engine = _engine(store, account, broker)

    # Directly record an unresolvable fill (missing filled_price) -- the
    # same shape app/economics.py's own incomplete_symbols tracking flags.
    unresolved_signal = Signal(source=SOURCE, symbol="MSFT", side=Side.BUY)
    store.save_signal(unresolved_signal)
    store.save_order_result(
        OrderResult(
            account_id="acct1",
            status=OrderStatus.FILLED,
            signal_id=unresolved_signal.id,
            filled_quantity=10.0,
            filled_price=None,
        ),
        broker="paper",
        symbol="MSFT",
        side=Side.BUY,
    )

    # A trivially small, obviously-under-the-ceiling entry -- would be
    # admitted by the ceiling check alone; must still be rejected because
    # this account's true exposure is unknown.
    signal = Signal(source=SOURCE, symbol=SYMBOL, side=Side.BUY, quantity=1.0, price=1.0)
    results = await engine.handle_signal(signal)

    assert results[0].status == OrderStatus.REJECTED
    assert "unresolved" in results[0].message.lower() or "MSFT" in results[0].message
    assert broker.fills == []


@pytest.mark.asyncio
async def test_resolved_exposure_on_other_accounts_is_unaffected_by_one_accounts_unresolved_symbol(tmp_path):
    """The block is per-account, not global -- a different account with
    fully resolved exposure is admitted normally."""
    store = SignalStore(tmp_path / "test.db")
    broker = PaperBroker()
    account = DestinationAccount(account_id="acct2", broker="paper", max_notional_exposure=100_000.0)
    engine = _engine(store, account, broker)

    signal = Signal(source=SOURCE, symbol=SYMBOL, side=Side.BUY, quantity=1.0, price=1.0)
    results = await engine.handle_signal(signal)

    assert results[0].status == OrderStatus.FILLED


# --- Owner-wide exposure: sums across every account this deployment knows ---


def _owner_engine(store: SignalStore, accounts: dict, broker: PaperBroker, max_owner_notional_exposure: float) -> SignalCopierEngine:
    # Two separate single-destination rules (keyed by distinct source
    # names) -- so a signal from "src1" reaches only acct1 and one from
    # "src2" reaches only acct2, letting each account's own admission be
    # driven independently while still sharing the one owner-wide ceiling.
    rules = [
        RoutingRule(source="src1", destinations=["acct1"]),
        RoutingRule(source="src2", destinations=["acct2"]),
    ]
    routing = RoutingConfig(rules=rules, accounts=accounts)
    lifecycle_manager = PositionLifecycleManager(brokers={"paper": broker}, store=store)
    engine = SignalCopierEngine(routing=routing, brokers={"paper": broker}, store=store, lifecycle_manager=lifecycle_manager)
    engine.max_owner_notional_exposure = max_owner_notional_exposure
    return engine


@pytest.mark.asyncio
async def test_owner_wide_ceiling_sums_exposure_across_every_configured_account(tmp_path):
    """Neither account has its OWN per-account ceiling -- only the
    owner-wide one, shared across both. acct1 alone fits under it; acct2's
    own request would independently also fit, but COMBINED with acct1's
    already-confirmed exposure it does not, and must be rejected even
    though acct2 itself has never held any exposure before."""
    store = SignalStore(tmp_path / "test.db")
    broker = PaperBroker()
    accounts = {
        "acct1": DestinationAccount(account_id="acct1", broker="paper"),
        "acct2": DestinationAccount(account_id="acct2", broker="paper"),
    }
    engine = _owner_engine(store, accounts, broker, max_owner_notional_exposure=1500.0)

    first = Signal(source="src1", symbol="AAPL", side=Side.BUY, quantity=10.0, price=100.0)  # notional 1000
    first_results = await engine.handle_signal(first)
    assert first_results[0].status == OrderStatus.FILLED

    second = Signal(source="src2", symbol="MSFT", side=Side.BUY, quantity=10.0, price=100.0)  # notional 1000
    second_results = await engine.handle_signal(second)

    assert second_results[0].account_id == "acct2"
    assert second_results[0].status == OrderStatus.REJECTED
    assert "owner-wide" in second_results[0].message


@pytest.mark.asyncio
async def test_owner_wide_ceiling_admits_when_combined_exposure_fits(tmp_path):
    store = SignalStore(tmp_path / "test.db")
    broker = PaperBroker()
    accounts = {
        "acct1": DestinationAccount(account_id="acct1", broker="paper"),
        "acct2": DestinationAccount(account_id="acct2", broker="paper"),
    }
    engine = _owner_engine(store, accounts, broker, max_owner_notional_exposure=5000.0)

    first = Signal(source="src1", symbol="AAPL", side=Side.BUY, quantity=10.0, price=100.0)
    await engine.handle_signal(first)

    second = Signal(source="src2", symbol="MSFT", side=Side.BUY, quantity=10.0, price=100.0)
    second_results = await engine.handle_signal(second)

    assert second_results[0].account_id == "acct2"
    assert second_results[0].status == OrderStatus.FILLED


# --- Risk-basis sizing: percent-of-equity risk-to-stop ---


class _EquityBroker(PaperBroker):
    """A PaperBroker whose get_account_balance reports a real, fixed
    equity figure -- PaperBroker itself always reports equity=None (see
    its own docstring: it tracks no live mark for open positions), so
    this stands in for a broker that genuinely CAN report equity."""

    def __init__(self, equity: float | None) -> None:
        super().__init__()
        self._equity = equity

    async def get_account_balance(self, account: DestinationAccount) -> AccountBalance | None:
        return AccountBalance(account_id=account.account_id, cash=self._equity, equity=self._equity)


@pytest.mark.asyncio
async def test_risk_basis_rejects_when_no_stop_loss_on_signal(tmp_path):
    store = SignalStore(tmp_path / "test.db")
    broker = _EquityBroker(equity=100_000.0)
    account = DestinationAccount(account_id="acct1", broker="paper", risk_percent_of_equity=0.02)
    engine = _engine(store, account, broker)

    signal = Signal(source=SOURCE, symbol=SYMBOL, side=Side.BUY, quantity=10.0, price=100.0)  # no stop_loss
    results = await engine.handle_signal(signal)

    assert results[0].status == OrderStatus.REJECTED
    assert "stop_loss" in results[0].message


@pytest.mark.asyncio
async def test_risk_basis_rejects_when_broker_cannot_report_equity(tmp_path):
    """When a broker reports balance but no equity figure (equity=None),
    a genuine 'broker reachable but this figure unavailable' case, not a
    stub. Must fail closed, not admit with an unverified risk figure.
    managed_lifecycle=True here purely so a real stop_loss is accepted at
    all (a plain account with a non-bracket-capable broker refuses any
    stop_loss outright, unrelated to this gate -- see EXE-08)."""
    from app.brokers.base import BrokerAdapter

    class NoEquityBroker(BrokerAdapter):
        """Mock broker that reports balance but no equity."""
        @property
        def has_balance_capability(self) -> bool:
            return True

        async def place_order(self, account, signal, quantity):
            return None

        async def place_protective_stop(self, account, symbol, quantity, stop_price, exit_side):
            return None

        async def get_account_balance(self, account):
            # Reports cash/buying_power but no equity
            return AccountBalance(account_id=account.account_id, cash=100_000.0, buying_power=100_000.0, equity=None)

    store = SignalStore(tmp_path / "test.db")
    broker = NoEquityBroker()
    account = DestinationAccount(
        account_id="acct1", broker="no_equity", managed_lifecycle=True, risk_percent_of_equity=0.02
    )
    engine = SignalCopierEngine(
        routing=RoutingConfig(rules=[RoutingRule(source=SOURCE, destinations=["acct1"])], accounts={"acct1": account}),
        brokers={"no_equity": broker},
        store=store,
        lifecycle_manager=PositionLifecycleManager(brokers={"no_equity": broker}, store=store)
    )

    signal = Signal(source=SOURCE, symbol=SYMBOL, side=Side.BUY, quantity=10.0, price=100.0, stop_loss=90.0)
    results = await engine.handle_signal(signal)

    assert results[0].status == OrderStatus.REJECTED
    assert "equity" in results[0].message


@pytest.mark.asyncio
async def test_risk_basis_rejects_when_risk_to_stop_exceeds_the_percentage_of_equity(tmp_path):
    store = SignalStore(tmp_path / "test.db")
    broker = _EquityBroker(equity=10_000.0)
    # 1% of 10,000 equity = 100 max risk.
    account = DestinationAccount(
        account_id="acct1", broker="paper", managed_lifecycle=True, risk_percent_of_equity=0.01
    )
    engine = _engine(store, account, broker)

    # risk-to-stop = |100 - 80| * 10 = 200 > 100 ceiling.
    signal = Signal(source=SOURCE, symbol=SYMBOL, side=Side.BUY, quantity=10.0, price=100.0, stop_loss=80.0)
    results = await engine.handle_signal(signal)

    assert results[0].status == OrderStatus.REJECTED
    assert "risk-to-stop" in results[0].message


@pytest.mark.asyncio
async def test_risk_basis_admits_when_risk_to_stop_fits_within_the_percentage_of_equity(tmp_path):
    store = SignalStore(tmp_path / "test.db")
    broker = _EquityBroker(equity=10_000.0)
    # 5% of 10,000 equity = 500 max risk.
    account = DestinationAccount(
        account_id="acct1", broker="paper", managed_lifecycle=True, risk_percent_of_equity=0.05
    )
    engine = _engine(store, account, broker)

    # risk-to-stop = |100 - 80| * 10 = 200 <= 500 ceiling.
    signal = Signal(source=SOURCE, symbol=SYMBOL, side=Side.BUY, quantity=10.0, price=100.0, stop_loss=80.0)
    results = await engine.handle_signal(signal)

    assert results[0].status in (OrderStatus.FILLED, OrderStatus.PENDING)


# --- Zero/NaN/negative price: capital-gate bypass, defense-in-depth ---
#
# A price of 0 makes `notional = abs(quantity) * price` compute to 0, so
# every notional/risk ceiling below is trivially satisfied regardless of
# real trade size (zero signal). A NaN price makes every `>` ceiling
# comparison silently evaluate False in Python (never trips). A negative
# price makes notional negative, letting an oversized order through and
# corrupting CapitalAllocator._pending's running total for concurrent
# admissions on that account. Sources (text_parser.py, ninjatrader.py,
# webhook.py) are expected to reject these before a Signal is ever built,
# but this admission path (_try_reserve_capital / _check_risk_basis) must
# never trust that blindly -- these tests go straight at the engine gate
# itself, bypassing any source-level validation, and assert no capital was
# ever reserved (not just that an exception was raised).


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_price", [0.0, -50.0])
async def test_try_reserve_capital_refuses_zero_or_negative_price_before_reserving(tmp_path, bad_price):
    store = SignalStore(tmp_path / "test.db")
    broker = PaperBroker()
    account = DestinationAccount(account_id="acct1", broker="paper", max_notional_exposure=1.0)
    engine = _engine(store, account, broker)

    signal = Signal(source=SOURCE, symbol=SYMBOL, side=Side.BUY, quantity=10_000.0, price=bad_price)
    admitted, notional, rejection = await engine._try_reserve_capital(account, signal, 10_000.0)

    assert admitted is False
    assert notional == 0.0
    assert rejection is not None
    assert "finite positive number" in rejection.message
    # No reservation was made for this account: the allocator's pending
    # total is still zero, not corrupted by a negative or zero notional.
    assert engine.capital_allocator.pending_reservation("acct1") == 0.0


@pytest.mark.asyncio
async def test_try_reserve_capital_refuses_nan_price_before_reserving(tmp_path):
    """NaN specifically: every `>` ceiling comparison against NaN
    evaluates False, so an unguarded gate would never trip for it."""
    store = SignalStore(tmp_path / "test.db")
    broker = PaperBroker()
    account = DestinationAccount(account_id="acct1", broker="paper", max_notional_exposure=1.0)
    engine = _engine(store, account, broker)

    signal = Signal(source=SOURCE, symbol=SYMBOL, side=Side.BUY, quantity=10_000.0, price=float("nan"))
    admitted, notional, rejection = await engine._try_reserve_capital(account, signal, 10_000.0)

    assert admitted is False
    assert notional == 0.0
    assert rejection is not None
    assert engine.capital_allocator.pending_reservation("acct1") == 0.0


@pytest.mark.asyncio
async def test_zero_price_signal_end_to_end_is_rejected_not_filled(tmp_path):
    """End-to-end (through handle_signal, not the private method directly):
    a zero-price signal must be REJECTED and never reach the broker."""
    store = SignalStore(tmp_path / "test.db")
    broker = PaperBroker()
    account = DestinationAccount(account_id="acct1", broker="paper", max_notional_exposure=1.0)
    engine = _engine(store, account, broker)

    signal = Signal(source=SOURCE, symbol=SYMBOL, side=Side.BUY, quantity=10_000.0, price=0.0)
    results = await engine.handle_signal(signal)

    assert results[0].status == OrderStatus.REJECTED
    assert broker.fills == []
    assert engine.capital_allocator.pending_reservation("acct1") == 0.0


@pytest.mark.asyncio
async def test_negative_price_signal_end_to_end_is_rejected_not_filled(tmp_path):
    store = SignalStore(tmp_path / "test.db")
    broker = PaperBroker()
    account = DestinationAccount(account_id="acct1", broker="paper", max_notional_exposure=1.0)
    engine = _engine(store, account, broker)

    signal = Signal(source=SOURCE, symbol=SYMBOL, side=Side.BUY, quantity=10_000.0, price=-50.0)
    results = await engine.handle_signal(signal)

    assert results[0].status == OrderStatus.REJECTED
    assert broker.fills == []
    assert engine.capital_allocator.pending_reservation("acct1") == 0.0


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_price", [0.0, -50.0, float("nan")])
async def test_check_risk_basis_refuses_zero_negative_or_nan_price(tmp_path, bad_price):
    store = SignalStore(tmp_path / "test.db")
    broker = _EquityBroker(equity=100_000.0)
    account = DestinationAccount(
        account_id="acct1", broker="paper", managed_lifecycle=True, risk_percent_of_equity=0.02
    )
    engine = _engine(store, account, broker)

    signal = Signal(source=SOURCE, symbol=SYMBOL, side=Side.BUY, quantity=10.0, price=bad_price, stop_loss=90.0)
    ok, rejection = await engine._check_risk_basis(account, signal, 10.0)

    assert ok is False
    assert rejection is not None
    assert "finite positive number" in rejection.message


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_stop_loss", [0.0, -10.0, float("nan")])
async def test_check_risk_basis_refuses_zero_negative_or_nan_stop_loss(tmp_path, bad_stop_loss):
    store = SignalStore(tmp_path / "test.db")
    broker = _EquityBroker(equity=100_000.0)
    account = DestinationAccount(
        account_id="acct1", broker="paper", managed_lifecycle=True, risk_percent_of_equity=0.02
    )
    engine = _engine(store, account, broker)

    signal = Signal(
        source=SOURCE, symbol=SYMBOL, side=Side.BUY, quantity=10.0, price=100.0, stop_loss=bad_stop_loss
    )
    ok, rejection = await engine._check_risk_basis(account, signal, 10.0)

    assert ok is False
    assert rejection is not None
    assert "finite positive number" in rejection.message
