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
from app.services.integration_inbox import PARKED_REASON_UNSUPPORTED_SCHEMA_VERSION


@dataclass(frozen=True)
class StreamStatus:
    source_stream: str
    environment: str
    registered_at: datetime
    received_count: int
    applied_count: int
    latest_received_at: datetime | None
    latest_applied_at: datetime | None
    #: INT-007 "Unsupported schema version": how many received-but-
    #: unapplied rows on this stream carry a `parked_reason` (never
    #: `None`, unlike a row merely parked behind an ordering gap -- see
    #: app/services/integration_inbox.py's own `_apply_projection`).
    #: > 0 means real economic history exists that this build could not
    #: honestly apply, never silently dropped or coerced.
    schema_incompatible_count: int
    #: The lowest `export_sequence` on this stream ever parked for an
    #: unsupported schema version -- INT-007's own "affected cutoff":
    #: every later event on this stream is also permanently parked
    #: behind it (see `_next_expected_sequence`'s own docstring), so
    #: this is the exact point a caller should say "history is
    #: incomplete from here" rather than showing a false complete-data
    #: state.
    earliest_schema_incompatible_sequence: int | None

    @property
    def unapplied_count(self) -> int:
        """Received but not yet applied -- either parked behind a
        genuine ordering gap, or parked because this build could not
        honestly interpret it (see `schema_incompatible_count`)."""
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
        schema_incompatible_count = (
            session.scalar(
                select(func.count())
                .select_from(InboxEvent)
                .where(
                    InboxEvent.tenant_id == tenant_id,
                    InboxEvent.source_stream == registration.source_stream,
                    InboxEvent.parked_reason.like(f"{PARKED_REASON_UNSUPPORTED_SCHEMA_VERSION}:%"),
                )
            )
            or 0
        )
        earliest_schema_incompatible_sequence = session.scalar(
            select(func.min(InboxEvent.export_sequence)).where(
                InboxEvent.tenant_id == tenant_id,
                InboxEvent.source_stream == registration.source_stream,
                InboxEvent.parked_reason.like(f"{PARKED_REASON_UNSUPPORTED_SCHEMA_VERSION}:%"),
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
                schema_incompatible_count=schema_incompatible_count,
                earliest_schema_incompatible_sequence=earliest_schema_incompatible_sequence,
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
