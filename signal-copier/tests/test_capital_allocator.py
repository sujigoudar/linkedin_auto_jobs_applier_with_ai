"""E03: unit coverage for app/capital_allocator.py's admission gate and
confirmed_open_notional's cost-basis exposure calculation."""
import asyncio

import pytest

from app.capital_allocator import CapitalAllocator, confirmed_open_notional, owner_wide_exposure
from app.db import SignalStore
from app.models import DestinationAccount, OrderResult, OrderStatus, Side, Signal

ACCOUNT_ID = "acct1"


def _fill(store: SignalStore, symbol: str, side: Side, quantity: float, price: float) -> None:
    signal = Signal(source="test", symbol=symbol, side=side)
    store.save_signal(signal)
    store.save_order_result(
        OrderResult(
            account_id=ACCOUNT_ID, status=OrderStatus.FILLED, signal_id=signal.id, filled_quantity=quantity, filled_price=price
        ),
        broker="paper",
        symbol=symbol,
        side=side,
    )


def test_confirmed_open_notional_from_a_single_open_position(tmp_path):
    store = SignalStore(tmp_path / "test.db")
    _fill(store, "AAPL", Side.BUY, 10.0, 100.0)

    report = confirmed_open_notional(store, ACCOUNT_ID)
    assert report.notional == pytest.approx(1000.0)
    assert report.unresolved_symbols == []
    assert report.has_unresolved is False


def test_confirmed_open_notional_sums_across_symbols(tmp_path):
    store = SignalStore(tmp_path / "test.db")
    _fill(store, "AAPL", Side.BUY, 10.0, 100.0)
    _fill(store, "MSFT", Side.BUY, 5.0, 200.0)

    assert confirmed_open_notional(store, ACCOUNT_ID).notional == pytest.approx(1000.0 + 1000.0)


def test_confirmed_open_notional_zero_after_full_close(tmp_path):
    store = SignalStore(tmp_path / "test.db")
    _fill(store, "AAPL", Side.BUY, 10.0, 100.0)
    _fill(store, "AAPL", Side.SELL, 10.0, 110.0)

    report = confirmed_open_notional(store, ACCOUNT_ID)
    assert report.notional == pytest.approx(0.0)
    assert report.unresolved_symbols == []


def test_confirmed_open_notional_reports_unresolved_symbols_not_zero(tmp_path):
    """The fix for 'unresolved exposure contributes nothing': a fill this
    replay can't use (here, a missing filled_price) leaves that symbol's
    real notional unknown -- it must NEVER be silently summed as 0.0
    alongside genuinely resolved symbols. This asserts the exact bug the
    audit named is closed: `unresolved_symbols` surfaces it instead."""
    store = SignalStore(tmp_path / "test.db")
    signal = Signal(source="test", symbol="AAPL", side=Side.BUY)
    store.save_signal(signal)
    store.save_order_result(
        OrderResult(
            account_id=ACCOUNT_ID,
            status=OrderStatus.FILLED,
            signal_id=signal.id,
            filled_quantity=10.0,
            filled_price=None,  # unresolvable fill: no price to compute a cost basis from
        ),
        broker="paper",
        symbol="AAPL",
        side=Side.BUY,
    )
    # A separate, fully resolved position exists too -- it must not mask
    # the unresolved one.
    _fill(store, "MSFT", Side.BUY, 5.0, 200.0)

    report = confirmed_open_notional(store, ACCOUNT_ID)
    assert report.has_unresolved is True
    assert "AAPL" in report.unresolved_symbols
    # The resolved MSFT position still contributes its real notional --
    # unresolved-ness of one symbol doesn't erase another's known total.
    assert report.notional == pytest.approx(1000.0)


def test_admit_allows_within_ceiling_and_rejects_over_it():
    allocator = CapitalAllocator()

    async def run():
        admitted = await allocator.admit(ACCOUNT_ID, 500.0, confirmed_exposure=0.0, max_exposure=1000.0)
        assert admitted is True
        rejected = await allocator.admit(ACCOUNT_ID, 600.0, confirmed_exposure=0.0, max_exposure=1000.0)
        assert rejected is False

    asyncio.run(run())


def test_release_frees_capacity_for_a_later_admission():
    allocator = CapitalAllocator()

    async def run():
        await allocator.admit(ACCOUNT_ID, 900.0, confirmed_exposure=0.0, max_exposure=1000.0)
        assert await allocator.admit(ACCOUNT_ID, 200.0, confirmed_exposure=0.0, max_exposure=1000.0) is False
        allocator.release(ACCOUNT_ID, 900.0)
        assert await allocator.admit(ACCOUNT_ID, 200.0, confirmed_exposure=0.0, max_exposure=1000.0) is True

    asyncio.run(run())


def test_concurrent_admissions_cannot_both_fit_over_the_ceiling():
    """The core E03 obligation: two competing calls, only one of which can
    fit under the ceiling, must not both be admitted."""
    allocator = CapitalAllocator()

    async def run():
        results = await asyncio.gather(
            allocator.admit(ACCOUNT_ID, 700.0, confirmed_exposure=0.0, max_exposure=1000.0),
            allocator.admit(ACCOUNT_ID, 700.0, confirmed_exposure=0.0, max_exposure=1000.0),
        )
        assert sorted(results) == [False, True]

    asyncio.run(run())


def test_release_never_goes_negative():
    allocator = CapitalAllocator()
    allocator.release(ACCOUNT_ID, 500.0)  # nothing was ever reserved

    async def run():
        return await allocator.admit(ACCOUNT_ID, 1000.0, confirmed_exposure=0.0, max_exposure=1000.0)

    assert asyncio.run(run()) is True


# P0-4: a restart must not assume "no in-flight admissions to lose" -- a
# remote broker can accept an order before this process dies, so a
# provisional reservation must survive a restart. These tests build a
# FRESH CapitalAllocator instance from the same on-disk database (never
# reusing the old Python object) to genuinely exercise that path, not
# just the in-memory ledger.


def test_reservation_survives_a_fresh_allocator_built_from_the_same_db(tmp_path):
    """The core P0-4 obligation: admit a reservation through one
    CapitalAllocator, then construct a completely new one (simulating a
    process restart) against the SAME database file. The second admission
    only fits under the ceiling if the first reservation was forgotten --
    it must still be correctly rejected."""
    db_path = tmp_path / "restart.db"
    store = SignalStore(db_path)

    async def run():
        allocator = CapitalAllocator(store=store)
        admitted = await allocator.admit(
            ACCOUNT_ID, 800.0, confirmed_exposure=0.0, max_exposure=1000.0, signal_id="sig-1"
        )
        assert admitted is True
        # Simulate the process dying right here: `allocator` (and its
        # in-memory `_pending`) is simply abandoned, never released or
        # torn down cleanly -- exactly what a crash looks like.

        # "Restart": a brand-new CapitalAllocator, from the same store.
        restarted_allocator = CapitalAllocator(store=store)
        # If the 800.0 reservation had been forgotten, this 300.0 request
        # (800 + 300 = 1100 > 1000) would wrongly be admitted.
        second_admission = await restarted_allocator.admit(
            ACCOUNT_ID, 300.0, confirmed_exposure=0.0, max_exposure=1000.0
        )
        assert second_admission is False
        # And the durable ledger still shows the original reservation as
        # the sole unresolved amount for this account.
        assert restarted_allocator.pending_reservation(ACCOUNT_ID) == pytest.approx(800.0)

    asyncio.run(run())


def test_reservation_survives_restart_across_two_separate_store_instances(tmp_path):
    """Same as above, but also constructs a fresh SignalStore against the
    file (not just a fresh CapitalAllocator against the same live store
    object) -- the closest thing to a genuine process restart this test
    suite can exercise without actually forking a process."""
    db_path = tmp_path / "restart2.db"
    store_before_restart = SignalStore(db_path)

    async def run():
        allocator = CapitalAllocator(store=store_before_restart)
        assert await allocator.admit(ACCOUNT_ID, 600.0, confirmed_exposure=0.0, max_exposure=1000.0) is True

        # A brand new process would open a brand new SignalStore against
        # this same file, and build its allocator from that.
        store_after_restart = SignalStore(db_path)
        restarted_allocator = CapitalAllocator(store=store_after_restart)
        # 600 (durable) + 500 would be 1100 > 1000 -- must still reject.
        assert (
            await restarted_allocator.admit(ACCOUNT_ID, 500.0, confirmed_exposure=0.0, max_exposure=1000.0)
            is False
        )
        # A request that fits alongside the surviving 600.0 reservation
        # is still correctly admitted (this isn't just refusing
        # everything -- the durable figure is being added, not some
        # blanket rejection).
        assert (
            await restarted_allocator.admit(ACCOUNT_ID, 300.0, confirmed_exposure=0.0, max_exposure=1000.0) is True
        )

    asyncio.run(run())


def test_released_reservation_does_not_survive_restart(tmp_path):
    """A reservation that was properly released before the restart must
    NOT reappear afterward -- durability only applies to still-unresolved
    reservations, never to ones already resolved."""
    db_path = tmp_path / "released.db"
    store = SignalStore(db_path)

    async def run():
        allocator = CapitalAllocator(store=store)
        assert await allocator.admit(ACCOUNT_ID, 900.0, confirmed_exposure=0.0, max_exposure=1000.0) is True
        allocator.release(ACCOUNT_ID, 900.0)  # e.g. the order came back REJECTED

        restarted_allocator = CapitalAllocator(store=store)
        assert restarted_allocator.pending_reservation(ACCOUNT_ID) == pytest.approx(0.0)
        assert await restarted_allocator.admit(ACCOUNT_ID, 900.0, confirmed_exposure=0.0, max_exposure=1000.0) is True

    asyncio.run(run())


# -- Mutation-testing follow-ups (track39) -----------------------------------
#
# Mutation testing against this module (scoped to this file plus
# test_b7_capital_contention.py / test_e03_capital_exposure_gate.py /
# test_tr02_capital_utilization.py) found several real survivors on
# financially load-bearing logic. The tests below close those gaps; see
# each one's docstring for exactly which mutant it kills and why no
# existing test already did.


def test_confirmed_open_notional_skips_only_the_flat_symbol_not_the_rest(tmp_path):
    """Kills a `continue` -> `break` mutant in `confirmed_open_notional`'s
    per-symbol loop. A symbol with `average_cost is None` (a fully CLOSED
    position, net quantity 0) must be SKIPPED, not treated as a reason to
    stop summing entirely -- every other (open) symbol for this account
    must still contribute its real notional.

    The existing `test_confirmed_open_notional_reports_unresolved_symbols_not_zero`
    doesn't catch this: its "unresolved" AAPL fill (missing `filled_price`)
    never makes it into `economics.per_symbol` at all (it's tracked
    separately via `incomplete_symbols`), so the `average_cost is None`
    branch in `confirmed_open_notional` is never actually reached by it.
    That branch IS reached by a symbol that fully round-tripped to flat
    (buy then an equal-size sell) -- which is exactly what this test
    constructs, ordered BEFORE a second, still-open symbol so a `break`
    would silently zero out the second symbol's very real exposure."""
    store = SignalStore(tmp_path / "test.db")
    _fill(store, "AAPL", Side.BUY, 10.0, 100.0)
    _fill(store, "AAPL", Side.SELL, 10.0, 110.0)  # round-tripped flat -> average_cost is None
    _fill(store, "MSFT", Side.BUY, 5.0, 200.0)  # still open -- must still count

    report = confirmed_open_notional(store, ACCOUNT_ID)
    assert report.notional == pytest.approx(1000.0)
    assert report.unresolved_symbols == []


def _economics_account(account_id: str) -> DestinationAccount:
    return DestinationAccount(account_id=account_id, broker="paper")


def test_owner_wide_exposure_sums_confirmed_notional_across_accounts(tmp_path):
    """Kills the `total = 1.0` (instead of `0.0`) mutant: with real,
    known positions on two accounts and no pending reservations at all,
    the owner-wide total must be EXACTLY the sum of each account's
    confirmed notional -- not off by a spurious +1.0."""
    store = SignalStore(tmp_path / "test.db")
    _fill(store, "AAPL", Side.BUY, 10.0, 100.0)  # acct1: 1000.0

    other_account = "acct2"

    def _fill_other(symbol, side, quantity, price):
        signal = Signal(source="test", symbol=symbol, side=side)
        store.save_signal(signal)
        store.save_order_result(
            OrderResult(
                account_id=other_account,
                status=OrderStatus.FILLED,
                signal_id=signal.id,
                filled_quantity=quantity,
                filled_price=price,
            ),
            broker="paper",
            symbol=symbol,
            side=side,
        )

    _fill_other("MSFT", Side.BUY, 5.0, 200.0)  # acct2: 1000.0

    allocator = CapitalAllocator()
    accounts = [_economics_account(ACCOUNT_ID), _economics_account(other_account)]
    report = owner_wide_exposure(store, accounts, allocator)
    assert report.notional == pytest.approx(2000.0)
    assert report.unresolved_symbols == []


def test_owner_wide_exposure_adds_pending_reservations_not_subtracts(tmp_path):
    """Kills the `total += report.notional - allocator.pending_reservation(...)`
    mutant (subtraction instead of addition): a real, still-outstanding
    reservation must INCREASE the owner-wide total the ceiling is checked
    against, never reduce it -- reducing it would let the owner-wide gate
    silently admit past its configured ceiling."""
    store = SignalStore(tmp_path / "test.db")
    _fill(store, "AAPL", Side.BUY, 10.0, 100.0)  # confirmed: 1000.0

    allocator = CapitalAllocator()

    async def reserve():
        assert await allocator.admit(ACCOUNT_ID, 250.0, confirmed_exposure=0.0, max_exposure=10_000.0) is True

    asyncio.run(reserve())

    report = owner_wide_exposure(store, [_economics_account(ACCOUNT_ID)], allocator)
    # 1000.0 confirmed + 250.0 pending == 1250.0. A subtraction would give
    # 750.0 instead -- understating real exposure.
    assert report.notional == pytest.approx(1250.0)


def test_owner_wide_exposure_uses_each_accounts_own_pending_reservation(tmp_path):
    """Kills the `allocator.pending_reservation(None)` mutant (the real
    `account.account_id` argument dropped): each account's contribution
    must reflect ITS OWN pending reservation, not a shared/absent key that
    always reads back 0.0."""
    store = SignalStore(tmp_path / "test.db")
    allocator = CapitalAllocator()

    async def reserve():
        assert await allocator.admit(ACCOUNT_ID, 400.0, confirmed_exposure=0.0, max_exposure=10_000.0) is True

    asyncio.run(reserve())

    report = owner_wide_exposure(store, [_economics_account(ACCOUNT_ID)], allocator)
    assert report.notional == pytest.approx(400.0)


def test_owner_wide_exposure_surfaces_real_unresolved_symbols(tmp_path):
    """Kills both the `unresolved_symbols=None` and the
    `unresolved_symbols` (dropped entirely, defaulting to `[]`) mutants:
    when a contributing account has a genuinely unresolved symbol, the
    owner-wide report must surface it (prefixed `account_id:symbol`), not
    silently report a clean/empty list -- callers rely on a non-empty
    list here to reject admission against unknown exposure."""
    store = SignalStore(tmp_path / "test.db")
    signal = Signal(source="test", symbol="AAPL", side=Side.BUY)
    store.save_signal(signal)
    store.save_order_result(
        OrderResult(
            account_id=ACCOUNT_ID,
            status=OrderStatus.FILLED,
            signal_id=signal.id,
            filled_quantity=10.0,
            filled_price=None,  # unresolvable
        ),
        broker="paper",
        symbol="AAPL",
        side=Side.BUY,
    )

    allocator = CapitalAllocator()
    report = owner_wide_exposure(store, [_economics_account(ACCOUNT_ID)], allocator)
    assert report.has_unresolved is True
    assert report.unresolved_symbols == [f"{ACCOUNT_ID}:AAPL"]


def test_admit_persists_the_real_signal_id_on_the_durable_reservation(tmp_path):
    """Kills the two `signal_id=None` mutants in `admit()`: the
    `signal_id` passed to `admit()` must reach the durable
    `capital_reservations` row unchanged, not be silently dropped to
    `None` regardless of what the caller passed."""
    store = SignalStore(tmp_path / "test.db")
    allocator = CapitalAllocator(store=store)

    async def run():
        assert await allocator.admit(
            ACCOUNT_ID, 100.0, confirmed_exposure=0.0, max_exposure=1000.0, signal_id="sig-real-123"
        ) is True

    asyncio.run(run())

    with store._connect() as conn:
        row = conn.execute("SELECT signal_id FROM capital_reservations WHERE account_id = ?", (ACCOUNT_ID,)).fetchone()
    assert row[0] == "sig-real-123"


def test_admit_accumulates_across_three_sequential_calls_not_overwrites(tmp_path):
    """Kills the `self._pending[account_id] = notional` mutant (plain
    assignment instead of `+=`): three sequential admissions to the SAME
    account, none released in between, must accumulate so a later
    admission that would push the cumulative total over the ceiling is
    correctly rejected -- an overwrite bug hides this exactly on the
    THIRD call (the first two calls happen to look identical either way,
    since the pending figure starts at 0.0)."""
    allocator = CapitalAllocator()

    async def run():
        assert await allocator.admit(ACCOUNT_ID, 300.0, confirmed_exposure=0.0, max_exposure=1000.0) is True
        assert await allocator.admit(ACCOUNT_ID, 300.0, confirmed_exposure=0.0, max_exposure=1000.0) is True
        # Cumulative reserved so far is 600.0 (300 + 300). A correct
        # implementation rejects this third 500.0 request (600 + 500 =
        # 1100 > 1000). An overwrite bug would have reset pending to 300.0
        # after the second call, wrongly admitting this.
        assert await allocator.admit(ACCOUNT_ID, 500.0, confirmed_exposure=0.0, max_exposure=1000.0) is False
        assert allocator.pending_reservation(ACCOUNT_ID) == pytest.approx(600.0)

    asyncio.run(run())


def test_reserve_locked_without_a_store_does_not_touch_store_at_all(tmp_path):
    """Kills the `is not None or notional` mutant: with `store=None` (the
    in-memory-only mode `app/backtest/replay.py` relies on) and a real,
    non-zero notional, `reserve_locked` must NOT attempt to call anything
    on `self.store` (which is None) -- it must simply update `_pending`.
    The buggy `or` condition is True here (`notional` is truthy) even
    though `self.store is not None` is False, so it would try to call
    `None.create_capital_reservation(...)` and crash."""
    allocator = CapitalAllocator(store=None)
    allocator.reserve_locked(ACCOUNT_ID, 150.0, signal_id="sig-x")
    assert allocator.pending_reservation(ACCOUNT_ID) == pytest.approx(150.0)


def test_reserve_locked_with_a_store_writes_a_durable_reservation_row(tmp_path):
    """Kills the `is None and notional` mutant: with a real `store` and a
    non-zero notional, `reserve_locked` must durably persist the
    reservation (visible via `sum_unresolved_capital_reservations`), the
    same P0-4 restart-survival guarantee `admit()` provides -- not skip
    the write whenever `self.store` actually IS set."""
    store = SignalStore(tmp_path / "test.db")
    allocator = CapitalAllocator(store=store)
    allocator.reserve_locked(ACCOUNT_ID, 275.0, signal_id="sig-y")
    assert allocator.pending_reservation(ACCOUNT_ID) == pytest.approx(275.0)

    # A FRESH allocator against the same store must reload this exact
    # reservation -- proof it was actually written to the durable table,
    # not just held in-memory.
    reloaded = CapitalAllocator(store=store)
    assert reloaded.pending_reservation(ACCOUNT_ID) == pytest.approx(275.0)


def test_reserve_locked_persists_the_real_signal_id(tmp_path):
    """Kills the two `signal_id=None` mutants in `reserve_locked()`,
    mirroring `test_admit_persists_the_real_signal_id_on_the_durable_reservation`
    for the lock-free variant."""
    store = SignalStore(tmp_path / "test.db")
    allocator = CapitalAllocator(store=store)
    allocator.reserve_locked(ACCOUNT_ID, 50.0, signal_id="sig-locked-456")

    with store._connect() as conn:
        row = conn.execute("SELECT signal_id FROM capital_reservations WHERE account_id = ?", (ACCOUNT_ID,)).fetchone()
    assert row[0] == "sig-locked-456"


def test_reserve_locked_accumulates_across_two_calls_not_overwrites(tmp_path):
    """Kills the `self._pending[account_id] = notional` mutant in
    `reserve_locked` (plain assignment instead of `+=`): two sequential
    lock-free reservations against the same account must accumulate."""
    allocator = CapitalAllocator()
    allocator.reserve_locked(ACCOUNT_ID, 100.0)
    allocator.reserve_locked(ACCOUNT_ID, 50.0)
    assert allocator.pending_reservation(ACCOUNT_ID) == pytest.approx(150.0)
