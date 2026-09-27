"""E06: authoritative account economics -- realized P&L, current cost
basis, and two distinct win-rate metrics computed by replaying this
account's own confirmed executions (SignalStore's `orders` table), not a
simulated or estimated equity curve.

Two win rates, not one, because they answer different questions and must
never be conflated (see the commercial platform's
signal-portfolio-commercial/spec/docs/04_metrics_accounting_and_truth.md,
"Completed-lifecycle win rate"):

- `closing_fill_win_rate`: fraction of individual REDUCING FILLS that were
  profitable. A single position closed via three partial-exit fills counts
  as three observations here, not one.
- `completed_lifecycle_win_rate`: fraction of independently completed
  POSITION EPISODES (flat -> non-flat -> flat again) that were net
  profitable, with break-even episodes counted separately rather than
  folded into either winners or losers. The same three-partial-exit
  position above counts as exactly one observation here.

`completed_trade_win_rate` (the original name) is kept as a deprecated
alias for `closing_fill_win_rate` -- despite its name, it was always the
fill-based metric, never the episode-based one; renaming it outright
would have silently changed the meaning of a name calling code may still
depend on.

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
    #: Independently completed position episodes (flat -> non-flat -> flat),
    #: as distinct from `closing_fills` above -- a position closed across
    #: several partial-exit fills is still exactly one episode.
    completed_episodes: int = 0
    winning_episodes: int = 0
    breakeven_episodes: int = 0
    #: Realized P&L accumulated so far within the CURRENTLY OPEN episode --
    #: not itself exposed; reset to 0.0 each time an episode completes.
    _open_episode_pnl: float = 0.0

    @property
    def closing_fill_win_rate(self) -> float | None:
        """Fraction of REDUCING fills (the only fills that can realize a
        gain or loss) that were profitable -- not a period-based ratio, and
        not the same thing as `completed_lifecycle_win_rate` below (a
        position closed over several fills counts once there, but once per
        fill here)."""
        if self.closing_fills == 0:
            return None
        return self.winning_closing_fills / self.closing_fills

    @property
    def completed_trade_win_rate(self) -> float | None:
        """Deprecated alias for `closing_fill_win_rate` -- kept because this
        was always the fill-based metric under a name that suggested
        otherwise; see `completed_lifecycle_win_rate` for the metric this
        name would imply."""
        return self.closing_fill_win_rate

    @property
    def losing_episodes(self) -> int:
        return self.completed_episodes - self.winning_episodes - self.breakeven_episodes

    @property
    def completed_lifecycle_win_rate(self) -> float | None:
        """Fraction of independently completed position episodes that were
        net profitable. Break-even episodes count toward the denominator
        (they are a completed episode) but not toward the numerator --
        exposed separately via `breakeven_episodes`, never silently folded
        into either winners or losers."""
        if self.completed_episodes == 0:
            return None
        return self.winning_episodes / self.completed_episodes


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
    def closing_fill_win_rate(self) -> float | None:
        total_closing = sum(s.closing_fills for s in self.per_symbol.values())
        if total_closing == 0:
            return None
        total_wins = sum(s.winning_closing_fills for s in self.per_symbol.values())
        return total_wins / total_closing

    @property
    def completed_trade_win_rate(self) -> float | None:
        """Deprecated alias for `closing_fill_win_rate` -- see that
        property's docstring."""
        return self.closing_fill_win_rate

    @property
    def completed_lifecycle_win_rate(self) -> float | None:
        total_episodes = sum(s.completed_episodes for s in self.per_symbol.values())
        if total_episodes == 0:
            return None
        total_wins = sum(s.winning_episodes for s in self.per_symbol.values())
        return total_wins / total_episodes

    def to_dict(self) -> dict:
        return {
            "account_id": self.account_id,
            "realized_pnl": self.realized_pnl,
            "closing_fill_win_rate": self.closing_fill_win_rate,
            "completed_lifecycle_win_rate": self.completed_lifecycle_win_rate,
            "completed_trade_win_rate": self.completed_trade_win_rate,
            "incomplete_symbols": self.incomplete_symbols,
            "note": "Gross of fees (not yet tracked). last_fill_price is the last price this "
            "account actually traded at, not a live market quote -- unrealized P&L is not "
            "reported here. completed_trade_win_rate is a deprecated alias for "
            "closing_fill_win_rate (a per-fill metric); completed_lifecycle_win_rate is the "
            "separate per-episode metric its name would suggest.",
            "per_symbol": {
                symbol: {
                    "realized_pnl": s.realized_pnl,
                    "open_quantity": s.open_quantity,
                    "average_cost": s.average_cost,
                    "last_fill_price": s.last_fill_price,
                    "closing_fills": s.closing_fills,
                    "closing_fill_win_rate": s.closing_fill_win_rate,
                    "completed_episodes": s.completed_episodes,
                    "winning_episodes": s.winning_episodes,
                    "breakeven_episodes": s.breakeven_episodes,
                    "completed_lifecycle_win_rate": s.completed_lifecycle_win_rate,
                }
                for symbol, s in self.per_symbol.items()
            },
        }


def _close_episode(se: SymbolEconomics) -> None:
    """Classify and finalize the episode that just returned to flat (or
    flipped through flat), then reset the accumulator for whatever episode
    comes next."""
    se.completed_episodes += 1
    if se._open_episode_pnl > 0:
        se.winning_episodes += 1
    elif se._open_episode_pnl == 0:
        se.breakeven_episodes += 1
    se._open_episode_pnl = 0.0


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
        se._open_episode_pnl += realized
        se.closing_fills += 1
        if realized > 0:
            se.winning_closing_fills += 1

        remainder = abs(signed_qty) - closing_quantity
        se.open_quantity += signed_qty
        if remainder > 0:
            # Flipped through flat: the position touched flat, so the
            # episode that was open closes here (classified below), and
            # what's left opens a FRESH episode/position in the new
            # direction, priced at this same fill.
            se.average_cost = price
            se.open_quantity = remainder if signed_qty > 0 else -remainder
            _close_episode(se)
        elif se.open_quantity == 0:
            se.average_cost = None
            _close_episode(se)

    return result
