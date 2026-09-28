"""Signal Platform Integration Correction Pack's own INTEGRATION_DECISION.md
S4.4/S6: the commercial inbox's own ingest path. See
app/models/integration_inbox.py's own docstring for the two tables this
reads/writes.

`ingest_export_event` is written to be safely callable by the
restricted relay worker (app/api/relay_routes.py, backed by
`relay_role` -- see app/db.py's `_apply_relay_role_access`) or directly
in tests against the RLS-bypassing `db_session` fixture -- it takes the
envelope's raw JSON exactly as the private export outbox stored it
(never a re-derived/rebuilt one), and is idempotent: calling it twice
with the identical bytes is a harmless no-op, calling it twice with the
SAME event_id but DIFFERENT bytes is a real, loud integrity error.

RLS chicken-and-egg resolution: every other tenant-scoped table in this
app relies on the generic per-tenant RLS policy (`app/db.py`'s
`tenant_isolation`), which only ever permits a session already scoped
(via `set_tenant_scope`) to ITS OWN tenant_id -- correct for a browser
request, which always knows its tenant before touching the database.
`_registered_tenant_id` below has the opposite shape: it looks up
`export_stream_registrations` BY `source_stream` specifically to
DISCOVER the tenant, before any scope can be set. `relay_role` gets
exactly one extra, bespoke permissive policy granting it unscoped SELECT
on that one table alone (`relay_stream_lookup`); `ingest_export_event`
resolves the tenant through it FIRST and calls `set_tenant_scope`
immediately after, before touching `inbox_events` or `ledger_entries`
-- both of which stay protected by the same, unmodified `tenant_isolation`
policy every other table uses.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session
from signal_platform_contracts import EventEnvelope, EventType, ExecutionAppliedPayload

from app.db import set_tenant_scope
from app.models.integration_inbox import ExportStreamRegistration, InboxEvent
from app.models.ledger import Book, Side
from app.services.ledger import append_entry

_SIDE_BY_PAYLOAD_VALUE = {"buy": Side.BUY, "sell": Side.SELL}


class StreamAlreadyRegisteredToAnotherTenantError(Exception):
    pass


class UnregisteredStreamError(Exception):
    pass


class EventIntegrityError(Exception):
    pass


def register_export_stream(
    session: Session, *, tenant_id: str, source_stream: str, environment: str
) -> ExportStreamRegistration:
    """S6: "Bind permitted stream/account/tenant pairs from server-
    controlled registration." An owner (never a sender) calls this once
    per real signal-copier deployment/stream to declare which tenant its
    events belong to -- `ingest_export_event` trusts ONLY this table,
    never anything an inbound envelope itself claims (it has no tenant_id
    field to claim one with at all)."""
    existing = session.scalars(
        select(ExportStreamRegistration).where(ExportStreamRegistration.source_stream == source_stream)
    ).first()
    if existing is not None:
        if existing.tenant_id != tenant_id:
            raise StreamAlreadyRegisteredToAnotherTenantError(
                f"source_stream {source_stream!r} is already registered to a different tenant"
            )
        return existing
    registration = ExportStreamRegistration(tenant_id=tenant_id, source_stream=source_stream, environment=environment)
    session.add(registration)
    session.flush()
    return registration


def _registered_tenant_id(session: Session, source_stream: str) -> str | None:
    registration = session.scalars(
        select(ExportStreamRegistration).where(ExportStreamRegistration.source_stream == source_stream)
    ).first()
    return registration.tenant_id if registration is not None else None


def ingest_export_event(session: Session, envelope_json: str) -> InboxEvent:
    """Idempotent, tenant-safe ingest of one exported envelope. Never
    trusts a tenant_id from the payload -- the ONLY source of tenant
    binding is `register_export_stream`'s own table, keyed by
    `source_stream`, per S6's own "server-controlled registration"
    requirement.

    Resolves the tenant BEFORE doing anything else and calls
    `set_tenant_scope` immediately after -- this is the real fix for the
    RLS chicken-and-egg gap this module's own docstring used to defer:
    under a real `relay_role` connection (app/db.py's
    `_apply_relay_role_access`), `export_stream_registrations` is the
    ONE table that role can read without any scope set yet (its own
    bespoke `relay_stream_lookup` policy); every other read/write in
    this function -- including the redelivery dedup lookup by
    `event_id` -- happens only AFTER scope is set, so it is protected by
    the same generic `tenant_isolation` policy as every other
    tenant-scoped table in this app."""
    envelope = EventEnvelope.model_validate_json(envelope_json)

    tenant_id = _registered_tenant_id(session, envelope.source_stream)
    if tenant_id is None:
        raise UnregisteredStreamError(
            f"source_stream {envelope.source_stream!r} has no registered tenant -- refusing to guess one"
        )
    set_tenant_scope(session, tenant_id)

    existing = session.get(InboxEvent, envelope.event_id)
    if existing is not None:
        if existing.payload_hash != envelope.payload_hash:
            raise EventIntegrityError(
                f"event {envelope.event_id!r} redelivered with a different payload_hash "
                "-- this is an integrity incident, never resolved by last-write-wins"
            )
        return existing

    inbox_event = InboxEvent(
        event_id=envelope.event_id,
        tenant_id=tenant_id,
        event_type=envelope.event_type.value,
        source_stream=envelope.source_stream,
        export_sequence=envelope.export_sequence,
        envelope_json=envelope_json,
        payload_hash=envelope.payload_hash,
    )
    session.add(inbox_event)
    session.flush()

    if envelope.event_type == EventType.EXECUTION_APPLIED:
        payload = ExecutionAppliedPayload.model_validate(envelope.payload)
        entry = append_entry(
            session,
            tenant_id=tenant_id,
            book=Book.PLATFORM,
            instrument=payload.instrument.instrument_id,
            side=_SIDE_BY_PAYLOAD_VALUE[payload.side],
            quantity=payload.filled_quantity,
            price=payload.filled_price,
            currency=payload.instrument.currency,
            multiplier=payload.instrument.multiplier,
            fee=payload.fee,
            event_time=envelope.event_time,
            source_authority=f"signal-copier-relay:{envelope.producer_id}",
            evidence_class=envelope.evidence_class,
        )
        inbox_event.ledger_entry_id = entry.entry_id
        inbox_event.applied_at = datetime.now(timezone.utc)
    elif envelope.event_type == EventType.SOURCE_RECEIPT:
        # S7: "SOURCE records what was recommended; it does not claim an
        # execution" -- no ledger projection. Applied immediately since
        # there is nothing further this event type could apply.
        inbox_event.applied_at = datetime.now(timezone.utc)
    # Every other EventType has no implemented payload yet (see
    # signal_platform_contracts's own IMPLEMENTED_EVENT_TYPES) -- the row
    # is stored (received) honestly, but stays un-applied rather than
    # fabricating a projection for a payload shape this build doesn't
    # understand yet.

    session.flush()
    return inbox_event
