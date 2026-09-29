"""CU-06 "Performance and costs" -- the real, customer-scoped read
backing `/app/performance`. See dashboard_spec/screens/CU-06.md for the
full screen contract this implements a bounded slice of.

"Report reconciled customer results separately from model or platform
records" (CU-06's own purpose) is exactly what `Book.FOLLOWER` already
exists to isolate (app/models/ledger.py's own docstring: "C2 strategy
model performance is not customer actual performance") -- this module
narrows `platform_performance.compute_book_performance` to
`Book.FOLLOWER` AND this one customer's own `PlatformConnection` ids
(never the whole tenant's FOLLOWER book, which spans every customer),
reusing that module's real volume-weighted-average-cost replay rather
than a second, parallel calculation.

## What is deliberately NOT computed here

No `Book.FOLLOWER` ledger entry has ever been written by any process in
this build yet (app/services/customer_performance_state.py's own
docstring: "Unreachable in practice today"), so `report` is real but,
today, always empty for every real caller -- the same "real query,
unreachable result until a real observation connector exists" shape
that module already establishes.

Costs and cash flows (subscription cost basis, deposits/withdrawals)
and Attribution have no backing model anywhere in this build -- no
subscription-to-selection linkage exists (AD-10's own docstring: "no
per-price Subscription linkage"), and no deposit/withdrawal/cashflow
ledger exists at all. Both are rendered as explicit UNSUPPORTED notes
by the template, never a fabricated figure.

## Maximum drawdown (M-CU-06-02) and completed-episode win rate (M-CU-06-03)

Both ARE now computed here, from the same real, timestamp-ordered
`Book.FOLLOWER` ledger entries `compute_book_performance` already
replays for this customer's own connections -- never a second,
diverging query.

- The equity series (`equity_series` below) is a real cumulative-
  realized-P&L walk built by replaying those entries through
  `platform_performance.load_ordered_root_entries` +
  `platform_performance.apply_entry` (the exact same real volume-
  weighted-average-cost position math `compute_book_performance` uses,
  not a reimplementation) -- normalized to a starting baseline of
  `Decimal(0)` (this system tracks no real starting-capital figure to
  index against, same honest convention `signal-copier/app/
  statistics.py`'s own module docstring establishes for its own
  `cumulative_pnl` series: an absolute P&L-delta series, never a
  fabricated indexed-to-100 return implying a starting capital this
  system doesn't track).
- `max_drawdown` is the real peak-to-trough walk over that series,
  ported from `signal-copier/app/statistics.py::compute_max_drawdown`
  (same running-peak-vs-largest-later-drop algorithm; see this
  module's own `_compute_max_drawdown` for the port and why it's a
  port, not an import: signal-copier is a separate deployable service,
  not a dependency of this app -- only `signal_platform_contracts`,
  which carries no such statistics code, is shared between them).
  `None` when fewer than 2 real points exist (no peak-to-trough is
  possible yet), never a fabricated 0.
- `win_rate` reuses `app/services/analyst_attribution.py`'s own real
  FIFO-lot open/close tracking (INT-026) -- a completed episode there
  is exactly one real lot that went from open to fully closed; `None`
  (never a fabricated 0%) until at least one real episode has
  completed.

Both stay honestly empty (`equity_series == []`, `max_drawdown is
None`, `win_rate is None`) for the same "real query, unreachable
result until a real observation connector exists" reason
`compute_book_performance`'s own aggregate report does today (no
`Book.FOLLOWER` ledger entry has ever been written by any process in
this build yet) -- never a guessed figure standing in for that.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.ledger import Book, LedgerEntry, ReconciliationState
from app.models.platform_connection import PlatformConnection, PlatformConnectionState
from app.services.analyst_attribution import compute_analyst_attribution
from app.services.customer_performance_state import PerformanceState, get_customer_performance_state
from app.services.platform_connection import list_own_platform_connections
from app.services.platform_performance import (
    InstrumentPerformance,
    PlatformPerformanceReport,
    apply_entry,
    compute_book_performance,
    load_ordered_root_entries,
)


@dataclass(frozen=True)
class EquityPoint:
    """One real point of this customer's own cumulative-realized-P&L
    walk -- see module docstring for why this, never an indexed
    percentage return."""

    at: datetime
    cumulative_pnl: Decimal


@dataclass(frozen=True)
class DrawdownResult:
    """A real peak-to-trough max drawdown, ported from signal-copier's
    own `compute_max_drawdown` -- see module docstring."""

    max_drawdown: Decimal  # always >= 0
    duration: timedelta  # real wall-clock span, peak timestamp to trough timestamp


def compute_customer_equity_series(
    session: Session, *, tenant_id: str, follower_connection_ids: frozenset[str]
) -> list[EquityPoint]:
    """Replays this customer's own real, timestamp-ordered
    `Book.FOLLOWER` ledger entries (scoped to `follower_connection_ids`,
    same as `compute_book_performance`) through
    `platform_performance.apply_entry` -- the SAME real volume-weighted-
    average-cost position math `compute_book_performance` itself uses,
    never a second, diverging replay -- accumulating a running total
    realized P&L across every instrument, in real chronological order.
    A point is recorded after every entry that actually realizes P&L
    (an opening/adding entry moves no cumulative total, so it adds no
    point); the series starts with a real `Decimal(0)` baseline at the
    first entry's own `event_time`, per module docstring. Empty when
    there are no real entries at all."""
    entries, latest_correction_by_original = load_ordered_root_entries(
        session, tenant_id=tenant_id, book=Book.FOLLOWER, follower_connection_ids=follower_connection_ids
    )
    if not entries:
        return []

    per_instrument: dict[str, InstrumentPerformance] = {}
    running_total = Decimal(0)
    series = [EquityPoint(at=entries[0].event_time, cumulative_pnl=Decimal(0))]

    for entry in entries:
        ip = per_instrument.setdefault(entry.instrument, InstrumentPerformance(instrument=entry.instrument))
        delta = apply_entry(ip, entry)
        if delta != 0:
            running_total += delta
            series.append(EquityPoint(at=entry.event_time, cumulative_pnl=running_total))

    return series


def _compute_max_drawdown(series: list[EquityPoint]) -> DrawdownResult | None:
    """Real peak-to-trough max drawdown over `series`, walking the FULL
    series once and tracking the running peak seen so far -- ported,
    same algorithm, from `signal-copier/app/statistics.py::
    compute_max_drawdown` (never approximated from just the first/last
    points, which would silently miss an interior drawdown that
    recovered before the series ends -- see that module's own
    docstring, and this module's own load-bearing test, which breaks
    this exact invariant on purpose and confirms the regression test
    catches it). `None` if fewer than 2 real points are available (no
    peak-to-trough is possible with just one point)."""
    if len(series) < 2:
        return None

    peak_value = series[0].cumulative_pnl
    peak_at = series[0].at
    best_drawdown = Decimal(0)
    best_duration = timedelta(0)

    for point in series[1:]:
        drawdown = peak_value - point.cumulative_pnl
        if drawdown > best_drawdown:
            best_drawdown = drawdown
            best_duration = point.at - peak_at
        if point.cumulative_pnl > peak_value:
            peak_value = point.cumulative_pnl
            peak_at = point.at

    return DrawdownResult(max_drawdown=best_drawdown, duration=best_duration)


@dataclass(frozen=True)
class CustomerPerformanceReport:
    connections: list[PlatformConnection]
    performance_state: PerformanceState
    report: PlatformPerformanceReport
    unresolved_item_count: int | None
    #: See module docstring -- empty until a real Book.FOLLOWER entry
    #: exists for this customer's own connections.
    equity_series: list[EquityPoint] = field(default_factory=list)
    #: `None` until at least 2 real equity_series points exist.
    max_drawdown: DrawdownResult | None = None
    #: `None` (never a fabricated 0%) until at least one real completed
    #: episode exists -- see `analyst_attribution.py`'s own FIFO-lot
    #: open/close tracking (INT-026), reused (not reimplemented) here.
    win_rate: Decimal | None = None
    completed_episode_count: int = 0
    winning_episode_count: int = 0


def get_own_performance_report(session: Session, *, tenant_id: str, user_id: str) -> CustomerPerformanceReport:
    connections = list_own_platform_connections(session, tenant_id=tenant_id, user_id=user_id)
    declared_connections = [c for c in connections if c.state == PlatformConnectionState.DECLARED]
    connection_ids = frozenset(c.connection_id for c in declared_connections)

    # get_customer_performance_state itself only inspects ONE connection
    # (CU-01/CU-03's own precedent); a customer's overall page-level state
    # is AVAILABLE if any one connection's own observations are AVAILABLE,
    # else the "most informative" state of the rest -- never a fabricated
    # aggregate not backed by a real per-connection check.
    performance_state = PerformanceState.NOT_CONNECTED
    for connection in declared_connections:
        state = get_customer_performance_state(session, tenant_id=tenant_id, connection=connection)
        if state == PerformanceState.AVAILABLE:
            performance_state = PerformanceState.AVAILABLE
            break
        if state == PerformanceState.AWAITING_OBSERVATIONS:
            performance_state = PerformanceState.AWAITING_OBSERVATIONS

    if connection_ids:
        report = compute_book_performance(
            session, tenant_id=tenant_id, book=Book.FOLLOWER, follower_connection_ids=connection_ids
        )
        unresolved_item_count = session.scalar(
            select(func.count())
            .select_from(LedgerEntry)
            .where(
                LedgerEntry.tenant_id == tenant_id,
                LedgerEntry.book == Book.FOLLOWER,
                LedgerEntry.follower_connection_id.in_(connection_ids),
                LedgerEntry.reconciliation_state != ReconciliationState.RECONCILED,
            )
        )
        equity_series = compute_customer_equity_series(
            session, tenant_id=tenant_id, follower_connection_ids=connection_ids
        )
        max_drawdown = _compute_max_drawdown(equity_series)
        episode_report = compute_analyst_attribution(
            session, tenant_id=tenant_id, book=Book.FOLLOWER, follower_connection_ids=connection_ids
        )
        win_rate = episode_report.win_rate
        completed_episode_count = episode_report.completed_episode_count
        winning_episode_count = episode_report.winning_episode_count
    else:
        report = PlatformPerformanceReport()
        # No declared connection at all -- there is no scoped snapshot to
        # count "unresolved" over yet, so this is omitted basis, never a
        # measured zero (CU-06's own "partial" state obligation).
        unresolved_item_count = None
        equity_series = []
        max_drawdown = None
        win_rate = None
        completed_episode_count = 0
        winning_episode_count = 0

    return CustomerPerformanceReport(
        connections=connections,
        performance_state=performance_state,
        report=report,
        unresolved_item_count=unresolved_item_count,
        equity_series=equity_series,
        max_drawdown=max_drawdown,
        win_rate=win_rate,
        completed_episode_count=completed_episode_count,
        winning_episode_count=winning_episode_count,
    )
