"""Signature verification for the restricted relay ingress
(app/api/relay_routes.py), per Signal Platform Integration Correction
Pack's own INTEGRATION_DECISION.md S4: "Use the existing qualified
authentication mechanism when available. Otherwise use a maintained
mTLS or audience-bound signed-service-token implementation, with
expiry, key rotation and replay protection."

No mTLS PKI exists in this environment to issue and rotate real
certificates against, so this implements the signed-service-token half
of that instruction: the exact HMAC-SHA256-over-"{timestamp}.{body}"
scheme app/services/stripe_webhook.py already uses for the same
purpose, deliberately NOT shared code with it (a relay credential
compromise must never be reachable through, or confused with, the
billing webhook's own secret and vice versa -- INTEGRATION_DECISION.md
S11's own "A stolen telemetry credential cannot become a trading
credential").

Honest limitation: the timestamp-tolerance window is real replay
protection for a captured request replayed LATER (outside the
window), but a request replayed WITHIN the window is not rejected by
this module alone -- app/services/integration_inbox.py's own
idempotent ingest (same event_id + same payload_hash is a no-op) is
what makes an in-window replay harmless rather than a duplicate fill.

## Key rotation

`verify_relay_signature` accepts a signature against either of two
configured secrets: `config.RELAY_SIGNING_SECRET` (CURRENT, tried
first) and the optional `config.RELAY_SIGNING_SECRET_PREVIOUS`
(PREVIOUS, tried only if CURRENT does not match). Both comparisons use
`hmac.compare_digest`, so accepting PREVIOUS is not a timing
side-channel on CURRENT, and a signature that matches neither
configured secret is always rejected -- there is no "rotation mode"
bypass. An operator rotates the real secret in two zero-downtime
steps:

1. Set `RELAY_SIGNING_SECRET_PREVIOUS` to the current (soon-to-be-old)
   secret value, then set `RELAY_SIGNING_SECRET` to the new value, on
   THIS (receiving) service. Update signal-copier's own relay worker to
   sign with the new secret. During the overlap, this ingress accepts
   both the new secret (already-updated worker instances) and the old
   one (worker instances that have not yet picked up the change), so
   real traffic never fails verification during the switch.
2. Once every signal-copier deployment is confirmed to be signing with
   the new secret, unset `RELAY_SIGNING_SECRET_PREVIOUS` here. A
   signature made with the old secret is rejected from that point on.

`RELAY_SIGNING_SECRET`/`RELAY_SIGNING_SECRET_PREVIOUS` remain
placeholders for local/test use only; a real deployment sets and
rotates them through a secrets manager, not a repo default.
"""
from __future__ import annotations

import hashlib
import hmac
import time

_DEFAULT_TOLERANCE_SECONDS = 300


class InvalidRelaySignatureHeaderError(Exception):
    pass


class RelaySignatureMismatchError(Exception):
    pass


class StaleRelayTimestampError(Exception):
    pass


def _parse_signature_header(sig_header: str) -> tuple[int, str]:
    """Same "t=<unix ts>,v1=<hex hmac>" shape as
    app/services/stripe_webhook.py's own header -- fail closed on
    anything that doesn't contain both a parseable `t` and a `v1`."""
    parts: dict[str, str] = {}
    for item in sig_header.split(","):
        if "=" not in item:
            continue
        key, _, value = item.partition("=")
        parts[key.strip()] = value.strip()

    if "t" not in parts or "v1" not in parts:
        raise InvalidRelaySignatureHeaderError(f"relay signature header missing t= or v1=: {sig_header!r}")
    try:
        timestamp = int(parts["t"])
    except ValueError as exc:
        raise InvalidRelaySignatureHeaderError(
            f"non-integer timestamp in relay signature header: {parts['t']!r}"
        ) from exc

    return timestamp, parts["v1"]


def sign_relay_payload(payload: bytes, secret: str, *, timestamp: int | None = None) -> str:
    """Builds the `X-Relay-Signature` header value a relay worker sends.
    Used by both the relay worker (signal-copier/app/relay_worker.py)
    and this module's own tests -- never by the ingress itself, which
    only ever verifies."""
    ts = timestamp if timestamp is not None else int(time.time())
    signed_payload = f"{ts}.".encode() + payload
    signature = hmac.new(secret.encode(), signed_payload, hashlib.sha256).hexdigest()
    return f"t={ts},v1={signature}"


def verify_relay_signature(
    payload: bytes,
    sig_header: str,
    secret: str,
    *,
    secret_previous: str | None = None,
    tolerance_seconds: int = _DEFAULT_TOLERANCE_SECONDS,
    now: float | None = None,
) -> None:
    """Raises on any failure -- a malformed header, a signature that
    doesn't match, or a timestamp outside the replay-tolerance window.
    Returns None so a caller cannot accidentally ignore a False result
    and proceed anyway.

    `secret` (CURRENT) is checked first. If it does not match and
    `secret_previous` (PREVIOUS) is a non-empty string, that secret is
    checked too -- this is the whole of the rotation window described
    in this module's own docstring: a signature must still
    cryptographically match one of the two real configured secrets,
    there is no bypass. Both checks use `hmac.compare_digest`."""
    timestamp, provided_signature = _parse_signature_header(sig_header)

    signed_payload = f"{timestamp}.".encode() + payload
    expected_signature = hmac.new(secret.encode(), signed_payload, hashlib.sha256).hexdigest()
    matched = hmac.compare_digest(expected_signature, provided_signature)
    if not matched and secret_previous:
        expected_signature_previous = hmac.new(
            secret_previous.encode(), signed_payload, hashlib.sha256
        ).hexdigest()
        matched = hmac.compare_digest(expected_signature_previous, provided_signature)
    if not matched:
        raise RelaySignatureMismatchError("relay signature does not match the expected value for this payload")

    current_time = now if now is not None else time.time()
    if abs(current_time - timestamp) > tolerance_seconds:
        raise StaleRelayTimestampError(
            f"relay timestamp {timestamp} is outside the {tolerance_seconds}s replay-tolerance window "
            f"(current time {current_time})"
        )
