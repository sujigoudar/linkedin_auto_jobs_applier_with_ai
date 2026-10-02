"""Exact money arithmetic and conversion.

WC-01 creates this module with the Cents type and to_cents() function.
Money is always exact: integer cents or Decimal, never float.
"""
from decimal import Decimal, ROUND_DOWN
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
        Integer cents (exact, never rounded).

    Raises:
        TypeError: If value is not one of the accepted types.
        ValueError: If value does not convert to exact cents or is negative.
    """
    if isinstance(value, int):
        if value < 0:
            raise ValueError(f"Value {value} cannot be negative")
        return value

    if isinstance(value, str):
        d = Decimal(value)
    elif isinstance(value, Decimal):
        d = value
    else:
        raise TypeError(f"Expected Decimal, str, or int, got {type(value).__name__}")

    if d < 0:
        raise ValueError(f"Value {value} cannot be negative")

    # Convert to cents (multiply by 100)
    cents_decimal = d * 100
    if cents_decimal % 1 != 0:
        raise ValueError(
            f"Value {value} does not convert to exact cents; "
            f"got fractional cent {cents_decimal}"
        )
    return int(cents_decimal)


def to_cents_floor(value: Union[Decimal, str, int]) -> Cents:
    """Convert a value to integer cents, rounding down (floor).

    WC-03: Used where the spec says quantities/reserves never round up.

    Args:
        value: A Decimal, str, or int representing money.

    Returns:
        Integer cents rounded down.

    Raises:
        TypeError: If value is not one of the accepted types.
        ValueError: If value is negative.
    """
    if isinstance(value, int):
        if value < 0:
            raise ValueError(f"Value {value} cannot be negative")
        return value

    if isinstance(value, str):
        d = Decimal(value)
    elif isinstance(value, Decimal):
        d = value
    else:
        raise TypeError(f"Expected Decimal, str, or int, got {type(value).__name__}")

    if d < 0:
        raise ValueError(f"Value {value} cannot be negative")

    # Convert to cents and floor
    cents_decimal = (d * 100).quantize(Decimal("1"), rounding=ROUND_DOWN)
    return int(cents_decimal)


def require_nonnegative_cents(value: Cents) -> Cents:
    """Verify that a Cents value is non-negative.

    Args:
        value: An integer cent value.

    Returns:
        The value unchanged.

    Raises:
        ValueError: If value is negative.
    """
    if value < 0:
        raise ValueError(f"Value {value} cannot be negative")
    return value
