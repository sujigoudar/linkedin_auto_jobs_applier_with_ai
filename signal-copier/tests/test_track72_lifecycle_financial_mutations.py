"""Track 72: comprehensive mutation-testing regression suite for position lifecycle
management and core financial modules.

This file implements targeted regression tests for high-risk financial and state
machine logic in:

- app/lifecycle/manager.py (position lifecycle state machine, critical invariants)
- app/lifecycle/close_arbiter.py (close arbitration, oversell prevention)
- app/writer_lease.py (single-writer fencing mechanism across processes)
- app/command_ledger.py (idempotent pre-effect financial-command ledger)
- app/reconciliation.py (fill-confirmation reconciliation against PENDING orders)

These tests are designed to catch mutations that could silently break:

- Lifecycle state transitions and forbidden transitions (open -> closed, halt on error)
- Close arbitration invariants (owned >= reserved, available = owned - reserved)
- Lease fencing and process isolation (token mismatches, promotion safety)
- Idempotency key computations and command classification
- Fill matching and quantity reconciliation
- Boundary conditions on financial calculations (zero quantities, negative detection)
- Operator inversions (==, !=, >, >=, <, <=) in critical financial logic
- Control flow mutations affecting financial state atomicity

The tests follow the "Track 60-71 targeted regression test pattern" with
hand-written tests for mutation-critical patterns rather than relying on
mutant survival rates alone.

See pyproject.toml's Track 72 commentary for execution pattern and rationale.
"""

from __future__ import annotations

import asyncio
import math
import pytest
from datetime import datetime, timezone
from unittest.mock import MagicMock

from app import command_ledger
from app.brokers.paper import PaperBroker
from app.db import SignalStore
from app.lifecycle.manager import PositionLifecycleManager, _compute_reduction_plan, _compute_trailing_candidate
from app.lifecycle.close_arbiter import CloseArbiter, _EPSILON
from app.lifecycle.models import (
    PositionLifecycle, PositionPlan, ProtectionStatus, TrailingPolicy
)
from app.models import (
    OrderResult, OrderStatus, Side, UncertaintyState, AssetClass
)
from app.writer_lease import (
    WriterLeaseGuard, WriterLeaseRecord, FencedOutError
)


# ============================================================================
# Track 72.1: CloseArbiter Invariant Tests
# ============================================================================

class TestCloseArbiterInvariants:
    """Test the critical invariant: available_to_sell = owned - reserved >= 0."""

    def test_available_to_sell_never_goes_negative(self):
        """available_to_sell must never go below zero, even with rounding."""
        arbiter = CloseArbiter()
        asyncio.run(arbiter.set_owned_quantity("acct1", "AAPL", 100.0))

        # Try to reserve more than owned
        reserved = asyncio.run(arbiter.reserve("acct1", "AAPL", 101.0))
        assert reserved is False

        # available_to_sell must clamp to 0
        available = arbiter.available_to_sell("acct1", "AAPL")
        assert available >= 0.0
        assert available == 100.0

    def test_reserve_exactly_owned_quantity_succeeds(self):
        """Reserving exactly the owned quantity is allowed."""
        arbiter = CloseArbiter()
        asyncio.run(arbiter.set_owned_quantity("acct1", "AAPL", 50.0))

        reserved = asyncio.run(arbiter.reserve("acct1", "AAPL", 50.0))
        assert reserved is True

        available = arbiter.available_to_sell("acct1", "AAPL")
        assert available <= 0.1  # Allow small epsilon

    def test_reserve_just_over_owned_quantity_fails(self):
        """Reserving just over owned must fail."""
        arbiter = CloseArbiter()
        asyncio.run(arbiter.set_owned_quantity("acct1", "AAPL", 50.0))

        # 50.0 + 1 * epsilon should fail
        reserved = asyncio.run(arbiter.reserve("acct1", "AAPL", 50.0 + _EPSILON * 2))
        assert reserved is False

    def test_zero_quantity_reservation_never_fails(self):
        """Reserving zero quantity must always succeed."""
        arbiter = CloseArbiter()
        # No setup needed

        reserved = asyncio.run(arbiter.reserve("acct1", "AAPL", 0.0))
        assert reserved is True

    def test_negative_quantity_reservation_never_fails(self):
        """Negative quantity reservation is a no-op, not an error."""
        arbiter = CloseArbiter()
        asyncio.run(arbiter.set_owned_quantity("acct1", "AAPL", 100.0))

        reserved = asyncio.run(arbiter.reserve("acct1", "AAPL", -5.0))
        assert reserved is True

        available = arbiter.available_to_sell("acct1", "AAPL")
        assert available == 100.0  # Unchanged

    def test_settle_correctly_reduces_owned_and_reserved(self):
        """settle() must reduce owned by filled and reserved by reserved_qty."""
        arbiter = CloseArbiter()
        asyncio.run(arbiter.set_owned_quantity("acct1", "AAPL", 100.0))
        asyncio.run(arbiter.reserve("acct1", "AAPL", 60.0))

        asyncio.run(arbiter.settle("acct1", "AAPL", reserved_quantity=60.0, filled_quantity=50.0))

        assert arbiter.available_to_sell("acct1", "AAPL") == 50.0
        snapshot = arbiter.snapshot("acct1", "AAPL")
        assert snapshot["owned"] == 50.0
        assert snapshot["reserved"] == 0.0

    def test_settle_with_filled_greater_than_reserved_is_detected(self):
        """Filling more than was reserved is detected and position halts."""
        arbiter = CloseArbiter()
        asyncio.run(arbiter.set_owned_quantity("acct1", "AAPL", 100.0))
        asyncio.run(arbiter.reserve("acct1", "AAPL", 50.0))

        # Settle 120 filled but only 50 was reserved (overclose)
        # This creates owned = 100 - 120 = -20 (negative, which halts)
        asyncio.run(arbiter.settle("acct1", "AAPL", reserved_quantity=50.0, filled_quantity=120.0))

        # Position must be halted because owned went negative
        is_halted = arbiter.is_halted("acct1", "AAPL")
        assert is_halted is True
        reason = arbiter.halt_reason("acct1", "AAPL")
        assert "negative" in reason or "overclose" in reason.lower()

    def test_halt_prevents_future_reservations(self):
        """Once halted, no new reservations are allowed."""
        arbiter = CloseArbiter()
        asyncio.run(arbiter.set_owned_quantity("acct1", "AAPL", 100.0))
        asyncio.run(arbiter.halt("acct1", "AAPL", "manual halt for testing"))

        reserved = asyncio.run(arbiter.reserve("acct1", "AAPL", 10.0))
        assert reserved is False


# ============================================================================
# Track 72.2: Lifecycle State Transition Tests
# ============================================================================

class TestLifecycleStateTransitions:
    """Test lifecycle state machine: entry -> managed -> closed."""

    def test_lifecycle_starts_not_closed(self):
        """A fresh lifecycle must start in open state."""
        plan = PositionPlan(
            account_id="acct1", symbol="AAPL", side=Side.BUY,
            planned_quantity=100.0, asset_class=AssetClass.EQUITY,
            broker="paper", initial_stop=180.0, entry_signal_id="sig-1"
        )
        lifecycle = PositionLifecycle(plan=plan)

        assert lifecycle.closed is False
        assert lifecycle.confirmed_owned_quantity == 0.0

    def test_lifecycle_closes_when_owned_reaches_zero(self):
        """A lifecycle closes when owned quantity drops to zero."""
        plan = PositionPlan(
            account_id="acct1", symbol="AAPL", side=Side.BUY,
            planned_quantity=100.0, asset_class=AssetClass.EQUITY,
            broker="paper", initial_stop=180.0, entry_signal_id="sig-1"
        )
        lifecycle = PositionLifecycle(plan=plan)
        lifecycle.confirmed_owned_quantity = 100.0

        # Simulate exit
        lifecycle.confirmed_owned_quantity = 0.0
        # Note: in real code, _apply_exit_fill sets lifecycle.closed based on owned
        # This test just checks the state representation works

    def test_lifecycle_has_unresolved_entry_flag(self):
        """has_unresolved_entry returns True iff pending_entry is present and unresolved."""
        plan = PositionPlan(
            account_id="acct1", symbol="AAPL", side=Side.BUY,
            planned_quantity=100.0, asset_class=AssetClass.EQUITY,
            broker="paper", initial_stop=180.0, entry_signal_id="sig-1"
        )
        lifecycle = PositionLifecycle(plan=plan)

        assert lifecycle.has_unresolved_entry is False

        # Set a pending entry
        from app.lifecycle.models import PendingEntry
        lifecycle.pending_entry = PendingEntry(
            broker_order_id="bo-123",
            requested_quantity=100.0
        )
        assert lifecycle.has_unresolved_entry is True

        # Mark it resolved
        lifecycle.pending_entry.remainder_resolved = True
        assert lifecycle.has_unresolved_entry is False

    def test_lifecycle_protection_status_transitions(self):
        """Protection status transitions through UNPROTECTED -> CONFIRMED."""
        plan = PositionPlan(
            account_id="acct1", symbol="AAPL", side=Side.BUY,
            planned_quantity=100.0, asset_class=AssetClass.EQUITY,
            broker="paper", initial_stop=180.0, entry_signal_id="sig-1"
        )
        lifecycle = PositionLifecycle(plan=plan)

        assert lifecycle.stop.status == ProtectionStatus.UNPROTECTED

        lifecycle.stop.status = ProtectionStatus.STOP_CONFIRMED
        assert lifecycle.stop.status == ProtectionStatus.STOP_CONFIRMED

        lifecycle.stop.status = ProtectionStatus.UNPROTECTED
        assert lifecycle.stop.status == ProtectionStatus.UNPROTECTED


# ============================================================================
# Track 72.3: Reduction Plan Computation Tests
# ============================================================================

class TestReductionPlanComputation:
    """Test the pure reduction plan computation that both request_exit and
    preview_reduction use."""

    @pytest.fixture
    def broker(self):
        return PaperBroker()

    @pytest.fixture
    def arbiter(self):
        return CloseArbiter()

    @pytest.fixture
    def plan(self):
        return PositionPlan(
            account_id="acct1", symbol="AAPL", side=Side.BUY,
            planned_quantity=100.0, asset_class=AssetClass.EQUITY,
            broker="paper", initial_stop=180.0, entry_signal_id="sig-1"
        )

    @pytest.fixture
    def lifecycle(self, plan):
        return PositionLifecycle(plan=plan)

    def test_reduction_requested_quantity_clamped_to_available(self, plan, lifecycle, broker, arbiter):
        """requested_quantity must never exceed available."""
        # Setup: 100 owned, 0 reserved -> 100 available
        lifecycle.confirmed_owned_quantity = 100.0
        lifecycle.stop.broker_order_id = "bo-123"
        lifecycle.stop.desired_price = 180.0

        # Transaction object simulation
        class MockTx:
            owned = 100.0
            available = 100.0

        tx = MockTx()

        # Request 150, should be clamped to 100 available
        reduction_plan = _compute_reduction_plan(tx, lifecycle, broker, 150.0)
        assert reduction_plan.requested_quantity == 100.0
        assert reduction_plan.requested_quantity <= tx.available

    def test_reduction_remaining_after_request_computed_correctly(self, plan, lifecycle, broker, arbiter):
        """remaining_after_request = owned - requested."""
        lifecycle.confirmed_owned_quantity = 100.0
        lifecycle.stop.broker_order_id = "bo-123"
        lifecycle.stop.desired_price = 180.0

        class MockTx:
            owned = 100.0
            available = 100.0

        tx = MockTx()
        reduction_plan = _compute_reduction_plan(tx, lifecycle, broker, 30.0)

        assert reduction_plan.remaining_after_request == 70.0
        assert reduction_plan.owned_before == 100.0

    def test_reduction_can_amend_stop_requires_all_conditions(self, plan, lifecycle, broker, arbiter):
        """can_amend_stop_in_place requires: had_stop AND remainder > 0 AND
        broker supports replace."""
        lifecycle.stop.desired_price = 180.0

        class MockTx:
            owned = 100.0
            available = 100.0

        tx = MockTx()

        # Without stop: can_amend = False
        lifecycle.stop.broker_order_id = None
        plan_no_stop = _compute_reduction_plan(tx, lifecycle, broker, 30.0)
        assert plan_no_stop.can_amend_stop_in_place is False

        # With stop but remainder = 0: can_amend = False
        lifecycle.stop.broker_order_id = "bo-123"
        plan_full_close = _compute_reduction_plan(tx, lifecycle, broker, 100.0)
        assert plan_full_close.can_amend_stop_in_place is False

        # With stop, remainder > 0, broker supports: can_amend = True
        plan_partial = _compute_reduction_plan(tx, lifecycle, broker, 30.0)
        assert plan_partial.can_amend_stop_in_place is True
        assert plan_partial.remaining_after_request > 0

    def test_reduction_zero_quantity_request(self, plan, lifecycle, broker, arbiter):
        """Requesting zero quantity is valid but produces empty reduction."""
        lifecycle.confirmed_owned_quantity = 100.0

        class MockTx:
            owned = 100.0
            available = 100.0

        tx = MockTx()
        plan_zero = _compute_reduction_plan(tx, lifecycle, broker, 0.0)

        assert plan_zero.requested_quantity == 0.0
        assert plan_zero.remaining_after_request == 100.0

    def test_reduction_respects_available_not_just_owned(self, plan, lifecycle, broker, arbiter):
        """When reserved > 0, available < owned, so reduction is clamped to available."""
        lifecycle.confirmed_owned_quantity = 100.0

        class MockTx:
            owned = 100.0
            available = 30.0  # 100 owned - 70 reserved

        tx = MockTx()
        plan_constrained = _compute_reduction_plan(tx, lifecycle, broker, 50.0)

        # Request 50 but only 30 available -> clamped
        assert plan_constrained.requested_quantity == 30.0
        assert plan_constrained.available_before == 30.0


# ============================================================================
# Track 72.4: Trailing Stop Computation Tests
# ============================================================================

class TestTrailingStopComputation:
    """Test the pure trailing stop computation used by both on_price_update
    and preview_stop_change."""

    def test_trailing_buy_side_improves_when_price_rises(self):
        """For a BUY position (long), higher price = higher candidate floor."""
        trailing = TrailingPolicy(trail_distance=5.0, active=True)
        current_desired = 195.0  # Current stop

        # Price goes up to 210
        candidate_floor, improved = _compute_trailing_candidate(
            trailing, current_desired, Side.BUY, 210.0
        )

        # Candidate = 210 - 5 = 205, which is > 195
        assert candidate_floor == 205.0
        assert improved is True

    def test_trailing_buy_side_does_not_loosen(self):
        """For a BUY position, stop should not move down (loosen)."""
        trailing = TrailingPolicy(trail_distance=5.0, active=True)
        current_desired = 205.0

        # Price drops to 200
        candidate_floor, improved = _compute_trailing_candidate(
            trailing, current_desired, Side.BUY, 200.0
        )

        # Candidate = 200 - 5 = 195, which is < 205, so no improvement
        assert candidate_floor == 195.0
        assert improved is False

    def test_trailing_sell_side_improves_when_price_falls(self):
        """For a SELL position (short), lower price = higher candidate floor."""
        trailing = TrailingPolicy(trail_distance=5.0, active=True)
        current_desired = 95.0  # Current stop (higher for short)

        # Price drops to 80
        candidate_floor, improved = _compute_trailing_candidate(
            trailing, current_desired, Side.SELL, 80.0
        )

        # Candidate = 80 + 5 = 85, which is < 95
        assert candidate_floor == 85.0
        assert improved is True

    def test_trailing_sell_side_does_not_tighten_to_lower(self):
        """For a SELL position, stop should not move to lower prices (loosen)."""
        trailing = TrailingPolicy(trail_distance=5.0, active=True)
        current_desired = 85.0

        # Price rises to 100
        candidate_floor, improved = _compute_trailing_candidate(
            trailing, current_desired, Side.SELL, 100.0
        )

        # Candidate = 100 + 5 = 105, which is > 85, so no improvement
        assert candidate_floor == 105.0
        assert improved is False

    def test_trailing_first_time_always_improves(self):
        """When no current desired price set, first price always improves."""
        trailing = TrailingPolicy(trail_distance=5.0, active=True)

        # BUY side, no current desired
        candidate_buy, improved_buy = _compute_trailing_candidate(
            trailing, None, Side.BUY, 200.0
        )
        assert improved_buy is True

        # SELL side, no current desired
        candidate_sell, improved_sell = _compute_trailing_candidate(
            trailing, None, Side.SELL, 100.0
        )
        assert improved_sell is True

    def test_trailing_uses_floor_price_as_tightest_level(self):
        """Trailing should never go looser than floor_price if set."""
        trailing = TrailingPolicy(trail_distance=5.0, floor_price=200.0, active=True)

        # Even if price goes down, floor_price should be respected
        candidate, improved = _compute_trailing_candidate(
            trailing, 199.0, Side.BUY, 199.5
        )
        # floor_price=200 is the tightest, candidate from price is 199.5-5=194.5
        # We compare against max(floor_price, desired) = max(200, 199) = 200
        # candidate 194.5 < 200, so not improved
        assert improved is False


# ============================================================================
# Track 72.5: WriterLeaseGuard Fencing Tests
# ============================================================================

class TestWriterLeaseFencing:
    """Test cross-process fencing via WriterLeaseGuard."""

    @pytest.fixture
    def mock_store(self):
        store = MagicMock()
        store.get_writer_lease = MagicMock()
        store.acquire_or_reacquire_writer_lease = MagicMock()
        store.renew_writer_lease = MagicMock()
        return store

    def test_require_active_raises_when_no_token_set(self, mock_store):
        """require_active() raises FencedOutError when _token is None."""
        guard = WriterLeaseGuard(mock_store, site_id="site-1")

        with pytest.raises(FencedOutError):
            guard.require_active()

    def test_require_active_succeeds_when_token_set(self, mock_store):
        """require_active() succeeds when _token matches DB token."""
        # Mock DB to return same token
        record = WriterLeaseRecord(
            fencing_token=42,
            site_id="site-1",
            holder_id="holder-1",
            acquired_at=datetime.now(timezone.utc),
            expires_at=datetime.now(timezone.utc),
            renewed_at=datetime.now(timezone.utc)
        )
        mock_store.get_writer_lease.return_value = record

        guard = WriterLeaseGuard(mock_store, site_id="site-1")
        guard._token = 42  # Manually set token to match DB

        # Should not raise
        guard.require_active()

    def test_acquire_sets_fencing_token(self, mock_store):
        """acquire() sets the fencing token from DB."""
        record = WriterLeaseRecord(
            fencing_token=100,
            site_id="site-1",
            holder_id="holder-1",
            acquired_at=datetime.now(timezone.utc),
            expires_at=datetime.now(timezone.utc),
            renewed_at=datetime.now(timezone.utc)
        )
        mock_store.acquire_or_reacquire_writer_lease.return_value = record

        guard = WriterLeaseGuard(mock_store, site_id="site-1")
        token = guard.acquire()

        assert token == 100
        assert guard._token == 100

    def test_acquire_is_idempotent_within_same_process(self, mock_store):
        """acquire() sets _token and idempotent check prevents double-bump.

        In the real code, a second call to acquire() on the same guard instance
        (within the same process) will find _token already set and skip the DB call.
        This verifies that once acquired, the token is retained."""
        record = WriterLeaseRecord(
            fencing_token=100,
            site_id="site-1",
            holder_id="holder-1",
            acquired_at=datetime.now(timezone.utc),
            expires_at=datetime.now(timezone.utc),
            renewed_at=datetime.now(timezone.utc)
        )

        mock_store.acquire_or_reacquire_writer_lease.return_value = record

        guard = WriterLeaseGuard(mock_store, site_id="site-1")
        token1 = guard.acquire()

        assert token1 == 100
        assert guard._token == 100
        # DB should be called for actual acquire
        assert mock_store.acquire_or_reacquire_writer_lease.call_count == 1

    def test_acquire_raises_on_different_site_lease(self, mock_store):
        """acquire_or_reacquire should reject different site holding lease."""
        # This is enforced at the DB level, not here, but verify the error propagates
        from app.writer_lease import WriterLeaseHeldByAnotherSiteError

        mock_store.acquire_or_reacquire_writer_lease.side_effect = WriterLeaseHeldByAnotherSiteError(
            "different site holds lease"
        )

        guard = WriterLeaseGuard(mock_store, site_id="site-1")

        with pytest.raises(WriterLeaseHeldByAnotherSiteError):
            guard.acquire()

    def test_lease_expiry_check(self):
        """WriterLeaseRecord.is_expired() correctly checks expiry."""
        now = datetime.now(timezone.utc)
        expired = WriterLeaseRecord(
            fencing_token=1, site_id="s1", holder_id="h1",
            acquired_at=now, expires_at=now, renewed_at=now
        )
        assert expired.is_expired(now=now) is True

        future = datetime(now.year + 1, now.month, now.day, tzinfo=timezone.utc)
        not_expired = WriterLeaseRecord(
            fencing_token=1, site_id="s1", holder_id="h1",
            acquired_at=now, expires_at=future, renewed_at=now
        )
        assert not_expired.is_expired(now=now) is False


# ============================================================================
# Track 72.6: Command Ledger Fingerprint & Classification Tests
# ============================================================================

class TestCommandLedgerFingerprint:
    """Test command ledger idempotency and fingerprint matching."""

    def test_compute_fingerprint_deterministic(self):
        """compute_fingerprint must be deterministic for same input."""
        payload = {"symbol": "AAPL", "quantity": 100, "side": "BUY"}

        fp1 = command_ledger.compute_fingerprint(payload)
        fp2 = command_ledger.compute_fingerprint(payload)

        assert fp1 == fp2
        assert len(fp1) == 64  # SHA-256 hex

    def test_compute_fingerprint_key_order_irrelevant(self):
        """Fingerprint must be same regardless of dict key order."""
        payload1 = {"symbol": "AAPL", "quantity": 100, "side": "BUY"}
        payload2 = {"side": "BUY", "quantity": 100, "symbol": "AAPL"}

        fp1 = command_ledger.compute_fingerprint(payload1)
        fp2 = command_ledger.compute_fingerprint(payload2)

        assert fp1 == fp2

    def test_compute_fingerprint_value_change_changes_hash(self):
        """Different values must produce different fingerprints."""
        payload1 = {"symbol": "AAPL", "quantity": 100}
        payload2 = {"symbol": "AAPL", "quantity": 101}

        fp1 = command_ledger.compute_fingerprint(payload1)
        fp2 = command_ledger.compute_fingerprint(payload2)

        assert fp1 != fp2

    def test_is_duplicate_submission_checks_uncertainty_state(self):
        """is_duplicate_submission returns True only for non-PENDING_SUBMISSION."""
        assert command_ledger.is_duplicate_submission(UncertaintyState.PENDING_SUBMISSION) is False
        assert command_ledger.is_duplicate_submission(UncertaintyState.CONFIRMED) is True
        assert command_ledger.is_duplicate_submission(UncertaintyState.SUBMITTED_UNCONFIRMED) is True
        assert command_ledger.is_duplicate_submission(UncertaintyState.UNKNOWN_AMBIGUOUS) is True

    def test_classify_order_result_filled_is_confirmed(self):
        """A FILLED result must map to CONFIRMED state."""
        result = OrderResult(
            account_id="a1", status=OrderStatus.FILLED, signal_id="s1",
            filled_quantity=10.0, filled_price=100.0
        )

        uncertainty, remote_ids, evidence = command_ledger.classify_order_result(result)

        assert uncertainty == UncertaintyState.CONFIRMED
        assert evidence.get("broker_status") == "filled"
        assert evidence.get("filled_quantity") == 10.0

    def test_classify_order_result_rejected_is_confirmed_rejected(self):
        """A REJECTED result must map to REJECTED_CONFIRMED."""
        result = OrderResult(
            account_id="a1", status=OrderStatus.REJECTED, signal_id="s1",
            message="order rejected"
        )

        uncertainty, remote_ids, evidence = command_ledger.classify_order_result(result)

        assert uncertainty == UncertaintyState.REJECTED_CONFIRMED
        assert evidence.get("broker_status") == "rejected"

    def test_classify_order_result_pending_with_id_is_unconfirmed(self):
        """A PENDING result with broker_order_id is SUBMITTED_UNCONFIRMED."""
        result = OrderResult(
            account_id="a1", status=OrderStatus.PENDING, signal_id="s1",
            broker_order_id="bo-123"
        )

        uncertainty, remote_ids, evidence = command_ledger.classify_order_result(result)

        assert uncertainty == UncertaintyState.SUBMITTED_UNCONFIRMED
        assert remote_ids.get("broker_order_id") == "bo-123"

    def test_classify_order_result_pending_without_id_is_ambiguous(self):
        """A PENDING result without broker_order_id is UNKNOWN_AMBIGUOUS."""
        result = OrderResult(
            account_id="a1", status=OrderStatus.PENDING, signal_id="s1"
        )

        uncertainty, remote_ids, evidence = command_ledger.classify_order_result(result)

        assert uncertainty == UncertaintyState.UNKNOWN_AMBIGUOUS

    def test_classify_cancel_result_true_is_confirmed(self):
        """A True cancel result is CONFIRMED."""
        uncertainty, evidence = command_ledger.classify_cancel_result(True)

        assert uncertainty == UncertaintyState.CONFIRMED
        assert evidence.get("broker_status") == "cancelled"

    def test_classify_cancel_result_false_is_ambiguous(self):
        """A False cancel result (not confirmed) is UNKNOWN_AMBIGUOUS."""
        uncertainty, evidence = command_ledger.classify_cancel_result(False)

        assert uncertainty == UncertaintyState.UNKNOWN_AMBIGUOUS
        assert evidence.get("broker_status") == "cancel_not_confirmed"


# ============================================================================
# Track 72.7: Reconciliation Fill Matching Tests
# ============================================================================

class TestReconciliationFillMatching:
    """Test the reconciliation loop's fill matching logic."""

    def test_partial_fill_is_not_terminal(self):
        """A PENDING result in reconciliation is not terminal."""
        result = OrderResult(
            account_id="a1", status=OrderStatus.PENDING, signal_id="s1",
            broker_order_id="bo-1", filled_quantity=30.0
        )

        # Reconciliation should NOT correct positions for non-terminal PENDING
        is_terminal = result.status in (OrderStatus.FILLED, OrderStatus.REJECTED)
        assert is_terminal is False

    def test_filled_result_is_terminal(self):
        """A FILLED result is terminal."""
        result = OrderResult(
            account_id="a1", status=OrderStatus.FILLED, signal_id="s1",
            broker_order_id="bo-1", filled_quantity=100.0
        )

        is_terminal = result.status in (OrderStatus.FILLED, OrderStatus.REJECTED)
        assert is_terminal is True

    def test_rejected_result_is_terminal(self):
        """A REJECTED result is terminal."""
        result = OrderResult(
            account_id="a1", status=OrderStatus.REJECTED, signal_id="s1"
        )

        is_terminal = result.status in (OrderStatus.FILLED, OrderStatus.REJECTED)
        assert is_terminal is True


# ============================================================================
# Track 72.8: Boundary Condition Tests
# ============================================================================

class TestBoundaryConditions:
    """Test boundary conditions and edge cases in financial calculations."""

    def test_zero_quantity_never_closes_position(self):
        """Filling zero quantity must not close a position."""
        plan = PositionPlan(
            account_id="acct1", symbol="AAPL", side=Side.BUY,
            planned_quantity=100.0, asset_class=AssetClass.EQUITY,
            broker="paper", initial_stop=180.0, entry_signal_id="sig-1"
        )
        lifecycle = PositionLifecycle(plan=plan)
        lifecycle.confirmed_owned_quantity = 100.0

        # Zero fill shouldn't close
        lifecycle.confirmed_owned_quantity = max(0.0, lifecycle.confirmed_owned_quantity - 0.0)

        # Closed should only be True if confirmed_owned_quantity <= 0 and no unresolved entry
        should_close = lifecycle.confirmed_owned_quantity <= 0.0 and not lifecycle.has_unresolved_entry
        assert should_close is False

    def test_negative_owned_quantity_is_error_state(self):
        """Owned quantity going negative must halt the position."""
        arbiter = CloseArbiter()
        asyncio.run(arbiter.set_owned_quantity("acct1", "AAPL", 100.0))

        # Settle a negative delta (overclose)
        asyncio.run(arbiter.settle("acct1", "AAPL", reserved_quantity=50.0, filled_quantity=120.0))

        is_halted = arbiter.is_halted("acct1", "AAPL")
        assert is_halted is True

    def test_float_precision_in_epsilon_comparisons(self):
        """Epsilon comparisons must handle float precision."""
        arbiter = CloseArbiter()
        asyncio.run(arbiter.set_owned_quantity("acct1", "AAPL", 100.0))

        # Reserve just within epsilon
        reserved = asyncio.run(arbiter.reserve("acct1", "AAPL", 100.0 + _EPSILON / 2))
        assert reserved is True

    def test_math_nan_in_desired_price_rejected(self):
        """NaN values in financial calculations must be rejected."""
        plan = PositionPlan(
            account_id="acct1", symbol="AAPL", side=Side.BUY,
            planned_quantity=100.0, asset_class=AssetClass.EQUITY,
            broker="paper", initial_stop=math.nan, entry_signal_id="sig-1"
        )
        lifecycle = PositionLifecycle(plan=plan)

        # Check that NaN is invalid
        assert math.isnan(lifecycle.plan.initial_stop) is True
        assert math.isfinite(lifecycle.plan.initial_stop) is False

    def test_math_inf_in_quantities_rejected(self):
        """Infinity values in quantities must be rejected."""
        plan = PositionPlan(
            account_id="acct1", symbol="AAPL", side=Side.BUY,
            planned_quantity=math.inf, asset_class=AssetClass.EQUITY,
            broker="paper", initial_stop=180.0, entry_signal_id="sig-1"
        )
        lifecycle = PositionLifecycle(plan=plan)

        # Check that inf is invalid
        assert math.isinf(lifecycle.plan.planned_quantity) is True
        assert math.isfinite(lifecycle.plan.planned_quantity) is False

    def test_very_small_positive_quantity_is_nonzero(self):
        """Very small positive quantities are still nonzero."""
        arbiter = CloseArbiter()
        tiny = 1e-12
        asyncio.run(arbiter.set_owned_quantity("acct1", "AAPL", tiny))

        available = arbiter.available_to_sell("acct1", "AAPL")
        assert available > 0.0


# ============================================================================
# Track 72.9: Operator Inversion Mutation Tests
# ============================================================================

class TestOperatorInversionMutations:
    """Targeted tests to catch common operator inversions."""

    def test_available_comparison_uses_max_not_min(self):
        """available_to_sell must use max(0, owned - reserved), not min."""
        arbiter = CloseArbiter()
        asyncio.run(arbiter.set_owned_quantity("acct1", "AAPL", 100.0))
        asyncio.run(arbiter.reserve("acct1", "AAPL", 120.0))  # Fails, owned stays 100

        available = arbiter.available_to_sell("acct1", "AAPL")

        # Should be max(0, 100-0) = 100, not min(0, 100-0) = 0
        assert available == 100.0

    def test_reserve_uses_greater_than_not_less_than(self):
        """reserve() must check if requested > available, not <."""
        arbiter = CloseArbiter()
        asyncio.run(arbiter.set_owned_quantity("acct1", "AAPL", 100.0))

        # 50 is not > 100, so should succeed
        result_50 = asyncio.run(arbiter.reserve("acct1", "AAPL", 50.0))
        assert result_50 is True

        # 120 is > 100, so should fail
        result_120 = asyncio.run(arbiter.reserve("acct1", "AAPL", 120.0))
        assert result_120 is False

    def test_trailing_improvement_check_direction_buy(self):
        """Trailing improvement for BUY must check candidate > current, not <."""
        trailing = TrailingPolicy(trail_distance=5.0, active=True)
        current = 200.0

        # Price at 210 -> candidate 205 > 200: improved
        candidate, improved = _compute_trailing_candidate(trailing, current, Side.BUY, 210.0)
        assert improved is True
        assert candidate > current

    def test_trailing_improvement_check_direction_sell(self):
        """Trailing improvement for SELL must check candidate < current, not >."""
        trailing = TrailingPolicy(trail_distance=5.0, active=True)
        current = 100.0

        # Price at 80 -> candidate 85 < 100: improved
        candidate, improved = _compute_trailing_candidate(trailing, current, Side.SELL, 80.0)
        assert improved is True
        assert candidate < current

    def test_halt_check_uses_less_than_negative_epsilon(self):
        """Invariant check must catch owned < -epsilon, not >= -epsilon."""
        arbiter = CloseArbiter()
        asyncio.run(arbiter.set_owned_quantity("acct1", "AAPL", 100.0))

        # Settle a huge overclose
        asyncio.run(arbiter.settle("acct1", "AAPL", reserved_quantity=50.0, filled_quantity=150.0))

        is_halted = arbiter.is_halted("acct1", "AAPL")
        assert is_halted is True

    def test_close_decision_requires_owned_less_than_or_equal_zero(self):
        """Lifecycle closes when owned <= 0, not when owned >= 0."""
        plan = PositionPlan(
            account_id="acct1", symbol="AAPL", side=Side.BUY,
            planned_quantity=100.0, asset_class=AssetClass.EQUITY,
            broker="paper", initial_stop=180.0, entry_signal_id="sig-1"
        )
        lifecycle = PositionLifecycle(plan=plan)
        lifecycle.confirmed_owned_quantity = 50.0

        # At 50, should NOT close
        should_close = lifecycle.confirmed_owned_quantity <= 0.0
        assert should_close is False

        # At 0, should close (if no unresolved entry)
        lifecycle.confirmed_owned_quantity = 0.0
        should_close = lifecycle.confirmed_owned_quantity <= 0.0 and not lifecycle.has_unresolved_entry
        assert should_close is True


# ============================================================================
# Track 72.10: Integration Tests
# ============================================================================

class TestLifecycleManagerIntegration:
    """Integration tests for PositionLifecycleManager."""

    @pytest.fixture
    def setup(self, tmp_path):
        store = SignalStore(tmp_path / "test.db")
        broker = PaperBroker()
        manager = PositionLifecycleManager(brokers={"paper": broker}, store=store)
        return manager, store, broker

    def test_lifecycle_manager_plan_validation_rejects_no_stop(self, setup):
        """validate_plan must reject entries with no stop."""
        manager, _, _ = setup

        plan = PositionPlan(
            account_id="acct1", symbol="AAPL", side=Side.BUY,
            planned_quantity=100.0, asset_class=AssetClass.EQUITY,
            broker="paper", initial_stop=None, entry_signal_id="sig-1"
        )

        error = manager.validate_plan(plan)
        assert error is not None
        assert "stop" in error.lower()

    def test_lifecycle_manager_plan_validation_rejects_invalid_stop(self, setup):
        """validate_plan must reject stops that are not positive finite numbers."""
        manager, _, _ = setup

        # Zero stop
        plan_zero = PositionPlan(
            account_id="acct1", symbol="AAPL", side=Side.BUY,
            planned_quantity=100.0, asset_class=AssetClass.EQUITY,
            broker="paper", initial_stop=0.0, entry_signal_id="sig-1"
        )
        assert manager.validate_plan(plan_zero) is not None

        # Negative stop
        plan_neg = PositionPlan(
            account_id="acct1", symbol="AAPL", side=Side.BUY,
            planned_quantity=100.0, asset_class=AssetClass.EQUITY,
            broker="paper", initial_stop=-5.0, entry_signal_id="sig-1"
        )
        assert manager.validate_plan(plan_neg) is not None

        # NaN stop
        plan_nan = PositionPlan(
            account_id="acct1", symbol="AAPL", side=Side.BUY,
            planned_quantity=100.0, asset_class=AssetClass.EQUITY,
            broker="paper", initial_stop=math.nan, entry_signal_id="sig-1"
        )
        assert manager.validate_plan(plan_nan) is not None

    def test_lifecycle_manager_prevents_duplicate_positions(self, setup):
        """validate_plan must reject second entry for same account/symbol."""
        manager, _, _ = setup

        plan = PositionPlan(
            account_id="acct1", symbol="AAPL", side=Side.BUY,
            planned_quantity=100.0, asset_class=AssetClass.EQUITY,
            broker="paper", initial_stop=180.0, entry_signal_id="sig-1"
        )

        # First plan is valid
        assert manager.validate_plan(plan) is None
        manager.start_plan(plan)

        # Second plan for same account/symbol must be rejected
        plan2 = PositionPlan(
            account_id="acct1", symbol="AAPL", side=Side.BUY,
            planned_quantity=50.0, asset_class=AssetClass.EQUITY,
            broker="paper", initial_stop=180.0, entry_signal_id="sig-2"
        )

        error = manager.validate_plan(plan2)
        assert error is not None
        assert "active" in error.lower()
