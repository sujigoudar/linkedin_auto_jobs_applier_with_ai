"""app/services/mam_allocation.py -- pure function tests, no database
needed."""
import dataclasses
from decimal import Decimal

import pytest

from app.services.mam_allocation import MamAllocationResult, allocate_fills


def test_exact_division_needs_no_remainder_allocation():
    result = allocate_fills(100, {"a": Decimal(1), "b": Decimal(1)})
    assert result.allocations == {"a": 50, "b": 50}
    assert result.unallocated_units == 0


def test_total_allocated_equals_total_fillable_when_uncapped():
    result = allocate_fills(10, {"a": Decimal(1), "b": Decimal(1), "c": Decimal(1)})
    assert sum(result.allocations.values()) == 10
    assert result.unallocated_units == 0


def test_largest_remainder_gets_the_extra_unit():
    # 10 units, weights 2:1:1 -> quotas 5.0, 2.5, 2.5 -> floors 5,2,2 = 9,
    # remainder 1 goes to the largest fractional remainder. b and c tie
    # at 0.5 -- account_id ascending breaks the tie, so b gets it.
    result = allocate_fills(10, {"a": Decimal(2), "b": Decimal(1), "c": Decimal(1)})
    assert result.allocations == {"a": 5, "b": 3, "c": 2}
    assert result.unallocated_units == 0


def test_tie_break_is_deterministic_by_account_id_ascending():
    result = allocate_fills(1, {"z": Decimal(1), "a": Decimal(1)})
    # Perfectly tied remainders (0.5 each) -- "a" wins the ascending tie-break.
    assert result.allocations == {"a": 1, "z": 0}


def test_a_capped_account_never_exceeds_its_maximum():
    result = allocate_fills(10, {"a": Decimal(1), "b": Decimal(1)}, max_allocations={"a": 3})
    assert result.allocations["a"] <= 3


def test_unallocatable_remainder_is_explicit_when_every_account_is_capped():
    result = allocate_fills(10, {"a": Decimal(1), "b": Decimal(1)}, max_allocations={"a": 3, "b": 3})
    assert sum(result.allocations.values()) == 6
    assert result.unallocated_units == 4


def test_no_negative_fillable_units():
    with pytest.raises(ValueError):
        allocate_fills(-1, {"a": Decimal(1)})


def test_empty_account_weights_leaves_everything_unallocated():
    result = allocate_fills(5, {})
    assert result.allocations == {}
    assert result.unallocated_units == 5


def test_zero_total_weight_is_rejected():
    with pytest.raises(ValueError):
        allocate_fills(10, {"a": Decimal(0), "b": Decimal(0)})


def test_a_fractional_total_weight_below_one_is_still_a_positive_weight():
    """Account weights are relative, not required to sum to 1 -- a
    total weight that is positive but less than 1 (e.g. 0.5) must be
    accepted, not mistaken for "not positive enough"."""
    result = allocate_fills(10, {"a": Decimal("0.5")})
    assert result.allocations == {"a": 10}
    assert result.unallocated_units == 0


def test_zero_fillable_units_is_valid_not_an_error():
    """Only a NEGATIVE total_fillable_units is invalid -- zero fillable
    units is a legitimate (if boring) case: nothing to allocate, no
    error, no units handed out."""
    result = allocate_fills(0, {"a": Decimal(1), "b": Decimal(1)})
    assert result.allocations == {"a": 0, "b": 0}
    assert result.unallocated_units == 0


def test_a_capped_account_first_in_priority_order_is_skipped_not_a_stop():
    """When the account whose turn comes first in largest-remainder
    priority order has already hit its cap, the redistribution loop
    must skip past it and keep handing the remainder to the next
    account in line -- not abandon distributing the remainder
    altogether just because the first candidate was capped."""
    result = allocate_fills(10, {"a": Decimal(1), "b": Decimal(1), "c": Decimal(1)}, max_allocations={"a": 3})
    # Quotas are 10/3 each (~3.33); floors are 3/3/3 (sum 9, remainder 1).
    # Tied remainders break ascending by account_id, so "a" is first in
    # line for the extra unit -- but "a" is already at its cap of 3, so
    # "b" (next in line) must get it instead.
    assert result.allocations == {"a": 3, "b": 4, "c": 3}
    assert result.unallocated_units == 0


def test_mam_allocation_result_is_immutable():
    """MamAllocationResult is a frozen dataclass -- an allocation
    record is meant to be an immutable audit artifact, never mutated
    in place after `allocate_fills` returns it."""
    result = allocate_fills(10, {"a": Decimal(1), "b": Decimal(1)})
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.unallocated_units = 999


def test_mam_allocation_result_remainders_defaults_to_an_empty_dict():
    """The `remainders` field's default (when a caller constructs the
    dataclass directly without it) is an empty dict, not None -- so
    any caller that iterates/indexes `.remainders` without a None
    check never blows up on the default."""
    result = MamAllocationResult(allocations={"a": 1}, unallocated_units=0)
    assert result.remainders == {}
