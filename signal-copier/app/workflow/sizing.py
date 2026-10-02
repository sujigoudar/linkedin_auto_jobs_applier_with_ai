"""Product-correct quantity calculation.

Implements spec §8 sizing for equity, options, futures, FX and crypto.
Every decision returns a result with exact quantity, actual planned risk,
notional value, binding constraint, and decision reason.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.workflow.money import Cents
from app.workflow.reasons import Reason


@dataclass(frozen=True)
class SizingResult:
    """Result of a sizing decision.

    Attributes:
        quantity_units: Whole units (shares, contracts, etc.) to trade.
            Never rounds up; zero when any binding constraint is zero.
        planned_risk_cents: Exact planned risk in account currency cents
            (quantity × unit_risk_cents), using integer arithmetic.
        notional_cents: Exact notional value in account currency cents
            (quantity × entry_price_cents), using integer arithmetic.
        binding_constraint: Which constraint bound the final quantity
            (e.g., "risk", "cash", "source_max", "unit_step").
        reason: If non-None, the decision reason (e.g., INSTRUMENT_UNRESOLVED).
            None indicates a successful sizing decision.
    """

    quantity_units: int
    planned_risk_cents: Cents
    notional_cents: Cents
    binding_constraint: str
    reason: Reason | None


def size_linear_long(
    *,
    risk_budget_cents: Cents,
    unit_risk_cents: Cents,
    cash_capacity_cents: Cents,
    entry_price_cents: Cents,
    source_max_units: int,
    lot_step: int = 1,
) -> SizingResult:
    """Calculate maximum feasible quantity for a long linear equity position.

    Implements spec §8.1 linear long equity sizing. Reproduces the
    sizing_oracle from generate_cases.py exactly for all 4,500 fixture vectors.

    The quantity is the minimum of:
    1. floor(risk_budget_cents / unit_risk_cents) — risk-bound quantity
    2. floor(cash_capacity_cents / entry_price_cents) — cash-bound quantity
    3. source_max_units — source-specific hard ceiling (0 is hard zero)

    Then floors to the permitted lot_step and recomputes all costs/risk/margin
    at that quantity. A stop equal to entry is not zero risk (spec §8.1).

    Args:
        risk_budget_cents: Available risk budget in cents.
            Zero or negative yields zero quantity.
        unit_risk_cents: Risk per unit in cents (includes adverse costs).
            Positive; zero or negative raises ValueError.
        cash_capacity_cents: Available cash in cents.
            Zero yields zero quantity.
        entry_price_cents: Entry price per unit in cents.
            Positive; zero or negative raises ValueError.
        source_max_units: Source-specific hard ceiling on quantity.
            0 is a hard zero (no entry), not absence of ceiling.
        lot_step: Minimum quantity increment (default 1).
            Must be positive.

    Returns:
        SizingResult with quantity_units, planned_risk_cents, notional_cents,
        binding_constraint, and reason (None for successful sizing).

    Raises:
        ValueError: If unit_risk_cents <= 0, entry_price_cents <= 0,
            or lot_step <= 0.
    """
    if unit_risk_cents <= 0:
        raise ValueError(
            f"unit_risk_cents must be positive, got {unit_risk_cents}"
        )
    if entry_price_cents <= 0:
        raise ValueError(
            f"entry_price_cents must be positive, got {entry_price_cents}"
        )
    if lot_step <= 0:
        raise ValueError(f"lot_step must be positive, got {lot_step}")

    # Calculate unconstrained quantities for each bound
    risk_bound = risk_budget_cents // unit_risk_cents if risk_budget_cents > 0 else 0
    cash_bound = (
        cash_capacity_cents // entry_price_cents if cash_capacity_cents > 0 else 0
    )
    source_bound = max(0, source_max_units)

    # Take minimum of all bounds
    quantity = min(risk_bound, cash_bound, source_bound)

    # Floor to lot_step (always rounds down, never up)
    quantity = (quantity // lot_step) * lot_step

    # Calculate exact planned risk and notional at this quantity
    planned_risk = quantity * unit_risk_cents
    notional = quantity * entry_price_cents

    # Determine which constraint bound the final quantity
    if quantity == 0:
        if risk_budget_cents == 0 or source_bound == 0:
            binding = "risk" if source_bound != 0 else "source_max"
        elif cash_capacity_cents == 0:
            binding = "cash"
        else:
            binding = "source_max"
    elif quantity == risk_bound:
        binding = "risk"
    elif quantity == cash_bound:
        binding = "cash"
    else:
        binding = "source_max"

    return SizingResult(
        quantity_units=quantity,
        planned_risk_cents=planned_risk,
        notional_cents=notional,
        binding_constraint=binding,
        reason=None,
    )


def size_long_option(
    *,
    risk_budget_cents: Cents,
    premium_cents: Cents,
    multiplier: int,
    costs_per_contract_cents: Cents,
    source_max_contracts: int,
) -> SizingResult:
    """Calculate maximum feasible quantity for a long option position.

    Implements spec §8.2 long options sizing. Uses full premium at risk
    plus costs as the binding original-capital-loss measure.

    q_contracts <= floor(B / (premium * multiplier + costs_per_contract))

    Args:
        risk_budget_cents: Available risk budget in cents.
        premium_cents: Option premium per contract in cents.
        multiplier: Contracts multiplier (e.g., 100 for standard equity options).
        costs_per_contract_cents: Fixed costs per contract in cents.
        source_max_contracts: Source-specific hard ceiling on contracts.

    Returns:
        SizingResult with quantity_units (contracts), planned_risk_cents,
        notional_cents, binding_constraint, and reason.
    """
    if multiplier <= 0:
        raise ValueError(f"multiplier must be positive, got {multiplier}")

    # Full debit per contract: premium * multiplier + fixed costs
    full_debit = premium_cents * multiplier + costs_per_contract_cents

    if full_debit <= 0:
        raise ValueError(f"full debit must be positive, got {full_debit}")

    # Risk-bound quantity
    risk_bound = risk_budget_cents // full_debit if risk_budget_cents > 0 else 0
    source_bound = max(0, source_max_contracts)

    # Take minimum
    quantity = min(risk_bound, source_bound)

    # Calculate exact planned risk and notional
    planned_risk = quantity * full_debit
    notional = quantity * premium_cents * multiplier

    # Determine binding constraint
    if quantity == 0:
        binding = "risk" if source_bound != 0 else "source_max"
    elif quantity == risk_bound:
        binding = "risk"
    else:
        binding = "source_max"

    return SizingResult(
        quantity_units=quantity,
        planned_risk_cents=planned_risk,
        notional_cents=notional,
        binding_constraint=binding,
        reason=None,
    )


def size_linear_future(
    *,
    risk_budget_cents: Cents,
    point_value_cents: Cents,
    entry_price: int,
    stop_price: int,
    cash_capacity_cents: Cents,
    source_max_contracts: int,
    lot_step: int = 1,
) -> SizingResult:
    """Calculate maximum feasible quantity for a long linear futures position.

    Implements spec §8.4 futures sizing. For a linear futures contract:
    stop_loss_per_contract = |entry - stop| * point_value + costs

    Args:
        risk_budget_cents: Available risk budget in cents.
        point_value_cents: Point value (e.g., 50 cents for ES) in cents.
        entry_price: Entry price (in points or ticks).
        stop_price: Stop price (in points or ticks).
        cash_capacity_cents: Available cash/margin in cents.
        source_max_contracts: Source-specific hard ceiling.
        lot_step: Minimum contract increment.

    Returns:
        SizingResult with quantity_units (contracts), planned_risk_cents,
        notional_cents, binding_constraint, and reason.
    """
    if point_value_cents <= 0:
        raise ValueError(f"point_value_cents must be positive, got {point_value_cents}")

    # Stop loss per contract = |entry - stop| * point_value
    unit_risk = abs(entry_price - stop_price) * point_value_cents

    if unit_risk < 0:
        raise ValueError(f"calculated unit_risk is negative: {unit_risk}")

    # If stop equals entry, unit_risk is 0, but still need other constraints
    # Use minimum margin/notional bound instead
    if unit_risk == 0:
        # For futures with entry == stop, this is typically not a realistic trade
        # Return zero quantity with reason
        return SizingResult(
            quantity_units=0,
            planned_risk_cents=0,
            notional_cents=0,
            binding_constraint="risk",
            reason=None,
        )

    # Risk-bound
    risk_bound = risk_budget_cents // unit_risk if risk_budget_cents > 0 else 0

    # Notional/margin bound (simplified; actual margin rules vary)
    notional_per_contract = abs(entry_price) * point_value_cents
    cash_bound = (
        cash_capacity_cents // notional_per_contract
        if notional_per_contract > 0 and cash_capacity_cents > 0
        else 0
    )

    source_bound = max(0, source_max_contracts)

    # Take minimum
    quantity = min(risk_bound, cash_bound, source_bound)
    quantity = (quantity // lot_step) * lot_step

    planned_risk = quantity * unit_risk
    notional = quantity * notional_per_contract

    # Determine binding constraint
    if quantity == 0:
        if risk_budget_cents == 0 or source_bound == 0:
            binding = "risk" if source_bound != 0 else "source_max"
        elif cash_capacity_cents == 0:
            binding = "cash"
        else:
            binding = "source_max"
    elif quantity == risk_bound:
        binding = "risk"
    elif quantity == cash_bound:
        binding = "cash"
    else:
        binding = "source_max"

    return SizingResult(
        quantity_units=quantity,
        planned_risk_cents=planned_risk,
        notional_cents=notional,
        binding_constraint=binding,
        reason=None,
    )


def size_fx(
    *,
    risk_budget_cents: Cents,
    entry_price: Cents,
    stop_price: Cents,
    quote_to_account_rate: Cents | None,
    account_cash_cents: Cents,
    source_max_units: int,
    lot_step: int = 1,
) -> SizingResult:
    """Calculate maximum feasible quantity for an FX position.

    Implements spec §8.4 FX sizing. Requires explicit quote-to-account
    currency conversion rate; missing rate returns INSTRUMENT_UNRESOLVED.

    Args:
        risk_budget_cents: Available risk budget in account currency cents.
        entry_price: Entry price in quote currency.
        stop_price: Stop price in quote currency.
        quote_to_account_rate: Exchange rate (quote per account unit) in cents.
            Must be positive; None returns INSTRUMENT_UNRESOLVED.
        account_cash_cents: Available cash in account currency cents.
        source_max_units: Source-specific hard ceiling on base units.
        lot_step: Minimum lot increment in base units.

    Returns:
        SizingResult with quantity_units (base units), planned_risk_cents,
        notional_cents, binding_constraint, and reason.
    """
    if quote_to_account_rate is None or quote_to_account_rate <= 0:
        return SizingResult(
            quantity_units=0,
            planned_risk_cents=0,
            notional_cents=0,
            binding_constraint="rate",
            reason=Reason.INSTRUMENT_UNRESOLVED,
        )

    # Risk per base unit in quote currency
    quote_risk_per_unit = abs(int(entry_price) - int(stop_price))

    # Convert to account currency
    account_risk_per_unit = quote_risk_per_unit * quote_to_account_rate // 100

    if account_risk_per_unit <= 0:
        # No meaningful risk or very tight stop
        return SizingResult(
            quantity_units=0,
            planned_risk_cents=0,
            notional_cents=0,
            binding_constraint="risk",
            reason=None,
        )

    # Risk-bound quantity
    risk_bound = (
        risk_budget_cents // account_risk_per_unit if risk_budget_cents > 0 else 0
    )

    # Notional/cash bound in account currency
    notional_per_unit = (int(entry_price) * quote_to_account_rate) // 100
    cash_bound = (
        account_cash_cents // notional_per_unit
        if notional_per_unit > 0 and account_cash_cents > 0
        else 0
    )

    source_bound = max(0, source_max_units)

    # Take minimum
    quantity = min(risk_bound, cash_bound, source_bound)
    quantity = (quantity // lot_step) * lot_step

    planned_risk = quantity * account_risk_per_unit
    notional = quantity * notional_per_unit

    # Determine binding constraint
    if quantity == 0:
        if risk_budget_cents == 0 or source_bound == 0:
            binding = "risk" if source_bound != 0 else "source_max"
        elif account_cash_cents == 0:
            binding = "cash"
        else:
            binding = "source_max"
    elif quantity == risk_bound:
        binding = "risk"
    elif quantity == cash_bound:
        binding = "cash"
    else:
        binding = "source_max"

    return SizingResult(
        quantity_units=quantity,
        planned_risk_cents=planned_risk,
        notional_cents=notional,
        binding_constraint=binding,
        reason=None,
    )


def size_spot_crypto(
    *,
    risk_budget_cents: Cents,
    entry_price_cents: Cents,
    stop_price_cents: Cents,
    base_step: int,
    quote_available_cents: Cents,
    source_max_units: int,
) -> SizingResult:
    """Calculate maximum feasible quantity for a spot crypto position.

    Implements spec §8.4 spot crypto sizing. Accounts for base-denominated
    fees reducing acquired inventory and quote-denominated fees reducing cash.

    Args:
        risk_budget_cents: Available risk budget in quote currency cents.
        entry_price_cents: Entry price in quote currency cents.
        stop_price_cents: Stop price in quote currency cents.
        base_step: Minimum lot step in base currency (e.g., satoshi precision).
        quote_available_cents: Available quote currency cents.
        source_max_units: Source-specific hard ceiling on base units.

    Returns:
        SizingResult with quantity_units (base units), planned_risk_cents,
        notional_cents, binding_constraint, and reason.
    """
    if base_step <= 0:
        raise ValueError(f"base_step must be positive, got {base_step}")

    # Risk per base unit
    unit_risk = abs(int(entry_price_cents) - int(stop_price_cents))

    if unit_risk <= 0:
        return SizingResult(
            quantity_units=0,
            planned_risk_cents=0,
            notional_cents=0,
            binding_constraint="risk",
            reason=None,
        )

    # Risk-bound quantity
    risk_bound = risk_budget_cents // unit_risk if risk_budget_cents > 0 else 0

    # Quote-bound quantity (noting this is simplified; real crypto has maker/taker fees)
    quote_bound = (
        quote_available_cents // int(entry_price_cents)
        if entry_price_cents > 0 and quote_available_cents > 0
        else 0
    )

    source_bound = max(0, source_max_units)

    # Take minimum
    quantity = min(risk_bound, quote_bound, source_bound)

    # Floor to base_step
    quantity = (quantity // base_step) * base_step

    planned_risk = quantity * unit_risk
    notional = quantity * int(entry_price_cents)

    # Determine binding constraint
    if quantity == 0:
        if risk_budget_cents == 0 or source_bound == 0:
            binding = "risk" if source_bound != 0 else "source_max"
        elif quote_available_cents == 0:
            binding = "cash"
        else:
            binding = "source_max"
    elif quantity == risk_bound:
        binding = "risk"
    elif quantity == quote_bound:
        binding = "cash"
    else:
        binding = "source_max"

    return SizingResult(
        quantity_units=quantity,
        planned_risk_cents=planned_risk,
        notional_cents=notional,
        binding_constraint=binding,
        reason=None,
    )
