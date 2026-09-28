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
by the template, never a fabricated figure. Maximum drawdown
(M-CU-06-02) and completed-episode win rate (M-CU-06-03) need a
chronological normalized-equity series and an episode/trade-completion
concept `compute_book_performance`'s own per-instrument replay does not
produce -- also rendered UNSUPPORTED, never guessed from the realized
P&L this module does compute.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.ledger import Book, LedgerEntry, ReconciliationState
from app.models.platform_connection import PlatformConnection, PlatformConnectionState
from app.services.customer_performance_state import PerformanceState, get_customer_performance_state
from app.services.platform_connection import list_own_platform_connections
from app.services.platform_performance import PlatformPerformanceReport, compute_book_performance


@dataclass(frozen=True)
class CustomerPerformanceReport:
    connections: list[PlatformConnection]
    performance_state: PerformanceState
    report: PlatformPerformanceReport
    unresolved_item_count: int | None


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
    else:
        report = PlatformPerformanceReport()
        # No declared connection at all -- there is no scoped snapshot to
        # count "unresolved" over yet, so this is omitted basis, never a
        # measured zero (CU-06's own "partial" state obligation).
        unresolved_item_count = None

    return CustomerPerformanceReport(
        connections=connections,
        performance_state=performance_state,
        report=report,
        unresolved_item_count=unresolved_item_count,
    )
