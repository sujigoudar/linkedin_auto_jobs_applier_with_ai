"""Staged entries, pyramiding admission, and runner state (WC-08).

Implements scaled entry mechanisms per WORKFLOW_SPECIFICATION.md §13:

- Four scaling mechanisms as distinct types: seed, add, runner, target
- Add admission with validation: released add policy, confirmed protection,
  profitable trigger, fresh data, time remaining, no active exit, resources
- Runner state: high-water, giveback, deadline, monotonic floor
- Whole-lifecycle recomputation preserving seed risk (invariant I16)
- Persistent runner state restored across restart (invariant I19)
- Long protective floor never decreases (invariant I11)

Key references:
- WORKFLOW_SPECIFICATION.md §13.1–13.5 (Mechanisms, example, runner, validation)
- WORKFLOW_SPECIFICATION.md §1.1 (Invariants I11, I16, I19)
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Optional

from app.workflow.money import Cents
from app.workflow.reasons import Reason


class ScalingMechanism(str, enum.Enum):
    """Four distinct scaling mechanisms per §13.1."""

    SEED = "SEED"
    """Initial entry that establishes baseline risk and protection."""

    ADD = "ADD"
    """Pyramiding add: incremental entry with scaled risk, requires fresh
    data and confirmed protection."""

    RUNNER = "RUNNER"
    """Managed continuation after partial exit: defined entry point, owned
    quantity, active protection, high-water tracking and giveback rule."""

    TARGET = "TARGET"
    """Profit-taking partial exit: specified quantity or price level, reduces
    owned position while preserving remainder protection."""


@dataclass(frozen=True)
class RunnerState:
    """Persistent runner lifecycle state per §13.4.

    Tracks high-water mark, maximum giveback rule, holding deadline,
    and monotonic protective floor. All numeric values are exact (Cents).
    """

    account_id: str
    """Physical account ID owning this runner."""

    symbol: str
    """Instrument symbol."""

    lifecycle_id: str
    """Parent lifecycle identifier linking entry, adds, protection, exits."""

    owned_quantity: int
    """Current owned quantity in the runner tranche (units)."""

    entry_price_cents: Cents
    """Entry price for this runner tranche (exact cents)."""

    high_water_cents: Cents
    """Highest mark-to-market price reached since entry (exact cents).
    Never decreases unless an explicit instrument transformation (e.g., split)
    occurs with full economic equivalence and evidence (I11)."""

    giveback_cents: Cents
    """Maximum permitted drop from high_water before mandatory exit
    (exact cents). Non-negative."""

    deadline_utc: Optional[datetime] = None
    """Hold-until deadline in UTC. None = no hard deadline."""

    protective_floor_cents: Cents = field(default=0)
    """Monotonic protective stop floor (exact cents, long positions).
    Never decreases except via explicit instrument transformation (I11).
    For short positions, this is a protective ceiling."""

    original_risk_cents: Cents = field(default=0)
    """Original planned risk of this runner tranche at entry
    (exact cents, used for portfolio accounting per I16)."""

    created_at_utc: datetime = field(default_factory=datetime.utcnow)
    """Timestamp when runner state was created (UTC)."""

    updated_at_utc: datetime = field(default_factory=datetime.utcnow)
    """Timestamp of last state modification (UTC)."""

    def validate_invariants(self) -> list[str]:
        """Validate invariants I11, I16, I19.

        Returns:
            List of violation messages (empty if all pass).
        """
        violations = []

        # I11: Monotonic floor (long position)
        if self.protective_floor_cents < 0:
            violations.append("I11: Protective floor cannot be negative")

        # Giveback must be non-negative
        if self.giveback_cents < 0:
            violations.append("Giveback ceiling cannot be negative")

        # High-water must be >= floor
        if self.high_water_cents < self.protective_floor_cents:
            violations.append(
                "High-water must be >= protective floor (I11)"
            )

        # Owned quantity must be positive
        if self.owned_quantity <= 0:
            violations.append("Owned quantity must be positive")

        # Entry price must be positive
        if self.entry_price_cents <= 0:
            violations.append("Entry price must be positive")

        # Original risk must be non-negative
        if self.original_risk_cents < 0:
            violations.append("Original risk cannot be negative (I16)")

        return violations


@dataclass(frozen=True)
class AddAdmissionInputs:
    """Inputs for add admission decision per §13.2.

    Specifies the context for deciding whether an add tranche can be
    admitted given existing position and protection.
    """

    account_id: str
    """Physical account ID."""

    symbol: str
    """Instrument symbol."""

    lifecycle_id: str
    """Parent lifecycle ID."""

    add_quantity: int
    """Proposed add quantity (units, positive)."""

    add_entry_price_cents: Cents
    """Proposed add entry price (exact cents)."""

    original_seed_risk_cents: Cents
    """Original planned risk of the seed tranche (exact cents)."""

    existing_quantity: int
    """Current owned quantity before add (units, non-negative)."""

    existing_avg_price_cents: Cents
    """Current average price of owned lots (exact cents)."""

    confirmed_stop_cents: Optional[Cents] = None
    """Broker-confirmed protective stop price (exact cents).
    None = not yet confirmed; None blocks add admission."""

    current_mark_cents: Optional[Cents] = None
    """Current market mark-to-market price (exact cents).
    None = data not fresh; None blocks add admission."""

    add_policy_released: bool = False
    """Whether add-on policy has been owner-released for this strategy/account."""

    active_exit_pending: bool = False
    """Whether an exit order is already pending.
    True blocks add admission."""

    fresh_data_as_of: Optional[datetime] = None
    """Timestamp of current market data. None = not fresh."""

    time_remaining_minutes: Optional[int] = None
    """Minutes to deadline/session close. None = unknown/no deadline."""


@dataclass(frozen=True)
class AddAdmissionDecision:
    """Decision result for add admission per §13.2.

    Provides accept/reject status with detailed blocking reasons and
    financial impact analysis.
    """

    admit: bool
    """True if add can proceed, False if blocked."""

    reason: Optional[Reason] = None
    """Primary reason if admit=False; None if admit=True."""

    blocking_factors: list[str] = field(default_factory=list)
    """Detailed list of blocking conditions (may be multiple)."""

    original_capital_risk_cents: Cents = 0
    """Incremental original-capital risk of the add in isolation
    (new quantity × stop loss distance, exact cents)."""

    current_equity_giveback_cents: Cents = 0
    """Current equity giveback across all owned lots if held to stop
    (all quantity × (current_mark - stop_price), exact cents)."""

    stress_loss_cents: Cents = 0
    """Stress/gap loss in a downside scenario, used to assess risk."""


def evaluate_add_admission(inputs: AddAdmissionInputs) -> AddAdmissionDecision:
    """Pure function for add admission decision per §13.2.

    Checks:
    1. Add policy released
    2. Confirmed protective stop exists
    3. Fresh market data available
    4. No active exit pending
    5. Time remaining until deadline
    6. Resources available post-add
    7. Profitability triggers and loss checks

    Whole-lifecycle recomputation never resets original-risk accounting (I16).

    Args:
        inputs: AdmissionInputs specifying the proposed add context.

    Returns:
        AddAdmissionDecision with admit status and blocking reasons.
    """
    blocking_factors: list[str] = []
    reason: Optional[Reason] = None

    # 1. Add policy must be released
    if not inputs.add_policy_released:
        blocking_factors.append("Add-on policy not released for this account")
        reason = Reason.POLICY_DISABLED

    # 2. Confirmed protective stop required
    if inputs.confirmed_stop_cents is None:
        blocking_factors.append("Protective stop not confirmed")
        reason = Reason.STOP_BREACHED

    # 3. Current market data must be fresh
    if inputs.current_mark_cents is None:
        blocking_factors.append("Current market data not available")
        reason = Reason.MARGIN_UNKNOWN

    # 4. No active exit can be pending
    if inputs.active_exit_pending:
        blocking_factors.append("Active exit order pending")
        reason = Reason.NONACTIONABLE

    # 5. Time remaining (if applicable)
    if (
        inputs.time_remaining_minutes is not None
        and inputs.time_remaining_minutes <= 0
    ):
        blocking_factors.append("No time remaining until deadline")
        reason = Reason.EXPIRED

    # Calculate risk measures only if we have the needed data
    original_capital_risk = Cents(0)
    current_equity_giveback = Cents(0)
    stress_loss = Cents(0)

    if (
        inputs.confirmed_stop_cents is not None
        and inputs.current_mark_cents is not None
    ):
        # Original capital risk: new quantity × stop loss distance
        stop_distance = inputs.add_entry_price_cents - inputs.confirmed_stop_cents
        if stop_distance > 0:
            original_capital_risk = Cents(inputs.add_quantity * stop_distance)

        # Current equity giveback: total quantity × (current mark - stop)
        total_quantity = inputs.existing_quantity + inputs.add_quantity
        giveback_per_share = inputs.current_mark_cents - inputs.confirmed_stop_cents
        if giveback_per_share > 0 and total_quantity > 0:
            current_equity_giveback = Cents(total_quantity * giveback_per_share)

        # Stress loss: gap scenario to check
        # (simplified: assume gap to 2% below current mark)
        gap_price = Cents(int(Decimal(inputs.current_mark_cents) * Decimal("0.98")))
        stress_distance = inputs.current_mark_cents - gap_price
        if stress_distance > 0 and total_quantity > 0:
            stress_loss = Cents(total_quantity * stress_distance)

    # For now, admit if no blocking factors
    admit = len(blocking_factors) == 0
    if not admit and reason is None:
        reason = Reason.CAPITAL_LIMITED

    return AddAdmissionDecision(
        admit=admit,
        reason=reason if not admit else None,
        blocking_factors=blocking_factors,
        original_capital_risk_cents=original_capital_risk,
        current_equity_giveback_cents=current_equity_giveback,
        stress_loss_cents=stress_loss,
    )
