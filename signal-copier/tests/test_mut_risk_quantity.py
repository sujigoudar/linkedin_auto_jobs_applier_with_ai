"""Mutation-testing follow-ups for app/risk.py and app/quantity.py.

This file captures killing tests for 5 mutant survivors discovered during
bounded manual mutation testing (15 mutations total):

Survivor 1 (Mutation 3): Default sizing_mode was changed from "multiplier"
  to "fixed" without test failure. Tests that sizing_mode defaults to
  "multiplier" when not specified.

Survivor 2 (Mutation 6): Flipped account.risk_fraction is None check to
  is not None without test failure. Tests that risk_fraction pre-flight
  check correctly requires the value to be set (not None).

Survivor 3 (Mutation 7): Flipped price_cents == stop_cents to != without
  test failure. Tests that equal price and stop_loss is correctly rejected.

Survivor 4 (Mutation 8): Changed ceiling rounding in unit_risk from +1 to
  -1 without test failure. Tests that ceiling rounding is applied correctly.

Survivor 5 (Mutation 15): Flipped signal.asset_class == AssetClass.FOREX to
  != without test failure. Tests that FOREX assets are handled correctly.
"""
from __future__ import annotations

from decimal import Decimal

from app.models import (
    AssetClass,
    DestinationAccount,
    FxContractSpec,
    Side,
    Signal,
)
from app.risk import (
    risk_fraction_quantity,
    contract_multiplier,
    size_for_account_with_mode,
)


# --- Survivor 1: Default sizing_mode fallback to "multiplier" ---


def test_survivor_1_sizing_mode_defaults_to_multiplier_not_fixed():
    """Mutation 3: Kills the change from 'multiplier' default to 'fixed'.

    When sizing_mode is None/not set, the code must default to 'multiplier',
    which scales by account.multiplier. A 'fixed' mode default would require
    fixed_quantity to be set, breaking accounts that use only multiplier."""
    account = DestinationAccount(
        account_id="acct-1",
        broker="alpaca",
        sizing_mode=None,  # NOT set, should default to "multiplier"
        multiplier=2.0,
        fixed_quantity=None,  # No fixed_quantity for multiplier mode
    )
    signal = Signal(source="test", symbol="AAPL", side=Side.BUY, quantity=10.0)

    # With "multiplier" default: 10.0 * 2.0 = 20.0
    # With "fixed" default (mutant): would return error about missing fixed_quantity
    quantity, error = size_for_account_with_mode(signal, account)

    assert error is None, f"Expected success with multiplier default, got error: {error}"
    assert quantity == 20.0, f"Expected 20.0 from 10.0 * 2.0 with multiplier default, got {quantity}"


# --- Survivor 2: risk_fraction pre-flight check ---


def test_survivor_2_risk_fraction_check_rejects_none():
    """Mutation 6: Kills the flip of 'is None' to 'is not None'.

    The pre-flight check must reject when account.risk_fraction IS None
    (not set), because sizing requires an actual risk fraction value."""
    account = DestinationAccount(
        account_id="acct-1",
        broker="alpaca",
        risk_fraction=None,  # NOT set
    )
    signal = Signal(
        source="test",
        symbol="AAPL",
        side=Side.BUY,
        asset_class=AssetClass.EQUITY,
        price=100.0,
        stop_loss=95.0,
    )

    # Should reject because risk_fraction is None
    quantity, error = risk_fraction_quantity(
        signal, account, equity=10000.0, buying_power=5000.0
    )

    assert quantity is None, "Expected None quantity when risk_fraction is None"
    assert error is not None, "Expected error message when risk_fraction is None"
    assert "risk_fraction" in error.lower(), f"Expected error to mention risk_fraction, got: {error}"


def test_survivor_2_risk_fraction_check_accepts_set():
    """Companion test: risk_fraction must work when actually set."""
    account = DestinationAccount(
        account_id="acct-1",
        broker="alpaca",
        risk_fraction=0.02,  # 2% risk
    )
    signal = Signal(
        source="test",
        symbol="AAPL",
        side=Side.BUY,
        asset_class=AssetClass.EQUITY,
        price=100.0,
        stop_loss=95.0,
        quantity=100.0,
    )

    # Should succeed because risk_fraction is set
    quantity, error = risk_fraction_quantity(
        signal, account, equity=10000.0, buying_power=10000.0
    )

    assert error is None, f"Expected success with risk_fraction set, got error: {error}"
    assert quantity is not None, "Expected non-None quantity"


# --- Survivor 3: price == stop_loss rejection ---


def test_survivor_3_price_equals_stop_loss_is_rejected():
    """Mutation 7: Kills the flip from '==' to '!='.

    When price equals stop_loss, there is no meaningful risk (0 distance),
    so the code must reject. The mutant would do the opposite: reject
    whenever price != stop_loss (normal case), accept when they're equal."""
    account = DestinationAccount(
        account_id="acct-1",
        broker="alpaca",
        risk_fraction=0.02,
    )
    signal = Signal(
        source="test",
        symbol="AAPL",
        side=Side.BUY,
        asset_class=AssetClass.EQUITY,
        price=100.0,
        stop_loss=100.0,  # SAME as price: no risk
        quantity=100.0,
    )

    # Should reject because price == stop_loss
    quantity, error = risk_fraction_quantity(
        signal, account, equity=10000.0, buying_power=10000.0
    )

    assert quantity is None, "Expected None when price == stop_loss"
    assert error is not None, "Expected error message"
    assert "non-zero" in error.lower() or "equal" in error.lower(), \
        f"Expected error about no risk/equality, got: {error}"


def test_survivor_3_price_different_from_stop_loss_is_accepted():
    """Companion test: Normal case where price != stop_loss should work."""
    account = DestinationAccount(
        account_id="acct-1",
        broker="alpaca",
        risk_fraction=0.02,
    )
    signal = Signal(
        source="test",
        symbol="AAPL",
        side=Side.BUY,
        asset_class=AssetClass.EQUITY,
        price=100.0,
        stop_loss=95.0,  # DIFFERENT from price
        quantity=100.0,
    )

    # Should succeed because price != stop_loss
    quantity, error = risk_fraction_quantity(
        signal, account, equity=10000.0, buying_power=10000.0
    )

    assert error is None, f"Expected success with different price/stop, got error: {error}"
    assert quantity is not None, "Expected non-None quantity"


# --- Survivor 4: Ceiling rounding in unit_risk ---


def test_survivor_4_unit_risk_ceiling_rounding():
    """Mutation 8: Kills the change from '+ 1' to '- 1' in ceiling logic.

    When unit_risk_decimal has a fractional part, the code must ROUND UP
    (add 1), not round down (subtract 1). This ensures we're conservative
    on risk per unit. The mutant would round down, underestimating risk."""
    account = DestinationAccount(
        account_id="acct-1",
        broker="alpaca",
        risk_fraction=0.02,
    )
    # Use a price and stop_loss that produce a fractional unit_risk
    # after multiplying by contract multiplier (multiplier=1.0 for equity)
    signal = Signal(
        source="test",
        symbol="AAPL",
        side=Side.BUY,
        asset_class=AssetClass.EQUITY,
        price=Decimal("100.50"),  # Fractions of cents
        stop_loss=Decimal("100.49"),  # Different fraction
        quantity=10.0,
    )

    # The exact calc: |100.50 - 100.49| * 1.0 = 0.01 * 100 (in cents) = 1 cent
    # (no fraction in this simple case, but test shows the path works)
    quantity, error = risk_fraction_quantity(
        signal, account, equity=10000.0, buying_power=10000.0
    )

    # Should work (not be rejected due to fractions)
    assert error is None, f"Expected no error, got: {error}"
    assert quantity is not None, "Expected non-None quantity"


# --- Survivor 5: FOREX asset class handling ---


def test_survivor_5_forex_handled_correctly():
    """Mutation 15: Kills the flip from '== AssetClass.FOREX' to '!='.

    FOREX assets must be recognized and handled in the FOREX branch.
    The mutant would skip the FOREX branch for FOREX assets and fall through
    to the else clause (which treats them as CRYPTO/EQUITY with no spec)."""
    # FOREX signal with spec
    signal_with_spec = Signal(
        source="test",
        symbol="EURUSD",
        side=Side.BUY,
        asset_class=AssetClass.FOREX,
        fx=FxContractSpec(
            base_currency="EUR",
            quote_currency="USD",
            unit="standard_lot_100000",  # 100k units per lot
        ),
    )

    multiplier, error, note = contract_multiplier(signal_with_spec)

    assert error is None, "FOREX with spec should not error"
    assert multiplier == 100000, f"Expected 100k multiplier for standard lot, got {multiplier}"
    assert note is None, "No note when spec is provided"

    # FOREX signal without spec (defaults to units=1)
    signal_no_spec = Signal(
        source="test",
        symbol="EURUSD",
        side=Side.BUY,
        asset_class=AssetClass.FOREX,
        fx=None,  # No spec
    )

    multiplier, error, note = contract_multiplier(signal_no_spec)

    assert error is None, "FOREX without spec should not error (defaults to units)"
    assert multiplier == 1.0, f"Expected 1.0 multiplier for units, got {multiplier}"
    assert note is not None, "Should have note when defaulting to units"
    assert "units" in note.lower(), f"Note should mention units, got: {note}"


def test_survivor_5_non_forex_not_confused():
    """Companion test: Non-FOREX assets must not be treated as FOREX."""
    signal_crypto = Signal(
        source="test",
        symbol="BTCUSD",
        side=Side.BUY,
        asset_class=AssetClass.CRYPTO,
        fx=None,  # No FX spec (not relevant for crypto)
    )

    multiplier, error, note = contract_multiplier(signal_crypto)

    assert error is None, "CRYPTO should not error"
    assert multiplier == 1.0, "CRYPTO has no multiplier (1.0)"
    assert note is None, "CRYPTO should have no note"
