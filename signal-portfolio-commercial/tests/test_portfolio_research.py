"""app/services/portfolio_research.py -- candidate enumeration and the
equal-weight recipe. Pure functions, no database needed."""
import itertools
from decimal import Decimal

import pytest

from app.services.portfolio_research import (
    MAX_ELIGIBLE_UNIVERSE,
    MAX_SLEEVE_WEIGHT,
    TooManyEligibleSleevesError,
    benchmark_subsets,
    enumerate_candidate_subsets,
    equal_weight_recipe,
)


def test_enumerates_every_subset_size_two_through_five():
    sleeves = ["a", "b", "c", "d", "e"]
    subsets = enumerate_candidate_subsets(sleeves)

    expected_count = sum(len(list(itertools.combinations(sleeves, k))) for k in range(2, 6))
    assert len(subsets) == expected_count
    assert all(2 <= len(s) <= 5 for s in subsets)
    # no duplicates, no dropped subsets
    assert len(set(subsets)) == len(subsets)


def test_no_subset_of_size_one_or_larger_than_five_is_produced():
    sleeves = [f"s{i}" for i in range(6)]
    subsets = enumerate_candidate_subsets(sleeves)
    assert all(2 <= len(s) <= 5 for s in subsets)
    assert any(len(s) == 5 for s in subsets)


def test_a_universe_of_more_than_twelve_sleeves_is_refused_not_sampled():
    sleeves = [f"s{i}" for i in range(MAX_ELIGIBLE_UNIVERSE + 1)]
    with pytest.raises(TooManyEligibleSleevesError):
        enumerate_candidate_subsets(sleeves)


def test_exactly_twelve_sleeves_is_allowed():
    sleeves = [f"s{i}" for i in range(MAX_ELIGIBLE_UNIVERSE)]
    subsets = enumerate_candidate_subsets(sleeves)
    assert len(subsets) > 0


def test_benchmark_subsets_are_single_sleeve_only():
    sleeves = ["a", "b", "c"]
    benchmarks = benchmark_subsets(sleeves)
    assert benchmarks == [("a",), ("b",), ("c",)]


def test_benchmark_subsets_also_refuse_an_oversized_universe():
    sleeves = [f"s{i}" for i in range(MAX_ELIGIBLE_UNIVERSE + 1)]
    with pytest.raises(TooManyEligibleSleevesError):
        benchmark_subsets(sleeves)


def test_five_sleeve_candidate_uses_the_base_cash_fraction():
    allocation = equal_weight_recipe(("a", "b", "c", "d", "e"))
    assert allocation.cash == Decimal("0.15")
    assert all(w == Decimal("0.17") for w in allocation.weights.values())


def test_two_sleeve_candidate_retains_extra_cash_instead_of_exceeding_the_cap():
    """The spec's own worked example: 2 sleeves at the naive 42.5% each
    would breach the 35% cap, so each is capped at 35% and the remaining
    30% -- not the base 15% -- sits in cash. This is NOT marked
    infeasible; it's a valid, just more conservative, candidate."""
    allocation = equal_weight_recipe(("a", "b"))
    assert allocation.weights == {"a": Decimal("0.35"), "b": Decimal("0.35")}
    assert allocation.cash == Decimal("0.30")


@pytest.mark.parametrize("size", [2, 3, 4, 5])
def test_no_sleeve_weight_ever_exceeds_the_cap(size):
    sleeve_ids = tuple(f"s{i}" for i in range(size))
    allocation = equal_weight_recipe(sleeve_ids)
    assert all(w <= MAX_SLEEVE_WEIGHT for w in allocation.weights.values())


@pytest.mark.parametrize("size", [1, 2, 3, 4, 5])
def test_weights_plus_cash_always_sum_to_exactly_one(size):
    sleeve_ids = tuple(f"s{i}" for i in range(size))
    allocation = equal_weight_recipe(sleeve_ids)
    assert sum(allocation.weights.values()) + allocation.cash == Decimal("1")


def test_no_weight_or_cash_is_ever_negative():
    for size in range(1, 6):
        allocation = equal_weight_recipe(tuple(f"s{i}" for i in range(size)))
        assert all(w >= 0 for w in allocation.weights.values())
        assert allocation.cash >= 0


def test_an_empty_candidate_is_rejected():
    with pytest.raises(ValueError):
        equal_weight_recipe(())
