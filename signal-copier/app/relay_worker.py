"""The private half of the restricted relay
(INTEGRATION_DECISION.md S4.3): reads app/db.py's own export outbox
(`list_undelivered_export_events`) and posts batches of exact,
unmodified envelope bytes to the ONE allowlisted commercial ingress URL
(`config.RELAY_INGRESS_URL`) -- never a generic proxy, never a
caller-supplied endpoint.

"It has no order-submission credentials, no public access and no
generic network proxy" (INTEGRATION_DECISION.md S4.3): this module
imports no broker adapter, opens no listening socket, and its only
outbound capability is one POST to one fixed URL. A stolen relay
secret cannot submit, cancel or modify an order -- it can only forward
already-committed, already-immutable export events.

`run_once` takes an injectable `http_post` callable so this can be
tested against a documented request/response shape with no live
network call, the same way every other adapter in this build (ccxt,
Alpaca, SignalStack) was built and tested before real credentials
existed for it.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import dataclass

import httpx
import structlog

from app import config
from app.db import SignalStore

logger = structlog.get_logger(__name__)


class RelayNotConfiguredError(Exception):
    pass


@dataclass(frozen=True)
class RelayIngestResult:
    delivered_event_ids: list[str]
    unregistered_stream_event_ids: list[str]
    integrity_error_event_ids: list[str]
    #: Track 42: a `"parked"` result this poll classified as TRANSIENT
    #: (see `classify_parked_reason`) -- stays in the outbox, undelivered,
    #: to be polled again next cycle.
    transiently_parked_event_ids: list[str]
    #: Track 42: a `"parked"` result this poll classified as STRUCTURAL
    #: -- `store.mark_export_events_terminally_parked` was already called
    #: for these, so they are EXCLUDED from future polls.
    terminally_parked_event_ids: list[str]


#: Track 42 (closing the honesty gap Track 40 flagged in
#: docs/KNOWN_ISSUES.md, now that signal-portfolio-commercial's own
#: `POST /internal/relay/ingest-batch` reports a real, distinct
#: `"parked"` status with its own `parked_reason` instead of
#: mislabeling every non-error outcome `"applied"`): the real
#: transient-vs-structural split over every named `PARKED_REASON_*`
#: constant that commercial's app/services/integration_inbox.py can set
#: (plus the one, deliberately unnamed, sequence-gap case, reported
#: here as `"sequence_gap_awaiting_predecessor"` -- see relay_routes.py's
#: own comment on why it's never left as a bare `None`).
#:
#: TRANSIENT means: this event is durably received and stored on the
#: commercial side already (idempotent redelivery would be a no-op
#: there either way), and the park resolves itself -- with NO code
#: change on either side -- once some OTHER, correlated event arrives
#: and the commercial inbox's own cascade (`_apply_and_cascade`/
#: `_activate_snapshot_and_reconcile`) walks forward through it. Kept in
#: this producer's own outbox and polled again next cycle: harmless
#: (the commercial side's own dedup absorbs the redundant resend), and
#: consistent with how `unregistered_stream`/`integrity_error` were
#: already handled before this track (left undelivered, retried).
#:
#: STRUCTURAL means: no event arriving anywhere, ever, changes this
#: outcome -- only a CODE change on the commercial side (a new schema
#: version accepted, a new event type implemented, a new ledger column
#: added) could. Redelivering it forever would be pure noise forever,
#: never progress -- the dishonest failure mode this track's own
#: instructions warn against as much as silently dropping it. These are
#: marked terminally parked (`store.mark_export_events_terminally_
#: parked`) and excluded from every future poll, but -- critically --
#: NEVER marked `delivered`: that column means "the commercial side
#: reported this as genuinely applied," and a structurally parked event
#: never was.
#:
#: `edit_without_resolvable_target` is the one reason that is BOTH,
#: split by its own suffix (see commercial's own `PARKED_REASON_
#: EDIT_WITHOUT_RESOLVABLE_TARGET` docstring): `missing_original_
#: source_event_id` means the SOURCE adapter never wired the revision
#: chain at all -- a producer-side code bug no amount of waiting fixes
#: (structural). Any other suffix is the unresolved native target key
#: itself -- the referenced ORIGINAL/ADD/REPLY genuinely may not have
#: arrived/applied yet, the same ordinary correlation-wait shape as
#: `fee_target_not_found`/`routing_outcome_target_not_found` (transient).
_STRUCTURAL_PARK_REASON_PREFIXES = (
    "unsupported_schema_version",
    "unimplemented_event_type",
    "source_event_kind_not_ledger_representable",
    "manifest_generation_mismatch",
    "manifest_metadata_mismatch",
    # INT-010: a detected generation rollback or an unreconciled new
    # generation needing bootstrap are never resolved by waiting or
    # redelivery alone -- both require a deliberate, human-reviewed
    # reconciliation step (accept the rollback, or send a real
    # reconciled bootstrap snapshot) that this build's own engine has no
    # automatic code path for (see signal-portfolio-commercial's
    # `_established_generation` docstring). Treated as structural so an
    # operator is alerted rather than this worker silently hammering the
    # same unresolved generation mismatch forever.
    "generation_rollback_detected",
    "new_generation_requires_bootstrap",
)

_TRANSIENT_PARK_REASON_PREFIXES = (
    "fee_target_not_found",
    "routing_outcome_target_not_found",
    "sequence_gap_awaiting_predecessor",
)

_EDIT_WITHOUT_RESOLVABLE_TARGET_PREFIX = "edit_without_resolvable_target"
_EDIT_MISSING_ORIGINAL_ID_SUFFIX = "missing_original_source_event_id"


def classify_parked_reason(parked_reason: str) -> str:
    """`"structural"` or `"transient"` -- see the module-level comment
    above for the full taxonomy this mirrors. Never guesses: a reason
    this function doesn't recognize (e.g. a new `PARKED_REASON_*` a
    future commercial-side change adds, that this worker hasn't been
    taught about yet) is treated as `"structural"` -- the safe failure
    mode per this track's own instructions ("never guess a behavior
    that could leave money-relevant events in an infinite loop or a
    silent black hole"): an unrecognized reason is surfaced for human
    attention and stops being resent, rather than silently retried
    forever on an assumption nobody verified."""
    if parked_reason.startswith(f"{_EDIT_WITHOUT_RESOLVABLE_TARGET_PREFIX}:"):
        suffix = parked_reason[len(_EDIT_WITHOUT_RESOLVABLE_TARGET_PREFIX) + 1 :]
        return "structural" if suffix == _EDIT_MISSING_ORIGINAL_ID_SUFFIX else "transient"
    for prefix in _TRANSIENT_PARK_REASON_PREFIXES:
        if parked_reason == prefix or parked_reason.startswith(f"{prefix}:"):
            return "transient"
    for prefix in _STRUCTURAL_PARK_REASON_PREFIXES:
        if parked_reason == prefix or parked_reason.startswith(f"{prefix}:"):
            return "structural"
    logger.error("relay_parked_reason_unrecognized", parked_reason=parked_reason)
    return "structural"


def sign_relay_payload(payload: bytes, secret: str, *, timestamp: int | None = None) -> str:
    """The exact "t=<unix ts>,v1=<hex hmac>" scheme
    signal-portfolio-commercial's own app/services/relay_auth.py verifies
    -- deliberately a separate, un-shared implementation on each side of
    the trust boundary (see that module's own docstring for why)."""
    ts = timestamp if timestamp is not None else int(time.time())
    signed_payload = f"{ts}.".encode() + payload
    signature = hmac.new(secret.encode(), signed_payload, hashlib.sha256).hexdigest()
    return f"t={ts},v1={signature}"


def build_batch_body(envelope_jsons: list[str]) -> bytes:
    """`envelope_jsons` are the exact strings
    `list_undelivered_export_events` reconstructed -- this function
    never re-derives or reinterprets their contents, only wraps them in
    the batch envelope the commercial ingress expects."""
    return json.dumps({"events": envelope_jsons}).encode()


def run_once(
    store: SignalStore,
    *,
    ingress_url: str | None = None,
    signing_secret: str | None = None,
    batch_size: int | None = None,
    http_post=None,
) -> RelayIngestResult:
    """One poll-and-forward cycle. Raises `RelayNotConfiguredError` if no
    ingress URL is set -- refuses to run rather than silently doing
    nothing forever, so a missing config is a loud startup failure, not
    a quiet no-op poll loop. Marks only the event_ids the commercial
    inbox actually reports as genuinely `applied` as delivered (a
    redelivery of an already-ingested, already-applied event is
    idempotent there, so it is always safe to mark delivered here too)
    -- an `unregistered_stream`/`integrity_error` result is left
    UNdelivered, so the next poll retries it rather than silently
    dropping it.

    Track 42: a `"parked"` result (the commercial side received and
    durably stored the event, but applied no ledger projection for it)
    is never treated as delivered -- that would repeat the exact
    dishonesty this track closed on the commercial side, just one hop
    later. See `classify_parked_reason` for what happens to it instead."""
    url = ingress_url if ingress_url is not None else config.RELAY_INGRESS_URL
    secret = signing_secret if signing_secret is not None else config.RELAY_SIGNING_SECRET
    limit = batch_size if batch_size is not None else config.RELAY_BATCH_SIZE
    #: Resolved here, not as a `http_post=httpx.post` default parameter
    #: value -- a default is bound once at function-definition time, so
    #: `monkeypatch.setattr("app.relay_worker.httpx.post", ...)` (the
    #: normal way a test patches an already-imported module attribute)
    #: would silently have no effect on it.
    post = http_post if http_post is not None else httpx.post
    if not url:
        raise RelayNotConfiguredError("RELAY_INGRESS_URL is not set -- refusing to guess a destination")

    envelopes = store.list_undelivered_export_events(limit=limit)
    if not envelopes:
        return RelayIngestResult(
            delivered_event_ids=[],
            unregistered_stream_event_ids=[],
            integrity_error_event_ids=[],
            transiently_parked_event_ids=[],
            terminally_parked_event_ids=[],
        )

    envelope_jsons = [envelope.model_dump_json() for envelope in envelopes]
    body = build_batch_body(envelope_jsons)
    signature = sign_relay_payload(body, secret)

    response = post(
        url,
        content=body,
        headers={"x-relay-signature": signature, "content-type": "application/json"},
    )
    response.raise_for_status()
    results = response.json()["results"]

    delivered: list[str] = []
    unregistered: list[str] = []
    integrity_errors: list[str] = []
    transiently_parked: list[str] = []
    terminally_parked: list[tuple[str, str]] = []
    for envelope, result in zip(envelopes, results, strict=True):
        status = result.get("status")
        if status == "applied":
            delivered.append(envelope.event_id)
        elif status == "unregistered_stream":
            unregistered.append(envelope.event_id)
            logger.warning("relay_unregistered_stream", event_id=envelope.event_id, source_stream=envelope.source_stream)
        elif status == "integrity_error":
            integrity_errors.append(envelope.event_id)
            logger.error("relay_integrity_error", event_id=envelope.event_id, detail=result.get("detail"))
        elif status == "parked":
            # Track 42: a genuinely parked event -- received and durably
            # stored on the commercial side, but with NO ledger
            # projection applied. See `classify_parked_reason`'s own
            # module-level comment for the full transient-vs-structural
            # taxonomy this branches on.
            parked_reason = result.get("parked_reason") or "unknown"
            if classify_parked_reason(parked_reason) == "transient":
                transiently_parked.append(envelope.event_id)
                logger.warning(
                    "relay_event_parked_transient",
                    event_id=envelope.event_id,
                    source_stream=envelope.source_stream,
                    parked_reason=parked_reason,
                )
            else:
                terminally_parked.append((envelope.event_id, parked_reason))
                logger.error(
                    "relay_event_parked_structural",
                    event_id=envelope.event_id,
                    source_stream=envelope.source_stream,
                    parked_reason=parked_reason,
                )
        # Any other/unrecognized status (e.g. `malformed_envelope`,
        # `sequence_slot_already_consumed`) is deliberately left
        # undelivered and unparked -- same "never guessed, retried next
        # poll" posture `unregistered_stream`/`integrity_error` already
        # had before this track.

    store.mark_export_events_delivered(delivered)
    store.mark_export_events_terminally_parked(terminally_parked)
    return RelayIngestResult(
        delivered_event_ids=delivered,
        unregistered_stream_event_ids=unregistered,
        integrity_error_event_ids=integrity_errors,
        transiently_parked_event_ids=transiently_parked,
        terminally_parked_event_ids=[event_id for event_id, _reason in terminally_parked],
    )
