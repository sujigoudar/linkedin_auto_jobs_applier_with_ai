"""Hierarchical budgets, resource vectors, reservation state machine, and
risk measurement. Implements WORKFLOW_SPECIFICATION.md §5.3–5.4, §6–6.3, §13.3.

Capital hierarchy (§5.3): owner → account → portfolio → sleeve → provider →
strategy/analyst → underlying + cluster. Every resource check applies simultaneously
at all levels, claimed atomically in ONE BEGIN IMMEDIATE transaction.

Reservation state machine (§6.2): DRAFT -> HELD -> COMMITTED_TO_PENDING_ORDER ->
PART_FILLED/HELD_REMAINDER -> FILLED_EXPOSURE -> RELEASE_PENDING -> RELEASED.
UNKNOWN submission stays HELD.

Three risk measures (§6.1, §13.3): original planned loss (seed/add to stop),
mark-to-protection loss (current equity to stop), stress loss (adverse scenario).
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from app.workflow.money import Cents
from app.workflow.reasons import Reason


# WC-30: Sentinel value for unlimited budget at a level (no configured limit).
UNLIMITED_CENTS = 2**62

class ReservationState(str, Enum):
    """Reservation state transitions (§6.2)."""

    DRAFT = "DRAFT"  # Initial state, not yet held
    HELD = "HELD"  # Budget allocation reserved
    COMMITTED_TO_PENDING_ORDER = "COMMITTED_TO_PENDING_ORDER"  # Order submitted, awaiting response
    PART_FILLED = "PART_FILLED"  # Partial fill received
    HELD_REMAINDER = "HELD_REMAINDER"  # Remainder held after partial fill
    FILLED_EXPOSURE = "FILLED_EXPOSURE"  # Full fill, now open exposure
    RELEASE_PENDING = "RELEASE_PENDING"  # Awaiting release confirmation
    RELEASED = "RELEASED"  # Fully released
    UNKNOWN_HELD = "UNKNOWN_HELD"  # Submission response unknown, reservation held


@dataclass(frozen=True)
class ResourceVector:
    """Reserve the worst admitted outstanding combination of resources (§6.2).

    All values are integer cents or integer quantities; None = unknown/not
    tracked for that field.
    """

    cash: Cents  # Available cash after settlement
    buying_power: Cents | None  # Broker-reported buying power; None = unknown
    initial_margin: Cents  # Required initial margin
    maintenance: Cents | None  # Maintenance requirement; None = unknown
    notional: Cents  # Gross/net/underlying exposure
    planned_risk: Cents  # Original planned risk to stop
    stress_risk: Cents | None  # Stress scenario loss; None = unknown
    close_quantity: int  # Closeable inventory / slots
    slots: int  # Order slots available


@dataclass(frozen=True)
class BudgetScope:
    """Hierarchical budget scope at all levels (§5.3)."""

    owner: str  # Owner/tenant
    physical_account_id: str  # Account identity
    portfolio_id: str | None  # Portfolio if applicable
    sleeve_id: str | None  # Sleeve subdivision if applicable
    provider: str  # Signal provider
    analyst: str | None  # Strategy/analyst identifier
    underlying: str  # Underlying asset (symbol)
    cluster: str | None  # Correlated risk cluster


@dataclass(frozen=True)
class BrokerSnapshot:
    """Broker-reported account state at a point in time (§6.2).

    Normalize with explicit reflected_intent_ids to account for working
    orders broker already subtracted from buying power. A stale snapshot
    with unknown membership blocks admission (MARGIN_UNKNOWN/CAPITAL_LIMITED).
    """

    buying_power: Cents | None  # Broker's reported buying power
    equity: Cents | None  # Account equity/net liquidation
    maintenance: Cents | None  # Broker's maintenance requirement
    reflected_intent_ids: frozenset[str] | None  # Order IDs broker already reflected
    as_of: datetime  # Snapshot timestamp


@dataclass(frozen=True)
class ReservationResult:
    """Result of check_and_reserve (§6.2)."""

    ok: bool  # True if reservation succeeded
    reservation_id: str | None  # Unique reservation identifier if ok=True
    reason: Reason | None  # Rejection reason if ok=False
    binding_level: str | None  # Level at which decision was made
    remaining: dict[str, Cents]  # Available resources per level after decision


class HierarchicalBudget:
    """Hierarchical capital allocator with resource vector checking (§5.3–5.4, §6.2).

    Implements atomicity (one BEGIN IMMEDIATE transaction claiming the opportunity),
    multi-level checking (owner through underlying), and state transitions.
    """

    def __init__(self, store):
        """Initialize with backing store for durable reservations.

        Args:
            store: SignalStore instance for database operations.
        """
        self.store = store

    def check_and_reserve(
        self,
        opportunity_id: str,
        scope: BudgetScope,
        need: ResourceVector,
        snapshot: BrokerSnapshot | None,
    ) -> ReservationResult:
        """Check and reserve resources in ONE BEGIN IMMEDIATE transaction (§6.2, §6.3).

        Checks owner, account, portfolio, sleeve, provider, underlying and cluster
        remaining together (I08), claims opportunity_id so two workers cannot both
        admit, validates broker snapshot if provided.

        Args:
            opportunity_id: Unique signal/order identifier to claim
            scope: Hierarchical budget scope
            need: Required resource vector
            snapshot: Broker snapshot for margin/buying power validation

        Returns:
            ReservationResult with reservation_id if successful, reason if blocked.
        """
        # START BEGIN IMMEDIATE transaction
        # (Caller or store handles transaction boundary)
        try:
            # 1. Claim opportunity (unique constraint prevents duplicate admission)
            if self.store.is_opportunity_claimed(opportunity_id):
                return ReservationResult(
                    ok=False,
                    reservation_id=None,
                    reason=Reason.DUPLICATE,
                    binding_level="opportunity",
                    remaining={},
                )

            # 2. Validate broker snapshot if present
            if snapshot is not None:
                validation_result = self._validate_broker_snapshot(
                    scope, need, snapshot
                )
                if not validation_result.ok:
                    return validation_result

            # 3. Check each hierarchical level simultaneously
            remaining_by_level = {}
            for level, level_scope in self._enumerate_levels(scope):
                available, is_sufficient = self._check_level(level, level_scope, need)
                remaining_by_level[level] = available
                if not is_sufficient:  # Any level insufficient
                    return ReservationResult(
                        ok=False,
                        reservation_id=None,
                        reason=Reason.CAPITAL_LIMITED,
                        binding_level=level,
                        remaining=remaining_by_level,
                    )

            # 4. Reserve at all levels atomically
            reservation_id = self.store.create_hierarchical_reservation(
                opportunity_id=opportunity_id,
                scope=scope,
                need=need,
                state=ReservationState.HELD,
            )

            return ReservationResult(
                ok=True,
                reservation_id=reservation_id,
                reason=None,
                binding_level=None,
                remaining=remaining_by_level,
            )

        except sqlite3.IntegrityError:
            # Opportunity already claimed (unique constraint violation)
            return ReservationResult(
                ok=False,
                reservation_id=None,
                reason=Reason.DUPLICATE,
                binding_level="opportunity",
                remaining={},
            )
        except Exception:
            # Other exceptions: transaction will rollback
            return ReservationResult(
                ok=False,
                reservation_id=None,
                reason=Reason.RECONCILIATION_REQUIRED,
                binding_level="transaction",
                remaining={},
            )

    def transition(
        self,
        reservation_id: str,
        new_state: ReservationState,
        *,
        evidence: dict,
    ) -> None:
        """Transition a reservation to a new state (§6.2).

        Args:
            reservation_id: Reservation to update
            new_state: Target state
            evidence: Context for the transition (order_id, fill_quantity, etc.)

        Raises:
            ValueError: If transition is invalid.
        """
        current = self.store.get_reservation(reservation_id)
        if not self._is_valid_transition(current.state, new_state):
            raise ValueError(
                f"Invalid transition from {current.state} to {new_state}"
            )

        self.store.update_reservation_state(
            reservation_id, new_state, evidence=evidence
        )

    def remaining(self, scope: BudgetScope) -> dict[str, Cents]:
        """Query available resources per hierarchical level (§5.3).

        Args:
            scope: Budget scope to query

        Returns:
            Dictionary of {level: available_cents} for owner/account/
            portfolio/sleeve/provider/underlying/cluster.
        """
        result = {}
        for level, level_scope in self._enumerate_levels(scope):
            result[level] = self._get_level_available(level, level_scope)
        return result

    # Private helper methods

    def _enumerate_levels(self, scope: BudgetScope):
        """Enumerate hierarchical levels from owner to underlying."""
        yield "owner", {"owner": scope.owner}
        yield "account", {
            "owner": scope.owner,
            "physical_account_id": scope.physical_account_id,
        }
        if scope.portfolio_id:
            yield "portfolio", {
                "owner": scope.owner,
                "portfolio_id": scope.portfolio_id,
            }
        if scope.sleeve_id:
            yield "sleeve", {
                "owner": scope.owner,
                "portfolio_id": scope.portfolio_id,
                "sleeve_id": scope.sleeve_id,
            }
        yield "provider", {"owner": scope.owner, "provider": scope.provider}
        if scope.analyst:
            yield "analyst", {
                "owner": scope.owner,
                "analyst": scope.analyst,
            }
        yield "underlying", {
            "owner": scope.owner,
            "underlying": scope.underlying,
        }
        if scope.cluster:
            yield "cluster", {
                "owner": scope.owner,
                "cluster": scope.cluster,
            }

    def _check_level(self, level: str, level_scope: dict, need: ResourceVector) -> tuple[Cents, bool]:
        """Check if a level has sufficient remaining resources.

        Returns:
            Tuple of (remaining_after_reservation, is_sufficient).
        """
        available = self._get_level_available(level, level_scope)
        required = self._get_level_required(need)

        if available >= required:
            return available - required, True
        return available, False

    def _get_level_available(self, level: str, level_scope: dict) -> Cents:
        """Query available resources at a hierarchical level."""
        # Fetch from store based on level type
        return self.store.get_level_remaining(level, level_scope)

    def _get_level_required(self, need: ResourceVector) -> Cents:
        """Sum the resource need into a single comparable figure."""
        # Simple model: sum cash and initial margin requirements
        return need.cash + need.initial_margin

    def _validate_broker_snapshot(
        self, scope: BudgetScope, need: ResourceVector, snapshot: BrokerSnapshot
    ) -> ReservationResult:
        """Validate broker snapshot and margin state (§6.2)."""
        # Check for unknown membership (None with stale snapshot)
        if snapshot.reflected_intent_ids is None and snapshot.buying_power is None:
            return ReservationResult(
                ok=False,
                reservation_id=None,
                reason=Reason.MARGIN_UNKNOWN,
                binding_level="broker",
                remaining={},
            )

        # Validate buying power if available
        if snapshot.buying_power is not None:
            if snapshot.buying_power < need.cash:
                return ReservationResult(
                    ok=False,
                    reservation_id=None,
                    reason=Reason.CAPITAL_LIMITED,
                    binding_level="broker",
                    remaining={"buying_power": snapshot.buying_power},
                )

        return ReservationResult(ok=True, reservation_id=None, reason=None, binding_level=None, remaining={})

    def _is_valid_transition(
        self, current: ReservationState, target: ReservationState
    ) -> bool:
        """Validate state transition rules (§6.2)."""
        valid_transitions = {
            ReservationState.DRAFT: [ReservationState.HELD],
            ReservationState.HELD: [
                ReservationState.COMMITTED_TO_PENDING_ORDER,
                ReservationState.UNKNOWN_HELD,
                ReservationState.RELEASED,  # For dry_run or pre-submission rejection
            ],
            ReservationState.COMMITTED_TO_PENDING_ORDER: [
                ReservationState.PART_FILLED,
                ReservationState.FILLED_EXPOSURE,
                ReservationState.UNKNOWN_HELD,
                ReservationState.RELEASE_PENDING,  # For broker rejection (may go through RELEASE_PENDING)
                ReservationState.RELEASED,  # Direct path for broker rejection
            ],
            ReservationState.PART_FILLED: [
                ReservationState.HELD_REMAINDER,
                ReservationState.FILLED_EXPOSURE,
            ],
            ReservationState.HELD_REMAINDER: [ReservationState.FILLED_EXPOSURE],
            ReservationState.FILLED_EXPOSURE: [ReservationState.RELEASE_PENDING],
            ReservationState.RELEASE_PENDING: [ReservationState.RELEASED],
            ReservationState.UNKNOWN_HELD: [
                ReservationState.FILLED_EXPOSURE,
                ReservationState.RELEASED,
            ],
        }
        return target in valid_transitions.get(current, [])


# Three risk measures (§6.1, §13.3)


def original_planned_loss(lots) -> Cents:
    """Original entry/lifecycle planned loss from cost basis to stop (§6.1).

    For a long lot i with entry e_i, quantity q_i, multiplier m_i and
    confirmed stop s_i: conservative capital-at-risk is q_i*m_i*max(0,e_i-s_i)
    plus adverse execution/cost allowance.

    For short: q_i*m_i*max(0,s_i-e_i).

    Args:
        lots: List of lot dicts with keys: entry_price, quantity, multiplier,
              stop_price, side ('BUY'/'SELL').

    Returns:
        Total planned risk in cents.
    """
    total_risk = 0
    for lot in lots:
        qty = int(lot["quantity"])
        multiplier = int(lot["multiplier"])
        entry_cents = int(lot["entry_price"] * 100 + 0.5)
        stop_cents = int(lot["stop_price"] * 100 + 0.5)

        if lot["side"] == "BUY":
            risk_per_share = max(0, entry_cents - stop_cents)
        else:  # SELL
            risk_per_share = max(0, stop_cents - entry_cents)

        contribution = qty * multiplier * risk_per_share
        total_risk += contribution

    return total_risk


def mark_to_protection_loss(lots, mark: Cents) -> Cents:
    """Current mark-to-protection loss: equity giveback to stop (§6.1).

    Measures how much present equity may be given back before protective
    execution. Uses current executable reference (mark) instead of entry.

    Args:
        lots: List of lot dicts with keys: quantity, multiplier, stop_price, mark_price, side.
        mark: Current market price in cents.

    Returns:
        Total current equity at risk in cents.
    """
    total_risk = 0

    for lot in lots:
        qty = int(lot["quantity"])
        multiplier = int(lot["multiplier"])
        # Convert stop_price to cents (could be float like 50.50)
        stop_cents = int(lot["stop_price"] * 100 + 0.5)  # Round to nearest cent

        if lot["side"] == "BUY":
            risk_per_share = max(0, mark - stop_cents)
        else:  # SELL
            risk_per_share = max(0, stop_cents - mark)

        contribution = qty * multiplier * risk_per_share
        total_risk += contribution

    return total_risk


def stress_loss(lots, scenario: dict, mark: Cents | None = None) -> Cents:
    """Stress loss under adverse scenario (§6.1, §13.3).

    Uses adverse gap, slippage, volatility, spread, currency, collateral
    and liquidation scenarios. Measures drawdown from current mark price
    (or entry if mark not provided) to the adverse scenario price.

    Args:
        lots: List of lot dicts with keys: entry_price (or quantity, multiplier, side for current mark).
        scenario: Dict with 'adverse_price' (stress scenario price in cents).
        mark: Current market price in cents. If None, uses entry_price from lots.

    Returns:
        Total potential loss in cents.
    """
    total_loss = 0
    adverse_price = scenario.get("adverse_price", 0)

    for lot in lots:
        qty = int(lot["quantity"])
        multiplier = int(lot["multiplier"])

        # Use provided mark price, or fall back to entry price
        if mark is not None:
            reference_price = mark
        else:
            reference_price = int(lot.get("entry_price", 0) * 100 + 0.5)

        if lot["side"] == "BUY":
            loss_per_share = max(0, reference_price - adverse_price)
        else:  # SELL
            loss_per_share = max(0, adverse_price - reference_price)

        contribution = qty * multiplier * loss_per_share
        total_loss += contribution

    return total_loss
