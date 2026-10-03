"""Phase A5: rolling return/volatility/Sharpe/Sortino/drawdown-duration
statistics computed from a real, per-account `cumulative_pnl` time series
(see app/equity_history.py's `EquitySnapshotter` -- the only source of
this data, and the module whose own honest-labeling rationale this module
preserves and extends).

## Honest scope: P&L-delta statistics, never a fabricated percentage return

`app/equity_history.py` already established (and re-checked here, not
re-assumed) that this codebase's `DestinationAccount`/`AccountRequest`
(app/models.py, app/main.py) have no `starting_balance`/`starting_capital`
field, and no other module anywhere in this codebase stores a real,
configured per-account capital figure either (checked: app/economics.py,
app/capital_allocator.py -- the latter tracks a *global* deployed-capital
ceiling across all accounts for sizing decisions, not a per-account
starting-balance baseline a return could be divided by). Dividing a P&L
delta by an arbitrary, undocumented number to produce a percentage
"return" would be exactly the kind of fabricated figure this project's
other statistics modules (app/economics.py, app/execution_quality.py,
app/equity_history.py) explicitly refuse to produce.

So every statistic in this module is computed in **absolute P&L-delta
terms**: the period-over-period change in `cumulative_pnl` (this
account's own honestly-labeled running realized+unrealized P&L, per
snapshot) is the analogue of a "return" here, never divided by any
capital figure. Every field name and docstring below says "pnl_delta",
never "return" or "return_pct", so a caller can never mistake this for a
percentage figure.

## Implicit zero risk-free rate

"Sharpe-equivalent" and "Sortino-equivalent" below are `mean(delta) /
stdev(delta)` (Sharpe-equivalent) and `mean(delta) / downside_stdev(delta)`
(Sortino-equivalent) -- the textbook Sharpe/Sortino ratios use `(return -
risk_free_rate) / volatility`; this codebase has no risk-free-rate figure
stored or configured anywhere (checked: app/config.py, app/context/*
(FRED integration exposes real macro series on request, but nothing here
calls it, and wiring a live rate in would silently couple this module's
math to network availability) -- so this module uses an **implicit
risk-free rate of 0**, not an invented one, and says so explicitly in
every docstring/field name ending `_equivalent` rather than calling these
"Sharpe ratio"/"Sortino ratio" outright.

## Minimum sample thresholds -- honest insufficiency, never a fabricated stat

- Rolling volatility / Sharpe-equivalent: need at least 2 P&L deltas
  (i.e. 3 snapshots) in the window to compute a non-degenerate sample
  stdev; below that, `None`.
- Sortino-equivalent: same 2-delta floor, but ALSO `None` (not 0.0 or
  `inf`) when the window has zero downside deltas -- a strategy with no
  losing periods in the window has an undefined (not infinite, not zero)
  downside-deviation ratio, and reporting either fabricated extreme would
  mislead a scorecard reader.
- Max drawdown / drawdown duration: need at least 2 snapshots (one prior
  point to have any peak-to-trough at all); below that, `None`.
- Pairwise correlation: need at least **10 overlapping snapshots** (by
  matching `captured_at` timestamp between both accounts' series) --
  chosen as this module's documented minimum sample threshold because
  Pearson's r is asymptotically unstable below ~10 points (a handful of
  points can produce an arbitrarily large |r| by chance) and this
  codebase has no other precedent threshold to defer to. Below 10
  overlapping points, correlation is omitted entirely (`None`), never
  reported as a numerically-valid-looking 0.0 or NaN-as-zero.

## Rolling window semantics

`window` is a snapshot COUNT (not a fixed calendar duration), matching
how `list_equity_snapshots` already returns bare snapshot rows with no
guaranteed uniform spacing (the interval is
`config.EQUITY_SNAPSHOT_INTERVAL_SECONDS`, but the actual gap between two
persisted rows can vary -- a missed loop pass, a process restart). All
of this module's rolling statistics are computed over the **last
`window` snapshots** of the series actually available, honestly
reflecting however much real calendar time that happens to span (each
result also reports `window_start`/`window_end` -- the real
`captured_at` bounds of the snapshots actually used -- so a caller never
has to assume a fixed duration).

## Max drawdown: real peak-to-trough, not first/last-point approximation

`compute_max_drawdown` walks the FULL real `cumulative_pnl` series once,
tracking the running peak seen so far and the largest peak-to-any-later-
trough drop -- this is the single most correctness-critical invariant in
this module (a naive `first - min` or `first - last` computation would
silently miss an interior drawdown that recovered before the series
ends). See this module's own load-bearing test
(`tests/test_statistics.py`), which breaks this invariant on purpose (by
replacing the running-peak walk with a first/last-only computation) and
confirms the test that exists specifically to catch that regression
fails.

## E-12: Unpriced open symbols — honest insufficiency when mark-availability varies

When an account holds open positions but has no live market price for
one or more of them (`unpriced_open_symbols` non-empty on a snapshot),
the `cumulative_pnl` in that snapshot folds a 0.0 for unrealized P&L on
those symbols -- and a mark arriving or departing (a `PriceMonitor` tick
starting/ending) then looks like a phantom P&L move in the time series,
inflating volatility/drawdown. To surface this, `compute_rolling_stats`
counts how many snapshots in the window had any unpriced-open-symbols
entry (`unpriced_snapshot_count`), so a caller (or a dashboard) can
decide whether to apply these statistics or report `None` when mark
availability is spotty.
"""
from __future__ import annotations

import statistics as stats
from dataclasses import dataclass
from datetime import datetime

#: This module's documented minimum overlapping-sample threshold for
#: reporting a pairwise correlation at all -- see this module's own
#: docstring for why 10.
MIN_CORRELATION_SAMPLES = 10

#: Minimum number of P&L deltas (one fewer than snapshots) needed for a
#: non-degenerate sample standard deviation.
_MIN_DELTAS_FOR_VOLATILITY = 2

#: Minimum number of snapshots needed to have any peak-to-trough drawdown
#: at all.
_MIN_SNAPSHOTS_FOR_DRAWDOWN = 2


def _parse(ts: str | datetime) -> datetime:
    return ts if isinstance(ts, datetime) else datetime.fromisoformat(ts)


def _pnl_deltas(snapshots: list[dict]) -> list[float]:
    """Period-over-period change in `cumulative_pnl` across consecutive
    snapshots -- the honest P&L-delta analogue of a "return" this module
    uses throughout (see module docstring for why never a percentage)."""
    return [
        float(b["cumulative_pnl"]) - float(a["cumulative_pnl"])
        # strict=False is intentional: snapshots[1:] is always exactly
        # one element shorter than snapshots by construction (the offset
        # pairing IS the point), never a real length mismatch to catch.
        for a, b in zip(snapshots, snapshots[1:], strict=False)
    ]


@dataclass
class RollingStats:
    account_id: str
    window: int
    #: How many real snapshots this computation actually used -- may be
    #: less than `window` if the account's real history is shorter.
    sample_count: int
    window_start: datetime | None
    window_end: datetime | None
    #: Mean period-over-period cumulative_pnl delta over the window --
    #: absolute P&L terms, never a percentage (see module docstring).
    mean_pnl_delta: float | None
    #: Sample standard deviation of those same deltas.
    volatility_pnl_delta: float | None
    #: mean_pnl_delta / volatility_pnl_delta, implicit zero risk-free
    #: rate (see module docstring) -- NEVER called "Sharpe ratio".
    sharpe_equivalent: float | None
    #: mean_pnl_delta / (stdev of only the negative deltas), implicit
    #: zero risk-free rate -- NEVER called "Sortino ratio".
    sortino_equivalent: float | None
    #: Largest real peak-to-trough drop in cumulative_pnl within the
    #: window, in absolute P&L terms (always >= 0, or None -- never a
    #: signed/negative figure).
    max_drawdown: float | None
    #: Real wall-clock span (seconds) from the peak that produced
    #: max_drawdown to the trough that realized it -- "how long
    #: underwater", not a fixed/assumed duration.
    max_drawdown_duration_seconds: float | None
    #: (E-12) How many snapshots in the window had any unpriced-open-
    #: symbols (holdings without a live market mark). When nonzero, this
    #: window contains phantom P&L moves from mark-availability changes,
    #: and statistics may be unreliable. A caller (or dashboard) should
    #: return None for all fields instead of reporting them when this is
    #: nonzero.
    unpriced_snapshot_count: int = 0

    def to_dict(self) -> dict:
        return {
            "account_id": self.account_id,
            "window": self.window,
            "sample_count": self.sample_count,
            "window_start": self.window_start.isoformat() if self.window_start else None,
            "window_end": self.window_end.isoformat() if self.window_end else None,
            "mean_pnl_delta": self.mean_pnl_delta,
            "volatility_pnl_delta": self.volatility_pnl_delta,
            "sharpe_equivalent": self.sharpe_equivalent,
            "sortino_equivalent": self.sortino_equivalent,
            "max_drawdown": self.max_drawdown,
            "max_drawdown_duration_seconds": self.max_drawdown_duration_seconds,
            "unpriced_snapshot_count": self.unpriced_snapshot_count,
            "note": "All figures are absolute cumulative_pnl-delta statistics, not percentage "
            "returns -- this account has no configured starting-balance/capital baseline to "
            "divide by (see app/equity_history.py and app/statistics.py's own module "
            "docstrings). sharpe_equivalent/sortino_equivalent use an implicit risk-free rate "
            "of 0 (this codebase stores no risk-free-rate figure), so neither is a textbook "
            "Sharpe/Sortino ratio. Any field is null when the account's real snapshot history "
            "is too short for that statistic to be meaningful, never a fabricated placeholder. "
            "When unpriced_snapshot_count > 0 (E-12), the window contains holdings without live "
            "market marks, so mark-availability changes masquerade as P&L moves and statistics may "
            "be unreliable.",
        }


def compute_max_drawdown(snapshots: list[dict]) -> tuple[float, float] | None:
    """Real peak-to-trough max drawdown (absolute P&L) and its duration
    (seconds, peak-timestamp to trough-timestamp), walking the FULL
    series once and tracking the running peak seen so far -- never
    approximated from just the first/last points, which would silently
    miss an interior drawdown that recovered before the series ends (see
    this module's docstring). `None` if fewer than
    `_MIN_SNAPSHOTS_FOR_DRAWDOWN` real snapshots are available."""
    if len(snapshots) < _MIN_SNAPSHOTS_FOR_DRAWDOWN:
        return None

    peak_value = float(snapshots[0]["cumulative_pnl"])
    peak_at = _parse(snapshots[0]["captured_at"])
    best_drawdown = 0.0
    best_duration = 0.0

    for row in snapshots[1:]:
        value = float(row["cumulative_pnl"])
        when = _parse(row["captured_at"])
        drawdown = peak_value - value
        if drawdown > best_drawdown:
            best_drawdown = drawdown
            best_duration = (when - peak_at).total_seconds()
        if value > peak_value:
            peak_value = value
            peak_at = when

    return best_drawdown, best_duration


def compute_rolling_stats(account_id: str, snapshots: list[dict], *, window: int) -> RollingStats:
    """Rolling volatility/Sharpe-equivalent/Sortino-equivalent/max-
    drawdown over the last `window` real snapshots of `snapshots`
    (oldest-first, exactly the shape `SignalStore.list_equity_snapshots`
    returns). Every field is `None` when the real available history is
    too short for that specific statistic -- never computed from too few
    points to be meaningful (see module docstring for each threshold)."""
    if window < 1:
        raise ValueError("window must be >= 1")

    windowed = snapshots[-window:] if len(snapshots) > window else list(snapshots)
    sample_count = len(windowed)
    window_start = _parse(windowed[0]["captured_at"]) if windowed else None
    window_end = _parse(windowed[-1]["captured_at"]) if windowed else None

    deltas = _pnl_deltas(windowed) if sample_count >= 2 else []

    mean_pnl_delta: float | None = None
    volatility_pnl_delta: float | None = None
    sharpe_equivalent: float | None = None
    sortino_equivalent: float | None = None

    if len(deltas) >= _MIN_DELTAS_FOR_VOLATILITY:
        mean_pnl_delta = stats.mean(deltas)
        volatility_pnl_delta = stats.stdev(deltas)
        if volatility_pnl_delta > 0:
            sharpe_equivalent = mean_pnl_delta / volatility_pnl_delta

        downside = [d for d in deltas if d < 0]
        if len(downside) >= _MIN_DELTAS_FOR_VOLATILITY:
            downside_stdev = stats.stdev(downside)
            if downside_stdev > 0:
                sortino_equivalent = mean_pnl_delta / downside_stdev

    drawdown = compute_max_drawdown(windowed)
    max_drawdown, max_drawdown_duration_seconds = drawdown if drawdown is not None else (None, None)

    # E-12: Count how many snapshots have unpriced-open-symbols
    unpriced_snapshot_count = sum(
        1 for snap in windowed
        if snap.get("unpriced_open_symbols") and len(snap.get("unpriced_open_symbols", [])) > 0
    )

    return RollingStats(
        account_id=account_id,
        window=window,
        sample_count=sample_count,
        window_start=window_start,
        window_end=window_end,
        mean_pnl_delta=mean_pnl_delta,
        volatility_pnl_delta=volatility_pnl_delta,
        sharpe_equivalent=sharpe_equivalent,
        sortino_equivalent=sortino_equivalent,
        max_drawdown=max_drawdown,
        max_drawdown_duration_seconds=max_drawdown_duration_seconds,
        unpriced_snapshot_count=unpriced_snapshot_count,
    )


@dataclass
class PairCorrelation:
    account_a: str
    account_b: str
    sample_count: int
    #: Pearson correlation coefficient between the two accounts'
    #: cumulative_pnl series over their real overlapping snapshots
    #: (matched by captured_at) -- `None` (never a fabricated 0/NaN) when
    #: `sample_count < MIN_CORRELATION_SAMPLES`.
    correlation: float | None
    #: (E-12) Count of overlapping snapshots where account_a had unpriced
    #: open symbols -- correlation may be unreliable when nonzero.
    unpriced_count_a: int = 0
    #: (E-12) Count of overlapping snapshots where account_b had unpriced
    #: open symbols -- correlation may be unreliable when nonzero.
    unpriced_count_b: int = 0

    def to_dict(self) -> dict:
        return {
            "account_a": self.account_a,
            "account_b": self.account_b,
            "sample_count": self.sample_count,
            "correlation": self.correlation,
            "unpriced_count_a": self.unpriced_count_a,
            "unpriced_count_b": self.unpriced_count_b,
            "note": f"Pearson correlation of the two accounts' real cumulative_pnl series over "
            f"their overlapping snapshots (matched by captured_at) -- used as a proxy for "
            f"strategy correlation since this codebase has no separate per-provider/per-analyst "
            f"equity attribution. Omitted (null) entirely, never a fabricated 0 or NaN-as-zero, "
            f"when fewer than {MIN_CORRELATION_SAMPLES} real overlapping snapshots exist. "
            f"When unpriced_count_a or unpriced_count_b is nonzero (E-12), those accounts held "
            f"unpriced positions at some overlapping moments, so mark-availability changes in "
            f"the series may inflate/deflate correlation values.",
        }


def compute_pairwise_correlation(
    account_a: str,
    snapshots_a: list[dict],
    account_b: str,
    snapshots_b: list[dict],
) -> PairCorrelation:
    """Real Pearson correlation between two accounts' `cumulative_pnl`
    series over their real overlapping time window, matched by exact
    `captured_at` timestamp (the only honest way to align two
    independently-ticking snapshot series without inventing an
    interpolated point). `None` when fewer than `MIN_CORRELATION_SAMPLES`
    real overlapping points exist."""
    by_time_b = {
        row["captured_at"]: (float(row["cumulative_pnl"]), row) for row in snapshots_b
    }
    paired_a: list[float] = []
    paired_b: list[float] = []
    unpriced_count_a = 0
    unpriced_count_b = 0

    for row_a in snapshots_a:
        ts = row_a["captured_at"]
        match = by_time_b.get(ts)
        if match is not None:
            pnl_b, row_b = match
            paired_a.append(float(row_a["cumulative_pnl"]))
            paired_b.append(pnl_b)
            # E-12: Track unpriced symbols at overlapping moments
            if row_a.get("unpriced_open_symbols") and len(row_a.get("unpriced_open_symbols", [])) > 0:
                unpriced_count_a += 1
            if row_b.get("unpriced_open_symbols") and len(row_b.get("unpriced_open_symbols", [])) > 0:
                unpriced_count_b += 1

    sample_count = len(paired_a)
    correlation: float | None = None
    if sample_count >= MIN_CORRELATION_SAMPLES:
        # A perfectly constant series on either side has zero variance,
        # making Pearson's r undefined -- statistics.correlation raises
        # StatisticsError there; treat that the same as "not enough real
        # signal to report", never a fabricated 0/NaN.
        try:
            correlation = stats.correlation(paired_a, paired_b)
        except stats.StatisticsError:
            correlation = None

    return PairCorrelation(
        account_a=account_a,
        account_b=account_b,
        sample_count=sample_count,
        correlation=correlation,
        unpriced_count_a=unpriced_count_a,
        unpriced_count_b=unpriced_count_b,
    )
