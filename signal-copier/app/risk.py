"""Position sizing and symbol translation applied per destination account."""
from __future__ import annotations

from app.models import DestinationAccount, Signal


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


def symbol_for_account(signal: Signal, account: DestinationAccount) -> str:
    """Translate a source symbol to the destination's own naming (e.g. TradingView's
    "BTCUSD" to a broker's "BTC/USDT" or an MT5 broker suffix like "EURUSD.pro")."""
    return account.symbol_map.get(signal.symbol, signal.symbol)
