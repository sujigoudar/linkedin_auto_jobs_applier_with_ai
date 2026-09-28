"""Money's own tests -- INTEGRATION_DECISION.md S6: "booleans, NaN/infinity
and missing units are not financial numbers.\""""
from decimal import Decimal

import pytest
from pydantic import BaseModel, ValidationError

from signal_platform_contracts.money import Money


class _Holder(BaseModel):
    amount: Money


def test_parses_a_decimal_string_exactly():
    assert _Holder(amount="100.10").amount == Decimal("100.10")


def test_parses_an_integer():
    assert _Holder(amount=5).amount == Decimal(5)


def test_rejects_a_bool_even_though_bool_is_an_int_subclass():
    with pytest.raises(ValidationError, match="boolean"):
        _Holder(amount=True)


def test_rejects_a_float():
    with pytest.raises(ValidationError, match="must be a decimal string or integer"):
        _Holder(amount=1.5)


def test_rejects_nan_string():
    with pytest.raises(ValidationError, match="NaN/Infinity"):
        _Holder(amount="nan")


def test_rejects_infinity_string():
    with pytest.raises(ValidationError, match="NaN/Infinity"):
        _Holder(amount="inf")


def test_rejects_a_malformed_decimal_string():
    with pytest.raises(ValidationError, match="not a valid decimal string"):
        _Holder(amount="not-a-number")


def test_serializes_back_out_as_a_string_not_a_json_float():
    holder = _Holder(amount="100.10")
    assert holder.model_dump(mode="json")["amount"] == "100.10"
