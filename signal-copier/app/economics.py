"""E06: authoritative account economics -- realized P&L, current cost
basis, and completed-trade win rate computed by replaying this account's
own confirmed executions (SignalStore's `orders` table), not a simulated
or estimated equity curve.

Deliberately narrow: this reports what the execution journal actually
proves. It does NOT report:

- Unrealized P&L against a live market price -- that needs a genuine
  current quote (see app/pricing.py's PriceMonitor/get_last_price, which
  only some brokers implement), not something this offline replay can
  invent. `last_fill_price` is reported separately and labeled as exactly
  that -- the last price this account actually traded at, not a live mark.
- Fees/commissions -- `orders` has no fee column yet (see the adoption
  plan's E06 notes); every reported number is gross of costs, and this
  module says so rather than silently treating fee-free as fee-verified.
- A "trade win rate" derived from return PERIODS (a day, a bar) the way
  return-series tools like QuantStats compute their own `win_rate` --
  that's a materially different metric (profitable periods, not winning
  trades) and must never be conflated with the one computed here.

Cost basis uses volume-weighted average cost per (account, symbol). A fill
on the SAME side as the current position (or opening a flat one) only
moves the average cost; a fill on the OPPOSITE side realizes P&L on
whatever quantity it closes, using the average cost at the moment of that
fill -- and, if it closes the entire existing position and still has
quantity left over, opens a fresh position in the new direction at this
fill's own price for the remainder (a "flip").
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from app.db import SignalStore
from app.models import Side


@dataclass
class SymbolEconomics:
    symbol: str
    realized_pnl: float = 0.0
    open_quantity: float = 0.0  # signed: positive = net long, negative = net short
    average_cost: float | None = None
    last_fill_price: float | None = None
    closing_fills: int = 0
    winning_closing_fills: int = 0

    @property
    def completed_trade_win_rate(self) -> float | None:
        """Fraction of REDUCING fills (the only fills that can realize a
        gain or loss) that were profitable -- not a period-based ratio."""
        if self.closing_fills == 0:
            return None
        return self.winning_closing_fills / self.closing_fills


@dataclass
class AccountEconomics:
    account_id: str
    realized_pnl: float = 0.0
    per_symbol: dict[str, SymbolEconomics] = field(default_factory=dict)
    #: Symbols with a fill this replay could not use (missing/invalid
    #: filled_quantity or filled_price, or an unresolved side) -- excluded
    #: from every total above rather than silently treated as zero, per
    #: this module's "incomplete stays incomplete" rule.
    incomplete_symbols: list[str] = field(default_factory=list)

    @property
    def completed_trade_win_rate(self) -> float | None:
        total_closing = sum(s.closing_fills for s in self.per_symbol.values())
        if total_closing == 0:
            return None
        total_wins = sum(s.winning_closing_fills for s in self.per_symbol.values())
        return total_wins / total_closing

    def to_dict(self) -> dict:
        return {
            "account_id": self.account_id,
            "realized_pnl": self.realized_pnl,
            "completed_trade_win_rate": self.completed_trade_win_rate,
            "incomplete_symbols": self.incomplete_symbols,
            "note": "Gross of fees (not yet tracked). last_fill_price is the last price this "
            "account actually traded at, not a live market quote -- unrealized P&L is not "
            "reported here.",
            "per_symbol": {
                symbol: {
                    "realized_pnl": s.realized_pnl,
                    "open_quantity": s.open_quantity,
                    "average_cost": s.average_cost,
                    "last_fill_price": s.last_fill_price,
                    "closing_fills": s.closing_fills,
                    "completed_trade_win_rate": s.completed_trade_win_rate,
                }
                for symbol, s in self.per_symbol.items()
            },
        }


def _signed_quantity(side: str, quantity: float) -> float | None:
    if side == Side.BUY.value:
        return quantity
    if side == Side.SELL.value:
        return -quantity
    return None  # Side.CLOSE (or anything else) is not a resolved direction


def compute_account_economics(store: SignalStore, account_id: str) -> AccountEconomics:
    result = AccountEconomics(account_id=account_id)
    per_symbol = result.per_symbol

    for order in store.list_filled_orders_chronological(account_id):
        symbol = order["symbol"]
        quantity = order["filled_quantity"]
        price = order["filled_price"]
        signed_qty = _signed_quantity(order["side"], quantity) if quantity is not None else None

        if (
            symbol is None
            or signed_qty is None
            or price is None
            or not math.isfinite(signed_qty)
            or not math.isfinite(price)
            or signed_qty == 0
        ):
            if symbol is not None and symbol not in result.incomplete_symbols:
                result.incomplete_symbols.append(symbol)
            continue

        se = per_symbol.setdefault(symbol, SymbolEconomics(symbol=symbol))
        se.last_fill_price = price

        if se.open_quantity == 0 or (se.open_quantity > 0) == (signed_qty > 0):
            # Opening or adding to a position on the same side: only the
            # volume-weighted average cost moves, nothing is realized yet.
            new_quantity = se.open_quantity + signed_qty
            existing_cost = (se.average_cost or 0.0) * abs(se.open_quantity)
            se.average_cost = (existing_cost + price * abs(signed_qty)) / abs(new_quantity)
            se.open_quantity = new_quantity
            continue

        # Opposite side: this fill reduces (and possibly flips) the
        # existing position. Realize P&L on whatever it closes, using the
        # average cost at the moment of this fill.
        closing_quantity = min(abs(signed_qty), abs(se.open_quantity))
        direction = 1 if se.open_quantity > 0 else -1
        realized = (price - se.average_cost) * direction * closing_quantity
        se.realized_pnl += realized
        result.realized_pnl += realized
        se.closing_fills += 1
        if realized > 0:
            se.winning_closing_fills += 1

        remainder = abs(signed_qty) - closing_quantity
        se.open_quantity += signed_qty
        if remainder > 0:
            # Flipped through flat: what's left opens a fresh position in
            # the new direction, priced at this same fill.
            se.average_cost = price
            se.open_quantity = remainder if signed_qty > 0 else -remainder
        elif se.open_quantity == 0:
            se.average_cost = None

    return result
