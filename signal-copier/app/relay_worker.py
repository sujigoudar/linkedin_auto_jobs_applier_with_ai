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
    inbox actually reports as `applied` or `duplicate`-equivalent
    (a redelivery of an already-ingested event is idempotent there, so
    it is always safe to mark delivered here too) -- an
    `unregistered_stream` or `integrity_error` result is left
    UNdelivered, so the next poll retries it rather than silently
    dropping it."""
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
        return RelayIngestResult(delivered_event_ids=[], unregistered_stream_event_ids=[], integrity_error_event_ids=[])

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

    store.mark_export_events_delivered(delivered)
    return RelayIngestResult(
        delivered_event_ids=delivered,
        unregistered_stream_event_ids=unregistered,
        integrity_error_event_ids=integrity_errors,
    )
