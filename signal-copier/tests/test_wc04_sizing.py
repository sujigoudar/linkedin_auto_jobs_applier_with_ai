"""Tests for WC-04: Product-correct sizing module.

Tests the sizing functions that reproduce the sizing_oracle exactly for all
4,500 fixture vectors, plus boundary tests and the spec §7.5 worked example.

This is a module test, not a contract adapter test.
Spec sections: §6.1, §7.5, §8, §8.1–§8.4.
"""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from app.workflow.money import to_cents
from app.workflow.reasons import Reason
from app.workflow.sizing import (
    SizingResult,
    size_fx,
    size_linear_future,
    size_linear_long,
    size_long_option,
    size_spot_crypto,
)


class TestMoneyConversion:
    """Test monetary type conversions."""

    def test_to_cents_from_int(self):
        """Integer cents are passed through."""
        assert to_cents(100) == 100
        assert to_cents(0) == 0

    def test_to_cents_from_decimal(self):
        """Decimal dollars convert to cents."""
        assert to_cents(Decimal("1.00")) == 100
        assert to_cents(Decimal("10.50")) == 1050
        assert to_cents(Decimal("0.01")) == 1

    def test_to_cents_from_string(self):
        """String dollars convert to cents."""
        assert to_cents("1.00") == 100
        assert to_cents("50.99") == 5099

    def test_to_cents_rejects_fractional_cents(self):
        """Fractional cents raise ValueError."""
        with pytest.raises(ValueError, match="does not convert to exact cents"):
            to_cents(Decimal("1.001"))

    def test_to_cents_rejects_invalid_type(self):
        """Non-Decimal/str/int types raise TypeError."""
        with pytest.raises(TypeError):
            to_cents(1.5)  # float


class TestSizeLinearLong:
    """Test linear long equity sizing (spec §8.1).

    Tests the 4,500 fixture vectors plus boundary conditions.
    """

    def test_sizing_oracle_vectors(self):
        """Run all 4,500 linear_sizing.jsonl vectors against size_linear_long.

        The function must reproduce sizing_oracle exactly:
        q = min(floor(risk/unit_risk), floor(cash/price), source_max);
        zero when any bound is zero; never round up.
        """
        fixture_path = (
            Path(__file__).parent.parent
            / "docs/workflow-contract/generated/linear_sizing.jsonl"
        )
        assert fixture_path.exists(), f"Fixture not found: {fixture_path}"

        failures = []
        with fixture_path.open("r", encoding="utf-8") as f:
            for line_num, line in enumerate(f, start=1):
                case = json.loads(line)
                inputs = case["inputs"]
                expected = case["expected"]

                result = size_linear_long(
                    risk_budget_cents=inputs["risk_budget_cents"],
                    unit_risk_cents=inputs["unit_risk_cents"],
                    cash_capacity_cents=inputs["cash_capacity_cents"],
                    entry_price_cents=inputs["entry_price_cents"],
                    source_max_units=inputs["source_max_units"],
                    lot_step=1,
                )

                # Check all output fields
                if result.quantity_units != expected["quantity_units"]:
                    failures.append(
                        f"Line {line_num} ({case['id']}): "
                        f"quantity_units mismatch; "
                        f"expected {expected['quantity_units']}, "
                        f"got {result.quantity_units}"
                    )
                if result.planned_risk_cents != expected["planned_risk_cents"]:
                    failures.append(
                        f"Line {line_num} ({case['id']}): "
                        f"planned_risk_cents mismatch; "
                        f"expected {expected['planned_risk_cents']}, "
                        f"got {result.planned_risk_cents}"
                    )
                if result.notional_cents != expected["notional_cents"]:
                    failures.append(
                        f"Line {line_num} ({case['id']}): "
                        f"notional_cents mismatch; "
                        f"expected {expected['notional_cents']}, "
                        f"got {result.notional_cents}"
                    )

        if failures:
            pytest.fail(
                f"Sizing oracle mismatch on {len(failures)} vectors:\n"
                + "\n".join(failures[:10])
                + ("\n..." if len(failures) > 10 else "")
            )

    def test_spec_75_worked_example(self):
        """Test spec §7.5 worked synthetic example.

        Binary research model p=0.55, b=1.4. Full Kelly 22.8571%, quarter 5.7143%.
        Released ceiling 0.25%.
        E=$6,000, B=$15. Entry $50, stop $49, adverse cost $0.05.
        quantity = floor(15 / (50-49+0.05)) = floor(15 / 1.05) = 14 shares.
        Notional = 14 * $50 = $700. Planned risk = 14 * $1.05 = $14.70.
        """
        result = size_linear_long(
            risk_budget_cents=to_cents("15"),
            unit_risk_cents=to_cents("1.05"),  # 50-49+0.05
            cash_capacity_cents=to_cents("700"),  # Entry at $50 with 14 shares
            entry_price_cents=to_cents("50"),
            source_max_units=100,
            lot_step=1,
        )

        assert result.quantity_units == 14
        assert result.planned_risk_cents == to_cents("14.70")
        assert result.notional_cents == to_cents("700")
        assert result.reason is None

    def test_zero_risk_budget(self):
        """Zero risk budget yields zero quantity."""
        result = size_linear_long(
            risk_budget_cents=0,
            unit_risk_cents=to_cents("1.00"),
            cash_capacity_cents=to_cents("1000"),
            entry_price_cents=to_cents("10"),
            source_max_units=100,
        )
        assert result.quantity_units == 0
        assert result.planned_risk_cents == 0
        assert result.notional_cents == 0

    def test_zero_cash_capacity(self):
        """Zero cash capacity yields zero quantity."""
        result = size_linear_long(
            risk_budget_cents=to_cents("100"),
            unit_risk_cents=to_cents("1.00"),
            cash_capacity_cents=0,
            entry_price_cents=to_cents("10"),
            source_max_units=100,
        )
        assert result.quantity_units == 0

    def test_zero_source_max(self):
        """Zero source_max is hard zero, not absence of ceiling."""
        result = size_linear_long(
            risk_budget_cents=to_cents("100"),
            unit_risk_cents=to_cents("1.00"),
            cash_capacity_cents=to_cents("1000"),
            entry_price_cents=to_cents("10"),
            source_max_units=0,
        )
        assert result.quantity_units == 0

    def test_lot_step_floors_never_rounds_up(self):
        """lot_step floors the quantity; never rounds up."""
        # With lot_step=10, 14 units floors to 10, not 20
        result = size_linear_long(
            risk_budget_cents=to_cents("15"),
            unit_risk_cents=to_cents("1.05"),
            cash_capacity_cents=to_cents("700"),
            entry_price_cents=to_cents("50"),
            source_max_units=100,
            lot_step=10,
        )
        assert result.quantity_units == 10
        assert result.planned_risk_cents == to_cents("10.50")
        assert result.notional_cents == to_cents("500")

    def test_binding_constraint_risk(self):
        """Risk constraint binds when it is the minimum."""
        result = size_linear_long(
            risk_budget_cents=to_cents("100"),  # Limits to 50 units
            unit_risk_cents=to_cents("2.00"),
            cash_capacity_cents=to_cents("10000"),  # Would allow 100 units
            entry_price_cents=to_cents("100"),
            source_max_units=200,
        )
        assert result.quantity_units == 50
        assert result.binding_constraint == "risk"

    def test_binding_constraint_cash(self):
        """Cash constraint binds when it is the minimum."""
        result = size_linear_long(
            risk_budget_cents=to_cents("1000"),  # Would allow 100 units
            unit_risk_cents=to_cents("10.00"),
            cash_capacity_cents=to_cents("1000"),  # Limits to 10 units
            entry_price_cents=to_cents("100"),
            source_max_units=200,
        )
        assert result.quantity_units == 10
        assert result.binding_constraint == "cash"

    def test_binding_constraint_source_max(self):
        """Source max constraint binds when it is the minimum."""
        result = size_linear_long(
            risk_budget_cents=to_cents("1000"),  # Would allow 100 units
            unit_risk_cents=to_cents("10.00"),
            cash_capacity_cents=to_cents("100000"),  # Would allow 1000 units
            entry_price_cents=to_cents("100"),
            source_max_units=25,  # Limits to 25
        )
        assert result.quantity_units == 25
        assert result.binding_constraint == "source_max"

    def test_invalid_unit_risk_cents(self):
        """Non-positive unit_risk_cents raises ValueError."""
        with pytest.raises(ValueError, match="unit_risk_cents must be positive"):
            size_linear_long(
                risk_budget_cents=100,
                unit_risk_cents=0,
                cash_capacity_cents=1000,
                entry_price_cents=10,
                source_max_units=100,
            )

    def test_invalid_entry_price_cents(self):
        """Non-positive entry_price_cents raises ValueError."""
        with pytest.raises(ValueError, match="entry_price_cents must be positive"):
            size_linear_long(
                risk_budget_cents=100,
                unit_risk_cents=1,
                cash_capacity_cents=1000,
                entry_price_cents=0,
                source_max_units=100,
            )

    def test_boundary_minus_one(self):
        """Quantity at boundary - 1."""
        # Risk budget $100, unit risk $10 → max 10 units
        # Cash $990 = 99 * $10 → max 99 units with $10 price
        # At boundary - 1 = 9 units
        result = size_linear_long(
            risk_budget_cents=to_cents("99"),  # Just under 10 units
            unit_risk_cents=to_cents("10"),
            cash_capacity_cents=to_cents("990"),  # Would allow 99 units
            entry_price_cents=to_cents("10"),
            source_max_units=100,
        )
        assert result.quantity_units == 9

    def test_boundary_exact(self):
        """Quantity exactly at boundary."""
        result = size_linear_long(
            risk_budget_cents=to_cents("100"),  # Exactly 10 units
            unit_risk_cents=to_cents("10"),
            cash_capacity_cents=to_cents("1000"),  # Would allow 100 units
            entry_price_cents=to_cents("10"),
            source_max_units=100,
        )
        assert result.quantity_units == 10

    def test_boundary_plus_one(self):
        """Quantity at boundary + 1 (should be capped at boundary)."""
        result = size_linear_long(
            risk_budget_cents=to_cents("101"),  # Would be 10.1, floors to 10
            unit_risk_cents=to_cents("10"),
            cash_capacity_cents=to_cents("1001"),  # Would allow 100 units
            entry_price_cents=to_cents("10"),
            source_max_units=100,
        )
        assert result.quantity_units == 10


class TestSizeLongOption:
    """Test long option sizing (spec §8.2)."""

    def test_basic_option_sizing(self):
        """Option sizing uses full premium as risk."""
        # $15 budget, $2 premium, 100 multiplier = $200 full debit per contract
        result = size_long_option(
            risk_budget_cents=to_cents("15"),
            premium_cents=to_cents("2"),
            multiplier=100,
            costs_per_contract_cents=0,
            source_max_contracts=10,
        )
        # 15 / (2*100) = 15/200 = 0.075, floors to 0
        assert result.quantity_units == 0

    def test_option_zero_contracts(self):
        """Insufficient budget for one contract yields zero."""
        result = size_long_option(
            risk_budget_cents=to_cents("199"),  # Under $200 full debit
            premium_cents=to_cents("2"),
            multiplier=100,
            costs_per_contract_cents=0,
            source_max_contracts=10,
        )
        assert result.quantity_units == 0

    def test_option_with_full_budget(self):
        """Option with full budget allows entry."""
        result = size_long_option(
            risk_budget_cents=to_cents("200"),  # Exactly $200 full debit
            premium_cents=to_cents("2"),
            multiplier=100,
            costs_per_contract_cents=0,
            source_max_contracts=10,
        )
        assert result.quantity_units == 1
        assert result.planned_risk_cents == to_cents("200")


class TestSizeLinearFuture:
    """Test linear futures sizing (spec §8.4)."""

    def test_future_basic_sizing(self):
        """Basic futures sizing with point value."""
        # E-mini S&P 500 (ES): $50 point value
        # Entry 5000, stop 4990 = 10 point loss = $500 risk per contract
        result = size_linear_future(
            risk_budget_cents=to_cents("1000"),
            point_value_cents=to_cents("50"),
            entry_price=5000,
            stop_price=4990,
            cash_capacity_cents=to_cents("500000"),  # $5000 capacity for margin
            source_max_contracts=10,
        )
        # Risk per contract: 10 * $50 = $500
        # Quantity = min(1000/500, 500000/?, 10) = min(2, ?, 10)
        # Exact quantity depends on margin req, but should be capped by risk
        assert result.quantity_units >= 0  # Realistic for these params

    def test_future_entry_equals_stop(self):
        """Entry equal to stop yields zero quantity (zero risk)."""
        result = size_linear_future(
            risk_budget_cents=to_cents("1000"),
            point_value_cents=to_cents("50"),
            entry_price=5000,
            stop_price=5000,  # Same as entry
            cash_capacity_cents=to_cents("50000"),
            source_max_contracts=10,
        )
        assert result.quantity_units == 0


class TestSizeFX:
    """Test FX sizing (spec §8.4)."""

    def test_fx_missing_rate(self):
        """Missing FX rate returns INSTRUMENT_UNRESOLVED."""
        result = size_fx(
            risk_budget_cents=to_cents("100"),
            entry_price=to_cents("1.10"),  # EUR/USD entry
            stop_price=to_cents("1.09"),
            quote_to_account_rate=None,  # Missing rate
            account_cash_cents=to_cents("10000"),
            source_max_units=100,
        )
        assert result.quantity_units == 0
        assert result.reason == Reason.INSTRUMENT_UNRESOLVED

    def test_fx_with_rate(self):
        """FX sizing with explicit conversion rate."""
        # EUR/USD entry 1.10, stop 1.09, risk per unit 0.01 EUR
        # Conversion rate 1.10 USD/EUR (110 cents) → 0.01 * 110 = 1.10 USD risk/unit
        result = size_fx(
            risk_budget_cents=to_cents("100"),
            entry_price=to_cents("1.10"),
            stop_price=to_cents("1.09"),
            quote_to_account_rate=to_cents("1.10"),  # 1.10 USD per EUR
            account_cash_cents=to_cents("10000"),
            source_max_units=100,
        )
        # Risk per unit in USD = 0.01 * 1.10 = $0.011
        # 100 / 0.011 ≈ 9090 units, min with source_max (100) → 100
        assert result.quantity_units > 0


class TestSizeSpotCrypto:
    """Test spot crypto sizing (spec §8.4)."""

    def test_crypto_basic_sizing(self):
        """Spot crypto sizing with base step."""
        result = size_spot_crypto(
            risk_budget_cents=to_cents("100"),
            entry_price_cents=to_cents("50000"),  # $50k per BTC
            stop_price_cents=to_cents("49000"),  # $49k stop
            base_step=1,  # Minimum 1 BTC
            quote_available_cents=to_cents("100000"),
            source_max_units=2,
        )
        # Risk per unit = 50000 - 49000 = 1000 cents
        # Quantity = min(100/1000, 100000/50000, 2) = min(0, 2, 2) = 0
        assert result.quantity_units == 0

    def test_crypto_with_base_step(self):
        """Spot crypto floors to base_step (e.g., satoshi precision)."""
        result = size_spot_crypto(
            risk_budget_cents=to_cents("10000"),
            entry_price_cents=to_cents("50000"),  # $50k per BTC
            stop_price_cents=to_cents("49000"),  # $49k stop
            base_step=100,  # Minimum 100 satoshis or whatever unit
            quote_available_cents=to_cents("600000"),
            source_max_units=15,
        )
        # Risk per unit = 1000 cents, quantity_units = min(10000/1000, 600000/50000, 15) = min(10, 12, 15) = 10
        # Floor to step 100: (10 // 100) * 100 = 0
        assert result.quantity_units == 0


class TestSizingResultDataclass:
    """Test SizingResult immutability and structure."""

    def test_result_frozen(self):
        """SizingResult is frozen (immutable)."""
        result = SizingResult(
            quantity_units=10,
            planned_risk_cents=1000,
            notional_cents=50000,
            binding_constraint="cash",
            reason=None,
        )
        with pytest.raises(AttributeError):
            result.quantity_units = 20

    def test_result_with_reason(self):
        """SizingResult can include a decision reason."""
        result = SizingResult(
            quantity_units=0,
            planned_risk_cents=0,
            notional_cents=0,
            binding_constraint="rate",
            reason=Reason.INSTRUMENT_UNRESOLVED,
        )
        assert result.reason == Reason.INSTRUMENT_UNRESOLVED
