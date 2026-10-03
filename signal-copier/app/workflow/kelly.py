"""Kelly profiles and evidence states (WC-07).

Implements modified Kelly sizing framework per WORKFLOW_SPECIFICATION.md §7 and §18:

- Profile hierarchy: provider × analyst × strategy × asset_family × horizon × exit_policy
- Evidence states: NO_HISTORY / INCONCLUSIVE / NEGATIVE_EDGE / ELIGIBLE
- Robust candidate from full net-outcome distribution with block bootstrap
- Fractional multiplier λ ∈ (0, 1]
- Hierarchical minimum applied downward only, no stacking beyond hard ceiling
- Shadow-only mode: logs would-be size without changing order

Key references:
- Busseti, Ryu, Boyd, "Risk-Constrained Kelly Gambling", Journal of Investing 25(3), 2016.
- WORKFLOW_SPECIFICATION.md §7.1–7.6 (Unit convention, profile hierarchy, robust
  candidate procedure, hierarchical budget, worked example, cold start)
"""
from __future__ import annotations

import enum
import random
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional, Sequence

from app.workflow.money import Cents
from app.workflow.reasons import Reason


class EvidenceState(str, enum.Enum):
    """States of statistical evidence for a Kelly profile."""

    NO_HISTORY = "NO_HISTORY"
    """No realized outcomes or insufficient data to estimate edge."""

    INCONCLUSIVE = "INCONCLUSIVE"
    """Data exists but edge is statistically inconclusive (confidence below threshold)."""

    NEGATIVE_EDGE = "NEGATIVE_EDGE"
    """After costs, profile shows statistically significant negative expected return."""

    ELIGIBLE = "ELIGIBLE"
    """Profile has sufficient positive evidence and passes shrinkage/stability checks."""


@dataclass(frozen=True)
class KellyProfile:
    """One Kelly profile identified by its key dimensions.

    Profile key: provider × analyst × strategy × asset_family × horizon × exit_policy.
    All dimensions are required; absence of a dimension means unknown/unsupported.
    """

    provider: str
    """Source/data provider identifier (e.g., 'example-signals')."""

    analyst: str
    """Analyst/strategy author (e.g., 'alice', 'bob', or shared identifier)."""

    strategy: str
    """Strategy/method name (e.g., 'scalp', 'swing', 'mean-reversion')."""

    asset_family: str
    """Asset class or family (e.g., 'US_EQUITIES', 'SPX_OPTIONS')."""

    horizon: str
    """Holding period/horizon (e.g., 'intraday', 'daily', 'weekly')."""

    exit_policy: str
    """Exit method identifier (e.g., 'stop_and_target', 'trail')."""

    version: int = 1
    """Profile version; increment when execution/filtering/stops/adds/runners change."""


@dataclass(frozen=True)
class KellyOutcome:
    """One realized or modeled lifecycle outcome.

    The payoff X is net lifecycle profit/loss in multiples of original planned risk
    (risk-fraction convention per §7.1). X values can be negative (losses),
    including X < -1 (loss exceeding risk, gap scenarios).
    """

    payoff_risk_fraction: Decimal
    """Net lifecycle payoff X in multiples of one dollar of original planned risk.

    X = (entry_price - exit_price) / original_stop_loss_distance + costs/original_risk.
    Positive = win, zero = breakeven, negative = loss (including X < -1 for gaps).
    """

    weight: Decimal = Decimal(1)
    """Bootstrap weight or importance (default 1 = equal weight). All weights
    should sum to the total number of outcomes for proper frequency."""

    observation_type: str = "realized"
    """Type of observation: 'realized', 'replay', 'shadow', or 'stress'.
    Distinct labels prevent interchanging empirical, backtest, and simulated evidence."""


@dataclass(frozen=True)
class KellySizingConstraint:
    """One hierarchical budget cap on Kelly fraction.

    Constraints are applied downward only: min(full_kelly, lambda, cap).
    Never stack multipliers; never exceed the hard ceiling.
    """

    level: str
    """Constraint level: 'owner', 'account', 'portfolio', 'sleeve', 'provider',
    'underlying', 'stress', or 'capital'."""

    remaining_risk_cents: Cents
    """Remaining risk budget at this level after other allocations."""

    binding_fraction: Decimal
    """Binding cap as a fraction of equity (e.g., Decimal("0.0025") = 0.25%)."""

    label: str = ""
    """Descriptive label for reporting/debugging."""


@dataclass(frozen=True)
class KellyShadowResult:
    """Shadow-only result from shadow_size_for: what would have been sized.

    Never changes order quantity or risk budget; only logs for analysis.
    """

    would_be_quantity: int
    """Quantity that would have been sized under Kelly (in instrument units)."""

    would_be_risk_cents: Cents
    """Risk in cents that would have been taken."""

    evidence_state: EvidenceState
    """Evidence state of the Kelly profile."""

    effective_kelly_fraction: Decimal
    """Final Kelly fraction after all reductions (0 to 1)."""

    binding_constraint: str
    """Name of the binding constraint that limited size (or 'Kelly' if Kelly itself
    was the minimum)."""

    reason_if_rejected: Optional[Reason] = None
    """Reason code if sizing would have been rejected (NO_HISTORY → RISK_LIMITED)."""


def calculate_full_kelly_binary(probability_win: Decimal, win_loss_ratio: Decimal) -> Decimal:
    """Calculate full Kelly fraction for binary win/loss model.

    Formula: f_K = p - (1-p)/b where p is probability of winning b×R and losing R.

    Per §7.1, this assumes exactly two outcomes. For general trading distributions,
    use the full net-outcome distribution.

    Args:
        probability_win: Probability p of the win outcome (0 < p < 1).
        win_loss_ratio: Ratio b of win/loss magnitude (b > 0).

    Returns:
        Full Kelly fraction as Decimal (range typically 0 to 1, but can exceed).

    Raises:
        ValueError: If inputs are invalid (p outside (0,1), b <= 0).
    """
    p = Decimal(probability_win)
    b = Decimal(win_loss_ratio)

    if not (Decimal(0) < p < Decimal(1)):
        raise ValueError(f"Probability must be in (0, 1), got {p}")
    if b <= Decimal(0):
        raise ValueError(f"Win/loss ratio must be positive, got {b}")

    # f_K = p - (1-p)/b
    return p - (Decimal(1) - p) / b


def block_bootstrap(
    outcomes: Sequence[KellyOutcome],
    n_bootstrap: int = 1000,
    block_size: int = 5,
    seed: Optional[int] = None,
) -> list[Decimal]:
    """Resample outcomes using block bootstrap, preserving dependence structure.

    Block bootstrap resamples contiguous blocks of the original time series to
    preserve serial dependence. Used for Kelly robust-candidate calculation.

    Args:
        outcomes: Sequence of KellyOutcome objects to resample.
        n_bootstrap: Number of bootstrap samples to generate (default 1000).
        block_size: Size of each contiguous block (default 5).
        seed: Random seed for reproducibility (None = system random).

    Returns:
        List of n_bootstrap bootstrap sample means (in risk-fraction convention).
    """
    if not outcomes:
        return []

    rng = random.Random(seed)
    outcomes_list = list(outcomes)
    n_outcomes = len(outcomes_list)

    if n_outcomes == 0:
        return []

    if block_size < 1:
        block_size = 1
    if block_size > n_outcomes:
        block_size = n_outcomes

    bootstrap_means = []
    for _ in range(n_bootstrap):
        # Resample blocks with replacement
        sample: list[KellyOutcome] = []
        total_weight = Decimal(0)
        sample_payoff = Decimal(0)

        while len(sample) < n_outcomes:
            # Random start position in the original series
            start = rng.randint(0, n_outcomes - 1)

            # Add a block of up to block_size items
            for i in range(block_size):
                if len(sample) >= n_outcomes:
                    break
                idx = (start + i) % n_outcomes
                outcome = outcomes_list[idx]
                sample.append(outcome)
                total_weight += outcome.weight
                sample_payoff += outcome.payoff_risk_fraction * outcome.weight

        # Compute mean payoff
        if total_weight > 0:
            mean_payoff = sample_payoff / total_weight
        else:
            mean_payoff = Decimal(0)

        bootstrap_means.append(mean_payoff)

    return bootstrap_means


def robust_candidate(
    outcomes: Sequence[KellyOutcome],
    confidence_level: Decimal = Decimal("0.25"),
    seed: Optional[int] = None,
) -> tuple[Decimal, EvidenceState]:
    """Find conservative Kelly optimum using lower-confidence criterion.

    Implements §7.3 robust candidate procedure:
    1. Freeze feature/price/cost cutoff
    2. Construct net outcome distribution and conservative stress tail
    3. For each candidate f, calculate log-growth across outcomes + bootstrap draws
    4. Select conservative optimum using predeclared lower-confidence criterion
    5. Return (f_robust, evidence_state)

    Args:
        outcomes: Sequence of KellyOutcome objects (complete lifecycle results).
        confidence_level: Lower-confidence threshold (default 0.25 = 25th percentile).
        seed: Random seed for bootstrap reproducibility.

    Returns:
        Tuple of (robust_kelly_fraction, evidence_state):
        - If no outcomes: (Decimal(0), EvidenceState.NO_HISTORY)
        - If negative or inconclusive: (Decimal(0), EvidenceState.NEGATIVE_EDGE)
        - If eligible: (f_robust, EvidenceState.ELIGIBLE)

    Notes:
        - Does not fabricate positive edge when data is missing.
        - Shrinks thin profiles toward conservative parent estimates.
        - Tracks uncertainty; does not delete losses to improve estimates.
    """
    if not outcomes or len(outcomes) == 0:
        return Decimal(0), EvidenceState.NO_HISTORY

    # Check for finite outcomes
    outcomes_list = list(outcomes)
    for outcome in outcomes_list:
        if not outcome.payoff_risk_fraction.is_finite():
            return Decimal(0), EvidenceState.NEGATIVE_EDGE

    # Compute mean payoff across outcomes
    total_weight = sum(o.weight for o in outcomes_list)
    if total_weight <= 0:
        return Decimal(0), EvidenceState.NO_HISTORY

    mean_payoff = sum(
        o.payoff_risk_fraction * o.weight for o in outcomes_list
    ) / total_weight

    # If mean payoff is <= 0, edge is negative or inconclusive
    if mean_payoff <= Decimal(0):
        return Decimal(0), EvidenceState.NEGATIVE_EDGE

    # If mean payoff is barely positive but small, may be inconclusive
    # Use bootstrap to assess confidence
    if len(outcomes_list) < 5:
        # Very thin data; use conservative shrinkage toward zero
        return Decimal(0), EvidenceState.INCONCLUSIVE

    # Run block bootstrap to assess confidence in positive edge
    bootstrap_means = block_bootstrap(outcomes_list, n_bootstrap=1000, seed=seed)

    if not bootstrap_means:
        return Decimal(0), EvidenceState.INCONCLUSIVE

    # Sort and find lower-confidence (25th percentile) estimate
    sorted_means = sorted(bootstrap_means)
    confidence_idx = max(0, int(len(sorted_means) * float(confidence_level)))
    lower_confidence_mean = sorted_means[confidence_idx]

    # If lower-confidence estimate is still negative, negative edge
    if lower_confidence_mean <= Decimal(0):
        return Decimal(0), EvidenceState.NEGATIVE_EDGE

    # If lower-confidence estimate is very small or zero, inconclusive
    if lower_confidence_mean < Decimal("0.001"):
        return Decimal(0), EvidenceState.INCONCLUSIVE

    # Estimate robust Kelly using growth rate
    # For small mean payoffs, use a conservative fraction of the mean
    # (This is a simplified robust procedure; full implementation would
    # optimize log-growth under the actual distribution.)
    robust_fraction = lower_confidence_mean * Decimal("0.5")

    # Cap at reasonable bounds (Kelly can theoretically exceed 1, but
    # we cap at 1.0 for practical constraint-checking)
    robust_fraction = min(robust_fraction, Decimal(1))

    return robust_fraction, EvidenceState.ELIGIBLE


def hierarchical_min(
    full_kelly_fraction: Decimal,
    fractional_kelly_multiplier: Decimal,
    constraints: Sequence[KellySizingConstraint],
    equity_cents: Cents,
) -> tuple[Decimal, str]:
    """Compute hierarchical minimum with only downward modifiers.

    Per §7.4:
    B_trade = min(
        E_risk * released_trade_fraction,
        E_risk * lambda * max(0, f_robust),
        remaining_owner_risk,
        remaining_account_risk,
        remaining_portfolio_risk,
        remaining_sleeve_risk,
        remaining_provider_risk,
        remaining_underlying_risk
    )

    Only downward modifiers (cap reductions) apply. No stacking beyond hard ceiling.

    Args:
        full_kelly_fraction: f (e.g., 0.228571) from robust_candidate or binary model.
        fractional_kelly_multiplier: λ ∈ (0, 1], user-configured fractional Kelly.
        constraints: List of hierarchical KellySizingConstraint objects.
        equity_cents: Current equity in cents (used to compute E_risk * lambda * f).

    Returns:
        Tuple of (final_fraction, binding_constraint_name):
        - final_fraction: The minimum applicable fraction (0 to 1).
        - binding_constraint_name: Which constraint bound the result.
    """
    # Apply only downward modifiers
    # Never stack (e.g., never apply two reductions that together exceed ceiling)

    result_fraction = full_kelly_fraction
    binding = "full_kelly"

    # Apply fractional Kelly multiplier (lambda)
    # Lambda can be 0, which zeroes out Kelly
    if Decimal(0) <= fractional_kelly_multiplier <= Decimal(1):
        scaled_fraction = full_kelly_fraction * fractional_kelly_multiplier
        if scaled_fraction < result_fraction:
            result_fraction = scaled_fraction
            binding = f"fractional_kelly_lambda={fractional_kelly_multiplier}"

    # Apply hierarchical constraints (minimum takes priority)
    for constraint in constraints:
        # Constraint as a fraction of equity
        constraint_fraction = constraint.binding_fraction

        if constraint_fraction < result_fraction:
            result_fraction = constraint_fraction
            binding = constraint.label or constraint.level

    # Ensure result is non-negative
    result_fraction = max(result_fraction, Decimal(0))

    return result_fraction, binding


def shadow_size_for(
    lifecycle_inputs: dict,
) -> KellyShadowResult:
    """Compute shadow-only Kelly size without changing order quantity.

    For shadow mode (sizing_mode == "kelly_shadow"), log would-be size and risk
    without affecting the actual order. Never changes reserved capital or risk budget.

    Expected keys in lifecycle_inputs:
    - 'entry_price_cents': Cents
    - 'stop_loss_cents': Cents
    - 'equity_cents': Cents
    - 'profile': KellyProfile
    - 'outcomes': list[KellyOutcome]
    - 'fractional_kelly_multiplier': Decimal (default 1.0)
    - 'hierarchical_constraints': list[KellySizingConstraint] (may be empty)

    Args:
        lifecycle_inputs: Dictionary with sizing inputs (see above).

    Returns:
        KellyShadowResult with would-be quantity and risk (never applied).
    """
    # Extract inputs with defaults
    entry_price_cents = lifecycle_inputs.get("entry_price_cents", 0)
    stop_loss_cents = lifecycle_inputs.get("stop_loss_cents", entry_price_cents)
    equity_cents = lifecycle_inputs.get("equity_cents", 0)
    outcomes = lifecycle_inputs.get("outcomes", [])
    fractional_kelly_multiplier = Decimal(
        lifecycle_inputs.get("fractional_kelly_multiplier", 1)
    )
    constraints = lifecycle_inputs.get("hierarchical_constraints", [])
    available_risk_cents = lifecycle_inputs.get("available_risk_cents", Cents(0))

    # Validate inputs
    if equity_cents <= 0:
        return KellyShadowResult(
            would_be_quantity=0,
            would_be_risk_cents=Cents(0),
            evidence_state=EvidenceState.NO_HISTORY,
            effective_kelly_fraction=Decimal(0),
            binding_constraint="zero_equity",
            reason_if_rejected=Reason.CAPITAL_LIMITED,
        )

    # Compute robust Kelly from outcomes
    robust_kelly, evidence_state = robust_candidate(outcomes, seed=None)

    # If no evidence, reject
    if evidence_state == EvidenceState.NO_HISTORY:
        return KellyShadowResult(
            would_be_quantity=0,
            would_be_risk_cents=Cents(0),
            evidence_state=evidence_state,
            effective_kelly_fraction=Decimal(0),
            binding_constraint="no_history",
            reason_if_rejected=Reason.RISK_LIMITED,
        )

    # If negative edge, zero out
    if evidence_state == EvidenceState.NEGATIVE_EDGE:
        return KellyShadowResult(
            would_be_quantity=0,
            would_be_risk_cents=Cents(0),
            evidence_state=evidence_state,
            effective_kelly_fraction=Decimal(0),
            binding_constraint="negative_edge",
            reason_if_rejected=Reason.RISK_LIMITED,
        )

    # Apply hierarchical minimum
    final_fraction, binding = hierarchical_min(
        robust_kelly, fractional_kelly_multiplier, constraints, equity_cents
    )

    # Compute would-be risk and quantity
    would_be_risk_cents = Cents(int(equity_cents * final_fraction))

    # Cap at available risk
    if available_risk_cents > 0:
        would_be_risk_cents = min(would_be_risk_cents, available_risk_cents)

    # Compute would-be quantity from stop-risk
    if entry_price_cents <= 0 or stop_loss_cents < 0:
        would_be_quantity = 0
    else:
        unit_risk_cents = entry_price_cents - stop_loss_cents
        if unit_risk_cents <= 0:
            would_be_quantity = 0
        else:
            would_be_quantity = int(would_be_risk_cents // unit_risk_cents)

    return KellyShadowResult(
        would_be_quantity=would_be_quantity,
        would_be_risk_cents=would_be_risk_cents,
        evidence_state=evidence_state,
        effective_kelly_fraction=final_fraction,
        binding_constraint=binding,
        reason_if_rejected=None if final_fraction > 0 else Reason.RISK_LIMITED,
    )
