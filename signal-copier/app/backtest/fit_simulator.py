"""A prospective customer's own "what would copying this source have done
to MY account" simulator -- the personalized-to-the-viewer feature real
copy-trading marketing funnels (e.g. Alertsify) use to answer "type your
account size, see what this trader would have paid you" BEFORE anyone
subscribes to anything. This is a genuinely new capability, not a rename
of `app/provider_value.py` (which only ever scores an account's own
REAL, ALREADY-SUBSCRIBED fill history) or `app/backtest/replay.py`'s
`BacktestEngine` (which replays every signal at its own recorded
`quantity`, never rescaled to a prospect's own constraints).

Built entirely on top of `BacktestEngine` -- this module adds exactly one
thing on top of it: rescaling each already-resolved trade's quantity (and
therefore its P&L, which is linear in quantity for every non-option asset
class `BacktestEngine` itself scores -- see its own `EXIT_UNSCORABLE`
handling for why options are excluded there too) to a hypothetical
prospect's own `account_size`/`max_per_trade`, then aggregating an
equity curve and a peak-to-trough drawdown from the rescaled results.

## Sizing methodology -- stated plainly, not hidden

For each of the source's own historical signals, replayed via
`BacktestEngine` exactly as it already is for the real (non-personalized)
`/backtest` endpoint:

- A trade whose own recorded `entry_price` is missing, non-positive, or
  itself GREATER than `max_per_trade` (i.e. even ONE unit would cost more
  than the prospect's own stated per-trade ceiling) does not "fit" --
  `fits=False`, no simulated quantity, no P&L contribution. This is the
  literal, direct meaning of "fit" used here: could this prospect have
  taken this trade AT ALL under their own stated constraint, not some
  softer proxy.
- A trade that fits is rescaled to
  `simulated_quantity = min(original_quantity, max_per_trade / entry_price)`
  -- i.e. the prospect replicates the source's own recommended size when
  it's small enough, capped at their own ceiling otherwise. This is a
  documented choice, not the only possible one (a real product might
  instead offer proportional-to-account-size scaling); it's the simplest
  rule that's honest about what `max_per_trade` (a value the UI
  explicitly asks the prospect for) is actually supposed to cap.
- P&L for a fitting, RESOLVED (WIN/LOSS) trade is the underlying
  `ReplayedTrade.pnl` scaled by `simulated_quantity / original_quantity`
  -- correct because `BacktestEngine`'s own P&L formula,
  `(exit - entry) * quantity`, is linear in quantity. A trade that isn't
  WIN/LOSS (AMBIGUOUS/STILL_OPEN/NO_PRICE_DATA/etc, see
  app/backtest/replay.py's own `TradeOutcome`) contributes no P&L here
  either, for the exact same "never fabricate a result for an unresolved
  trade" reason that engine already follows.
- `account_size` is accepted and stored on the report (the personalized
  UI a caller builds needs to show it back), but this module does NOT
  use it to further constrain sizing beyond `max_per_trade` -- there is
  no additional "never risk more than N% of account_size on any one
  trade" rule layered in here; a caller who wants that derives its own
  `max_per_trade` (e.g. `account_size * 0.10`) before calling this.

## Known, disclosed limitations

- Inherits every limitation `BacktestEngine` itself already documents
  (no shared-capital modeling across concurrent trades even within this
  one simulated account, no slippage/fees unless a caller separately
  applies `app/backtest/cost_stress.py`, no `Side.CLOSE` replay, no
  path-dependent AMBIGUOUS-bar guessing).
- "Worst losing run" here is the maximum PEAK-TO-TROUGH drawdown of the
  cumulative simulated equity curve (a standard, well-defined quant
  metric) -- NOT "longest consecutive count of losing trades." If a
  product surfaces this number, it should be labeled precisely.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from app.backtest.models import PriceHistoryProvider
from app.backtest.replay import BacktestEngine, BacktestReport, ReplayedTrade, TradeOutcome
from app.db import SignalStore


@dataclass
class FitSimTrade:
    signal_id: str
    symbol: str
    entry_time: datetime
    outcome: TradeOutcome
    fits: bool
    original_quantity: float | None
    simulated_quantity: float | None = None
    exit_time: datetime | None = None
    pnl: float | None = None


@dataclass
class ProviderFitReport:
    source: str
    account_size: float
    max_per_trade: float
    lookback_days: float
    trades: list[FitSimTrade] = field(default_factory=list)
    #: The unscaled BacktestEngine report this was built from -- "their
    #: own record at full (recorded) size," for a caller that wants to
    #: show it alongside the personalized numbers the way the marketing
    #: pattern this module is modeled on does.
    full_size_report: BacktestReport = field(default_factory=BacktestReport)

    @property
    def total(self) -> int:
        return len(self.trades)

    @property
    def fit_count(self) -> int:
        return sum(1 for t in self.trades if t.fits)

    @property
    def fit_percentage(self) -> float | None:
        """None (not 0) when there's nothing to measure a percentage of --
        an empty history can't be mistaken for "0% fit."""
        if self.total == 0:
            return None
        return self.fit_count / self.total

    @property
    def _resolved_fitting_trades(self) -> list[FitSimTrade]:
        return [t for t in self.trades if t.fits and t.pnl is not None]

    @property
    def total_pnl(self) -> float:
        return sum(t.pnl for t in self._resolved_fitting_trades if t.pnl is not None)

    @property
    def equity_curve(self) -> list[tuple[datetime, float]]:
        """Cumulative simulated P&L over time, ordered by each trade's own
        exit_time (fall back to entry_time only if exit_time is somehow
        unset, which shouldn't happen for a resolved trade but is handled
        rather than assumed away)."""
        ordered = sorted(self._resolved_fitting_trades, key=lambda t: t.exit_time or t.entry_time)
        curve: list[tuple[datetime, float]] = []
        running = 0.0
        for t in ordered:
            running += t.pnl or 0.0
            curve.append((t.exit_time or t.entry_time, running))
        return curve

    @property
    def worst_drawdown(self) -> float:
        """Maximum peak-to-trough drawdown of `equity_curve`, as a
        non-positive number (0.0 when there's nothing to draw down from).
        See this module's own docstring: this is NOT a losing-streak
        count."""
        peak = 0.0
        running = 0.0
        worst = 0.0
        for _, cumulative in self.equity_curve:
            running = cumulative
            peak = max(peak, running)
            worst = min(worst, running - peak)
        return worst

    def summary(self) -> dict:
        return {
            "source": self.source,
            "account_size": self.account_size,
            "max_per_trade": self.max_per_trade,
            "lookback_days": self.lookback_days,
            "total_signals": self.total,
            "fit_count": self.fit_count,
            "fit_percentage": self.fit_percentage,
            "simulated_pnl_at_your_size": self.total_pnl,
            "worst_drawdown_at_your_size": self.worst_drawdown,
            "full_size_summary": self.full_size_report.summary(),
        }


def simulate_provider_fit(
    store: SignalStore,
    price_provider: PriceHistoryProvider,
    *,
    source: str,
    account_size: float,
    max_per_trade: float,
    lookback_days: float = 90.0,
    max_hold: timedelta = timedelta(days=30),
    now: datetime | None = None,
) -> ProviderFitReport:
    if account_size <= 0:
        raise ValueError("account_size must be positive")
    if max_per_trade <= 0:
        raise ValueError("max_per_trade must be positive")
    if lookback_days <= 0:
        raise ValueError("lookback_days must be positive")

    now = now or datetime.now(timezone.utc)
    start = now - timedelta(days=lookback_days)

    rows = store.list_signals_in_range(source=source, start=start, end=now)
    engine = BacktestEngine(price_provider, max_hold=max_hold)
    full_size_report = engine.run(rows)

    sim_trades = [
        _rescale_trade(trade, max_per_trade=max_per_trade) for trade in full_size_report.trades
    ]

    return ProviderFitReport(
        source=source,
        account_size=account_size,
        max_per_trade=max_per_trade,
        lookback_days=lookback_days,
        trades=sim_trades,
        full_size_report=full_size_report,
    )


def _rescale_trade(trade: ReplayedTrade, *, max_per_trade: float) -> FitSimTrade:
    def not_fit() -> FitSimTrade:
        return FitSimTrade(
            signal_id=trade.signal_id,
            symbol=trade.symbol,
            entry_time=trade.entry_time,
            outcome=trade.outcome,
            fits=False,
            original_quantity=trade.quantity,
            exit_time=trade.exit_time,
        )

    if trade.entry_price is None or trade.entry_price <= 0:
        return not_fit()
    if trade.entry_price > max_per_trade:
        return not_fit()
    if trade.quantity is None:
        # A real entry price within budget, but no recorded quantity to
        # rescale from -- matches BacktestEngine's own EXIT_UNSCORABLE
        # reasoning: never fabricate a size that was never recorded.
        return not_fit()

    simulated_quantity = min(trade.quantity, max_per_trade / trade.entry_price)
    rescaled_pnl = None
    if trade.outcome in (TradeOutcome.WIN, TradeOutcome.LOSS) and trade.pnl is not None and trade.quantity:
        rescaled_pnl = trade.pnl * (simulated_quantity / trade.quantity)

    return FitSimTrade(
        signal_id=trade.signal_id,
        symbol=trade.symbol,
        entry_time=trade.entry_time,
        outcome=trade.outcome,
        fits=True,
        original_quantity=trade.quantity,
        simulated_quantity=simulated_quantity,
        exit_time=trade.exit_time,
        pnl=rescaled_pnl,
    )
