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

Ordering and gap detection (INTEGRATION_ACCEPTANCE_CASES.json INT-006,
INTEGRATION_DECISION.md S6's own "Keep separate received, validated and
applied high-water marks... An incompatible schema, unknown identity or
out-of-order dependency parks the stream at the relevant applied
boundary"): every envelope is always RECEIVED and durably stored the
moment it arrives (`InboxEvent.received_at`), regardless of order. It
is only APPLIED (a real ledger projection, `applied_at` set) once its
own `export_sequence` is next in line for that `source_stream` --
`_next_expected_sequence` computes that boundary from the highest
already-APPLIED sequence, never from what's merely been received. An
event that arrives ahead of its predecessor is parked (received, not
applied) rather than silently skipped or applied out of order;
`_apply_and_cascade` re-checks, after applying each event, whether the
next expected sequence is now sitting in the table already received
and unapplied, and keeps applying forward until it hits a genuine gap
or runs out of received events -- so 42-then-41 still ends with both
42 and 41 correctly applied, in the right order, without needing 42
redelivered a second time.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session
from signal_platform_contracts import CONTRACT_SCHEMA_VERSION, EventEnvelope, EventType, ExecutionAppliedPayload

from app.db import set_tenant_scope
from app.models.integration_inbox import ExportStreamRegistration, InboxEvent
from app.models.ledger import Book, Side
from app.services.ledger import append_entry

_SIDE_BY_PAYLOAD_VALUE = {"buy": Side.BUY, "sell": Side.SELL}

#: Every `schema_version` this build can honestly interpret. Exact-match
#: only, deliberately -- no semver "compatible minor bump" guessing,
#: since INT-007's own prohibited outcome is "best-effort financial
#: coercion" of a payload shape this build was never verified against.
#: A future version-negotiation slice (this case's own failure_remedy)
#: can widen this once it has a real migration path to test against.
_SUPPORTED_SCHEMA_VERSIONS = frozenset({CONTRACT_SCHEMA_VERSION})

#: Prefixes `InboxEvent.parked_reason` is ever set to -- see that
#: column's own docstring.
PARKED_REASON_UNSUPPORTED_SCHEMA_VERSION = "unsupported_schema_version"
PARKED_REASON_UNIMPLEMENTED_EVENT_TYPE = "unimplemented_event_type"


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


def _next_expected_sequence(session: Session, *, tenant_id: str, source_stream: str) -> int:
    """One past the highest already-APPLIED export_sequence for this
    stream -- 0 if nothing has been applied yet. Deliberately keyed off
    `applied_at IS NOT NULL`, never off what's merely been received:
    a received-but-parked event must never advance this boundary, or a
    real gap would be invisible."""
    highest_applied = session.scalar(
        select(func.max(InboxEvent.export_sequence)).where(
            InboxEvent.tenant_id == tenant_id,
            InboxEvent.source_stream == source_stream,
            InboxEvent.applied_at.is_not(None),
        )
    )
    return 0 if highest_applied is None else highest_applied + 1


def _apply_projection(session: Session, inbox_event: InboxEvent, envelope: EventEnvelope, *, tenant_id: str) -> None:
    """Mutates `inbox_event` in place: applies its real projection (if
    any) and sets `applied_at`. Never called for an event whose sequence
    isn't next in line -- see `_next_expected_sequence`.

    Checked BEFORE dispatching on `event_type`: an envelope whose
    `schema_version` this build has never been verified against is
    parked exactly like an unimplemented event type (row received and
    durably stored, `applied_at` left NULL, `parked_reason` set) --
    INT-007's own "Unknown event is parked without economic
    application", never "best-effort financial coercion" by assuming an
    unfamiliar version parses the same as one this build actually knows."""
    if envelope.schema_version not in _SUPPORTED_SCHEMA_VERSIONS:
        inbox_event.parked_reason = f"{PARKED_REASON_UNSUPPORTED_SCHEMA_VERSION}:{envelope.schema_version}"
        return

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
    else:
        # Every other EventType has no implemented payload yet (see
        # signal_platform_contracts's own IMPLEMENTED_EVENT_TYPES) -- the
        # row is stored (received) honestly, but stays un-applied rather
        # than fabricating a projection for a payload shape this build
        # doesn't understand yet. Note this means such an event can
        # never advance `_next_expected_sequence` either -- an
        # unimplemented event type permanently parks every later
        # sequence on its own stream until a future build adds real
        # support for it, which is the honest consequence of "keep
        # separate received/applied cursors", not a bug.
        inbox_event.parked_reason = f"{PARKED_REASON_UNIMPLEMENTED_EVENT_TYPE}:{envelope.event_type.value}"


def _apply_and_cascade(
    session: Session, inbox_event: InboxEvent, envelope: EventEnvelope, *, tenant_id: str
) -> None:
    """Applies `inbox_event` (already confirmed next-in-line by the
    caller), then keeps walking forward: if the event now sitting at the
    new expected sequence was already received earlier (parked behind
    this one), apply it too, and repeat -- so redelivering the ONE
    missing sequence number unblocks every later event already sitting
    in the table, without needing any of them redelivered again.

    `_apply_projection` does not always actually apply: a schema-
    incompatible or unimplemented-event-type row stays `applied_at IS
    NULL` on purpose (see that function's own docstring). Since
    `_next_expected_sequence` is keyed off `applied_at`, such a row would
    keep matching itself as "the next parked event" forever if the loop
    didn't stop -- checked explicitly after every `_apply_projection`
    call, first-event-in-this-call included, never assumed from a
    changed `expected`."""
    _apply_projection(session, inbox_event, envelope, tenant_id=tenant_id)
    session.flush()
    if inbox_event.applied_at is None:
        return

    while True:
        expected = _next_expected_sequence(session, tenant_id=tenant_id, source_stream=envelope.source_stream)
        next_parked = session.scalars(
            select(InboxEvent).where(
                InboxEvent.tenant_id == tenant_id,
                InboxEvent.source_stream == envelope.source_stream,
                InboxEvent.export_sequence == expected,
                InboxEvent.applied_at.is_(None),
            )
        ).first()
        if next_parked is None:
            return
        next_envelope = EventEnvelope.model_validate_json(next_parked.envelope_json)
        _apply_projection(session, next_parked, next_envelope, tenant_id=tenant_id)
        session.flush()
        if next_parked.applied_at is None:
            return


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

    expected = _next_expected_sequence(session, tenant_id=tenant_id, source_stream=envelope.source_stream)
    if envelope.export_sequence <= expected:
        # Next in line (or, rarer, an anomalously low sequence a correct
        # producer would never send -- applying it directly can't create
        # a gap either way). Apply now, then walk forward through
        # whatever was already parked waiting for exactly this sequence.
        _apply_and_cascade(session, inbox_event, envelope, tenant_id=tenant_id)
    # else: export_sequence > expected -- a genuine gap. Received and
    # durably stored (inbox_event is already committed-on-flush above),
    # but deliberately left unapplied: `_next_expected_sequence` stays
    # at its current value until the missing predecessor(s) arrive.

    return inbox_event
