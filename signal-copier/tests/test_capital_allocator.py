"""E03: unit coverage for app/capital_allocator.py's admission gate and
confirmed_open_notional's cost-basis exposure calculation."""
import asyncio

import pytest

from app.capital_allocator import CapitalAllocator, confirmed_open_notional
from app.db import SignalStore
from app.models import OrderResult, OrderStatus, Side, Signal

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
