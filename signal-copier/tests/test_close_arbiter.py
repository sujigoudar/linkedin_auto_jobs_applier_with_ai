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
async def test_snapshot_round_trips_through_restore():
    """Track 44 mutation-testing gap: `snapshot()`/`restore()` had no test
    at all before this -- a dict-key-rename regression in `snapshot()`
    (e.g. "owned" -> "OWNED") would silently break every real
    PositionLifecycleManager save/restore round trip (this module's own
    documented "no startup reconciliation" gap means a lost/mismatched
    key here has no other safety net) without failing a single test."""
    arbiter = CloseArbiter()
    await arbiter.set_owned_quantity("acct1", "AAPL", 62)
    await arbiter.reserve("acct1", "AAPL", 15)
    await arbiter.halt("acct1", "AAPL", "a real reason")

    snapshot = arbiter.snapshot("acct1", "AAPL")

    assert snapshot == {"owned": 62, "reserved": 15, "halted": True, "halt_reason": "a real reason"}

    restored = CloseArbiter()
    restored.restore(
        "acct2",
        "MSFT",
        owned=snapshot["owned"],
        reserved=snapshot["reserved"],
        halted=snapshot["halted"],
        halt_reason=snapshot["halt_reason"],
    )

    assert restored.available_to_sell("acct2", "MSFT") == 62 - 15
    assert restored.is_halted("acct2", "MSFT") is True
    assert restored.halt_reason("acct2", "MSFT") == "a real reason"
    assert restored.snapshot("acct2", "MSFT") == snapshot


@pytest.mark.asyncio
async def test_halt_preserves_the_real_reason_not_a_fabricated_one():
    """The whole point of recording a halt reason is so an operator (or
    `request_exit`'s own "halted: {tx.halt_reason}" rejection message)
    can see WHY -- discarding the real reason (e.g. replacing it with
    None) was undetected because no existing test checked halt_reason()
    after a direct halt() call."""
    arbiter = CloseArbiter()
    await arbiter.set_owned_quantity("acct1", "AAPL", 62)

    await arbiter.halt("acct1", "AAPL", "broker reported an unreconcilable position mismatch")

    assert arbiter.halt_reason("acct1", "AAPL") == "broker reported an unreconcilable position mismatch"


@pytest.mark.asyncio
async def test_reserve_of_exactly_zero_is_always_a_true_no_op():
    """`_reserve_locked`'s `quantity <= 0: return True` guard is a
    deliberate no-op fast path (reserving nothing always "succeeds",
    even on a halted position) -- distinct from the `quantity < 0`
    boundary and from returning False for this case."""
    arbiter = CloseArbiter()
    await arbiter.set_owned_quantity("acct1", "AAPL", 62)
    await arbiter.halt("acct1", "AAPL", "test halt")

    assert await arbiter.reserve("acct1", "AAPL", 0) is True
    # and it must not have reserved anything
    assert arbiter.available_to_sell("acct1", "AAPL") == 62
    assert arbiter.is_halted("acct1", "AAPL") is True


@pytest.mark.asyncio
async def test_reserve_at_exactly_the_epsilon_tolerance_boundary_succeeds():
    """The `_EPSILON` slack in `quantity > available + _EPSILON` exists so a
    float-imprecise request for "everything available" isn't spuriously
    refused. A request exactly AT that tolerance boundary must still
    succeed (`>`, not `>=`) -- only a request genuinely beyond the
    tolerance is refused."""
    arbiter = CloseArbiter()
    await arbiter.set_owned_quantity("acct1", "AAPL", 10)

    from app.lifecycle.close_arbiter import _EPSILON

    assert await arbiter.reserve("acct1", "AAPL", 10 + _EPSILON) is True
    assert arbiter.available_to_sell("acct1", "AAPL") == 0


@pytest.mark.asyncio
async def test_owned_dropping_below_an_outstanding_reservation_halts_even_without_going_negative():
    """PRO-08's sibling case: `_check_invariant`'s SECOND branch (`reserved
    > owned`) fires independently of the first (`owned < 0`) -- e.g. a
    corrected/decreased confirmed-owned observation (PositionLifecycleManager
    on_entry_fill/resolve_pending_entry's `tx.set_owned`) landing while an
    exit's reservation is still outstanding, with owned staying
    non-negative throughout. This must halt (defensively) exactly like an
    overclose does, with a reason naming reserved-exceeds-owned -- not
    silently let a reservation exceed real ownership."""
    arbiter = CloseArbiter()
    await arbiter.set_owned_quantity("acct1", "AAPL", 62)
    assert await arbiter.reserve("acct1", "AAPL", 50) is True

    async with arbiter.transition("acct1", "AAPL") as tx:
        tx.set_owned(40)  # drops below the 50 already reserved, but stays positive

    # Nothing has re-checked the invariant yet (set_owned itself doesn't) --
    # the next real ledger operation (here, a no-op settle) must catch it.
    await arbiter.settle("acct1", "AAPL", reserved_quantity=0, filled_quantity=0)

    assert arbiter.is_halted("acct1", "AAPL") is True
    assert "reserved" in arbiter.halt_reason("acct1", "AAPL")
    assert "exceeds" in arbiter.halt_reason("acct1", "AAPL")


@pytest.mark.asyncio
async def test_reserved_exceeding_owned_at_exactly_the_epsilon_boundary_does_not_spuriously_halt():
    """The sibling boundary for the `reserved > owned` check: exactly AT
    the epsilon tolerance must NOT halt (`>`, not `>=`)."""
    arbiter = CloseArbiter()
    from app.lifecycle.close_arbiter import _EPSILON

    await arbiter.set_owned_quantity("acct1", "AAPL", 40 + _EPSILON)
    assert await arbiter.reserve("acct1", "AAPL", 40 + _EPSILON) is True

    async with arbiter.transition("acct1", "AAPL") as tx:
        tx.set_owned(40)  # reserved (40+EPSILON) now exceeds owned by exactly EPSILON

    await arbiter.settle("acct1", "AAPL", reserved_quantity=0, filled_quantity=0)

    assert arbiter.is_halted("acct1", "AAPL") is False


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
    # Track 44 mutation-testing gap: the only other assertion on this
    # message checked "negative" -- a mutmut survivor showed the REST of
    # the message (naming this specific anomaly as an "overclose") could
    # be mangled/reworded undetected, which matters because this exact
    # wording is what an operator reads to tell "owned went negative"
    # apart from "reserved exceeds owned" (the invariant's other branch,
    # checked below).
    assert "overclose" in arbiter.halt_reason("acct1", "AAPL")


@pytest.mark.asyncio
async def test_a_fresh_ledger_has_an_empty_not_none_halt_reason():
    """Track 44 mutation-testing gap: `_Ledger.__init__`'s `halt_reason = ""`
    default had no test at all -- a mutmut survivor showed it could become
    `None` (or any other placeholder string) with nothing failing.
    `request_exit`'s own halted-rejection message embeds `tx.halt_reason`
    directly in an f-string, so a `None` default would render as the
    literal text "None" instead of an honest empty string for a position
    that was never halted."""
    arbiter = CloseArbiter()
    await arbiter.set_owned_quantity("acct1", "AAPL", 10)

    assert arbiter.halt_reason("acct1", "AAPL") == ""


@pytest.mark.asyncio
async def test_owned_at_exactly_minus_epsilon_is_the_real_halt_boundary():
    """Track 44 mutation-testing gap: `_check_invariant`'s first branch is
    `owned < -_EPSILON` (strictly less than) -- a mutmut survivor showed
    `<=` passes every existing boundary test undetected, because the
    nearby epsilon-boundary test above settles to `-_EPSILON / 2` (inside
    the tolerance either way). Only a value at EXACTLY `-_EPSILON` tells
    the two operators apart: this is still within float-imprecision
    tolerance of zero (the docstring's reasoning for the tolerance at
    all) and must NOT halt."""
    from app.lifecycle.close_arbiter import _EPSILON

    arbiter = CloseArbiter()
    await arbiter.set_owned_quantity("acct1", "AAPL", 10)
    # Set owned to EXACTLY -_EPSILON directly, rather than via a settle
    # subtraction (whose own float rounding could land a hair past the
    # boundary either way) -- the point here is the `<` vs `<=`
    # comparison itself, not float arithmetic.
    arbiter._ledgers[("acct1", "AAPL")].owned = -_EPSILON

    # settle() with a zero delta re-runs _check_invariant without changing
    # owned any further.
    await arbiter.settle("acct1", "AAPL", reserved_quantity=0, filled_quantity=0)

    assert arbiter.is_halted("acct1", "AAPL") is False


@pytest.mark.asyncio
async def test_transaction_release_frees_a_reservation_without_touching_owned():
    """Track 44 mutation-testing gap: `_TransactionOps.release` (the
    `tx.release()` lock-free delegate used inside a `transition()` block)
    had no test at all -- a mutmut survivor showed its `self._key`/
    `quantity` arguments could be swapped, dropped, or replaced with
    `None` undetected. Not currently called by app/lifecycle/manager.py
    (which releases reservations only via `settle`), but it is part of
    `_TransactionOps`'s public surface for a future caller inside a held
    transition, same as `reserve`/`settle` already are."""
    arbiter = CloseArbiter()
    await arbiter.set_owned_quantity("acct1", "AAPL", 62)

    async with arbiter.transition("acct1", "AAPL") as tx:
        assert tx.reserve(15) is True
        assert tx.available == 47
        tx.release(15)
        assert tx.available == 62
        assert tx.reserved == 0

    assert arbiter.available_to_sell("acct1", "AAPL") == 62
