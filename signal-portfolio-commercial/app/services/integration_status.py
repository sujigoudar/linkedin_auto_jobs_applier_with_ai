"""INTEGRATION_DECISION.md S11's own "Integration Status panel showing
mapped streams, published/received/applied watermarks, gaps, rejected
records, last qualified snapshot, source-observation age, end-to-end
lag and reconciliation status" -- the bounded slice of it this build
can honestly compute right now: mapped streams
(`ExportStreamRegistration`), received/applied watermarks and counts
(`InboxEvent`), and the resulting real `Book.PLATFORM` ledger entries
those events produced.

This is also S12's own "First complete proof" surface: "one labeled
simulated source instruction produces authoritative simulated
executions in the private engine, exports its exact history once,
populates the corresponding private staff view" -- this module is that
private staff view's own backing query. It has no fabricated numbers:
a stream with zero received events reports `received_count=0`, not
`unsupported`, because "zero events received" is itself a real,
honestly-computed fact (unlike AD-01's own "Open incidents", which has
no backing model at all and so stays `unsupported`).

Deliberately NOT built here (later, explicitly scoped work): "gaps",
"last qualified snapshot", "source-observation age" and "end-to-end
lag" all need either a snapshot/bootstrap mechanism
(INTEGRATION_DECISION.md S6's own "Snapshot plus deltas", not yet
built) or a reconciliation cursor comparing this side's counts against
signal-copier's own outbox counts (a cross-service read this app has
no access path for yet) -- this module reports only what a single
query against this app's own tables can honestly answer.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.integration_inbox import ExportStreamRegistration, InboxEvent
from app.models.ledger import Book, LedgerEntry


@dataclass(frozen=True)
class StreamStatus:
    source_stream: str
    environment: str
    registered_at: datetime
    received_count: int
    applied_count: int
    latest_received_at: datetime | None
    latest_applied_at: datetime | None

    @property
    def unapplied_count(self) -> int:
        """Received but not yet applied -- always 0 in this build today
        (app/services/integration_inbox.py's own ingest_export_event
        applies synchronously, in the same call that receives), but
        computed for real rather than hardcoded, so a future async
        projection worker (this module's own docstring: "a later slice")
        shows a genuine backlog here instead of silently reporting 0
        forever."""
        return self.received_count - self.applied_count


@dataclass(frozen=True)
class IntegrationStatusReport:
    streams: list[StreamStatus]
    platform_ledger_entries_count: int


def get_integration_status(session: Session, *, tenant_id: str) -> IntegrationStatusReport:
    registrations = list(
        session.scalars(
            select(ExportStreamRegistration)
            .where(ExportStreamRegistration.tenant_id == tenant_id)
            .order_by(ExportStreamRegistration.source_stream)
        ).all()
    )

    streams: list[StreamStatus] = []
    for registration in registrations:
        received_count = (
            session.scalar(
                select(func.count())
                .select_from(InboxEvent)
                .where(InboxEvent.tenant_id == tenant_id, InboxEvent.source_stream == registration.source_stream)
            )
            or 0
        )
        applied_count = (
            session.scalar(
                select(func.count())
                .select_from(InboxEvent)
                .where(
                    InboxEvent.tenant_id == tenant_id,
                    InboxEvent.source_stream == registration.source_stream,
                    InboxEvent.applied_at.is_not(None),
                )
            )
            or 0
        )
        latest_received_at = session.scalar(
            select(func.max(InboxEvent.received_at)).where(
                InboxEvent.tenant_id == tenant_id, InboxEvent.source_stream == registration.source_stream
            )
        )
        latest_applied_at = session.scalar(
            select(func.max(InboxEvent.applied_at)).where(
                InboxEvent.tenant_id == tenant_id, InboxEvent.source_stream == registration.source_stream
            )
        )
        streams.append(
            StreamStatus(
                source_stream=registration.source_stream,
                environment=registration.environment,
                registered_at=registration.created_at,
                received_count=received_count,
                applied_count=applied_count,
                latest_received_at=latest_received_at,
                latest_applied_at=latest_applied_at,
            )
        )

    platform_ledger_entries_count = (
        session.scalar(
            select(func.count())
            .select_from(LedgerEntry)
            .where(LedgerEntry.tenant_id == tenant_id, LedgerEntry.book == Book.PLATFORM)
        )
        or 0
    )

    return IntegrationStatusReport(streams=streams, platform_ledger_entries_count=platform_ledger_entries_count)
