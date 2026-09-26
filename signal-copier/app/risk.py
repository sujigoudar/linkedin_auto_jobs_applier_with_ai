"""Position sizing and symbol translation applied per destination account."""
from __future__ import annotations

from app.models import DestinationAccount, Signal


def size_for_account(signal: Signal, account: DestinationAccount) -> float:
    """Decide how much to trade on this account for this signal.

    Precedence: an account-level fixed quantity wins outright (useful for
    accounts that always trade a flat size regardless of the source's own
    sizing); otherwise the source's quantity is scaled by the account's
    multiplier (useful for copying a master account into a smaller/larger
    sub-account proportionally).
    """
    if account.fixed_quantity is not None:
        return account.fixed_quantity
    base_quantity = signal.quantity if signal.quantity is not None else 1.0
    return base_quantity * account.multiplier


def symbol_for_account(signal: Signal, account: DestinationAccount) -> str:
    """Translate a source symbol to the destination's own naming (e.g. TradingView's
    "BTCUSD" to a broker's "BTC/USDT" or an MT5 broker suffix like "EURUSD.pro")."""
    return account.symbol_map.get(signal.symbol, signal.symbol)
