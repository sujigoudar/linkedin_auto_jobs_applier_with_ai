import asyncio

import pytest

from app.lifecycle.close_arbiter import CloseArbiter


@pytest.mark.asyncio
async def test_reserve_within_owned_succeeds():
    arbiter = CloseArbiter()
    await arbiter.set_owned_quantity("acct1", "AAPL", 62)

    assert await arbiter.reserve("acct1", "AAPL", 15) is True
    assert arbiter.available_to_sell("acct1", "AAPL") == 47


@pytest.mark.asyncio
async def test_reserve_beyond_owned_is_refused():
    arbiter = CloseArbiter()
    await arbiter.set_owned_quantity("acct1", "AAPL", 62)

    assert await arbiter.reserve("acct1", "AAPL", 70) is False
    assert arbiter.available_to_sell("acct1", "AAPL") == 62  # nothing reserved


@pytest.mark.asyncio
async def test_the_key_invariant_never_allows_two_full_position_sells():
    """The exact scenario the design calls out: several independent
    full-position sells must never both succeed."""
    arbiter = CloseArbiter()
    await arbiter.set_owned_quantity("acct1", "AAPL", 100)

    first = await arbiter.reserve("acct1", "AAPL", 100)  # e.g. protective stop
    second = await arbiter.reserve("acct1", "AAPL", 100)  # e.g. a target firing concurrently

    assert first is True
    assert second is False
    assert arbiter.available_to_sell("acct1", "AAPL") == 0


@pytest.mark.asyncio
async def test_settle_with_partial_fill_trues_up_owned_not_reserved_amount():
    """Design section 6: a 15-share sell that only fills 8 must leave owned at
    54 (62 - 8), not 47 (62 - 15)."""
    arbiter = CloseArbiter()
    await arbiter.set_owned_quantity("acct1", "AAPL", 62)
    await arbiter.reserve("acct1", "AAPL", 15)

    await arbiter.settle("acct1", "AAPL", reserved_quantity=15, filled_quantity=8)

    assert arbiter.available_to_sell("acct1", "AAPL") == 54


@pytest.mark.asyncio
async def test_release_of_a_rejected_order_restores_availability():
    arbiter = CloseArbiter()
    await arbiter.set_owned_quantity("acct1", "AAPL", 62)
    await arbiter.reserve("acct1", "AAPL", 15)

    await arbiter.release("acct1", "AAPL", 15)

    assert arbiter.available_to_sell("acct1", "AAPL") == 62


@pytest.mark.asyncio
async def test_halted_position_refuses_new_reservations():
    arbiter = CloseArbiter()
    await arbiter.set_owned_quantity("acct1", "AAPL", 62)
    await arbiter.halt("acct1", "AAPL", "test halt")

    assert await arbiter.reserve("acct1", "AAPL", 1) is False
    assert arbiter.is_halted("acct1", "AAPL") is True


@pytest.mark.asyncio
async def test_concurrent_reservations_never_oversell():
    """Fire 20 concurrent 10-share reservation attempts against a 62-share
    position (which only allows 6 of them) and confirm the total reserved
    across all winners never exceeds what's owned."""
    arbiter = CloseArbiter()
    await arbiter.set_owned_quantity("acct1", "AAPL", 62)

    results = await asyncio.gather(*[arbiter.reserve("acct1", "AAPL", 10) for _ in range(20)])

    granted = sum(1 for r in results if r)
    assert granted == 6  # floor(62 / 10)
    assert arbiter.available_to_sell("acct1", "AAPL") == 2
    assert not arbiter.is_halted("acct1", "AAPL")


@pytest.mark.asyncio
async def test_transition_holds_lock_across_multiple_operations():
    arbiter = CloseArbiter()
    await arbiter.set_owned_quantity("acct1", "AAPL", 62)

    async with arbiter.transition("acct1", "AAPL") as tx:
        assert tx.owned == 62
        assert tx.reserve(15) is True
        assert tx.available == 47
        tx.settle(reserved_quantity=15, filled_quantity=8)
        assert tx.owned == 54
        assert tx.available == 54

    assert arbiter.available_to_sell("acct1", "AAPL") == 54


@pytest.mark.asyncio
async def test_reserve_blocks_while_a_transition_holds_the_lock():
    """A concurrent reserve() call for the same key must wait for an in-progress
    transition to finish — proving the race in design section 7 can't happen."""
    arbiter = CloseArbiter()
    await arbiter.set_owned_quantity("acct1", "AAPL", 62)

    order = []

    async def run_transition():
        async with arbiter.transition("acct1", "AAPL") as tx:
            order.append("transition-start")
            await asyncio.sleep(0.05)
            tx.reserve(62)
            order.append("transition-end")

    async def run_reserve():
        await asyncio.sleep(0.01)  # start after the transition has the lock
        order.append("reserve-attempt")
        result = await arbiter.reserve("acct1", "AAPL", 62)
        order.append(("reserve-result", result))

    await asyncio.gather(run_transition(), run_reserve())

    # the reserve() call must not have completed until after the transition ended
    assert order.index("transition-end") < order.index(("reserve-result", False))


@pytest.mark.asyncio
async def test_overclose_preserves_the_real_negative_inventory_and_halts():
    """PRO-08: settling more filled quantity than was actually owned (e.g.
    12 confirmed against 10 owned) must preserve the real signed result
    (-2), not silently clamp to 0 -- and must halt the position rather than
    let it keep operating on an unexplained anomaly."""
    arbiter = CloseArbiter()
    await arbiter.set_owned_quantity("acct1", "AAPL", 10)
    assert await arbiter.reserve("acct1", "AAPL", 10) is True

    await arbiter.settle("acct1", "AAPL", reserved_quantity=10, filled_quantity=12)

    ledger = arbiter._ledgers[("acct1", "AAPL")]
    assert ledger.owned == -2
    assert arbiter.is_halted("acct1", "AAPL") is True
    assert "negative" in arbiter.halt_reason("acct1", "AAPL")
