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


def to_cents(value: Union[Decimal, str, int, float]) -> Cents:
    """Convert a value to exact integer cents.

    Args:
        value: A Decimal, str, int or float representing money.

    Returns:
        Integer cents rounded down (floor).

    Raises:
        TypeError: If value is not convertible to Decimal.
        ValueError: If value is NaN or Infinity.
    """
    if isinstance(value, int):
        return value

    if isinstance(value, float):
        # Convert float to string to avoid binary representation issues
        d = Decimal(str(value))
    else:
        d = Decimal(value) if not isinstance(value, Decimal) else value

    # Check for NaN or Infinity
    if not d.is_finite():
        raise ValueError(f"Money value must be finite, got {value}")

    # Floor to integer cents
    return int(d)
