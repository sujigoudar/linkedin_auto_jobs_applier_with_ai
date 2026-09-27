"""app/services/mam_allocation.py -- pure function tests, no database
needed."""
from decimal import Decimal

import pytest

from app.services.mam_allocation import allocate_fills


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
