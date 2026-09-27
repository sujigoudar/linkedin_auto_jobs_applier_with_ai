"""Stripe webhook signature verification and event-idempotency recording,
per spec/docs/08_products_billing_and_entitlements.md: "Verify raw-body
webhook signature, timestamp/replay tolerance... Persist event IDs
before asynchronous processing."

No Stripe SDK call and no real Stripe account exist in this
environment -- there is nothing here that calls stripe.Webhook or any
Stripe API. `verify_signature` reimplements Stripe's own documented,
public signing scheme (HMAC-SHA256 over "{timestamp}.{raw_body}") so it
can be built and tested against synthetic, Stripe-shaped payloads
signed with a local test secret, the same way every other adapter in
this build (Collective2, eToro) was built against a documented request
shape without live transport. Wiring a real
`STRIPE_WEBHOOK_SIGNING_SECRET` and receiving real events is future work
gated on CARD-4 (payment processor approval).
"""
from __future__ import annotations

import hashlib
import hmac
import time

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.webhook_event import ProcessedWebhookEvent

_DEFAULT_TOLERANCE_SECONDS = 300


class InvalidSignatureHeaderError(Exception):
    pass


class SignatureMismatchError(Exception):
    pass


class StaleTimestampError(Exception):
    pass


def _parse_signature_header(sig_header: str) -> tuple[int, str]:
    """Stripe's header shape: "t=<unix ts>,v1=<hex hmac>[,v0=...]" --
    fail closed on anything that doesn't contain both a parseable `t` and
    a `v1`, rather than guessing a default or skipping verification."""
    parts: dict[str, str] = {}
    for item in sig_header.split(","):
        if "=" not in item:
            continue
        key, _, value = item.partition("=")
        parts[key.strip()] = value.strip()

    if "t" not in parts or "v1" not in parts:
        raise InvalidSignatureHeaderError(f"signature header missing t= or v1=: {sig_header!r}")
    try:
        timestamp = int(parts["t"])
    except ValueError as exc:
        raise InvalidSignatureHeaderError(f"non-integer timestamp in signature header: {parts['t']!r}") from exc

    return timestamp, parts["v1"]


def verify_signature(
    payload: bytes,
    sig_header: str,
    secret: str,
    *,
    tolerance_seconds: int = _DEFAULT_TOLERANCE_SECONDS,
    now: float | None = None,
) -> None:
    """Raises on any failure -- a malformed header, a signature that
    doesn't match, or a timestamp outside the replay-tolerance window.
    Returns None (does not return a bool) so a caller cannot accidentally
    ignore a False result and proceed anyway."""
    timestamp, provided_signature = _parse_signature_header(sig_header)

    signed_payload = f"{timestamp}.".encode() + payload
    expected_signature = hmac.new(secret.encode(), signed_payload, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected_signature, provided_signature):
        raise SignatureMismatchError("webhook signature does not match the expected value for this payload")

    current_time = now if now is not None else time.time()
    if abs(current_time - timestamp) > tolerance_seconds:
        raise StaleTimestampError(
            f"webhook timestamp {timestamp} is outside the {tolerance_seconds}s replay-tolerance window "
            f"(current time {current_time})"
        )


def record_event_if_new(session: Session, event_id: str) -> bool:
    """Persist `event_id` and return True if this is the first time it's
    been seen; return False (and persist nothing new) if it has already
    been recorded -- the caller's job is to skip reprocessing on False,
    never to re-derive entitlement effects from a duplicate delivery."""
    existing = session.scalars(
        select(ProcessedWebhookEvent).where(ProcessedWebhookEvent.event_id == event_id)
    ).first()
    if existing is not None:
        return False

    session.add(ProcessedWebhookEvent(event_id=event_id))
    session.flush()
    return True
