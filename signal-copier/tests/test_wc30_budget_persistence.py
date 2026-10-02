"""WC-30 tests: Hierarchical budget persistence on SignalStore.

Implements WORKFLOW_SPECIFICATION.md §5.3–5.4, §6–6.3 requirements for:
- Durable budget reservation CRUD (create, get, update)
- Hierarchical budget limit enforcement at multiple levels
- Atomic opportunity claiming (concurrent safety)
"""
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

import pytest

from app.db import BudgetReservationRow, SignalStore
from app.workflow.budget import (
    UNLIMITED_CENTS,
    BudgetScope,
    HierarchicalBudget,
    ReservationState,
    ResourceVector,
    Reason,
)


@pytest.fixture
def tmp_store(tmp_path: Path) -> SignalStore:
    """Create a fresh SignalStore on tmp_path for each test."""
    store = SignalStore(tmp_path / "test.db")
    return store


class TestBudgetReservationCRUD:
    """Round-trip tests for create, get, update operations."""

    def test_create_and_get_reservation(self, tmp_store: SignalStore):
        """WC-30: Create a reservation and retrieve it with all fields intact."""
        opportunity_id = "opp_test_001"

        # Create portfolio and sleeve for foreign key constraints
        tmp_store.create_portfolio("port_001", "alice")
        tmp_store.create_strategy_sleeve("sleeve_001", "port_001", "provider_a")

        scope = BudgetScope(
            owner="alice",
            physical_account_id="acct_001",
            portfolio_id="port_001",
            sleeve_id="sleeve_001",
            provider="provider_a",
            analyst="analyst_1",
            underlying="AAPL",
            cluster="cluster_tech",
        )
        need = ResourceVector(
            cash=50000,
            buying_power=100000,
            initial_margin=25000,
            maintenance=None,
            notional=200000,
            planned_risk=5000,
            stress_risk=10000,
            close_quantity=10,
            slots=1,
        )

        # Create reservation
        res_id = tmp_store.create_hierarchical_reservation(
            opportunity_id=opportunity_id,
            scope=scope,
            need=need,
            state=ReservationState.HELD,
        )

        # Verify it's a UUID hex string
        assert isinstance(res_id, str)
        assert len(res_id) == 32

        # Retrieve and verify all fields
        row = tmp_store.get_reservation(res_id)
        assert isinstance(row, BudgetReservationRow)
        assert row.reservation_id == res_id
        assert row.opportunity_id == opportunity_id
        assert row.owner == "alice"
        assert row.physical_account_id == "acct_001"
        assert row.portfolio_id == "port_001"
        assert row.sleeve_id == "sleeve_001"
        assert row.provider == "provider_a"
        assert row.analyst == "analyst_1"
        assert row.underlying == "AAPL"
        assert row.cluster_id == "cluster_tech"
        assert row.needed_cash_cents == 50000
        assert row.needed_margin_cents == 25000
        assert row.needed_notional_cents == 200000
        assert row.needed_planned_risk_cents == 5000
        assert row.needed_stress_risk_cents == 10000
        assert row.state == ReservationState.HELD

    def test_get_missing_reservation_raises_key_error(self, tmp_store: SignalStore):
        """WC-30: KeyError when reservation doesn't exist."""
        with pytest.raises(KeyError, match="not found"):
            tmp_store.get_reservation("nonexistent_id")

    def test_update_reservation_state_and_evidence(self, tmp_store: SignalStore):
        """WC-30: Update state and append evidence atomically."""
        opportunity_id = "opp_test_update"
        scope = BudgetScope(
            owner="bob",
            physical_account_id="acct_002",
            portfolio_id=None,
            sleeve_id=None,
            provider="provider_b",
            analyst=None,
            underlying="TSLA",
            cluster=None,
        )
        need = ResourceVector(
            cash=30000,
            buying_power=None,
            initial_margin=15000,
            maintenance=None,
            notional=100000,
            planned_risk=3000,
            stress_risk=None,
            close_quantity=0,
            slots=1,
        )

        res_id = tmp_store.create_hierarchical_reservation(
            opportunity_id=opportunity_id,
            scope=scope,
            need=need,
            state=ReservationState.HELD,
        )

        # Transition HELD → COMMITTED_TO_PENDING_ORDER with evidence
        evidence_1 = {"event": "submitted", "order_id": "ord_123"}
        tmp_store.update_reservation_state(res_id, ReservationState.COMMITTED_TO_PENDING_ORDER, evidence=evidence_1)

        row = tmp_store.get_reservation(res_id)
        assert row.state == ReservationState.COMMITTED_TO_PENDING_ORDER
        evidence_list = json.loads(row.evidence)
        # Evidence list includes the initial ResourceVector dict, then the event
        assert len(evidence_list) == 2
        assert evidence_list[1] == evidence_1

        # Transition COMMITTED → FILLED_EXPOSURE with more evidence
        evidence_2 = {"event": "filled", "fill_quantity": 100}
        tmp_store.update_reservation_state(res_id, ReservationState.FILLED_EXPOSURE, evidence=evidence_2)

        row = tmp_store.get_reservation(res_id)
        assert row.state == ReservationState.FILLED_EXPOSURE
        evidence_list = json.loads(row.evidence)
        assert len(evidence_list) == 3
        assert evidence_list[2] == evidence_2

    def test_update_missing_reservation_raises_key_error(self, tmp_store: SignalStore):
        """WC-30: KeyError when updating a nonexistent reservation."""
        with pytest.raises(KeyError, match="not found"):
            tmp_store.update_reservation_state("nonexistent", ReservationState.HELD, evidence={})


class TestOpportunityClaiming:
    """Tests for is_opportunity_claimed and UNIQUE constraint."""

    def test_is_opportunity_claimed_false_initially(self, tmp_store: SignalStore):
        """WC-30: Unclaimed opportunity returns False."""
        assert not tmp_store.is_opportunity_claimed("opp_never_claimed")

    def test_is_opportunity_claimed_true_after_create(self, tmp_store: SignalStore):
        """WC-30: After creation, is_opportunity_claimed returns True."""
        opportunity_id = "opp_test_claimed"
        scope = BudgetScope(
            owner="charlie",
            physical_account_id="acct_003",
            portfolio_id=None,
            sleeve_id=None,
            provider="provider_c",
            analyst=None,
            underlying="MSFT",
            cluster=None,
        )
        need = ResourceVector(
            cash=20000, buying_power=None, initial_margin=10000, maintenance=None,
            notional=50000, planned_risk=2000, stress_risk=None,
            close_quantity=0, slots=1,
        )

        assert not tmp_store.is_opportunity_claimed(opportunity_id)

        tmp_store.create_hierarchical_reservation(
            opportunity_id=opportunity_id,
            scope=scope,
            need=need,
            state=ReservationState.HELD,
        )

        assert tmp_store.is_opportunity_claimed(opportunity_id)

    def test_is_opportunity_claimed_false_after_released(self, tmp_store: SignalStore):
        """WC-30: Released reservation is no longer "claimed"."""
        opportunity_id = "opp_test_released"
        scope = BudgetScope(
            owner="david",
            physical_account_id="acct_004",
            portfolio_id=None,
            sleeve_id=None,
            provider="provider_d",
            analyst=None,
            underlying="GOOG",
            cluster=None,
        )
        need = ResourceVector(
            cash=15000, buying_power=None, initial_margin=7500, maintenance=None,
            notional=40000, planned_risk=1500, stress_risk=None,
            close_quantity=0, slots=1,
        )

        res_id = tmp_store.create_hierarchical_reservation(
            opportunity_id=opportunity_id,
            scope=scope,
            need=need,
            state=ReservationState.HELD,
        )

        assert tmp_store.is_opportunity_claimed(opportunity_id)

        # Transition to RELEASED
        tmp_store.update_reservation_state(res_id, ReservationState.RELEASED, evidence={"event": "released"})

        # Now it should not be "claimed"
        assert not tmp_store.is_opportunity_claimed(opportunity_id)

    def test_create_duplicate_opportunity_raises_integrity_error(self, tmp_store: SignalStore):
        """WC-30: UNIQUE constraint on opportunity_id prevents duplicates."""
        opportunity_id = "opp_duplicate"
        scope = BudgetScope(
            owner="eve",
            physical_account_id="acct_005",
            portfolio_id=None,
            sleeve_id=None,
            provider="provider_e",
            analyst=None,
            underlying="NVDA",
            cluster=None,
        )
        need = ResourceVector(
            cash=25000, buying_power=None, initial_margin=12500, maintenance=None,
            notional=60000, planned_risk=2500, stress_risk=None,
            close_quantity=0, slots=1,
        )

        # First creation succeeds
        tmp_store.create_hierarchical_reservation(
            opportunity_id=opportunity_id,
            scope=scope,
            need=need,
            state=ReservationState.HELD,
        )

        # Second creation for the same opportunity_id raises IntegrityError
        with pytest.raises(sqlite3.IntegrityError):
            tmp_store.create_hierarchical_reservation(
                opportunity_id=opportunity_id,
                scope=scope,
                need=need,
                state=ReservationState.HELD,
            )


class TestBudgetLimits:
    """Tests for budget limit CRUD operations."""

    def test_set_and_get_budget_limit(self, tmp_store: SignalStore):
        """WC-30: Set and retrieve budget limits."""
        # Set limit
        tmp_store.set_budget_limit("account", "acct_001", 100000)

        # Get limit
        limit = tmp_store.get_budget_limit("account", "acct_001")
        assert limit == 100000

    def test_get_missing_budget_limit_returns_none(self, tmp_store: SignalStore):
        """WC-30: Missing limit returns None."""
        limit = tmp_store.get_budget_limit("account", "nonexistent_acct")
        assert limit is None

    def test_update_budget_limit(self, tmp_store: SignalStore):
        """WC-30: Update existing limit."""
        tmp_store.set_budget_limit("analyst", "alice_strategy", 50000)
        limit = tmp_store.get_budget_limit("analyst", "alice_strategy")
        assert limit == 50000

        tmp_store.set_budget_limit("analyst", "alice_strategy", 75000)
        limit = tmp_store.get_budget_limit("analyst", "alice_strategy")
        assert limit == 75000

    def test_delete_budget_limit(self, tmp_store: SignalStore):
        """WC-30: Delete a limit."""
        tmp_store.set_budget_limit("underlying", "AAPL", 200000)
        assert tmp_store.get_budget_limit("underlying", "AAPL") == 200000

        tmp_store.delete_budget_limit("underlying", "AAPL")
        assert tmp_store.get_budget_limit("underlying", "AAPL") is None

    def test_list_budget_limits(self, tmp_store: SignalStore):
        """WC-30: List all limits."""
        tmp_store.set_budget_limit("account", "acct_1", 100000)
        tmp_store.set_budget_limit("analyst", "alice", 50000)
        tmp_store.set_budget_limit("underlying", "TSLA", 150000)

        limits = tmp_store.list_budget_limits()
        assert len(limits) == 3
        levels_and_keys = {(l["level"], l["key"]) for l in limits}
        assert ("account", "acct_1") in levels_and_keys
        assert ("analyst", "alice") in levels_and_keys
        assert ("underlying", "TSLA") in levels_and_keys


class TestOwnerLimits:
    """Tests for owner-level limits."""

    def test_set_and_get_owner_limit(self, tmp_store: SignalStore):
        """WC-30: Set and retrieve owner limits."""
        tmp_store.set_owner_limit(
            "owner_alice",
            max_notional_cents=1000000,
            max_planned_risk_cents=50000,
            max_stress_risk_cents=100000,
        )

        limit = tmp_store.get_owner_limit("owner_alice")
        assert limit is not None
        assert limit["max_notional_cents"] == 1000000
        assert limit["max_planned_risk_cents"] == 50000
        assert limit["max_stress_risk_cents"] == 100000

    def test_get_missing_owner_limit_returns_none(self, tmp_store: SignalStore):
        """WC-30: Missing owner limit returns None."""
        limit = tmp_store.get_owner_limit("nonexistent_owner")
        assert limit is None

    def test_update_owner_limit(self, tmp_store: SignalStore):
        """WC-30: Update owner limit."""
        tmp_store.set_owner_limit("owner_bob", max_notional_cents=500000)
        limit = tmp_store.get_owner_limit("owner_bob")
        assert limit["max_notional_cents"] == 500000

        tmp_store.set_owner_limit("owner_bob", max_notional_cents=750000)
        limit = tmp_store.get_owner_limit("owner_bob")
        assert limit["max_notional_cents"] == 750000


class TestPortfolioAndSleeve:
    """Tests for portfolio and strategy sleeve management."""

    def test_create_portfolio(self, tmp_store: SignalStore):
        """WC-30: Create a portfolio."""
        tmp_store.create_portfolio("port_alice_1", "alice", name="Alice's Portfolio")
        # Verify by querying (indirectly through backings or getting a reservation)
        # or by checking the DB directly
        with tmp_store._connect() as conn:
            row = conn.execute("SELECT owner, name FROM portfolios WHERE portfolio_id = ?", ("port_alice_1",)).fetchone()
        assert row is not None
        assert row[0] == "alice"
        assert row[1] == "Alice's Portfolio"

    def test_add_portfolio_backing(self, tmp_store: SignalStore):
        """WC-30: Add backing to a portfolio."""
        tmp_store.create_portfolio("port_bob_1", "bob")
        tmp_store.add_portfolio_backing("port_bob_1", "acct_001", 500000)

        with tmp_store._connect() as conn:
            row = conn.execute(
                "SELECT dedicated_equity_cents FROM portfolio_backings WHERE portfolio_id = ? AND physical_account_id = ?",
                ("port_bob_1", "acct_001"),
            ).fetchone()
        assert row is not None
        assert row[0] == 500000

    def test_create_strategy_sleeve(self, tmp_store: SignalStore):
        """WC-30: Create a strategy sleeve."""
        tmp_store.create_portfolio("port_charlie_1", "charlie")
        tmp_store.create_strategy_sleeve(
            "sleeve_strategy_1",
            "port_charlie_1",
            "provider_a",
            analyst="charlie_strat",
            name="Charlie's Strategy",
            max_notional_cents=1000000,
        )

        with tmp_store._connect() as conn:
            row = conn.execute(
                "SELECT provider, analyst, name, max_notional_cents FROM strategy_sleeves WHERE sleeve_id = ?",
                ("sleeve_strategy_1",),
            ).fetchone()
        assert row is not None
        assert row[0] == "provider_a"
        assert row[1] == "charlie_strat"
        assert row[2] == "Charlie's Strategy"
        assert row[3] == 1000000


class TestGetLevelRemaining:
    """Tests for hierarchical budget remaining at each level."""

    def test_level_remaining_owner_with_limit(self, tmp_store: SignalStore):
        """WC-30: Owner level with configured limit."""
        owner = "owner_alice"
        tmp_store.set_owner_limit(owner, max_notional_cents=100000)

        # No reservations yet
        remaining = tmp_store.get_level_remaining("owner", {"owner": owner})
        assert remaining == 100000

        # Create a reservation needing 30000
        scope = BudgetScope(
            owner=owner,
            physical_account_id="acct_001",
            portfolio_id=None,
            sleeve_id=None,
            provider="provider_a",
            analyst=None,
            underlying="AAPL",
            cluster=None,
        )
        need = ResourceVector(
            cash=20000, buying_power=None, initial_margin=10000, maintenance=None,
            notional=100000, planned_risk=5000, stress_risk=None,
            close_quantity=0, slots=1,
        )
        tmp_store.create_hierarchical_reservation(
            opportunity_id="opp_001",
            scope=scope,
            need=need,
            state=ReservationState.HELD,
        )

        # Remaining should be 100000 - (20000 + 10000) = 70000
        remaining = tmp_store.get_level_remaining("owner", {"owner": owner})
        assert remaining == 70000

    def test_level_remaining_owner_no_limit(self, tmp_store: SignalStore):
        """WC-30: Owner with no limit configured returns UNLIMITED_CENTS."""
        remaining = tmp_store.get_level_remaining("owner", {"owner": "unknown_owner"})
        assert remaining == UNLIMITED_CENTS

    def test_level_remaining_portfolio_with_backing(self, tmp_store: SignalStore):
        """WC-30: Portfolio level limited by backing."""
        portfolio_id = "port_alice"
        tmp_store.create_portfolio(portfolio_id, "alice")
        tmp_store.add_portfolio_backing(portfolio_id, "acct_001", 50000)

        remaining = tmp_store.get_level_remaining("portfolio", {"portfolio_id": portfolio_id})
        assert remaining == 50000

        # Create reservation on this portfolio
        scope = BudgetScope(
            owner="alice",
            physical_account_id="acct_001",
            portfolio_id=portfolio_id,
            sleeve_id=None,
            provider="provider_a",
            analyst=None,
            underlying="AAPL",
            cluster=None,
        )
        need = ResourceVector(
            cash=15000, buying_power=None, initial_margin=5000, maintenance=None,
            notional=50000, planned_risk=2000, stress_risk=None,
            close_quantity=0, slots=1,
        )
        tmp_store.create_hierarchical_reservation(
            opportunity_id="opp_port_001",
            scope=scope,
            need=need,
            state=ReservationState.HELD,
        )

        # Remaining: 50000 - (15000 + 5000) = 30000
        remaining = tmp_store.get_level_remaining("portfolio", {"portfolio_id": portfolio_id})
        assert remaining == 30000

    def test_level_remaining_portfolio_no_backing(self, tmp_store: SignalStore):
        """WC-30: Portfolio with no backing has 0 remaining."""
        portfolio_id = "port_no_backing"
        tmp_store.create_portfolio(portfolio_id, "bob")

        remaining = tmp_store.get_level_remaining("portfolio", {"portfolio_id": portfolio_id})
        assert remaining == 0

    def test_level_remaining_account_with_limit(self, tmp_store: SignalStore):
        """WC-30: Account level with budget_limits."""
        account_id = "acct_limited"
        tmp_store.set_budget_limit("account", account_id, 80000)

        remaining = tmp_store.get_level_remaining("account", {"owner": "owner", "physical_account_id": account_id})
        assert remaining == 80000

    def test_level_remaining_released_not_counted(self, tmp_store: SignalStore):
        """WC-30: Released reservations don't count toward usage."""
        owner = "owner_bob"
        tmp_store.set_owner_limit(owner, max_notional_cents=100000)

        scope = BudgetScope(
            owner=owner,
            physical_account_id="acct_002",
            portfolio_id=None,
            sleeve_id=None,
            provider="provider_b",
            analyst=None,
            underlying="TSLA",
            cluster=None,
        )
        need = ResourceVector(
            cash=30000, buying_power=None, initial_margin=20000, maintenance=None,
            notional=100000, planned_risk=5000, stress_risk=None,
            close_quantity=0, slots=1,
        )

        res_id = tmp_store.create_hierarchical_reservation(
            opportunity_id="opp_release_test",
            scope=scope,
            need=need,
            state=ReservationState.HELD,
        )

        # After creation: 100000 - 50000 = 50000 remaining
        remaining = tmp_store.get_level_remaining("owner", {"owner": owner})
        assert remaining == 50000

        # Release the reservation
        tmp_store.update_reservation_state(res_id, ReservationState.RELEASED, evidence={"event": "released"})

        # After release: should be back to 100000
        remaining = tmp_store.get_level_remaining("owner", {"owner": owner})
        assert remaining == 100000


class TestBudgetBoundaryConditions:
    """Boundary tests: −1 / equal / +1 at limit thresholds."""

    def test_owner_limit_boundary_minus_one(self, tmp_store: SignalStore):
        """WC-30: Just under limit is allowed."""
        owner = "owner_boundary"
        limit_cents = 100000
        tmp_store.set_owner_limit(owner, max_notional_cents=limit_cents)

        scope = BudgetScope(
            owner=owner,
            physical_account_id="acct_bound",
            portfolio_id=None,
            sleeve_id=None,
            provider="provider",
            analyst=None,
            underlying="TEST",
            cluster=None,
        )
        need = ResourceVector(
            cash=49999, buying_power=None, initial_margin=49999, maintenance=None,
            notional=1, planned_risk=1, stress_risk=None,
            close_quantity=0, slots=1,
        )

        tmp_store.create_hierarchical_reservation(
            opportunity_id="opp_boundary_minus1",
            scope=scope,
            need=need,
            state=ReservationState.HELD,
        )

        remaining = tmp_store.get_level_remaining("owner", {"owner": owner})
        assert remaining == 2  # limit_cents - 99998 = 2

    def test_owner_limit_boundary_equal(self, tmp_store: SignalStore):
        """WC-30: Exactly at limit is allowed."""
        owner = "owner_boundary_equal"
        limit_cents = 100000
        tmp_store.set_owner_limit(owner, max_notional_cents=limit_cents)

        scope = BudgetScope(
            owner=owner,
            physical_account_id="acct_bound_eq",
            portfolio_id=None,
            sleeve_id=None,
            provider="provider",
            analyst=None,
            underlying="TEST",
            cluster=None,
        )
        need = ResourceVector(
            cash=50000, buying_power=None, initial_margin=50000, maintenance=None,
            notional=1, planned_risk=1, stress_risk=None,
            close_quantity=0, slots=1,
        )

        tmp_store.create_hierarchical_reservation(
            opportunity_id="opp_boundary_equal",
            scope=scope,
            need=need,
            state=ReservationState.HELD,
        )

        remaining = tmp_store.get_level_remaining("owner", {"owner": owner})
        assert remaining == 0

    def test_portfolio_limit_boundary_plus_one(self, tmp_store: SignalStore):
        """WC-30: Over limit is still recorded (limit check is elsewhere)."""
        portfolio_id = "port_boundary"
        tmp_store.create_portfolio(portfolio_id, "owner")
        tmp_store.add_portfolio_backing(portfolio_id, "acct", 100000)

        scope = BudgetScope(
            owner="owner",
            physical_account_id="acct",
            portfolio_id=portfolio_id,
            sleeve_id=None,
            provider="provider",
            analyst=None,
            underlying="TEST",
            cluster=None,
        )
        need = ResourceVector(
            cash=50001, buying_power=None, initial_margin=50001, maintenance=None,
            notional=1, planned_risk=1, stress_risk=None,
            close_quantity=0, slots=1,
        )

        res_id = tmp_store.create_hierarchical_reservation(
            opportunity_id="opp_boundary_plus1",
            scope=scope,
            need=need,
            state=ReservationState.HELD,
        )

        remaining = tmp_store.get_level_remaining("portfolio", {"portfolio_id": portfolio_id})
        # 100000 - (50001 + 50001) = -2, clamped to 0
        assert remaining == 0


class TestConcurrentOpportunityClaiming:
    """Concurrency test: two threads racing on the same opportunity."""

    def test_concurrent_claim_exactly_one_succeeds(self, tmp_store: SignalStore):
        """WC-30: Exactly one of two concurrent claimers gets the opportunity."""
        opportunity_id = "opp_concurrent"
        results = []
        errors = []

        def claim_opportunity(store_path: Path, thread_id: int):
            try:
                store = SignalStore(store_path)
                scope = BudgetScope(
                    owner="shared_owner",
                    physical_account_id=f"acct_{thread_id}",
                    portfolio_id=None,
                    sleeve_id=None,
                    provider=f"provider_{thread_id}",
                    analyst=None,
                    underlying="AAPL",
                    cluster=None,
                )
                need = ResourceVector(
                    cash=20000, buying_power=None, initial_margin=10000, maintenance=None,
                    notional=50000, planned_risk=2000, stress_risk=None,
                    close_quantity=0, slots=1,
                )
                res_id = store.create_hierarchical_reservation(
                    opportunity_id=opportunity_id,
                    scope=scope,
                    need=need,
                    state=ReservationState.HELD,
                )
                results.append((thread_id, res_id))
            except sqlite3.IntegrityError as e:
                errors.append((thread_id, str(e)))

        db_path = tmp_store.db_path
        thread_1 = threading.Thread(target=claim_opportunity, args=(db_path, 1))
        thread_2 = threading.Thread(target=claim_opportunity, args=(db_path, 2))

        thread_1.start()
        thread_2.start()
        thread_1.join()
        thread_2.join()

        # Exactly one should succeed, one should get IntegrityError
        assert len(results) == 1, f"Expected 1 success, got {len(results)}"
        assert len(errors) == 1, f"Expected 1 error, got {len(errors)}"

        # Verify only one row in the DB
        with tmp_store._connect() as conn:
            row_count = conn.execute(
                "SELECT COUNT(*) FROM budget_reservations WHERE opportunity_id = ?",
                (opportunity_id,),
            ).fetchone()
        assert row_count[0] == 1


class TestHierarchicalBudgetIntegration:
    """Integration tests with HierarchicalBudget using SignalStore."""

    def test_check_and_reserve_with_owner_limit(self, tmp_store: SignalStore):
        """WC-30: HierarchicalBudget.check_and_reserve respects owner limit."""
        owner = "integration_owner"
        tmp_store.set_owner_limit(owner, max_notional_cents=100000)

        budget = HierarchicalBudget(tmp_store)
        scope = BudgetScope(
            owner=owner,
            physical_account_id="acct_integ",
            portfolio_id=None,
            sleeve_id=None,
            provider="provider_integ",
            analyst=None,
            underlying="AAPL",
            cluster=None,
        )
        need = ResourceVector(
            cash=30000, buying_power=None, initial_margin=20000, maintenance=None,
            notional=100000, planned_risk=5000, stress_risk=None,
            close_quantity=0, slots=1,
        )

        # First call should succeed
        result = budget.check_and_reserve(
            opportunity_id="opp_integ_1",
            scope=scope,
            need=need,
            snapshot=None,
        )
        assert result.ok is True
        assert result.reservation_id is not None
        assert result.reason is None

    def test_check_and_reserve_duplicate_maps_to_reason_duplicate(self, tmp_store: SignalStore):
        """WC-30: Duplicate opportunity_id is caught and mapped to Reason.DUPLICATE."""
        owner = "dup_owner"
        tmp_store.set_owner_limit(owner, max_notional_cents=200000)

        budget = HierarchicalBudget(tmp_store)
        scope = BudgetScope(
            owner=owner,
            physical_account_id="acct_dup",
            portfolio_id=None,
            sleeve_id=None,
            provider="provider_dup",
            analyst=None,
            underlying="TSLA",
            cluster=None,
        )
        need = ResourceVector(
            cash=30000, buying_power=None, initial_margin=20000, maintenance=None,
            notional=100000, planned_risk=5000, stress_risk=None,
            close_quantity=0, slots=1,
        )

        # First call succeeds
        result1 = budget.check_and_reserve(
            opportunity_id="opp_dup",
            scope=scope,
            need=need,
            snapshot=None,
        )
        assert result1.ok is True

        # Second call for same opportunity
        result2 = budget.check_and_reserve(
            opportunity_id="opp_dup",
            scope=scope,
            need=need,
            snapshot=None,
        )
        assert result2.ok is False
        assert result2.reason == Reason.DUPLICATE
        assert result2.binding_level == "opportunity"
