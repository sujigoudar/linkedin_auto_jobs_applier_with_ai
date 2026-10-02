"""Exact money arithmetic and conversion.

WC-01 creates this module with the Cents type and to_cents() function.
Money is always exact: integer cents or Decimal, never float.
"""
from decimal import Decimal
from typing import Union


Cents = int
"""Money is always exact integer cents in the account currency.

Never float arithmetic on money. Use Decimal for intermediate calculations
and convert to Cents only after rounding is explicitly specified.
"""


def to_cents(value: Union[Decimal, str, int]) -> Cents:
    """Convert a value to exact integer cents.

    Args:
        value: A Decimal, str, or int representing money.

    Returns:
        Integer cents rounded down (floor).

    Raises:
        TypeError: If value is not one of the accepted types.
        ValueError: If value does not convert to exact cents.
    """
    if isinstance(value, int):
        return value

    if isinstance(value, str):
        d = Decimal(value)
    elif isinstance(value, Decimal):
        d = value
    else:
        raise TypeError(f"Expected Decimal, str, or int, got {type(value).__name__}")

    # Convert to cents (multiply by 100)
    cents_decimal = d * 100
    if cents_decimal % 1 != 0:
        raise ValueError(
            f"Value {value} does not convert to exact cents; "
            f"got fractional cent {cents_decimal}"
        )
    return int(cents_decimal)
