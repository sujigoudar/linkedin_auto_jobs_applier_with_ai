"""The commercial-side half of the restricted relay
(INTEGRATION_DECISION.md S4.3/S6): the ONLY HTTP route signal-copier's
own relay worker (signal-copier/app/relay_worker.py) ever calls. This
route has no counterpart reachable by a browser session or a customer
API key -- app/api/dependencies.py's `get_relay_db_session` binds it to
`relay_role`, a distinct, restricted Postgres role
(app/db.py's `_apply_relay_role_access`) that cannot read or write
anything outside `export_stream_registrations` (lookup only),
`inbox_events` and `ledger_entries`, and even those only for the one
tenant a request's own envelopes resolve to.

"A stolen telemetry credential cannot become a trading credential"
(INTEGRATION_DECISION.md S11): this route ingests observations only --
it has no code path that submits, cancels or modifies a broker order,
and `RELAY_SIGNING_SECRET` is a distinct secret from every customer-
or owner-facing credential in this service.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.exc import DataError
from sqlalchemy.orm import Session

from app import config
from app.api.dependencies import get_relay_db_session
from app.services.integration_inbox import (
    EventIntegrityError,
    SequenceSlotAlreadyConsumedError,
    UnregisteredStreamError,
    ingest_export_event,
)
from app.services.relay_auth import (
    InvalidRelaySignatureHeaderError,
    RelaySignatureMismatchError,
    StaleRelayTimestampError,
    verify_relay_signature,
)

router = APIRouter()

#: INTEGRATION_DECISION.md S11: "Use a configurable initial one-second
#: outbox poll and 100-event batches for non-live testing." A batch
#: larger than this is refused outright rather than silently
#: truncated -- the relay worker's own job is to respect this bound,
#: not this endpoint's.
_MAX_BATCH_SIZE = 100


class _IngestBatchRequest(BaseModel):
    events: list[str] = Field(min_length=1, max_length=_MAX_BATCH_SIZE)


@router.post("/internal/relay/ingest-batch")
async def ingest_batch(
    request: Request,
    session: Session = Depends(get_relay_db_session),
) -> dict:
    raw_body = await request.body()
    sig_header = request.headers.get("x-relay-signature", "")
    try:
        verify_relay_signature(
            raw_body,
            sig_header,
            config.RELAY_SIGNING_SECRET,
            secret_previous=config.RELAY_SIGNING_SECRET_PREVIOUS or None,
        )
    except (InvalidRelaySignatureHeaderError, RelaySignatureMismatchError, StaleRelayTimestampError) as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    try:
        batch = _IngestBatchRequest.model_validate_json(raw_body)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    results = []
    for envelope_json in batch.events:
        try:
            inbox_event = ingest_export_event(session, envelope_json)
        except UnregisteredStreamError as exc:
            session.rollback()
            results.append({"status": "unregistered_stream", "detail": str(exc)})
            continue
        except EventIntegrityError as exc:
            session.rollback()
            results.append({"status": "integrity_error", "detail": str(exc)})
            continue
        except SequenceSlotAlreadyConsumedError as exc:
            session.rollback()
            results.append({"status": "sequence_slot_already_consumed", "detail": str(exc)})
            continue
        except ValidationError as exc:
            # Track 40 (fault-injection fuzzing): `ingest_export_event`
            # itself validates `envelope_json` (`EventEnvelope.model_
            # validate_json`, its own very first line) and, deeper in,
            # `_apply_projection` validates the per-event-type payload
            # (e.g. `ExecutionAppliedPayload.model_validate`) -- NEITHER
            # was ever wrapped, unlike `_IngestBatchRequest.model_
            # validate_json(raw_body)` just above, which already is. A
            # single malformed event string anywhere in the batch (an
            # extra/wrong-typed payload field, or JSON nested deep enough
            # to trip pydantic-core's own internal recursion guard) used
            # to raise straight out of this loop as an unhandled 500 --
            # crashing the WHOLE request/batch, including every other,
            # perfectly valid event already applied-and-committed earlier
            # in the same loop. Same shape as the three sibling except
            # blocks above: rollback (a no-op if nothing was flushed yet,
            # a clean discard of this one event's uncommitted work if
            # something was -- see `ingest_export_event`'s own comment on
            # why it never rolls back itself), report a clean per-event
            # result, and keep processing the rest of the batch.
            session.rollback()
            results.append({"status": "malformed_envelope", "detail": str(exc)})
            continue
        except DataError as exc:
            # Track 40 (fault-injection fuzzing): a well-formed-JSON,
            # well-typed envelope whose string field nonetheless carries
            # a byte Postgres `text`/`varchar` columns reject outright
            # (an embedded NUL byte -- `psycopg.DataError: PostgreSQL
            # text fields cannot contain NUL (0x00) bytes` -- surfaces
            # here, not as a pydantic ValidationError, since nothing at
            # the Python/pydantic layer rejects a NUL byte in a plain
            # `str` field). Raised from `append_entry`'s own INSERT
            # (app/services/ledger.py), reached via `ingest_export_event`
            # -> `_apply_and_cascade` -> `_apply_projection`'s real
            # `session.flush()` -- same "one malformed event must not
            # crash the whole batch" contract as the `ValidationError`
            # block above, and the same safe rollback (a flush is never
            # committed, so there is nothing durable to undo beyond
            # discarding it).
            session.rollback()
            results.append({"status": "malformed_envelope", "detail": str(exc)})
            continue
        #: Read before `commit()`, not after: `set_tenant_scope` uses
        #: `set_config(..., is_local=true)` (app/db.py's own docstring on
        #: why -- never leak scope to a reused pooled connection), which
        #: resets at commit. A post-commit attribute access would trigger
        #: SQLAlchemy's default expire-on-commit refresh, re-running the
        #: SELECT with NO tenant scope set -- fail-closed RLS then makes
        #: that refresh look like the row was deleted.
        #:
        #: Track 42 (closing the honesty gap Track 40 flagged, never
        #: fixed, in docs/KNOWN_ISSUES.md): this used to unconditionally
        #: report `"applied"` here for every event that reached this
        #: line without raising one of the named exceptions above --
        #: including a genuinely PARKED one (`inbox_event.applied_at`
        #: still `None`: a sequence gap, an unsupported schema version,
        #: an unimplemented event type, or any other
        #: `PARKED_REASON_*` from app/services/integration_inbox.py).
        #: `applied_at` is read HERE, before `commit()`, for the exact
        #: same expire-on-commit reason the comment above already
        #: explains -- `inbox_event` is the SAME ORM object
        #: `ingest_export_event` returned, already reflecting whatever
        #: `_apply_projection`/`_apply_and_cascade` decided, so no extra
        #: query is needed to know which case this is.
        applied_event_id = inbox_event.event_id
        really_applied = inbox_event.applied_at is not None
        #: A sequence-gap park carries NO named `parked_reason` of its
        #: own (see app/services/integration_inbox.py's own
        #: `_next_expected_sequence` docstring and
        #: app/services/source_coverage.py's own module docstring for
        #: why -- it is just as genuinely PARKED as a named reason, but
        #: the cause is "waiting on a lower export_sequence", not
        #: anything this row's own processing discovered). Reported
        #: with an explicit, honest reason string instead of `None` --
        #: a caller (signal-copier's own relay_worker.py) that keys off
        #: `parked_reason` should never have to special-case "no reason
        #: given" as a THIRD, undocumented meaning.
        park_reason: str = (
            inbox_event.parked_reason or "sequence_gap_awaiting_predecessor" if not really_applied else ""
        )
        session.commit()
        if really_applied:
            results.append({"status": "applied", "event_id": applied_event_id})
        else:
            results.append(
                {"status": "parked", "event_id": applied_event_id, "parked_reason": park_reason}
            )

    return {"results": results}
