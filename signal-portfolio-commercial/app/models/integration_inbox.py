"""ExportStreamRegistration + InboxEvent: the commercial half of the
Signal Platform Integration Correction Pack's own INTEGRATION_DECISION.md
S4.4/S6 -- the inbox a restricted relay (a later slice) delivers
signal-copier's own export outbox events into.

`ExportStreamRegistration` is the "server-controlled registration" S6
requires: "Bind permitted stream/account/tenant pairs from server-
controlled registration. Never accept an arbitrary tenant_id, book or
account from a sender merely because the request is authenticated." An
inbound envelope's own `source_stream` is looked up here to find which
tenant it belongs to -- the envelope itself carries no tenant_id at all
(signal-copier has no commercial-tenant concept), so there is nothing
to trust or distrust from the payload; the mapping is entirely this
table's own, owner-registered row.

`InboxEvent` is the receipt record: one row per event_id, ever --
`event_id` is the primary key, so a redelivery of the identical event
is a harmless re-insert-or-detect-existing (app/services/
integration_inbox.py's own ingest_export_event, never a second row).
`applied_at` is separate from `received_at` (S6's own "received,
validated and applied high-water marks", folded to two marks in this
bounded slice: this build has no schema-version negotiation yet, so
"validated" collapses into "received" for now) -- a row can be received
before its projection (e.g. a real four-book ledger entry) is applied,
though in this slice's own synchronous ingest_export_event both happen
in the same call and the same transaction.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ExportStreamRegistration(Base):
    __tablename__ = "export_stream_registrations"

    registration_id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    #: Matches signal_platform_contracts.EventEnvelope.source_stream
    #: exactly -- globally unique (not per-tenant) since a stream can
    #: only ever belong to the ONE tenant it was registered for.
    source_stream: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    environment: Mapped[str] = mapped_column(String, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)


class InboxEvent(Base):
    __tablename__ = "inbox_events"

    #: The producer's own idempotency key (EventEnvelope.event_id) --
    #: this table's own primary key too, so a redelivery can never create
    #: a second row for the same real event.
    event_id: Mapped[str] = mapped_column(String, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String, nullable=False)
    source_stream: Mapped[str] = mapped_column(String, nullable=False)
    export_sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    envelope_json: Mapped[str] = mapped_column(String, nullable=False)
    payload_hash: Mapped[str] = mapped_column(String, nullable=False)

    #: Set when this row is first inserted -- when the relay's delivery
    #: was durably recorded, regardless of whether its projection (e.g. a
    #: ledger entry) has been applied yet.
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    #: NULL until this event's projection (if it has one -- SOURCE_RECEIPT
    #: has none, by S7's own "SOURCE records what was recommended; it
    #: does not claim an execution") has actually been applied.
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: Set only for an event this app's own append-only ledger recorded
    #: from this event's payload -- lets a caller trace an inbox row to
    #: its exact resulting LedgerEntry, never re-derived by matching on
    #: loose fields.
    ledger_entry_id: Mapped[str | None] = mapped_column(String, nullable=True)
    #: NULL for every normally-applied or normally-parked-on-a-gap row.
    #: Set (and `applied_at` left NULL, permanently, for THIS row and
    #: every later `export_sequence` on the same stream -- see
    #: app/services/integration_inbox.py's own `_next_expected_sequence`
    #: docstring) when this row could be received and durably stored but
    #: NOT understood well enough to apply honestly: an unsupported
    #: `schema_version` (INTEGRATION_ACCEPTANCE_CASES.json INT-007,
    #: "Unsupported schema version": "Unknown event is parked without
    #: economic application... Schema incompatibility and affected
    #: cutoff are visible", never "best-effort financial coercion") or
    #: an `EventType` with no implemented payload model yet. A short,
    #: machine-parseable prefix (`unsupported_schema_version:`,
    #: `unimplemented_event_type:`) so a caller (e.g.
    #: app/services/integration_status.py) can group and surface these
    #: without re-parsing `envelope_json`.
    parked_reason: Mapped[str | None] = mapped_column(String, nullable=True)

    __table_args__ = (UniqueConstraint("source_stream", "export_sequence", name="uq_inbox_events_stream_sequence"),)
