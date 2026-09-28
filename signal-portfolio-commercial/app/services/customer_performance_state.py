"""CU-01/CU-03's own "Actual performance"/"Observed net P&L" cards --
Signal Platform Integration Correction Pack's own INTEGRATION_DECISION.md
S11: "Replace blanket UNSUPPORTED with precise states:
NOT_IMPLEMENTED, NOT_CONNECTED, AWAITING_OBSERVATIONS,
INCOMPLETE_HISTORY, STALE, RIGHTS_RESTRICTED, PLATFORM_UNSUPPORTED or
AVAILABLE. A state transition must depend on backend evidence, not
merely a file's existence."

This module computes exactly the three states this build can honestly
distinguish today, from real evidence:

- NOT_CONNECTED: no DECLARED PlatformConnection backs this customer's
  mandate.
- AWAITING_OBSERVATIONS: a connection exists, but zero `Book.FOLLOWER`
  ledger entries have ever been recorded against it (real query against
  `LedgerEntry.follower_connection_id`, per app/models/ledger.py's own
  docstring on that column) -- this is the ONLY state reachable in
  practice today, since app/models/platform_connection.py's own
  docstring establishes that a connection here is only ever DECLARED
  (customer-asserted), never a real authorized observation channel; no
  process in this codebase has ever written a FOLLOWER-book entry.
- AVAILABLE: at least one real `Book.FOLLOWER` entry exists for this
  connection. Unreachable with today's callers, but the query and the
  state it reports are both real -- ready for the actual observation
  connector (INTEGRATION_DECISION.md S12 step 6, not yet built) to make
  reachable, without this module needing to change.

Deliberately NOT computed here (the other four S11 states):
INCOMPLETE_HISTORY, STALE and RIGHTS_RESTRICTED all need signals a real
observation connector would carry (a coverage/backfill cursor, a last-
observed-at watermark, a rights grant record) that don't exist for
FOLLOWER data in this build. PLATFORM_UNSUPPORTED needs a per-platform
capability registry this build also doesn't have. Returning one of
these four without real backing evidence would be exactly the
"transition that depends on... a file's existence" S11 warns against --
so this module never returns them; a future connector slice adds them
alongside the real signals that justify them.
"""
from __future__ import annotations

import enum

from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from app.models.ledger import Book, LedgerEntry
from app.models.platform_connection import PlatformConnection


class PerformanceState(str, enum.Enum):
    NOT_CONNECTED = "not_connected"
    AWAITING_OBSERVATIONS = "awaiting_observations"
    AVAILABLE = "available"


def get_customer_performance_state(
    session: Session, *, tenant_id: str, connection: PlatformConnection | None
) -> PerformanceState:
    if connection is None:
        return PerformanceState.NOT_CONNECTED

    has_observation = session.scalar(
        select(
            exists().where(
                LedgerEntry.tenant_id == tenant_id,
                LedgerEntry.book == Book.FOLLOWER,
                LedgerEntry.follower_connection_id == connection.connection_id,
            )
        )
    )
    return PerformanceState.AVAILABLE if has_observation else PerformanceState.AWAITING_OBSERVATIONS
