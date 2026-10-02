"""Account selection: choose one eligible physical account from candidates.

This module implements deterministic candidate ranking per
docs/workflow-contract/WORKFLOW_SPECIFICATION.md §5.2 (feasibility before
ranking, explicit ranking criteria, persisted inclusion/exclusion reasons).

A single DecisionTrace row is persisted per candidate, recording feasibility,
rank, and inclusion/exclusion reason. The selected account has the best rank.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Candidate:
    """A physical account candidate for an execution opportunity.

    Attributes:
        physical_account_id: Immutable broker account identity.
        feasible: Can buy the minimum required unit (1 share, 1 contract, etc.).
        rank: Candidate ranking (lower is better). Feasible candidates only.
        reason: Inclusion reason (if feasible) or exclusion reason (if not).
    """

    physical_account_id: str
    feasible: bool
    rank: int | None  # None if not feasible.
    reason: str  # Inclusion/exclusion reason.


@dataclass(frozen=True)
class RankingPolicy:
    """Policy for deterministic candidate ranking (§5.2).

    Attributes:
        strategy_account_preference: Set of account IDs with explicit
            released strategy preference. Earlier in this set = better rank.
        default_tiebreaker: Fallback tiebreaker when preference doesn't
            apply (e.g., "lower_cost", "fresher_evidence", "stable_id").
    """

    strategy_account_preference: set[str]
    default_tiebreaker: str


@dataclass(frozen=True)
class Selection:
    """Account selection result.

    Attributes:
        selected_physical_account_id: The chosen account ID (must be feasible).
        all_candidates: List of Candidate records (all evaluated candidates,
            feasible and infeasible, with reasons).
    """

    selected_physical_account_id: str
    all_candidates: list[Candidate]


def select_account(
    candidates: list[Candidate], policy: RankingPolicy
) -> Selection:
    """Select one account from feasible candidates using deterministic ranking.

    Args:
        candidates: All candidate accounts with feasibility and rank.
        policy: Ranking policy (strategy preference, tiebreaker).

    Returns:
        Selection with one selected account and all candidates with reasons.

    Raises:
        ValueError: If no candidates are feasible.
    """
    feasible = [c for c in candidates if c.feasible]
    if not feasible:
        raise ValueError("select_account: no feasible candidates")

    # TODO (WC-05): Implement deterministic ranking with strategy preference,
    # full recipe qualification, concentration/stress, cost, evidence freshness,
    # and stable account ID tiebreakers (§5.2).
    #
    # For now, return the first feasible candidate by stable account ID.
    selected = min(feasible, key=lambda c: c.physical_account_id)

    return Selection(
        selected_physical_account_id=selected.physical_account_id,
        all_candidates=candidates,
    )
