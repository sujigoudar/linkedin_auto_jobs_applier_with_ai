"""app/services/business_economics.py -- pure function tests, no
database needed."""
from decimal import Decimal

from app.services.business_economics import compute_break_even, compute_unit_contribution


def test_unit_contribution_nets_out_taxes_refunds_and_processor_fees():
    result = compute_unit_contribution(
        revenue=Decimal("100"),
        taxes=Decimal("5"),
        refunds=Decimal("2"),
        processor_fees=Decimal("3"),
        variable_costs=Decimal("20"),
        royalties=Decimal("10"),
    )
    # 100 - 5 - 2 - 3 - 20 - 10 = 60
    assert result.value == Decimal("60")
    assert result.is_warning is False


def test_a_zero_contribution_is_a_warning():
    result = compute_unit_contribution(
        revenue=Decimal("10"),
        taxes=Decimal("0"),
        refunds=Decimal("0"),
        processor_fees=Decimal("0"),
        variable_costs=Decimal("10"),
        royalties=Decimal("0"),
    )
    assert result.value == Decimal("0")
    assert result.is_warning is True


def test_a_negative_contribution_is_a_warning():
    result = compute_unit_contribution(
        revenue=Decimal("10"),
        taxes=Decimal("0"),
        refunds=Decimal("0"),
        processor_fees=Decimal("0"),
        variable_costs=Decimal("50"),
        royalties=Decimal("0"),
    )
    assert result.value < 0
    assert result.is_warning is True


def test_break_even_divides_fixed_costs_by_positive_contribution():
    contribution = compute_unit_contribution(
        revenue=Decimal("100"),
        taxes=Decimal("0"),
        refunds=Decimal("0"),
        processor_fees=Decimal("0"),
        variable_costs=Decimal("50"),
        royalties=Decimal("0"),
    )
    result = compute_break_even(approved_fixed_costs=Decimal("500"), unit_contribution=contribution)
    assert result.units_to_break_even == Decimal("10")
    assert result.is_warning is False


def test_break_even_is_a_warning_not_a_number_when_contribution_is_non_positive():
    contribution = compute_unit_contribution(
        revenue=Decimal("10"),
        taxes=Decimal("0"),
        refunds=Decimal("0"),
        processor_fees=Decimal("0"),
        variable_costs=Decimal("50"),
        royalties=Decimal("0"),
    )
    result = compute_break_even(approved_fixed_costs=Decimal("500"), unit_contribution=contribution)
    assert result.units_to_break_even is None
    assert result.is_warning is True
