"""Candidate family enumeration and the equal-weight capital recipe, per
spec/docs/03_portfolio_research_and_selection.md's "Candidate family and
finite evaluation" and "Dynamic allocation" sections.

Deliberately bounded: this is the deterministic, data-free half of Phase
04 (combinatorics and capital-weight arithmetic). It does NOT implement
complementarity/correlation statistics, the other three recipes (inverse-
volatility, hierarchical risk parity, constrained minimum-CVaR), or
walk-forward/holdout evaluation -- all of those need real authorized
historical sleeve data to mean anything, and fabricating that data or the
statistics computed from it would violate this build's own "never
generate fabricated returns" rule. Those remain open, tracked in
ops/commercial_state.json, not silently assumed done.

Research defaults below are exactly the ones the spec names as its
defaults -- "deliberately conservative study settings, not changes to the
owner's existing financial limits or a live recommendation. Lower limits
from an actual account/product always prevail."
"""
from __future__ import annotations

import itertools
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal

MIN_SLEEVES_PER_CANDIDATE = 2
MAX_SLEEVES_PER_CANDIDATE = 5
#: "no more than 12 eligible sleeves in one approved exhaustive run"
MAX_ELIGIBLE_UNIVERSE = 12
BASE_CASH_FRACTION = Decimal("0.15")
MAX_SLEEVE_WEIGHT = Decimal("0.35")

_QUANT = Decimal("0.00000001")


class TooManyEligibleSleevesError(Exception):
    """Raised instead of silently sampling a smaller universe -- spec:
    "If the approved run would exceed the compute budget, do not randomly
    sample and call it exhaustive: schedule all deterministic shards or
    obtain approval for a separately named smaller universe." Enumerating
    a >12-sleeve universe and calling the result exhaustive would be
    exactly that prohibited silent behavior, so this refuses outright."""


def _check_universe_size(sleeve_ids: Sequence[str]) -> None:
    if len(sleeve_ids) > MAX_ELIGIBLE_UNIVERSE:
        raise TooManyEligibleSleevesError(
            f"{len(sleeve_ids)} eligible sleeves exceeds the approved exhaustive-run limit of "
            f"{MAX_ELIGIBLE_UNIVERSE}; obtain approval for a separately named smaller universe or "
            "schedule deterministic shards instead of enumerating this run"
        )


def enumerate_candidate_subsets(
    sleeve_ids: Sequence[str],
    *,
    min_size: int = MIN_SLEEVES_PER_CANDIDATE,
    max_size: int = MAX_SLEEVES_PER_CANDIDATE,
) -> list[tuple[str, ...]]:
    """Every subset of size min_size..max_size (AD-04's own "Minimum
    sleeves"/"Maximum sleeves" fields) from the eligible universe --
    "enumerate every subset within the approved run universe and size
    bound... do not silently drop awkward subsets." Deterministic order
    (sorted sleeve_ids, combinations in their natural itertools order)
    so re-running against the same universe always produces the same
    candidate list in the same order. Defaults to the module's own
    2..5 bounds -- callers that never pass min_size/max_size see
    unchanged behavior."""
    if min_size < 1 or max_size < min_size:
        raise ValueError(f"invalid subset bounds: min_size={min_size}, max_size={max_size}")
    _check_universe_size(sleeve_ids)
    ordered = sorted(set(sleeve_ids))
    subsets: list[tuple[str, ...]] = []
    for size in range(min_size, min(max_size, len(ordered)) + 1):
        subsets.extend(itertools.combinations(ordered, size))
    return subsets


def benchmark_subsets(sleeve_ids: Sequence[str]) -> list[tuple[str, ...]]:
    """Single-sleeve benchmarks -- spec: "Include existing single-sleeve
    strategies and current production portfolio as benchmarks." These are
    reported alongside the 2-5 sleeve candidates above, never merged into
    that combinatorial list (a benchmark is not itself a diversification
    candidate)."""
    _check_universe_size(sleeve_ids)
    return [(sleeve_id,) for sleeve_id in sorted(set(sleeve_ids))]


@dataclass(frozen=True)
class CandidateAllocation:
    weights: dict[str, Decimal]
    cash: Decimal


def equal_weight_recipe(
    sleeve_ids: tuple[str, ...],
    *,
    base_cash_fraction: Decimal = BASE_CASH_FRACTION,
    max_sleeve_weight: Decimal = MAX_SLEEVE_WEIGHT,
) -> CandidateAllocation:
    """Equal sleeve capital, capped at `max_sleeve_weight` per sleeve.

    "Two-sleeve candidates may retain additional cash because 2x35% cannot
    invest 85%" -- i.e. when the naive equal split would exceed the cap,
    every sleeve is capped instead of renormalized upward, and the
    uninvested remainder becomes EXTRA cash beyond the base 15% (never
    marked INFEASIBLE merely for having a small candidate size, and never
    silently redistributed past the cap onto other sleeves)."""
    if not sleeve_ids:
        raise ValueError("a candidate must contain at least one sleeve")

    n = Decimal(len(sleeve_ids))
    naive_weight = ((Decimal(1) - base_cash_fraction) / n).quantize(_QUANT, rounding=ROUND_HALF_EVEN)

    if naive_weight > max_sleeve_weight:
        weight = max_sleeve_weight
    else:
        weight = naive_weight

    weights = {sleeve_id: weight for sleeve_id in sleeve_ids}
    cash = Decimal(1) - (weight * n)
    return CandidateAllocation(weights=weights, cash=cash)
