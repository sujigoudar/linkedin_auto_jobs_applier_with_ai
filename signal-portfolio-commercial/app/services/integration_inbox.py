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
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from signal_platform_contracts import (
    CONTRACT_SCHEMA_VERSION,
    EventEnvelope,
    EventType,
    ExecutionAppliedPayload,
    FeePayload,
    PositionSnapshotPayload,
    RoutingAdmissionOutcomePayload,
    SourceReceiptPayload,
)

from app.db import set_tenant_scope
from app.models.integration_inbox import ExportStreamRegistration, InboxEvent
from app.models.ledger import Book, LedgerEntry, Side
from app.services.ledger import append_correction, append_entry
from app.services.sleeve_mapping import resolve_sleeve_for_source

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
PARKED_REASON_FEE_TARGET_NOT_FOUND = "fee_target_not_found"
PARKED_REASON_ROUTING_OUTCOME_TARGET_NOT_FOUND = "routing_outcome_target_not_found"
PARKED_REASON_GENERATION_ROLLBACK_DETECTED = "generation_rollback_detected"
PARKED_REASON_NEW_GENERATION_REQUIRES_BOOTSTRAP = "new_generation_requires_bootstrap"
PARKED_REASON_MANIFEST_METADATA_MISMATCH = "manifest_metadata_mismatch"
PARKED_REASON_MANIFEST_GENERATION_MISMATCH = "manifest_generation_mismatch"


def _execution_correlation_key(*, broker: str, broker_order_id: str) -> str:
    """The one identity `ExecutionAppliedPayload` and `FeePayload` both
    carry -- see `InboxEvent.execution_correlation_key`'s own
    docstring."""
    return f"{broker}|{broker_order_id}"


class StreamAlreadyRegisteredToAnotherTenantError(Exception):
    pass


class UnregisteredStreamError(Exception):
    pass


class EventIntegrityError(Exception):
    pass


class SequenceSlotAlreadyConsumedError(Exception):
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


def _next_expected_sequence(session: Session, *, tenant_id: str, source_stream: str, producer_generation: int) -> int:
    """One past the highest already-APPLIED export_sequence for this
    stream WITHIN `producer_generation` -- 0 if nothing has been applied
    yet for that generation. Deliberately keyed off `applied_at IS NOT
    NULL`, never off what's merely been received: a received-but-parked
    event must never advance this boundary, or a real gap would be
    invisible. Scoped to `producer_generation` because `export_sequence`
    is only ever monotonic WITHIN one generation of a stream
    (`signal_platform_contracts.EventEnvelope.producer_generation`'s own
    docstring) -- a new generation's own sequence 0 is not "behind" the
    previous generation's sequence 100, it is a different numbering
    entirely.

    Also floored at `_snapshot_floor(...) + 1` when an ACTIVATED bootstrap
    snapshot exists for this (stream, generation) -- INT-008 "Snapshot
    and delta overlap": once a snapshot with `cutoff_sequence=100` is
    active, the next expected sequence is never behind 101, regardless
    of which individual sequences <= 100 were or weren't ever applied
    the ordinary, one-event-at-a-time way."""
    highest_applied = session.scalar(
        select(func.max(InboxEvent.export_sequence)).where(
            InboxEvent.tenant_id == tenant_id,
            InboxEvent.source_stream == source_stream,
            InboxEvent.producer_generation == producer_generation,
            InboxEvent.applied_at.is_not(None),
        )
    )
    next_from_applied = 0 if highest_applied is None else highest_applied + 1
    floor = _snapshot_floor(
        session, tenant_id=tenant_id, source_stream=source_stream, producer_generation=producer_generation,
    )
    return next_from_applied if floor is None else max(next_from_applied, floor + 1)


def _established_generation(session: Session, *, tenant_id: str, source_stream: str) -> int | None:
    """The `producer_generation` of this stream's own already-APPLIED
    history -- `None` if nothing has ever been applied yet, in which
    case ANY generation is acceptable as the first one, establishing the
    baseline. Once established, every APPLIED row on this stream shares
    the identical generation by construction (a mismatched generation is
    always parked, never applied -- see `ingest_export_event`), so any
    one of them answers this.

    INTEGRATION_ACCEPTANCE_CASES.json INT-010 "Producer restored to
    older database": a producer whose own storage was restored to an
    older backup and resumes emitting is, per the contract's own
    `producer_generation` docstring ("Bumped when a stream is
    re-bootstrapped from a fresh snapshot"), expected to bump its own
    generation on any such discontinuity. This function is how a
    receiver enforces that a stream's own established generation, once
    set, cannot silently drift: an envelope claiming an OLDER generation
    than what's already established is a detected rollback ("Old
    economic IDs do not apply again" -- INT-010's own expected outcome);
    a NEWER one is a legitimate-looking but unverified re-bootstrap that
    "requires reconciled bootstrap" (INT-010's own words) this build
    does not have (INT-008/INT-009, explicitly out of scope) -- neither
    is ever silently applied; both park with a distinct, honest reason."""
    return session.scalar(
        select(InboxEvent.producer_generation)
        .where(
            InboxEvent.tenant_id == tenant_id,
            InboxEvent.source_stream == source_stream,
            InboxEvent.applied_at.is_not(None),
        )
        .limit(1)
    )


def _snapshot_floor(session: Session, *, tenant_id: str, source_stream: str, producer_generation: int) -> int | None:
    """The `cutoff_sequence` of the most recently ACTIVATED bootstrap
    snapshot for (stream, generation) -- `None` if none has ever
    activated. "Activated" means every page of that manifest has
    `applied_at` set (see `_activate_snapshot_and_reconcile`); an
    in-progress, incomplete manifest never contributes a floor (INT-009's
    own "Partial snapshot labeled complete", prohibited).

    INTEGRATION_ACCEPTANCE_CASES.json INT-008 "Snapshot and delta
    overlap": once a floor is set, `_next_expected_sequence` treats
    `floor + 1` as a hard minimum (never behind it, regardless of what
    was or wasn't applied below it the ordinary way), and
    `_apply_projection` treats any NON-snapshot envelope at or below the
    floor as already reflected in the snapshot's own positions --
    applied, but producing no second ledger entry ("Position snapshot is
    not an extra execution", and no double count of 1..cutoff)."""
    return session.scalar(
        select(func.max(InboxEvent.snapshot_cutoff_sequence)).where(
            InboxEvent.tenant_id == tenant_id,
            InboxEvent.source_stream == source_stream,
            InboxEvent.producer_generation == producer_generation,
            InboxEvent.event_type == EventType.POSITION_SNAPSHOT.value,
            InboxEvent.applied_at.is_not(None),
        )
    )


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
    unfamiliar version parses the same as one this build actually knows.

    Never called with a `POSITION_SNAPSHOT` envelope -- a manifest page
    is handled entirely by `_ingest_position_snapshot_page`, outside
    this function and outside the ordinary sequence gate (see
    `ingest_export_event`'s own comment for why). Every OTHER event type
    still funnels through here, gated by `_next_expected_sequence`."""
    if envelope.schema_version not in _SUPPORTED_SCHEMA_VERSIONS:
        inbox_event.parked_reason = f"{PARKED_REASON_UNSUPPORTED_SCHEMA_VERSION}:{envelope.schema_version}"
        return

    floor = _snapshot_floor(
        session, tenant_id=tenant_id, source_stream=envelope.source_stream,
        producer_generation=envelope.producer_generation,
    )
    if floor is not None and envelope.export_sequence <= floor:
        # INT-008 "Snapshot and delta overlap": this economic fact is
        # already reflected in the activated snapshot's own baseline
        # positions -- applying it a second time via its own ledger
        # projection would double-count it. Marked applied (it must
        # still advance past, never park forever) but with NO ledger
        # entry of its own. Clears any stale parked_reason from an
        # earlier, pre-activation attempt (e.g. a FEE that once parked
        # `fee_target_not_found` before its own execution ever arrived)
        # -- once covered, this row is applied, not parked, and must not
        # show both.
        inbox_event.applied_at = datetime.now(timezone.utc)
        inbox_event.parked_reason = None
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
            originating_analyst_id=payload.originating_analyst_id,
        )
        inbox_event.ledger_entry_id = entry.entry_id
        inbox_event.execution_correlation_key = _execution_correlation_key(
            broker=payload.broker, broker_order_id=payload.broker_order_id,
        )
        inbox_event.applied_at = datetime.now(timezone.utc)
    elif envelope.event_type == EventType.SOURCE_RECEIPT:
        # S7: "SOURCE records what was recommended; it does not claim an
        # execution" -- a real Book.SOURCE ledger entry (S12 step 5
        # "Portfolio Lab source feed"), never a Book.PLATFORM/execution
        # one, and ONLY when the recommendation itself carried a real
        # quantity AND price -- LedgerEntry.quantity/price are NOT NULL
        # columns, and a bare "buy AAPL" alert with neither genuinely
        # has nothing to record numerically yet. Such a receipt is still
        # applied (there is nothing further this event type could apply
        # once its ledger projection, if any, is decided) but produces
        # no ledger row -- an honest partial-coverage outcome, not a
        # parked/incomplete one.
        source_payload = SourceReceiptPayload.model_validate(envelope.payload)
        if source_payload.quantity is not None and source_payload.price is not None:
            sleeve = resolve_sleeve_for_source(
                session,
                tenant_id=tenant_id,
                source_provider_id=source_payload.source.source_provider_id,
                analyst_id=source_payload.source.analyst_id,
                parser_version=source_payload.source.parser_version,
            )
            entry = append_entry(
                session,
                tenant_id=tenant_id,
                book=Book.SOURCE,
                instrument=source_payload.instrument.instrument_id,
                side=_SIDE_BY_PAYLOAD_VALUE[source_payload.side],
                quantity=source_payload.quantity,
                price=source_payload.price,
                currency=source_payload.instrument.currency,
                multiplier=source_payload.instrument.multiplier,
                event_time=envelope.event_time,
                source_authority=f"signal-copier-relay:{envelope.producer_id}",
                evidence_class=envelope.evidence_class,
                originating_analyst_id=source_payload.source.analyst_id,
                sleeve_id=sleeve.sleeve_id if sleeve is not None else None,
            )
            inbox_event.ledger_entry_id = entry.entry_id
        inbox_event.applied_at = datetime.now(timezone.utc)
    elif envelope.event_type == EventType.FEE:
        payload = FeePayload.model_validate(envelope.payload)
        correlation_key = _execution_correlation_key(broker=payload.broker, broker_order_id=payload.broker_order_id)
        execution_inbox_event = session.scalars(
            select(InboxEvent).where(
                InboxEvent.tenant_id == tenant_id,
                InboxEvent.execution_correlation_key == correlation_key,
                InboxEvent.ledger_entry_id.is_not(None),
            )
        ).first()
        if execution_inbox_event is None or execution_inbox_event.ledger_entry_id is None:
            # INT-012's own "not yet applicable" case: a fee arrived for
            # an execution this stream hasn't (yet, or ever) applied.
            # Parked, not coerced onto some other entry and not raised
            # as an integrity error -- the execution may simply not have
            # arrived yet (ordinary redelivery/backfill timing), which is
            # a real, honest "waiting", not a fault.
            inbox_event.parked_reason = f"{PARKED_REASON_FEE_TARGET_NOT_FOUND}:{correlation_key}"
            return
        original = session.get(LedgerEntry, execution_inbox_event.ledger_entry_id)
        assert original is not None  # ledger_entry_id only ever points at a real, still-existing row
        correction = append_correction(
            session,
            original_entry_id=original.entry_id,
            quantity=original.quantity,
            price=original.price,
            event_time=envelope.event_time,
            source_authority=f"signal-copier-relay:{envelope.producer_id}",
            fee=payload.fee,
        )
        inbox_event.ledger_entry_id = correction.entry_id
        inbox_event.applied_at = datetime.now(timezone.utc)
    elif envelope.event_type == EventType.ROUTING_ADMISSION_OUTCOME:
        # INT-027 "All permitted source outcomes reach research": the
        # real routing/admission/fill outcome for a SOURCE_RECEIPT
        # already applied earlier -- correlates by
        # `originating_source_event_id`, which IS that receipt's own
        # `InboxEvent.event_id` (the receipt needs no separate
        # correlation-key column the way EXECUTION_APPLIED/FEE do,
        # since its own primary key already serves that role). Mirrors
        # FEE's own late-arriving-correlated-update idiom: if the
        # target receipt hasn't been received/applied yet, park rather
        # than fabricate or drop it -- same honest "waiting" posture as
        # `PARKED_REASON_FEE_TARGET_NOT_FOUND`. In practice this should
        # be unreachable in the ordinary flow: both event types share
        # the SAME source_stream, so this event's own (necessarily
        # higher) export_sequence can never be applied before its own
        # receipt's lower one already has been -- kept as a real,
        # checked defense anyway, not an assumption.
        payload = RoutingAdmissionOutcomePayload.model_validate(envelope.payload)
        source_row = session.get(InboxEvent, payload.originating_source_event_id)
        if source_row is None or source_row.applied_at is None:
            inbox_event.parked_reason = (
                f"{PARKED_REASON_ROUTING_OUTCOME_TARGET_NOT_FOUND}:{payload.originating_source_event_id}"
            )
            return
        source_row.routing_outcome = payload.outcome
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
        expected = _next_expected_sequence(
            session, tenant_id=tenant_id, source_stream=envelope.source_stream,
            producer_generation=envelope.producer_generation,
        )
        next_parked = session.scalars(
            select(InboxEvent).where(
                InboxEvent.tenant_id == tenant_id,
                InboxEvent.source_stream == envelope.source_stream,
                InboxEvent.producer_generation == envelope.producer_generation,
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


def _ingest_position_snapshot_page(
    session: Session, inbox_event: InboxEvent, envelope: EventEnvelope, *, tenant_id: str
) -> None:
    """One page of a bootstrap snapshot manifest (S6 "Snapshot plus
    deltas", INT-008/INT-009). Called directly from `ingest_export_event`
    for EVERY `POSITION_SNAPSHOT` envelope, unconditionally and
    immediately on receipt -- deliberately NEVER gated by
    `_next_expected_sequence`/`_apply_and_cascade` the way every other
    event type is. INT-009's own "Interrupted bootstrap resumes" requires
    a manifest to be detected complete and activated the instant its own
    last missing page arrives, regardless of arrival order -- gating page
    receipt on ordinary sequence order would let an out-of-order page get
    silently gap-parked by the generic mechanism WITHOUT its own manifest
    metadata ever being cached, permanently deadlocking that manifest's
    own completeness check (a real bug this exact design avoids).

    Caches the page's own manifest metadata onto `inbox_event`
    unconditionally, but only actually activates the manifest (real
    ledger baseline entries, every page's own `applied_at` set) once
    every `page_index` in `0..page_count-1` has been received --
    INT-009's own "Partial snapshot labeled complete" is prohibited, so a
    page received before its siblings stays durably received but
    unapplied.

    Validated against every sibling page already received for the SAME
    `manifest_id` before caching anything: a page claiming a different
    `producer_generation` than its siblings is INT-009's own "Attempt to
    mix a page from another generation... rejected"
    (`manifest_generation_mismatch`); one claiming a different
    `page_count`/`cutoff_sequence` than its siblings is a manifest whose
    own metadata disagrees with itself, never silently reconciled
    (`manifest_metadata_mismatch`). Either park this page and leave it
    permanently unapplied -- same "never coerced" posture as every other
    `parked_reason`."""
    if envelope.schema_version not in _SUPPORTED_SCHEMA_VERSIONS:
        inbox_event.parked_reason = f"{PARKED_REASON_UNSUPPORTED_SCHEMA_VERSION}:{envelope.schema_version}"
        return

    payload = PositionSnapshotPayload.model_validate(envelope.payload)

    sibling_pages = session.scalars(
        select(InboxEvent).where(
            InboxEvent.tenant_id == tenant_id,
            InboxEvent.manifest_id == payload.manifest_id,
            InboxEvent.event_id != inbox_event.event_id,
        )
    ).all()
    for sibling in sibling_pages:
        if sibling.producer_generation != envelope.producer_generation:
            inbox_event.parked_reason = f"{PARKED_REASON_MANIFEST_GENERATION_MISMATCH}:{payload.manifest_id}"
            return
        if sibling.snapshot_page_count != payload.page_count or sibling.snapshot_cutoff_sequence != payload.cutoff_sequence:
            inbox_event.parked_reason = f"{PARKED_REASON_MANIFEST_METADATA_MISMATCH}:{payload.manifest_id}"
            return

    inbox_event.manifest_id = payload.manifest_id
    inbox_event.snapshot_page_index = payload.page_index
    inbox_event.snapshot_page_count = payload.page_count
    inbox_event.snapshot_cutoff_sequence = payload.cutoff_sequence
    session.flush()

    received_page_indices = set(
        session.scalars(
            select(InboxEvent.snapshot_page_index).where(
                InboxEvent.tenant_id == tenant_id,
                InboxEvent.manifest_id == payload.manifest_id,
            )
        ).all()
    )
    if len(received_page_indices) < payload.page_count:
        # Awaiting the rest of the manifest -- not an error, not yet
        # applied. Resuming after an interruption (INT-009) is exactly
        # this: whichever pages already arrived stay durably received,
        # and the FIRST later call that completes the set is the one
        # that activates it, regardless of which page happened to be last.
        return

    _activate_snapshot_and_reconcile(
        session,
        tenant_id=tenant_id,
        source_stream=envelope.source_stream,
        producer_generation=envelope.producer_generation,
        manifest_id=payload.manifest_id,
        cutoff_sequence=payload.cutoff_sequence,
        producer_id=envelope.producer_id,
        evidence_class=envelope.evidence_class,
    )


def _activate_snapshot_and_reconcile(
    session: Session,
    *,
    tenant_id: str,
    source_stream: str,
    producer_generation: int,
    manifest_id: str,
    cutoff_sequence: int,
    producer_id: str,
    evidence_class: object,
) -> None:
    """Called exactly once per manifest, the instant its last page
    arrives. Three things, in order, all inside the caller's own
    transaction:

    1. One real `Book.PLATFORM` ledger baseline entry per
       `PositionSnapshotEntry` across every page of the manifest --
       "Position snapshot is not an extra execution", so tagged with its
       own `source_authority` prefix (`signal-copier-snapshot-relay:`,
       never the ordinary `signal-copier-relay:` an EXECUTION_APPLIED
       row gets) and timestamped at the EARLIEST `event_time` among the
       manifest's own pages, since the snapshot as a whole describes one
       single point-in-time state even though its pages were emitted
       (and may have arrived) separately.
    2. Every page's own `applied_at` is set -- this is what makes
       `_snapshot_floor` see this manifest's `cutoff_sequence` as a real
       floor from this point on.
    3. Reconciliation, per INT-008's own "Snapshot and delta overlap":
       any non-snapshot row already received for this exact
       (stream, generation) at or below `cutoff_sequence` -- whether it
       arrived before or after this manifest completed -- is swept
       through `_apply_projection`, which (now that the floor is set)
       marks it applied with NO second ledger entry, never double-
       counting an economic fact the snapshot's own baseline already
       covers. Anything ABOVE `cutoff_sequence` is then cascaded forward
       exactly like `_apply_and_cascade` already does, since a bootstrap
       snapshot activating is exactly the kind of event that can unblock
       a long run of already-received, previously out-of-order deltas.

    Deliberately out of this slice's own bounded scope: a non-snapshot
    row at or below `cutoff_sequence` that was already applied NORMALLY
    (with its own real ledger entry) before this manifest ever activated
    is never revisited or corrected here -- this build does not attempt
    to retroactively remove or net out a ledger entry that already
    existed at the time a later snapshot's own baseline was established.
    """
    pages = session.scalars(
        select(InboxEvent).where(
            InboxEvent.tenant_id == tenant_id,
            InboxEvent.manifest_id == manifest_id,
        )
    ).all()
    envelopes_by_event_id = {page.event_id: EventEnvelope.model_validate_json(page.envelope_json) for page in pages}
    earliest_event_time = min(env.event_time for env in envelopes_by_event_id.values())

    for page in pages:
        page_payload = PositionSnapshotPayload.model_validate(envelopes_by_event_id[page.event_id].payload)
        for position in page_payload.positions:
            append_entry(
                session,
                tenant_id=tenant_id,
                book=Book.PLATFORM,
                instrument=position.instrument.instrument_id,
                side=_SIDE_BY_PAYLOAD_VALUE[position.side],
                quantity=position.quantity,
                price=position.average_cost,
                currency=position.instrument.currency,
                multiplier=position.instrument.multiplier,
                event_time=earliest_event_time,
                source_authority=f"signal-copier-snapshot-relay:{producer_id}",
                evidence_class=evidence_class,
            )

    now = datetime.now(timezone.utc)
    for page in pages:
        page.applied_at = now
    session.flush()

    covered = session.scalars(
        select(InboxEvent).where(
            InboxEvent.tenant_id == tenant_id,
            InboxEvent.source_stream == source_stream,
            InboxEvent.producer_generation == producer_generation,
            InboxEvent.event_type != EventType.POSITION_SNAPSHOT.value,
            InboxEvent.export_sequence <= cutoff_sequence,
            InboxEvent.applied_at.is_(None),
        )
    ).all()
    for row in covered:
        row_envelope = EventEnvelope.model_validate_json(row.envelope_json)
        _apply_projection(session, row, row_envelope, tenant_id=tenant_id)
    session.flush()

    while True:
        expected = _next_expected_sequence(
            session, tenant_id=tenant_id, source_stream=source_stream, producer_generation=producer_generation,
        )
        next_parked = session.scalars(
            select(InboxEvent).where(
                InboxEvent.tenant_id == tenant_id,
                InboxEvent.source_stream == source_stream,
                InboxEvent.producer_generation == producer_generation,
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
        producer_generation=envelope.producer_generation,
        export_sequence=envelope.export_sequence,
        envelope_json=envelope_json,
        payload_hash=envelope.payload_hash,
    )
    session.add(inbox_event)
    try:
        session.flush()
    except IntegrityError as exc:
        # INT-010 "Producer restored to older database": a producer that
        # reuses an export_sequence WITHIN the same producer_generation
        # it already used it in (a fresh event_id claiming a sequence
        # slot the unique constraint below already has a row for) is
        # never silently applied as a new financial fact -- this is
        # exactly "Reset sequence accepted as new financial history",
        # the case's own prohibited outcome, surfaced as a clear,
        # dedicated error instead of a raw DB constraint violation.
        # Never rolled back HERE -- same contract as
        # UnregisteredStreamError/EventIntegrityError above: the caller
        # (e.g. app/api/relay_routes.py's own per-event try/except)
        # decides when to roll back, since it may be mid-batch.
        raise SequenceSlotAlreadyConsumedError(
            f"source_stream {envelope.source_stream!r} generation {envelope.producer_generation} "
            f"export_sequence {envelope.export_sequence} is already occupied by a different event_id "
            "-- this producer_generation/export_sequence pair was already consumed, never reusable"
        ) from exc

    established_generation = _established_generation(session, tenant_id=tenant_id, source_stream=envelope.source_stream)
    if established_generation is not None and envelope.producer_generation != established_generation:
        if envelope.producer_generation < established_generation:
            inbox_event.parked_reason = (
                f"{PARKED_REASON_GENERATION_ROLLBACK_DETECTED}:{envelope.producer_generation}"
            )
        else:
            inbox_event.parked_reason = (
                f"{PARKED_REASON_NEW_GENERATION_REQUIRES_BOOTSTRAP}:{envelope.producer_generation}"
            )
        return inbox_event

    if envelope.event_type == EventType.POSITION_SNAPSHOT:
        # A manifest page is handled entirely OUTSIDE the ordinary
        # `_next_expected_sequence` gate -- see `_ingest_position_snapshot_page`'s
        # own docstring for why: INT-009's own "Interrupted bootstrap
        # resumes" requires a manifest to activate the instant its own
        # last missing page is delivered, regardless of which page index
        # that happens to be or how it relates to any OTHER (non-
        # snapshot) event's sequence position on the same stream. Gating
        # page receipt on ordinary sequence order would let an
        # out-of-order page get gap-parked by the generic mechanism
        # below WITHOUT ever having its own manifest metadata cached --
        # silently deadlocking a manifest that can now never be detected
        # complete.
        _ingest_position_snapshot_page(session, inbox_event, envelope, tenant_id=tenant_id)
        return inbox_event

    expected = _next_expected_sequence(
        session, tenant_id=tenant_id, source_stream=envelope.source_stream,
        producer_generation=envelope.producer_generation,
    )
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
