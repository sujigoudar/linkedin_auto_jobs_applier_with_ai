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

    assert confirmed_open_notional(store, ACCOUNT_ID) == pytest.approx(1000.0)


def test_confirmed_open_notional_sums_across_symbols(tmp_path):
    store = SignalStore(tmp_path / "test.db")
    _fill(store, "AAPL", Side.BUY, 10.0, 100.0)
    _fill(store, "MSFT", Side.BUY, 5.0, 200.0)

    assert confirmed_open_notional(store, ACCOUNT_ID) == pytest.approx(1000.0 + 1000.0)


def test_confirmed_open_notional_zero_after_full_close(tmp_path):
    store = SignalStore(tmp_path / "test.db")
    _fill(store, "AAPL", Side.BUY, 10.0, 100.0)
    _fill(store, "AAPL", Side.SELL, 10.0, 110.0)

    assert confirmed_open_notional(store, ACCOUNT_ID) == pytest.approx(0.0)


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
