"""Regression tests for Track 57 mutation testing.

These tests specifically target real bugs found by mutmut that were not
previously caught:
- ExposureReport field default mutation (survived)
- reserve_locked store condition (`and` vs `or` confusion)
- reserve_locked accumulation (replace vs add)
- release underflow protection (max constant value)
- release operator (- vs +)
"""
import asyncio
from app.capital_allocator import CapitalAllocator, ExposureReport

ACCOUNT_ID = "acct1"


class TestExposureReportDefaults:
    """Regression: ExposureReport.unresolved_symbols must be a list, not None."""

    def test_unresolved_symbols_is_always_a_list(self):
        """Mutant 2 (capital_allocator): unresolved_symbols = None would
        cause AttributeError when iterating."""
        report = ExposureReport(notional=1000.0)
        # Must not be None; must be an empty list by default
        assert report.unresolved_symbols == []
        assert isinstance(report.unresolved_symbols, list)

    def test_unresolved_symbols_property_reads_empty_list_as_false(self):
        """has_unresolved property relies on unresolved_symbols being iterable."""
        report = ExposureReport(notional=1000.0)
        assert report.has_unresolved is False
        # With None, this would crash: bool(None) works, but
        # the property is used to mean "this list is empty"
        report_with_unresolved = ExposureReport(notional=1000.0, unresolved_symbols=["AAPL"])
        assert report_with_unresolved.has_unresolved is True


class TestReserveLockedStoreCondition:
    """Regression: reserve_locked must check `and`, not `or`, before
    accessing self.store."""

    def test_reserve_locked_does_not_access_store_when_none(self):
        """Mutant 36: if self.store is not None and notional would become
        if self.store is not None or notional -- causing AttributeError
        when self.store is None but notional > 0."""
        allocator = CapitalAllocator(store=None)
        # This must NOT raise AttributeError even though self.store is None
        allocator.reserve_locked(ACCOUNT_ID, 500.0)
        assert allocator.pending_reservation(ACCOUNT_ID) == 500.0

    def test_reserve_locked_with_zero_notional_and_no_store(self):
        """Ensure zero notional doesn't cause issues."""
        allocator = CapitalAllocator(store=None)
        allocator.reserve_locked(ACCOUNT_ID, 0.0)
        assert allocator.pending_reservation(ACCOUNT_ID) == 0.0


class TestReserveLockedAccumulation:
    """Regression: reserve_locked must ADD to _pending, not replace it."""

    def test_multiple_reserve_locked_calls_accumulate(self):
        """Mutant 37: += would become = notional, losing previous reservations."""
        allocator = CapitalAllocator()
        allocator.reserve_locked(ACCOUNT_ID, 100.0)
        allocator.reserve_locked(ACCOUNT_ID, 200.0)
        # Total must be 300.0, not just 200.0
        assert allocator.pending_reservation(ACCOUNT_ID) == 300.0

    def test_reserve_locked_accumulates_across_different_signals(self):
        """Each signal's reservation must add to the total."""
        allocator = CapitalAllocator()
        allocator.reserve_locked(ACCOUNT_ID, 500.0, signal_id="sig1")
        allocator.reserve_locked(ACCOUNT_ID, 700.0, signal_id="sig2")
        assert allocator.pending_reservation(ACCOUNT_ID) == 1200.0


class TestReleaseUnderflowProtection:
    """Regression: release must use max(0.0, ...) to prevent negative values."""

    def test_release_never_goes_negative_with_max_0(self):
        """Mutant 39: max(0.0, ...) would become max(1.0, ...) or similar,
        preventing full release."""
        allocator = CapitalAllocator()
        # Never reserve anything
        allocator.release(ACCOUNT_ID, 500.0)
        # Must be exactly 0.0, not 1.0
        assert allocator.pending_reservation(ACCOUNT_ID) == 0.0

    def test_release_with_partial_reservation(self):
        """Release part of what was reserved."""
        allocator = CapitalAllocator()
        allocator.reserve_locked(ACCOUNT_ID, 1000.0)
        allocator.release(ACCOUNT_ID, 400.0)
        assert allocator.pending_reservation(ACCOUNT_ID) == 600.0

    def test_release_more_than_reserved_clamps_to_zero(self):
        """Over-releasing must not go negative."""
        allocator = CapitalAllocator()
        allocator.reserve_locked(ACCOUNT_ID, 300.0)
        allocator.release(ACCOUNT_ID, 500.0)
        assert allocator.pending_reservation(ACCOUNT_ID) == 0.0


class TestReleaseOperator:
    """Regression: release must SUBTRACT from _pending, not add."""

    def test_release_subtracts_not_adds(self):
        """Mutant 40: max(0.0, self._pending - notional) would become + notional."""
        allocator = CapitalAllocator()
        allocator.reserve_locked(ACCOUNT_ID, 1000.0)
        allocator.release(ACCOUNT_ID, 250.0)
        # Must be 750.0 (1000 - 250), not 1250.0 (1000 + 250)
        assert allocator.pending_reservation(ACCOUNT_ID) == 750.0

    def test_release_reduces_pending_correctly(self):
        """Multiple releases must reduce correctly."""
        allocator = CapitalAllocator()
        allocator.reserve_locked(ACCOUNT_ID, 2000.0)
        allocator.release(ACCOUNT_ID, 300.0)
        assert allocator.pending_reservation(ACCOUNT_ID) == 1700.0
        allocator.release(ACCOUNT_ID, 200.0)
        assert allocator.pending_reservation(ACCOUNT_ID) == 1500.0


class TestAdmitComparisonOperator:
    """Regression: admit's exposure check uses >, not >= or other operators."""

    def test_admit_uses_greater_not_greater_equal(self):
        """The exposure check must use > to allow when AT the ceiling."""
        allocator = CapitalAllocator()

        async def run():
            # Reserve exactly at the ceiling: 500 + 0 + 500 = 1000 (NOT > 1000)
            admitted1 = await allocator.admit(
                ACCOUNT_ID, 500.0, confirmed_exposure=500.0, max_exposure=1000.0
            )
            assert admitted1 is True
            # Next entry would put us OVER 1000.0
            # confirmed=500 + pending=500 + new=100 = 1100 > 1000, so rejected
            admitted2 = await allocator.admit(
                ACCOUNT_ID, 100.0, confirmed_exposure=500.0, max_exposure=1000.0
            )
            assert admitted2 is False

        asyncio.run(run())
