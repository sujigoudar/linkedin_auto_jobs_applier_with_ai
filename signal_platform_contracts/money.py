"""INTEGRATION_DECISION.md S6: "Payload numbers cross the contract
boundary as validated decimal strings or defined integer units; booleans,
NaN/infinity and missing units are not financial numbers."

`Money` is the one field type every payload in this package uses for an
amount, quantity or price -- so that rule holds by construction, in one
place, rather than being re-implemented (and possibly forgotten) per
payload model.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Annotated

from pydantic import BeforeValidator, PlainSerializer


def _parse_money(value: object) -> Decimal:
    if isinstance(value, bool):
        # bool is a subclass of int in Python -- checked before the int
        # branch below, or `True`/`False` would silently parse as 1/0.
        raise ValueError("a boolean is never a financial amount")
    if isinstance(value, Decimal):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = Decimal(value)
        except InvalidOperation as exc:
            raise ValueError(f"{value!r} is not a valid decimal string") from exc
    elif isinstance(value, int):
        parsed = Decimal(value)
    else:
        # Deliberately no `float` branch: a float already lost precision
        # before it got here, and silently accepting one would let that
        # loss cross the contract boundary looking exact.
        raise ValueError(f"{value!r} must be a decimal string or integer, not {type(value).__name__}")
    if not parsed.is_finite():
        raise ValueError("NaN/Infinity is never a financial amount")
    return parsed


#: A `Decimal` that only ever parses from a string or integer (never a
#: bool or float) and is always finite -- and always serializes back out
#: as a string, so re-exporting a payload never reintroduces float error.
Money = Annotated[Decimal, BeforeValidator(_parse_money), PlainSerializer(str, return_type=str)]
