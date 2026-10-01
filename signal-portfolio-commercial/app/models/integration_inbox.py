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
    #: `signal_platform_contracts.EventEnvelope.producer_generation` --
    #: "Bumped when a stream is re-bootstrapped from a fresh snapshot...
    #: lets a receiver detect and isolate a new generation rather than
    #: interleaving it with the old one's sequence" (envelope.py's own
    #: docstring). `export_sequence` is only ever monotonic WITHIN
    #: (source_stream, producer_generation) -- see this table's own
    #: `__table_args__` uniqueness scope below, and
    #: app/services/integration_inbox.py's own `_established_generation`
    #: docstring (INTEGRATION_ACCEPTANCE_CASES.json INT-010 "Producer
    #: restored to older database").
    producer_generation: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
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
    #: Set ONLY for an applied `EXECUTION_APPLIED` row, to
    #: f"{broker}|{broker_order_id}" from that event's own payload --
    #: the one identity a later, separately-delivered `FEE` event for the
    #: SAME execution can correlate against (INT-012 "Late fee revises
    #: net report"). Neither payload carries the other's `event_id` or
    #: this app's own `ledger_entry_id`, so this column is the only
    #: shared key between the two. NULL for every other row.
    execution_correlation_key: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
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
    #: an `EventType` with no implemented payload model yet, or a
    #: `producer_generation` mismatch against this stream's own
    #: established generation (INT-010 "Producer restored to older
    #: database" -- app/services/integration_inbox.py's own
    #: `_established_generation` docstring: an OLDER generation than
    #: already established is a detected rollback, a NEWER one requires
    #: a reconciled bootstrap this build does not have; neither is ever
    #: silently applied), or a `POSITION_SNAPSHOT` page whose own
    #: manifest is internally inconsistent (`manifest_metadata_mismatch:`
    #: -- a later page disagreeing with an earlier one's own
    #: cutoff_sequence/page_count) or spans more than one
    #: producer_generation (`manifest_generation_mismatch:` -- INT-009's
    #: own "Attempt to mix a page from another generation... rejected").
    #: A short, machine-parseable prefix (`unsupported_schema_version:`,
    #: `unimplemented_event_type:`, `generation_rollback_detected:`,
    #: `new_generation_requires_bootstrap:`, `manifest_metadata_mismatch:`,
    #: `manifest_generation_mismatch:`) so a caller (e.g.
    #: app/services/integration_status.py) can group and surface these
    #: without re-parsing `envelope_json`.
    parked_reason: Mapped[str | None] = mapped_column(String, nullable=True)

    #: The four columns below are set ONLY for a `POSITION_SNAPSHOT` row
    #: (S6 "Snapshot plus deltas", INTEGRATION_ACCEPTANCE_CASES.json
    #: INT-008 "Snapshot and delta overlap" / INT-009 "Interrupted
    #: bootstrap resumes") -- NULL for every other event type. Cached
    #: straight from that row's own `PositionSnapshotPayload` so a
    #: manifest's own completeness/consistency can be queried directly
    #: against this table, never by re-parsing every page's
    #: `envelope_json`. See app/services/integration_inbox.py's own
    #: `_apply_position_snapshot_page` docstring for how these are used.
    manifest_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    snapshot_page_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    snapshot_page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    snapshot_cutoff_sequence: Mapped[int | None] = mapped_column(Integer, nullable=True)

    #: INT-027 "All permitted source outcomes reach research": set ONLY on
    #: a `SOURCE_RECEIPT` row, once a real, later, correlated
    #: `ROUTING_ADMISSION_OUTCOME` event for the SAME signal has been
    #: applied -- the real routing/admission/fill outcome signal-copier's
    #: own `app/engine.py` reached for it (one of
    #: `signal_platform_contracts.payloads._KNOWN_ROUTING_OUTCOMES`, e.g.
    #: "admitted_filled", "rejected", "not_routed" -- see that tuple's own
    #: comment for exactly which INT-027-requested categories this build
    #: can and cannot produce). `None` until that follow-up event arrives
    #: and applies -- never guessed from this row's own disposition alone
    #: (app/services/source_coverage.py's own module docstring used to
    #: name this exact gap before this column existed). NULL for every
    #: non-`SOURCE_RECEIPT` row.
    routing_outcome: Mapped[str | None] = mapped_column(String, nullable=True)

    #: Track 35: set on a `SOURCE_EVENT` row, to
    #: f"{tenant_id}|{source_provider_id}|{source_channel_id}|
    #: {source_event_id}" from that event's own `SourceEventPayload.
    #: source` (app/services/integration_inbox.py's own
    #: `_source_event_native_key`) -- the one native-provider identity
    #: `signal_platform_contracts.identity.SourceIdentity`'s own
    #: docstring says is stable across redelivery ("true deduplication
    #: and edit/delete/reply correlation both key off this pair, never
    #: off re-parsed message text"). Lets a LATER `SourceEventKind.EDIT`
    #: naming this same native message (via its own `source.
    #: original_source_event_id`) resolve back to it without ever
    #: guessing from re-parsed text or arrival order.
    #:
    #: Track 41: ALSO set, the identical way, on a `SOURCE_RECEIPT`
    #: row -- signal-copier's own `_handle_signal` dedupes an edited
    #: message by exact `(channel_id, message_id, revision_id)`, so an
    #: edited instruction's `SOURCE_RECEIPT` arrives as its own,
    #: independent row rather than a mutation of the original's. A
    #: LATER receipt whose own `source.original_source_event_id` names
    #: an earlier receipt resolves back to it through this same column,
    #: letting `_apply_projection` book a correction (superseding the
    #: earlier `Book.SOURCE` entry) instead of a second, independent
    #: one for the same economic fact. NULL for every row that is
    #: neither a `SOURCE_EVENT` nor a `SOURCE_RECEIPT`.
    source_event_native_key: Mapped[str | None] = mapped_column(String, nullable=True, index=True)

    __table_args__ = (
        #: Scoped to (source_stream, producer_generation, export_sequence)
        #: -- NOT (source_stream, export_sequence) alone -- because
        #: export_sequence is only ever monotonic WITHIN one generation
        #: of a stream (envelope.py's own docstring); a legitimate new
        #: generation restarting its own sequence at 0 must never
        #: collide with the previous generation's own sequence 0.
        UniqueConstraint(
            "source_stream", "producer_generation", "export_sequence", name="uq_inbox_events_stream_generation_sequence",
        ),
    )
