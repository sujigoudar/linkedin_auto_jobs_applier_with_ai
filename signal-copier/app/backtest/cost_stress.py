"""E07 (bounded): a simple, linear cost-stress pass over an existing
`BacktestReport` -- "does the apparent edge survive realistic execution
costs, or does it evaporate the moment slippage and fees are accounted
for" -- the one E07 acceptance obligation ("execution quality and cost
stress can reject an apparent historical gain") this session can deliver
without the rest of E07's much larger ask (point-in-time dataset
versioning, a separate resource-limited research process, forward-shadow
promotion gates). Every other disclosed limitation in
app/backtest/replay.py's module docstring (no shared-capital modeling,
no path-dependent-bar guessing, no Side.CLOSE replay) is UNCHANGED by
this -- this only touches the P&L of trades that already resolved to a
real WIN/LOSS.

Deliberately simple, not a liquidity/market-impact model: applies a flat
slippage in basis points against every resolved trade's exit price, in
the direction that always makes the fill worse (a long's exit gets
pushed down, a short's exit gets pushed up), plus a flat per-trade fee
subtracted from P&L. This is a stress test, not a claim of what real
slippage/fees would specifically be -- see this module's `note` in the
returned dict for that disclosure, and pass 0 for either parameter to
skip that dimension entirely.
"""
from __future__ import annotations

from dataclasses import replace

from app.backtest.replay import BacktestReport, ReplayedTrade, TradeOutcome
from app.models import Side


def _stress_one_trade(trade: ReplayedTrade, *, slippage_bps: float, fee_per_trade: float) -> ReplayedTrade:
    if trade.outcome not in (TradeOutcome.WIN, TradeOutcome.LOSS) or trade.exit_price is None or trade.quantity is None:
        return trade

    slippage_fraction = slippage_bps / 10_000.0
    if trade.side == Side.BUY:
        stressed_exit_price = trade.exit_price * (1 - slippage_fraction)
    else:
        stressed_exit_price = trade.exit_price * (1 + slippage_fraction)

    entry_price = trade.entry_price
    if entry_price is None:
        return trade

    stressed_pnl = (
        (stressed_exit_price - entry_price) * trade.quantity
        if trade.side == Side.BUY
        else (entry_price - stressed_exit_price) * trade.quantity
    )
    stressed_pnl -= fee_per_trade

    stressed_outcome = TradeOutcome.WIN if stressed_pnl > 0 else TradeOutcome.LOSS
    return replace(trade, exit_price=stressed_exit_price, pnl=stressed_pnl, outcome=stressed_outcome)


def apply_cost_stress(report: BacktestReport, *, slippage_bps: float = 0.0, fee_per_trade: float = 0.0) -> BacktestReport:
    """Returns a NEW BacktestReport with slippage/fees applied to every
    already-resolved (WIN/LOSS) trade -- never mutates `report`, so the
    raw and stressed summaries can be compared side by side."""
    stressed_trades = [
        _stress_one_trade(t, slippage_bps=slippage_bps, fee_per_trade=fee_per_trade) for t in report.trades
    ]
    return BacktestReport(trades=stressed_trades)
