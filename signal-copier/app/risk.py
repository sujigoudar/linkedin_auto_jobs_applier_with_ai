"""Position sizing and symbol translation applied per destination account."""
from __future__ import annotations

from app.models import DestinationAccount, Signal


def size_for_account(signal: Signal, account: DestinationAccount, *, drawdown_size_multiplier: float = 1.0) -> float:
    """Decide how much to trade on this account for this signal.

    Precedence: an account-level fixed quantity wins outright (useful for
    accounts that always trade a flat size regardless of the source's own
    sizing); otherwise the source's quantity is scaled by the account's
    multiplier (useful for copying a master account into a smaller/larger
    sub-account proportionally).

    `drawdown_size_multiplier` (app/drawdown_governor.py's REDUCE_NEW_SIZE
    action, real sizing-path wiring, not just a logged intent): applied
    LAST, to whichever base size the precedence above already picked --
    including a `fixed_quantity` account, since REDUCE_NEW_SIZE exists to
    reduce the risk of a NEW entry regardless of how that account is
    normally sized. Defaults to 1.0 (no change) so every existing caller
    that doesn't pass it keeps today's exact sizing.
    """
    if account.fixed_quantity is not None:
        base = account.fixed_quantity
    else:
        base_quantity = signal.quantity if signal.quantity is not None else 1.0
        base = base_quantity * account.multiplier
    return base * drawdown_size_multiplier


def symbol_for_account(signal: Signal, account: DestinationAccount) -> str:
    """Translate a source symbol to the destination's own naming (e.g. TradingView's
    "BTCUSD" to a broker's "BTC/USDT" or an MT5 broker suffix like "EURUSD.pro")."""
    return account.symbol_map.get(signal.symbol, signal.symbol)
