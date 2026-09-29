"""The Signal Backtester's replay engine: given a set of historical
signals and a price history source, ask "what would have happened to
each signal's own resolved stop/target" — NOT a portfolio simulation.

## Scope, stated plainly

Each signal with a resolved `stop_loss` and/or `take_profit` is replayed
as one independent round-trip trade (entry at the signal's own `price`,
walked bar-by-bar forward until the stop, the target, or the available
price history runs out). This is a deliberate, documented scoping
decision, not an oversight:

- **Shared-account capital modeling is now real, but opt-in and bounded.**
  `BacktestEngine.run()` (used by default, and by every caller that
  doesn't ask for the overlay below) still sizes every signal at its own
  `quantity` in total isolation -- that naive replay is preserved
  unchanged, since it's what every existing report/comparison already
  trusts. `BacktestEngine.run_with_capital_contention()` is a real
  overlay on top of it: given one account's real, configured
  `max_notional_exposure` (`app/capital_allocator.py`'s opt-in ceiling --
  the exact same one `app/engine.py` checks before submitting a real live
  order), it replays signals in real chronological order and calls that
  SAME `CapitalAllocator.admit()` the live engine calls, so a later
  signal that would push two overlapping positions' notional past the
  account's real ceiling is genuinely rejected here too -- never a
  parallel, hand-rolled capital model that could quietly drift from what
  live trading actually enforces. See that method's own docstring for
  the full design and why rejection (not partial sizing-down) is the
  faithful choice. Still bounded: no basis-currency conversion, no
  cross-account ceiling, no PENDING-order timing nuance (a backtest signal
  either fully fills at its resolving bar or it doesn't exist at all) --
  the same real, narrower slice `app/capital_allocator.py`'s own module
  docstring already discloses for live trading.
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
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone

from app.backtest.models import PriceHistoryProvider
from app.backtest.simulator import BarOutcome, simulate_bar_fill
from app.capital_allocator import CapitalAllocator
from app.models import Side


class TradeOutcome(str, enum.Enum):
    WIN = "win"
    LOSS = "loss"
    AMBIGUOUS = "ambiguous"  # a bar touched both stop and target; order unrecoverable from OHLC
    STILL_OPEN = "still_open"  # available price history ran out before stop or target was hit
    NO_PRICE_DATA = "no_price_data"  # the price provider has no bars for this symbol/period at all
    NO_EXIT_LEVELS = "no_exit_levels"  # signal has neither stop_loss nor take_profit resolved — nothing to simulate
    NOT_REPLAYED = "not_replayed"  # e.g. a raw CLOSE signal — depends on runtime position state this engine doesn't reconstruct
    #: FIN-02: a stop/target genuinely fired, but no trustworthy cash P&L
    #: can be computed for it -- quantity is missing (silently treating
    #: that as 0 fabricated an exact-zero "win"), or the instrument needs
    #: contract metadata this system doesn't track (an option's real cash
    #: P&L is (exit - entry) * quantity * the contract's multiplier, which
    #: nothing here records -- share-style math would be wrong by whatever
    #: that multiplier actually is).
    EXIT_UNSCORABLE = "exit_unscorable"
    #: This signal's own trade resolved fine in isolation, but
    #: `run_with_capital_contention`'s real chronological replay found the
    #: account's configured `max_notional_exposure` already committed to
    #: earlier-admitted, still-open overlapping signal(s) when this one's
    #: own notional arrived -- see that method's docstring. Never produced
    #: by the plain `run()` (naive, no capital modeling).
    CAPITAL_REJECTED = "capital_rejected"


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
            "exit_unscorable": self.count(TradeOutcome.EXIT_UNSCORABLE),
            "capital_rejected": self.count(TradeOutcome.CAPITAL_REJECTED),
            "resolved_trades": len(self.resolved_trades),
            "win_rate": self.win_rate,
            "total_pnl": self.total_pnl,
            "average_win": self.average_win,
            "average_loss": self.average_loss,
            "expectancy": self.expectancy,
            "profit_factor": pf_value,
            "profit_factor_note": pf_note,
        }


@dataclass
class CapitalContentionReport:
    """Real result of `BacktestEngine.run_with_capital_contention` -- the
    comparison between the naive, independent replay (`run()`, every
    signal sized as if it had the account's full capital to itself) and a
    contention-aware replay that actually shares one account's real
    configured capital ceiling across overlapping signals. `status` is
    `"not_tracked"` (never a fabricated `"implemented"` with an invented
    ceiling) whenever this run's own account/context has no real
    `max_notional_exposure` configured to check contention against."""

    status: str  # "implemented" | "not_tracked"
    reason: str = ""
    account_id: str | None = None
    max_notional_exposure: float | None = None
    #: Count of signals whose trade the contention-aware replay rejected
    #: outright that the naive replay resolved (WIN/LOSS/AMBIGUOUS/
    #: STILL_OPEN) -- the real, honest "how many trades did shared capital
    #: actually change" figure.
    reduced_or_rejected_count: int = 0
    rejected_signal_ids: list[str] = field(default_factory=list)
    #: signal_id -> the real, specific rejection note the contention-aware
    #: replay recorded for it (see `run_with_capital_contention`'s own
    #: CAPITAL_REJECTED note text). The persisted `backtest_runs.trades_json`
    #: is always the NAIVE replay's trades (every existing report/comparison
    #: already trusts that), so it never carries this signal's real
    #: CAPITAL_REJECTED note -- this field is that note's one real, durable
    #: home, not re-derived or guessed from the naive trade.
    rejected_notes: dict[str, str] = field(default_factory=dict)
    naive_summary: dict | None = None
    contention_aware_summary: dict | None = None

    @classmethod
    def not_tracked(cls, reason: str) -> "CapitalContentionReport":
        return cls(status="not_tracked", reason=reason)


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

    async def run_with_capital_contention(
        self, signal_rows: list[dict], *, account_id: str, max_notional_exposure: float
    ) -> CapitalContentionReport:
        """Real cross-signal capital-sharing overlay on top of `run()`'s
        naive, per-trade-independent replay.

        ## Design, and why

        1. **Reuse the real gate, don't reinvent it.** The actual
           admission decision -- "does this notional fit under the
           account's ceiling right now" -- is made by calling
           `app/capital_allocator.py`'s own `CapitalAllocator.admit()`,
           the exact same check `app/engine.py` runs before submitting a
           real live order (`confirmed_exposure + pending + notional >
           max_exposure`). This module never re-implements that
           arithmetic; it only supplies the sequence of (notional,
           timing) events a backtest replay -- unlike live trading --
           can know in advance.
        2. **Real chronological order.** Signals are walked in ascending
           `entry_time` order, exactly as they'd have arrived at a live
           account. A signal's capital reservation is released the moment
           an earlier signal's own real exit (from the naive replay's
           already-simulated `exit_time`) falls at or before it -- capital
           genuinely freed by that point in time is genuinely available
           again. A `STILL_OPEN` trade's reservation is never released
           within the run (its capital really is still committed at the
           end of the replay window).
        3. **Reject, don't size down.** `CapitalAllocator.admit()` is
           itself a binary admit/reject gate in the real live code --
           there is no partial-admission notion for it to mirror. Sizing
           a rejected signal down to "whatever headroom is left" would
           invent a capital-rationing model the live engine doesn't
           actually have. A signal that doesn't fit is `CAPITAL_REJECTED`
           here, the same as it would never have been submitted live.
        4. **Only signals with a real, sizeable notional participate.**
           A trade needs a real `entry_price` AND `quantity` to compute
           `abs(quantity) * entry_price`; one that never reached a real
           fill decision (`NO_PRICE_DATA`/`NO_EXIT_LEVELS`/`NOT_REPLAYED`/
           `EXIT_UNSCORABLE`) never held real capital in the first place
           and passes through unaffected, exactly as `run()` already
           reported it.
        5. **Backtest, not a live account.** `confirmed_exposure` is
           always `0.0` -- a backtest replays only the signals it's given,
           never a pre-existing live position this account also holds
           (that's real state this replay engine has no access to).
        """
        naive = self.run(signal_rows)
        sizeable_outcomes = (TradeOutcome.WIN, TradeOutcome.LOSS, TradeOutcome.AMBIGUOUS, TradeOutcome.STILL_OPEN)
        sizeable = sorted(
            (
                t for t in naive.trades
                if t.entry_price is not None and t.quantity is not None and t.outcome in sizeable_outcomes
            ),
            key=lambda t: t.entry_time,
        )

        allocator = CapitalAllocator()
        open_reservations: list[tuple[datetime | None, float]] = []
        contention_trades: list[ReplayedTrade] = list(naive.trades)
        index_by_signal_id = {t.signal_id: i for i, t in enumerate(contention_trades)}
        rejected_signal_ids: list[str] = []
        rejected_notes: dict[str, str] = {}

        for t in sizeable:
            still_open: list[tuple[datetime | None, float]] = []
            for exit_time, notional in open_reservations:
                if exit_time is not None and exit_time <= t.entry_time:
                    allocator.release(account_id, notional)
                else:
                    still_open.append((exit_time, notional))
            open_reservations = still_open

            # `sizeable`'s own filter above already guarantees both are
            # non-None for every `t` reached here; mypy can't carry that
            # narrowing through the generator-expression filter into this
            # loop, so narrow it again explicitly rather than silencing the
            # checker (a stale `# type: ignore[operator]` used to sit here,
            # but that error code doesn't even match this one; a real
            # `quantity`/`entry_price` of None reaching this line would be
            # a genuine bug the assert below is meant to catch, not paper
            # over).
            quantity = t.quantity
            entry_price = t.entry_price
            assert quantity is not None and entry_price is not None
            notional = abs(quantity) * entry_price
            admitted = await allocator.admit(
                account_id, notional, confirmed_exposure=0.0, max_exposure=max_notional_exposure
            )
            if admitted:
                open_reservations.append((t.exit_time, notional))
                continue

            rejected_signal_ids.append(t.signal_id)
            i = index_by_signal_id[t.signal_id]
            committed = allocator.pending_reservation(account_id)
            note = (
                f"Rejected by account {account_id!r}'s real capital ledger: max_notional_exposure="
                f"{max_notional_exposure:g} was already committed to {committed:g} of overlapping, "
                f"earlier-admitted open notional when this signal's own notional ({notional:g}) "
                "arrived -- mirrors app/capital_allocator.py's CapitalAllocator.admit()."
            )
            rejected_notes[t.signal_id] = note
            contention_trades[i] = replace(
                t,
                outcome=TradeOutcome.CAPITAL_REJECTED,
                exit_time=None,
                exit_price=None,
                pnl=None,
                note=note,
            )

        contention_report = BacktestReport(trades=contention_trades)
        return CapitalContentionReport(
            status="implemented",
            account_id=account_id,
            max_notional_exposure=max_notional_exposure,
            reduced_or_rejected_count=len(rejected_signal_ids),
            rejected_signal_ids=rejected_signal_ids,
            rejected_notes=rejected_notes,
            naive_summary=naive.summary(),
            contention_aware_summary=contention_report.summary(),
        )

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
            quantity = row.get("quantity")
            entry_price = row["price"]

            if quantity is None or row.get("asset_class") == "option":
                # FIN-02: don't fabricate a cash P&L -- see EXIT_UNSCORABLE's
                # docstring for why a missing quantity or an option's
                # untracked contract multiplier both block this.
                return ReplayedTrade(
                    **base,
                    outcome=TradeOutcome.EXIT_UNSCORABLE,
                    exit_time=bar.timestamp,
                    exit_price=exit_price,
                    pnl=None,
                    note="quantity is missing" if quantity is None else "option contract multiplier is not tracked",
                )

            pnl = (exit_price - entry_price) * quantity if side == Side.BUY else (entry_price - exit_price) * quantity
            # FIN-02: outcome must reflect the actual economic result, not
            # which level fired -- a stop that fires at a price still above
            # entry (e.g. a trailing/breakeven stop) is a real profit, and
            # labeling it LOSS just because it was "the stop" is wrong.
            outcome = TradeOutcome.WIN if pnl > 0 else TradeOutcome.LOSS
            return ReplayedTrade(
                **base, outcome=outcome, exit_time=bar.timestamp, exit_price=exit_price, pnl=pnl
            )

        return ReplayedTrade(**base, outcome=TradeOutcome.STILL_OPEN)


def _parse_time(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
