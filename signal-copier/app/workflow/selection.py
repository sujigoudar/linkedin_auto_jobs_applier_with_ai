"""Account selection: choose one eligible physical account from candidates.

This module implements deterministic candidate ranking per
docs/workflow-contract/WORKFLOW_SPECIFICATION.md §5.2 (feasibility before
ranking, explicit ranking criteria, persisted inclusion/exclusion reasons).

A single DecisionTrace row is persisted per candidate, recording feasibility,
rank, and inclusion/exclusion reason. The selected account has the best rank.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Candidate:
    """A physical account candidate for an execution opportunity.

    Attributes:
        physical_account_id: Immutable broker account identity.
        feasible: Can buy the minimum required unit (1 share, 1 contract, etc.).
        rank: Candidate ranking (lower is better). Feasible candidates only.
        reason: Inclusion reason (if feasible) or exclusion reason (if not).
        recipe_fully_qualified: Whether full order-recipe requirements are met.
            None = unknown, unknown ranks AFTER known at this criterion.
        incremental_concentration_bps: Incremental concentration in basis points.
            Integer, None = unknown. Lower is better.
        incremental_stress_cents: Incremental stress in integer cents.
            None = unknown. Lower is better (fail closed on missing).
        estimated_cost_cents: Estimated execution/funding cost in integer cents.
            None = unknown. Lower is better.
        evidence_as_of: Freshness of operational evidence (datetime).
            None = unknown. More recent (later datetime) is better.
    """

    physical_account_id: str
    feasible: bool
    rank: int | None  # None if not feasible.
    reason: str  # Inclusion/exclusion reason.
    recipe_fully_qualified: bool | None = None
    incremental_concentration_bps: int | None = None
    incremental_stress_cents: int | None = None
    estimated_cost_cents: int | None = None
    evidence_as_of: datetime | None = None


@dataclass(frozen=True)
class RankingPolicy:
    """Policy for deterministic candidate ranking (§5.2).

    Attributes:
        strategy_account_preference: Ordered sequence (tuple or list) of account IDs
            with explicit released strategy preference. Earlier = better rank.
            If a set is provided, it is treated as unordered (members tie on preference).
    """

    strategy_account_preference: tuple[str, ...] | list[str] | set[str]


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

    Implements deterministic ranking per WORKFLOW_SPECIFICATION.md §5.2:
    1. Feasibility first (infeasible candidates are excluded with rank=None)
    2. Feasible candidates ranked by:
       a. Released strategy/account preference (earlier in list = better)
       b. Full order-recipe qualification (known > unknown)
       c. Lower incremental concentration (basis points)
       d. Lower incremental stress (cents)
       e. Lower cost (cents)
       f. Fresher evidence (more recent datetime > older > unknown)
       g. Stable account ID (lexicographic tiebreaker)

    Args:
        candidates: All candidate accounts with feasibility and ranking inputs.
        policy: Ranking policy with strategy preference (ordered sequence or set).

    Returns:
        Selection with one selected account (rank 1) and all candidates with
        updated rank/reason. Feasible candidates get rank 1..n. Infeasible
        candidates have rank=None with exclusion reason.

    Raises:
        ValueError: If no candidates are feasible.
    """
    # Normalize strategy preference to a tuple for indexing.
    if isinstance(policy.strategy_account_preference, set):
        # Unordered set: all members tie on preference.
        preference_list = tuple(policy.strategy_account_preference)
        preference_index = {pid: 0 for pid in preference_list}
    else:
        # Ordered sequence (list/tuple): index determines rank.
        preference_list = tuple(policy.strategy_account_preference)
        preference_index = {pid: i for i, pid in enumerate(preference_list)}

    # Separate feasible and infeasible candidates.
    feasible = [c for c in candidates if c.feasible]
    if not feasible:
        raise ValueError("select_account: no feasible candidates")

    # Sort feasible candidates by the ranking criteria.
    # The sort key is a tuple of (preference_idx, not_recipe_qualified,
    # concentration, stress, cost, -evidence_timestamp, account_id).
    # Ties on all numeric/boolean fields are broken by stable account ID.
    def ranking_key(candidate: Candidate) -> tuple:
        # Preference index: earlier (lower) is better.
        pref_idx = preference_index.get(candidate.physical_account_id, len(preference_list))

        # Recipe qualification: known is better than unknown.
        # True < False when inverted, so we use "not qualified" for sort stability.
        recipe_not_qualified = candidate.recipe_fully_qualified is not True

        # Concentration: lower is better. Unknown (None) ranks last.
        concentration = (
            (1, float("inf"))  # Unknown: sort after any known value.
            if candidate.incremental_concentration_bps is None
            else (0, float(candidate.incremental_concentration_bps))
        )

        # Stress: lower is better. Unknown (None) ranks last.
        stress = (
            (1, float("inf"))
            if candidate.incremental_stress_cents is None
            else (0, float(candidate.incremental_stress_cents))
        )

        # Cost: lower is better. Unknown (None) ranks last.
        cost = (
            (1, float("inf"))
            if candidate.estimated_cost_cents is None
            else (0, float(candidate.estimated_cost_cents))
        )

        # Evidence freshness: more recent is better. Negate datetime to sort
        # in descending order. Unknown (None) ranks last.
        if candidate.evidence_as_of is None:
            evidence = (1, 0.0)  # Unknown, sorts last.
        else:
            # Convert datetime to timestamp for negation.
            # More recent (larger timestamp) should rank first (smaller in sort).
            evidence = (0, -candidate.evidence_as_of.timestamp())

        # Stable account ID (lexicographic).
        account_id = candidate.physical_account_id

        return (pref_idx, recipe_not_qualified, concentration, stress, cost, evidence, account_id)

    # Sort feasible candidates by the ranking key.
    sorted_feasible = sorted(feasible, key=ranking_key)

    # Build updated candidate list with ranks and reasons.
    updated_candidates = []
    selected = sorted_feasible[0]

    for i, candidate in enumerate(sorted_feasible, start=1):
        if i == 1:
            # Selected candidate.
            reason = "selected"
        else:
            # Alternative candidate: find the first criterion where it differs from selected.
            reason = _compute_alternative_reason(candidate, selected, preference_index, preference_list)

        # Create new Candidate instance with updated rank and reason.
        updated_candidates.append(
            Candidate(
                physical_account_id=candidate.physical_account_id,
                feasible=True,
                rank=i,
                reason=reason,
                recipe_fully_qualified=candidate.recipe_fully_qualified,
                incremental_concentration_bps=candidate.incremental_concentration_bps,
                incremental_stress_cents=candidate.incremental_stress_cents,
                estimated_cost_cents=candidate.estimated_cost_cents,
                evidence_as_of=candidate.evidence_as_of,
            )
        )

    # Add infeasible candidates (rank=None) with their exclusion reasons.
    for candidate in candidates:
        if not candidate.feasible:
            updated_candidates.append(
                Candidate(
                    physical_account_id=candidate.physical_account_id,
                    feasible=False,
                    rank=None,
                    reason=candidate.reason,  # Exclusion reason from input.
                    recipe_fully_qualified=candidate.recipe_fully_qualified,
                    incremental_concentration_bps=candidate.incremental_concentration_bps,
                    incremental_stress_cents=candidate.incremental_stress_cents,
                    estimated_cost_cents=candidate.estimated_cost_cents,
                    evidence_as_of=candidate.evidence_as_of,
                )
            )

    return Selection(
        selected_physical_account_id=selected.physical_account_id,
        all_candidates=updated_candidates,
    )


def _compute_alternative_reason(
    candidate: Candidate,
    selected: Candidate,
    preference_index: dict[str, int],
    preference_list: tuple[str, ...],
) -> str:
    """Compute the exclusion reason for an alternative candidate.

    Finds the first ranking criterion on which the candidate differs from
    the selected candidate and returns "alternative:<criterion>".

    Args:
        candidate: The alternative candidate.
        selected: The selected candidate.
        preference_index: Map of account ID to preference index.
        preference_list: Ordered preference list.

    Returns:
        Alternative reason string.
    """
    # 1. Check preference.
    pref_candidate = preference_index.get(candidate.physical_account_id, len(preference_list))
    pref_selected = preference_index.get(selected.physical_account_id, len(preference_list))
    if pref_candidate != pref_selected:
        return "alternative:preference"

    # 2. Check recipe qualification.
    recipe_cand = candidate.recipe_fully_qualified is True
    recipe_sel = selected.recipe_fully_qualified is True
    if recipe_cand != recipe_sel:
        return "alternative:recipe_fully_qualified"

    # 3. Check concentration.
    conc_cand = candidate.incremental_concentration_bps
    conc_sel = selected.incremental_concentration_bps
    if conc_cand != conc_sel and (conc_cand is not None or conc_sel is not None):
        return "alternative:concentration"

    # 4. Check stress.
    stress_cand = candidate.incremental_stress_cents
    stress_sel = selected.incremental_stress_cents
    if stress_cand != stress_sel and (stress_cand is not None or stress_sel is not None):
        return "alternative:stress"

    # 5. Check cost.
    cost_cand = candidate.estimated_cost_cents
    cost_sel = selected.estimated_cost_cents
    if cost_cand != cost_sel and (cost_cand is not None or cost_sel is not None):
        return "alternative:cost"

    # 6. Check evidence freshness.
    evid_cand = candidate.evidence_as_of
    evid_sel = selected.evidence_as_of
    if evid_cand != evid_sel and (evid_cand is not None or evid_sel is not None):
        return "alternative:evidence"

    # 7. Stable account ID (should never be equal if they're different candidates).
    return "alternative:account_id"
