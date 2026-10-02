"""Position sizing and symbol translation applied per destination account."""
from __future__ import annotations

import math

from app.models import AssetClass, DestinationAccount, Signal


class UnsizedEntryError(ValueError):
    """Raised when an entry signal has no quantity and the account has no fixed_quantity."""

    pass


def size_for_account(signal: Signal, account: DestinationAccount) -> float:
    """Decide how much to trade on this account for this signal.

    Precedence: an account-level fixed quantity wins outright (useful for
    accounts that always trade a flat size regardless of the source's own
    sizing); otherwise the source's quantity is scaled by the account's
    multiplier (useful for copying a master account into a smaller/larger
    sub-account proportionally).

    Raises:
        UnsizedEntryError: When both account.fixed_quantity and signal.quantity
            are None (refuses to default to 1.0).
    """
    if account.fixed_quantity is not None:
        return account.fixed_quantity
    if signal.quantity is not None:
        return signal.quantity * account.multiplier
    raise UnsizedEntryError(
        f"entry has no quantity and account '{account.account_id}' has no fixed_quantity "
        "(refusing to default to 1.0)"
    )


def size_for_account_with_mode(
    signal: Signal, account: DestinationAccount, equity: float | None = None
) -> tuple[float | None, str | None]:
    """WC-20 STEP 3: Compute position sizing based on account's sizing_mode.

    Evaluates the account's sizing_mode configuration and delegates to the
    appropriate sizing function:
    - "multiplier": uses fixed_quantity if set, else signal.quantity * multiplier
    - "fixed": uses fixed_quantity only (requires fixed_quantity to be set)
    - "risk_fraction": dynamic sizing based on risk fraction and stop loss

    Args:
        signal: The incoming signal.
        account: The destination account with sizing_mode and related config.
        equity: The account's current equity (required for risk_fraction mode,
                optional for other modes).

    Returns:
        (quantity, None) if sizing succeeds, or (None, error_message) if rejected.
    """
    sizing_mode = account.sizing_mode or "multiplier"

    if sizing_mode == "fixed":
        if account.fixed_quantity is None:
            return None, f"fixed sizing mode requires fixed_quantity; account '{account.account_id}' has none"
        return account.fixed_quantity, None

    elif sizing_mode == "risk_fraction":
        # risk_fraction mode requires equity, price, and stop_loss
        quantity, error = risk_fraction_quantity(signal, account, equity)
        if error is not None:
            return None, error
        return quantity, None

    elif sizing_mode == "multiplier":
        # Default multiplier mode
        try:
            quantity = size_for_account(signal, account)
            return quantity, None
        except UnsizedEntryError as e:
            return None, str(e)

    else:
        return None, f"unknown sizing_mode '{sizing_mode}' on account '{account.account_id}'"


def risk_fraction_quantity(
    signal: Signal, account: DestinationAccount, equity: float | None
) -> tuple[float | None, str | None]:
    """Size a position based on risk fraction: floor(equity * risk_fraction / (|price - stop_loss| * multiplier)).

    Args:
        signal: The incoming signal with price, stop_loss, and asset class.
        account: The destination account with risk_fraction and contract specs.
        equity: The account's current equity (required; fail closed if missing).

    Returns:
        (quantity, None) if sizing succeeds, or (None, error_message) if inputs are missing or invalid.

    Rejects when:
        - account.risk_fraction is None
        - signal.price is None
        - signal.stop_loss is None
        - price == stop_loss (division by zero)
        - calculated quantity is 0 or less
        - equity is None
    """
    if account.risk_fraction is None:
        return None, "risk_fraction sizing requires account.risk_fraction; not set"
    if signal.price is None:
        return None, "risk_fraction sizing requires signal.price; signal has none"
    if signal.stop_loss is None:
        return None, "risk_fraction sizing requires signal.stop_loss; signal has none"
    if equity is None:
        return None, "risk_fraction sizing requires equity; adapter reports none"

    # Get contract multiplier based on asset class
    multiplier, error, _note = contract_multiplier(signal)
    if error is not None:
        return None, error

    price_stop_diff = abs(signal.price - signal.stop_loss)
    if price_stop_diff == 0:
        return None, "risk_fraction sizing requires non-zero (price - stop_loss); they are equal"

    quantity = math.floor(equity * account.risk_fraction / (price_stop_diff * multiplier))
    if quantity <= 0:
        return None, f"risk_fraction sizing calculated quantity {quantity}; must be positive"

    return quantity, None


def symbol_for_account(signal: Signal, account: DestinationAccount) -> str:
    """Translate a source symbol to the destination's own naming (e.g. TradingView's
    "BTCUSD" to a broker's "BTC/USDT" or an MT5 broker suffix like "EURUSD.pro")."""
    return account.symbol_map.get(signal.symbol, signal.symbol)


def contract_multiplier(signal: Signal) -> tuple[float, str | None, str | None]:
    """Extract the contract multiplier from a Signal based on its asset class and spec.

    Returns (multiplier, error_message, note_message). error_message is non-None only when
    a required contract spec is missing (rejection case). note_message is non-None only when
    providing informational context (e.g., FX with no spec: "fx unit assumed: units").

    - CRYPTO/EQUITY: multiplier = 1.0 (no spec required)
    - OPTION: multiplier = signal.option.multiplier (default 100.0, spec required)
    - FUTURE: multiplier = signal.future.multiplier (spec required)
    - FOREX with spec: multiplier derived from unit ("standard_lot_100000"→100000,
                       "mini_lot_10000"→10000, "micro_lot_1000"→1000, "units"→1)
    - FOREX without spec: multiplier = 1.0, note_message = "fx unit assumed: units" (non-error)
    """
    if signal.asset_class == AssetClass.OPTION:
        if signal.option is None:
            return 1.0, "OPTION signal missing required OptionContractSpec", None
        return signal.option.multiplier, None, None
    elif signal.asset_class == AssetClass.FUTURE:
        if signal.future is None:
            return 1.0, "FUTURE signal missing required FutureContractSpec", None
        return signal.future.multiplier, None, None
    elif signal.asset_class == AssetClass.FOREX:
        if signal.fx is None:
            # FX spec optional; default to units (1.0 multiplier) with info note
            return 1.0, None, "fx unit assumed: units"
        # Parse unit string: "standard_lot_100000" -> 100000
        unit_str = signal.fx.unit
        unit_mapping = {
            "standard_lot_100000": 100000,
            "mini_lot_10000": 10000,
            "micro_lot_1000": 1000,
            "units": 1,
        }
        multiplier = unit_mapping.get(unit_str, 1.0)
        return multiplier, None, None
    else:
        # CRYPTO and EQUITY have no contract multiplier
        return 1.0, None, None
