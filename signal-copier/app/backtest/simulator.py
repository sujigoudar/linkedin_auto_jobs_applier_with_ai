"""Resolves whether a stop/target would have triggered against one
historical OHLC bar — and, critically, when it genuinely can't be known.

A bar only records four prices, not the path between them. If a long
position has a stop at 95 and a target at 105, and a bar's range is
[94, 106], the stop AND the target both fall inside that range — but
whether price touched 95 or 105 *first* isn't recoverable from OHLC data
alone (that needs tick/quote-level history this project doesn't have a
source for either). Reporting one of them as "the outcome" would be
picking whichever answer the backtest report calls favorable, which is
exactly the kind of self-serving simulation this module refuses to
produce. `AMBIGUOUS` is a real, first-class outcome — app/backtest/replay.py
carries it through to the report rather than resolving it by assumption.

The one case that IS resolvable without extra data: a gap. If the bar's
`open` itself already satisfies the stop or target, that level was
touched (or gapped through) before anything else in the bar could
happen, so that outcome is used even when the other level is also within
the bar's range.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass

from app.backtest.models import HistoricalBar
from app.models import Side


class BarOutcome(str, enum.Enum):
    NEITHER = "neither"
    STOP_ONLY = "stop_only"
    TARGET_ONLY = "target_only"
    AMBIGUOUS = "ambiguous"  # both levels fall within the bar's range; order can't be determined from OHLC alone


@dataclass(frozen=True)
class BarResult:
    outcome: BarOutcome
    #: The price the resolvable outcome (STOP_ONLY/TARGET_ONLY) is deemed to
    #: have filled at — the level itself, not a modeled slippage price
    #: (slippage modeling is a documented gap, not silently assumed away).
    fill_price: float | None = None


def simulate_bar_fill(
    side: Side, bar: HistoricalBar, stop_price: float | None, target_price: float | None
) -> BarResult:
    """`side` is the ENTRY side (BUY for long, SELL for short) — stop/target
    trigger conditions are mirrored accordingly. Either `stop_price` or
    `target_price` may be None (position with only one of the two active)."""
    if side == Side.BUY:
        gapped_through_stop = stop_price is not None and bar.open <= stop_price
        gapped_through_target = target_price is not None and bar.open >= target_price
    elif side == Side.SELL:
        gapped_through_stop = stop_price is not None and bar.open >= stop_price
        gapped_through_target = target_price is not None and bar.open <= target_price
    else:
        raise ValueError(f"simulate_bar_fill needs an entry side (BUY/SELL), got {side}")

    # FIN-03: "was this level touched during the bar" is bar.low <= level <=
    # bar.high, regardless of side -- the OLD code only checked the
    # one-sided inequality (e.g. `bar.low <= stop_price` for a long), which
    # is trivially true for ANY stop_price above the bar's low, including
    # one far ABOVE the bar's entire range that price never came close to.
    # A 95 stop on a bar trading [88, 92] was reported "filled at 95" even
    # though price never rose anywhere near it. Requiring the level to fall
    # within [low, high] is what actually proves it was reachable this bar.
    stop_hit = stop_price is not None and bar.low <= stop_price <= bar.high
    target_hit = target_price is not None and bar.low <= target_price <= bar.high

    if gapped_through_stop and gapped_through_target:
        # Both were already breached at the open — genuinely can't tell which
        # of two simultaneous conditions "happened first" at a single price point.
        return BarResult(BarOutcome.AMBIGUOUS)
    if gapped_through_stop:
        # FIN-03: claiming a fill at the stop LEVEL is only honest when that
        # level actually fell within this bar's own traded range -- when the
        # gap carried price PAST the bar's entire range (the level was never
        # a real, observed price this bar), reporting it anyway invents
        # precision the bar doesn't have and understates the real loss (the
        # actual gap price is worse than the stop level). Fall back to the
        # bar's own open -- the real, observed price the gap actually
        # produced -- rather than the unreached level.
        fill = stop_price if bar.low <= stop_price <= bar.high else bar.open
        return BarResult(BarOutcome.STOP_ONLY, fill_price=fill)
    if gapped_through_target:
        fill = target_price if bar.low <= target_price <= bar.high else bar.open
        return BarResult(BarOutcome.TARGET_ONLY, fill_price=fill)

    if stop_hit and target_hit:
        return BarResult(BarOutcome.AMBIGUOUS)
    if stop_hit:
        return BarResult(BarOutcome.STOP_ONLY, fill_price=stop_price)
    if target_hit:
        return BarResult(BarOutcome.TARGET_ONLY, fill_price=target_price)
    return BarResult(BarOutcome.NEITHER)
