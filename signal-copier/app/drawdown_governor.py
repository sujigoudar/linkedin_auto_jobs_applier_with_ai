"""Daily and peak-equity drawdown circuit breakers -- admission control
that reacts to REALIZED loss and open-position mark-to-last-trade loss,
not just exposure size. Two distinct baselines, both genuinely persisted
(survive a restart -- see `app/db.py`'s `account_equity_snapshots` /
`account_peak_equity` tables):

- `START_OF_DAY_DRAWDOWN`: loss relative to this account's own equity the
  FIRST time it was observed on the current UTC calendar date -- locked
  in for the rest of that day (never re-anchored to a later, lower
  reading the same day; see `SignalStore.set_start_of_day_equity_if_absent`).
- `PEAK_EQUITY_DRAWDOWN`: loss relative to the real historical peak
  equity ever observed for this account -- ratcheted up (never down) each
  time a new high is seen, never recomputed from scratch each call (see
  `SignalStore.upsert_peak_equity`).

## Equity basis: REALIZED_PLUS_UNREALIZED, reusing app/economics.py

`compute_account_equity` below reuses `app/economics.py`'s
`compute_account_economics` for BOTH components, never a second/third P&L
calculation:

- Realized: `AccountEconomics.realized_pnl`, exactly as economics.py
  already computes it (volume-weighted average-cost replay of every
  confirmed FILLED order).
- Unrealized: derived from the SAME per-symbol fields economics.py
  already exposes for every open position -- `open_quantity`,
  `average_cost`, `last_fill_price` -- as
  `(last_fill_price - average_cost) * open_quantity`. There is no live
  market-quote feed available synchronously at admission time (see
  `app/pricing.py`'s own module docstring: only managed-lifecycle
  positions on a broker with `has_last_price_capability` are ever polled,
  asynchronously, on a background interval, and even then the result
  isn't cached anywhere this module could read it back from
  synchronously). Marking an open position against its own last recorded
  fill price is the exact same disclosed limitation
  `SymbolEconomics.last_fill_price` already carries in economics.py's own
  docstring ("the last price this account actually traded at, not a live
  market quote") -- this module doesn't invent a new number, it just uses
  fields economics.py already computes for the one purpose they weren't
  exposed as yet.

Thresholds are absolute currency-unit LOSS amounts, not percentages: the
equity basis here is a cumulative P&L figure (naturally near zero for a
fresh account), not a total account balance, so "drawdown as a percentage
of equity" is ill-defined exactly where it matters most (a small or
negative starting equity). An absolute-loss threshold (e.g. "warn after a
$500 loss from today's start") is what real trading circuit breakers
commonly express anyway.

## Actions -- separately configurable per threshold, per dimension

`WARN` < `REDUCE_NEW_SIZE` < `PAUSE_NEW_ENTRIES` < `REQUIRE_REVIEW` in
severity. Each threshold is independently configurable (`None` = not
configured, that action never triggers); the action actually in effect
for a dimension is whichever configured threshold the current drawdown
has crossed, evaluated most-severe-first. Per the spec this batch
implements: **emergency liquidation is never the default response to a
drawdown threshold, and is never implemented by this module at all** --
every action here either only logs (`WARN`), only reduces the size of a
NEW admission (`REDUCE_NEW_SIZE`), or only blocks NEW admissions
(`PAUSE_NEW_ENTRIES` / `REQUIRE_REVIEW`); none of them ever touches an
EXISTING position.

`REQUIRE_REVIEW` is deliberately STICKY: once a `require_review`
threshold is crossed for an account, `account_drawdown_review.
review_required` is persisted as `True` and stays `True` across every
later `evaluate()` call -- however much equity recovers -- until
`clear_review()` (a real, explicit action; see that method's own
docstring) sets it back to `False`. `evaluate()` itself never calls
`clear_review()`. `PAUSE_NEW_ENTRIES` is NOT sticky the same way: it is
re-derived live from the current drawdown on every `evaluate()` call, so
it lifts on its own once the account's drawdown genuinely recovers back
under its configured threshold -- the spec's "don't auto-clear, don't
auto-resume" language is specific to `REQUIRE_REVIEW`.

## Scope: account-level only

Per-provider/per-analyst equity attribution does not exist anywhere in
this codebase (`app/economics.py`'s own replay is scoped to one
`account_id`; there is no notion of "this slice of an account's equity
belongs to provider X" -- a single account can receive fills from many
providers/analysts with no per-source cost-basis split). Building that
attribution from scratch would be inventing a data source this batch's
own standing rules say not to invent. This module is honestly scoped to
account-level only; a real per-provider/per-analyst drawdown governor
would need that attribution built first, as its own separate slice.
"""
from __future__ import annotations

import enum
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from app import config
from app.db import SignalStore
from app.economics import compute_account_economics

logger = logging.getLogger(__name__)


class DrawdownAction(str, enum.Enum):
    NONE = "none"
    WARN = "warn"
    REDUCE_NEW_SIZE = "reduce_new_size"
    PAUSE_NEW_ENTRIES = "pause_new_entries"
    REQUIRE_REVIEW = "require_review"


_SEVERITY: dict[DrawdownAction, int] = {
    DrawdownAction.NONE: 0,
    DrawdownAction.WARN: 1,
    DrawdownAction.REDUCE_NEW_SIZE: 2,
    DrawdownAction.PAUSE_NEW_ENTRIES: 3,
    DrawdownAction.REQUIRE_REVIEW: 4,
}


def compute_account_equity(store: SignalStore, account_id: str) -> float:
    """REALIZED_PLUS_UNREALIZED equity for `account_id` -- see this
    module's own docstring for exactly why both components reuse
    app/economics.py's already-computed fields rather than a second
    calculation."""
    economics = compute_account_economics(store, account_id)
    unrealized = 0.0
    for symbol_economics in economics.per_symbol.values():
        if symbol_economics.open_quantity == 0:
            continue
        if symbol_economics.average_cost is None or symbol_economics.last_fill_price is None:
            continue
        unrealized += (symbol_economics.last_fill_price - symbol_economics.average_cost) * symbol_economics.open_quantity
    return economics.realized_pnl + unrealized


@dataclass
class DrawdownThresholds:
    warn: float | None = None
    reduce_new_size: float | None = None
    reduce_new_size_multiplier: float = 0.5
    pause_new_entries: float | None = None
    require_review: float | None = None

    def action_for(self, drawdown: float) -> DrawdownAction:
        """Most-severe-threshold-first: a drawdown that has crossed
        `require_review` is reported as REQUIRE_REVIEW even if it has
        also (necessarily) crossed every less severe threshold too --
        never double-counted as several simultaneous actions for the same
        dimension."""
        if self.require_review is not None and drawdown >= self.require_review:
            return DrawdownAction.REQUIRE_REVIEW
        if self.pause_new_entries is not None and drawdown >= self.pause_new_entries:
            return DrawdownAction.PAUSE_NEW_ENTRIES
        if self.reduce_new_size is not None and drawdown >= self.reduce_new_size:
            return DrawdownAction.REDUCE_NEW_SIZE
        if self.warn is not None and drawdown >= self.warn:
            return DrawdownAction.WARN
        return DrawdownAction.NONE


@dataclass
class DrawdownGovernorConfig:
    start_of_day: DrawdownThresholds = field(default_factory=DrawdownThresholds)
    peak_equity: DrawdownThresholds = field(default_factory=DrawdownThresholds)

    @classmethod
    def from_config(cls) -> "DrawdownGovernorConfig":
        return cls(
            start_of_day=DrawdownThresholds(
                warn=config.DRAWDOWN_START_OF_DAY_WARN_THRESHOLD,
                reduce_new_size=config.DRAWDOWN_START_OF_DAY_REDUCE_SIZE_THRESHOLD,
                reduce_new_size_multiplier=config.DRAWDOWN_START_OF_DAY_REDUCE_SIZE_MULTIPLIER,
                pause_new_entries=config.DRAWDOWN_START_OF_DAY_PAUSE_THRESHOLD,
                require_review=config.DRAWDOWN_START_OF_DAY_REVIEW_THRESHOLD,
            ),
            peak_equity=DrawdownThresholds(
                warn=config.DRAWDOWN_PEAK_EQUITY_WARN_THRESHOLD,
                reduce_new_size=config.DRAWDOWN_PEAK_EQUITY_REDUCE_SIZE_THRESHOLD,
                reduce_new_size_multiplier=config.DRAWDOWN_PEAK_EQUITY_REDUCE_SIZE_MULTIPLIER,
                pause_new_entries=config.DRAWDOWN_PEAK_EQUITY_PAUSE_THRESHOLD,
                require_review=config.DRAWDOWN_PEAK_EQUITY_REVIEW_THRESHOLD,
            ),
        )


@dataclass
class DrawdownEvaluation:
    account_id: str
    equity: float
    start_of_day_equity: float
    start_of_day_drawdown: float
    peak_equity: float
    peak_equity_drawdown: float
    action: DrawdownAction
    size_multiplier: float
    review_required: bool
    reasons: list[str]


class DrawdownGovernor:
    """One instance shared by the engine for its whole lifetime, like
    `CapitalAllocator`/`PlacementRateLimiter`. Every `evaluate()` call
    recomputes equity fresh from the store and reads/updates the two
    persisted baselines -- safe to call as often as needed (e.g. once per
    admission attempt); a restart loses nothing because nothing it needs
    lives only in memory."""

    def __init__(self, store: SignalStore, thresholds: DrawdownGovernorConfig | None = None):
        self.store = store
        self.thresholds = thresholds or DrawdownGovernorConfig.from_config()

    def evaluate(self, account_id: str, *, now: datetime | None = None) -> DrawdownEvaluation:
        now = now or datetime.now(timezone.utc)
        today = now.date().isoformat()

        equity = compute_account_equity(self.store, account_id)

        start_of_day_equity = self.store.set_start_of_day_equity_if_absent(account_id, today, equity)
        start_of_day_drawdown = max(0.0, start_of_day_equity - equity)

        existing_peak = self.store.get_peak_equity(account_id)
        if existing_peak is None or equity > existing_peak:
            self.store.upsert_peak_equity(account_id, equity, now)
            peak_equity = equity
        else:
            peak_equity = existing_peak
        peak_equity_drawdown = max(0.0, peak_equity - equity)

        sod_action = self.thresholds.start_of_day.action_for(start_of_day_drawdown)
        peak_action = self.thresholds.peak_equity.action_for(peak_equity_drawdown)

        reasons: list[str] = []
        if sod_action is not DrawdownAction.NONE:
            reasons.append(
                f"start_of_day_drawdown={start_of_day_drawdown:.2f} (equity={equity:.2f}, "
                f"start_of_day_equity={start_of_day_equity:.2f}) -> {sod_action.value}"
            )
        if peak_action is not DrawdownAction.NONE:
            reasons.append(
                f"peak_equity_drawdown={peak_equity_drawdown:.2f} (equity={equity:.2f}, "
                f"peak_equity={peak_equity:.2f}) -> {peak_action.value}"
            )

        live_action = sod_action if _SEVERITY[sod_action] >= _SEVERITY[peak_action] else peak_action

        # REQUIRE_REVIEW is sticky -- see this module's own docstring.
        review_record = self.store.get_drawdown_review(account_id)
        review_required = bool(review_record["review_required"]) if review_record else False
        if live_action is DrawdownAction.REQUIRE_REVIEW and not review_required:
            reason = reasons[-1] if reasons else "a drawdown REQUIRE_REVIEW threshold was crossed"
            self.store.set_drawdown_review(account_id, True, reason=reason)
            review_required = True

        action = DrawdownAction.REQUIRE_REVIEW if review_required else live_action

        size_multiplier = 1.0
        if action is DrawdownAction.REDUCE_NEW_SIZE:
            # Whichever dimension actually produced REDUCE_NEW_SIZE supplies
            # its own configured multiplier; on a tie (both dimensions
            # simultaneously at REDUCE_NEW_SIZE) start_of_day's wins -- an
            # arbitrary but stable, documented tie-break, never a silently
            # compounded double-reduction.
            size_multiplier = (
                self.thresholds.start_of_day.reduce_new_size_multiplier
                if sod_action is DrawdownAction.REDUCE_NEW_SIZE
                else self.thresholds.peak_equity.reduce_new_size_multiplier
            )

        return DrawdownEvaluation(
            account_id=account_id,
            equity=equity,
            start_of_day_equity=start_of_day_equity,
            start_of_day_drawdown=start_of_day_drawdown,
            peak_equity=peak_equity,
            peak_equity_drawdown=peak_equity_drawdown,
            action=action,
            size_multiplier=size_multiplier,
            review_required=review_required,
            reasons=reasons,
        )

    def clear_review(self, account_id: str) -> None:
        """The one real, explicit unblock action for a sticky
        REQUIRE_REVIEW -- `evaluate()` itself never calls this, regardless
        of how favorably equity has moved since the review was triggered.
        Intended to back a real operator-facing action (e.g. a dashboard
        "Acknowledge and resume" button on the account -- the mechanism
        itself, wiring an actual route to it is a later slice); calling it
        when no review is set is a harmless no-op."""
        self.store.set_drawdown_review(account_id, False, reason="")
