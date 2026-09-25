"""The Signal Backtester's replay engine: given a set of historical
signals and a price history source, ask "what would have happened to
each signal's own resolved stop/target" — NOT a portfolio simulation.

## Scope, stated plainly

Each signal with a resolved `stop_loss` and/or `take_profit` is replayed
as one independent round-trip trade (entry at the signal's own `price`,
walked bar-by-bar forward until the stop, the target, or the available
price history runs out). This is a deliberate, documented scoping
decision, not an oversight:

- **No shared-account capital modeling.** Every signal is sized at its
  own `quantity`; two overlapping signals on the same account aren't
  constrained by a shared capital limit the way `app/risk.py` /
  `app/lifecycle/` constrain live trading. A portfolio-level replay
  (shared capital across concurrent positions) is a real, larger
  follow-up, not implemented here.
- **No `Side.CLOSE` replay.** A `close` signal's real meaning depends on
  runtime position state (`SignalStore.get_position` / `CloseArbiter`)
  this replay doesn't reconstruct; CLOSE signals are reported, not
  simulated, so they're visible in the report rather than silently
  dropped.
- **No slippage/fee modeling.** A resolved trade fills at the exact
  stop/target price (see app/backtest/simulator.py's `BarResult.fill_price`)
  — real fills would differ by spread, slippage, and any broker fee,
  none of which is modeled here yet.
- **No path-dependent guessing.** A bar where both the stop and target
  fall inside its [low, high] range is `AMBIGUOUS`, not resolved by
  assumption — see app/backtest/simulator.py's module docstring.

Every one of these is a live limitation on how far to trust a report
from this engine, not a solved problem it's quietly hiding.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from app.backtest.models import PriceHistoryProvider
from app.backtest.simulator import BarOutcome, simulate_bar_fill
from app.models import Side


class TradeOutcome(str, enum.Enum):
    WIN = "win"
    LOSS = "loss"
    AMBIGUOUS = "ambiguous"  # a bar touched both stop and target; order unrecoverable from OHLC
    STILL_OPEN = "still_open"  # available price history ran out before stop or target was hit
    NO_PRICE_DATA = "no_price_data"  # the price provider has no bars for this symbol/period at all
    NO_EXIT_LEVELS = "no_exit_levels"  # signal has neither stop_loss nor take_profit resolved — nothing to simulate
    NOT_REPLAYED = "not_replayed"  # e.g. a raw CLOSE signal — depends on runtime position state this engine doesn't reconstruct


@dataclass
class ReplayedTrade:
    signal_id: str
    source: str
    symbol: str
    side: Side
    analyst: str | None
    entry_time: datetime
    entry_price: float | None
    quantity: float | None
    stop_price: float | None
    target_price: float | None
    outcome: TradeOutcome
    exit_time: datetime | None = None
    exit_price: float | None = None
    pnl: float | None = None
    note: str = ""


@dataclass
class BacktestReport:
    trades: list[ReplayedTrade] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.trades)

    def count(self, outcome: TradeOutcome) -> int:
        return sum(1 for t in self.trades if t.outcome == outcome)

    @property
    def resolved_trades(self) -> list[ReplayedTrade]:
        return [t for t in self.trades if t.outcome in (TradeOutcome.WIN, TradeOutcome.LOSS)]

    @property
    def win_rate(self) -> float | None:
        resolved = self.resolved_trades
        if not resolved:
            return None
        return self.count(TradeOutcome.WIN) / len(resolved)

    @property
    def total_pnl(self) -> float:
        return sum(t.pnl for t in self.resolved_trades if t.pnl is not None)

    @property
    def average_win(self) -> float | None:
        wins = [t.pnl for t in self.trades if t.outcome == TradeOutcome.WIN and t.pnl is not None]
        return sum(wins) / len(wins) if wins else None

    @property
    def average_loss(self) -> float | None:
        losses = [t.pnl for t in self.trades if t.outcome == TradeOutcome.LOSS and t.pnl is not None]
        return sum(losses) / len(losses) if losses else None

    @property
    def expectancy(self) -> float | None:
        """Average P&L per resolved trade — None (not 0) when there's
        nothing resolved to average, so an empty backtest can't be
        mistaken for a breakeven one."""
        resolved = self.resolved_trades
        if not resolved:
            return None
        return self.total_pnl / len(resolved)

    @property
    def profit_factor(self) -> tuple[float | None, str]:
        """Returns (value, note). Explicitly not `float('inf')` on zero
        gross losses — "an unexplained infinite score" is exactly what the
        design this engine follows calls out as wrong. Returns `(None,
        reason)` for every undefined case instead."""
        gross_profit = sum(t.pnl for t in self.trades if t.outcome == TradeOutcome.WIN and t.pnl)
        gross_loss = -sum(t.pnl for t in self.trades if t.outcome == TradeOutcome.LOSS and t.pnl)
        if gross_profit == 0 and gross_loss == 0:
            return None, "no resolved trades"
        if gross_loss == 0:
            return None, "undefined: no losing trades in the resolved set"
        return gross_profit / gross_loss, ""

    def summary(self) -> dict:
        pf_value, pf_note = self.profit_factor
        return {
            "total_signals": self.total,
            "wins": self.count(TradeOutcome.WIN),
            "losses": self.count(TradeOutcome.LOSS),
            "ambiguous": self.count(TradeOutcome.AMBIGUOUS),
            "still_open": self.count(TradeOutcome.STILL_OPEN),
            "no_price_data": self.count(TradeOutcome.NO_PRICE_DATA),
            "no_exit_levels": self.count(TradeOutcome.NO_EXIT_LEVELS),
            "not_replayed": self.count(TradeOutcome.NOT_REPLAYED),
            "resolved_trades": len(self.resolved_trades),
            "win_rate": self.win_rate,
            "total_pnl": self.total_pnl,
            "average_win": self.average_win,
            "average_loss": self.average_loss,
            "expectancy": self.expectancy,
            "profit_factor": pf_value,
            "profit_factor_note": pf_note,
        }


class BacktestEngine:
    def __init__(self, price_provider: PriceHistoryProvider, max_hold: timedelta = timedelta(days=30)):
        self.price_provider = price_provider
        #: How far forward to look for a resolving bar before giving up and
        #: reporting STILL_OPEN — a real position doesn't have an infinite
        #: horizon in a backtest that has to terminate.
        self.max_hold = max_hold

    def run(self, signal_rows: list[dict]) -> BacktestReport:
        """`signal_rows` — dicts shaped like `SignalStore.list_signals_in_range`'s
        return value. Reads `id`, `source`, `symbol`, `side`, `analyst`,
        `price`, `stop_loss`, `take_profit`, `quantity`, `received_at`."""
        trades = [self._replay_one(row) for row in signal_rows]
        return BacktestReport(trades=trades)

    def _replay_one(self, row: dict) -> ReplayedTrade:
        side = Side(row["side"])
        entry_time = _parse_time(row["received_at"])
        base = dict(
            signal_id=row["id"],
            source=row["source"],
            symbol=row["symbol"],
            side=side,
            analyst=row.get("analyst"),
            entry_time=entry_time,
            entry_price=row.get("price"),
            quantity=row.get("quantity"),
            stop_price=row.get("stop_loss"),
            target_price=row.get("take_profit"),
        )

        if side == Side.CLOSE:
            return ReplayedTrade(
                **base, outcome=TradeOutcome.NOT_REPLAYED, note="close signals depend on runtime position state"
            )
        if row.get("price") is None:
            return ReplayedTrade(**base, outcome=TradeOutcome.NO_PRICE_DATA, note="signal has no entry price recorded")
        if row.get("stop_loss") is None and row.get("take_profit") is None:
            return ReplayedTrade(**base, outcome=TradeOutcome.NO_EXIT_LEVELS)

        bars = self.price_provider.get_bars(row["symbol"], entry_time, entry_time + self.max_hold)
        bars = [b for b in bars if b.timestamp >= entry_time]
        if not bars:
            return ReplayedTrade(**base, outcome=TradeOutcome.NO_PRICE_DATA)

        for bar in bars:
            result = simulate_bar_fill(side, bar, row.get("stop_loss"), row.get("take_profit"))
            if result.outcome == BarOutcome.NEITHER:
                continue
            if result.outcome == BarOutcome.AMBIGUOUS:
                return ReplayedTrade(**base, outcome=TradeOutcome.AMBIGUOUS, exit_time=bar.timestamp)

            exit_price = result.fill_price
            quantity = row.get("quantity") or 0.0
            entry_price = row["price"]
            pnl = (exit_price - entry_price) * quantity if side == Side.BUY else (entry_price - exit_price) * quantity
            outcome = TradeOutcome.WIN if result.outcome == BarOutcome.TARGET_ONLY else TradeOutcome.LOSS
            return ReplayedTrade(
                **base, outcome=outcome, exit_time=bar.timestamp, exit_price=exit_price, pnl=pnl
            )

        return ReplayedTrade(**base, outcome=TradeOutcome.STILL_OPEN)


def _parse_time(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
