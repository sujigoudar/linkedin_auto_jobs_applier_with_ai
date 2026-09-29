"""Signature verification for the ONE public-reachable-through-a-relay
route this service exposes: `POST /catalog/providers/{source}/fit-
simulation` (app/main.py). This is the bounded, service-to-service path
signal-portfolio-commercial's own backend uses to run the Alertsify-
style "what would copying this PUBLISHED provider have done to MY
account" simulator for an anonymous public-catalog visitor, described
in that endpoint's own docstring and in `/providers/{source}/fit-
simulation`'s (the owner-gated original) docstring.

## Why this is a SEPARATE module/secret from app/services -- wait, this
## service has no `relay_auth.py` of its own; the shared-scheme half
## lives in `app/relay_worker.py` (`sign_relay_payload`) --

`CATALOG_FIT_SIM_SIGNING_SECRET` (app/config.py) is a distinct secret
from `RELAY_SIGNING_SECRET`, and this is a distinct verifier from
anything relay-related, for the exact "a stolen credential must not
become a different credential" reason INTEGRATION_DECISION.md S11
already states for the relay secret vs the commercial billing-webhook
secret: a leaked catalog-fit-sim secret must never be usable to forge a
relay export-event batch (which, unlike this endpoint, feeds real
ledger entries), and a leaked relay secret must never be usable to call
this endpoint. Reusing one secret/verifier for both would silently
merge two trust boundaries that this project has consistently kept
separate everywhere else (see also app/auth.py's `RequireOwner`, a
third, completely independent trust boundary this module's token can
NEVER satisfy -- there is no code path anywhere that accepts a valid
catalog-fit-sim signature in place of an owner session/CSRF pair).

## The scheme: the same "t=<unix ts>,v1=<hex hmac>" shape as
## app/relay_worker.py's `sign_relay_payload`, but audience-bound

`signed_payload = f"{ts}.{_AUDIENCE}.".encode() + payload` -- the fixed
audience string `_AUDIENCE` is part of what gets signed, not just
documentation. This means a signature computed for ANY other purpose
with the SAME secret value (e.g. if an operator ever mistakenly reused
this secret for something else) still fails verification here, because
the exact bytes being signed differ. It is not full "one token, one
scoped capability" infrastructure (there's no key-rotation or per-
request audience list, matching this project's own already-documented
relay-auth limitation) -- it is the same audience-bound signed-service-
token shape INTEGRATION_DECISION.md S4 asks for, sized to the one
narrow use this needs.

## Honest limitations (same shape as app/services/relay_auth.py's own
## documented ones on the commercial side)

- Replay protection is timestamp-window only: a request captured and
  replayed WITHIN `tolerance_seconds` is not rejected by this module
  alone. Unlike the relay ingest (idempotent by event_id/payload_hash),
  a replayed fit-simulation call just recomputes the same bounded, read-
  only report again -- wasted work, not a data-integrity or financial
  risk, which is why this module's default tolerance is deliberately
  short (60s, not the relay's 300s) rather than needing a separate
  idempotency layer.

## Key rotation

`verify_catalog_fit_sim_signature` accepts a signature against either
of two configured secrets: `config.CATALOG_FIT_SIM_SIGNING_SECRET`
(CURRENT, tried first) and the optional
`config.CATALOG_FIT_SIM_SIGNING_SECRET_PREVIOUS` (PREVIOUS, tried only
if CURRENT does not match). Both comparisons use
`hmac.compare_digest`, so accepting PREVIOUS is not a timing
side-channel on CURRENT, and a signature that matches neither
configured secret is always rejected -- there is no "rotation mode"
bypass. An operator rotates the real secret in two zero-downtime
steps:

1. Set `CATALOG_FIT_SIM_SIGNING_SECRET_PREVIOUS` to the current
   (soon-to-be-old) secret value, then set
   `CATALOG_FIT_SIM_SIGNING_SECRET` to the new value, on THIS
   (receiving) service. Update signal-portfolio-commercial's own
   caller to sign with the new secret. During the overlap, this route
   accepts both the new secret (already-updated caller) and the old
   one (a caller that has not yet picked up the change), so real
   traffic never fails verification during the switch.
2. Once signal-portfolio-commercial is confirmed to be signing with
   the new secret, unset `CATALOG_FIT_SIM_SIGNING_SECRET_PREVIOUS`
   here. A signature made with the old secret is rejected from that
   point on.

`CATALOG_FIT_SIM_SIGNING_SECRET`/
`CATALOG_FIT_SIM_SIGNING_SECRET_PREVIOUS` remain placeholders for
local/test use only; a real deployment sets and rotates them through a
secrets manager, not a repo default, and keeps
`CATALOG_FIT_SIM_SIGNING_SECRET` in sync with signal-portfolio-
commercial's own identically-named setting.
"""
from __future__ import annotations

import hashlib
import hmac
import time

_AUDIENCE = "catalog-fit-sim-v1"
_DEFAULT_TOLERANCE_SECONDS = 60


class InvalidCatalogFitSimSignatureHeaderError(Exception):
    pass


class CatalogFitSimSignatureMismatchError(Exception):
    pass


class StaleCatalogFitSimTimestampError(Exception):
    pass


def _parse_signature_header(sig_header: str) -> tuple[int, str]:
    """Same "t=<unix ts>,v1=<hex hmac>" shape as
    app/relay_worker.py's own header -- fail closed on anything that
    doesn't contain both a parseable `t` and a `v1`."""
    parts: dict[str, str] = {}
    for item in sig_header.split(","):
        if "=" not in item:
            continue
        key, _, value = item.partition("=")
        parts[key.strip()] = value.strip()

    if "t" not in parts or "v1" not in parts:
        raise InvalidCatalogFitSimSignatureHeaderError(
            f"catalog fit-sim signature header missing t= or v1=: {sig_header!r}"
        )
    try:
        timestamp = int(parts["t"])
    except ValueError as exc:
        raise InvalidCatalogFitSimSignatureHeaderError(
            f"non-integer timestamp in catalog fit-sim signature header: {parts['t']!r}"
        ) from exc

    return timestamp, parts["v1"]


def sign_catalog_fit_sim_request(payload: bytes, secret: str, *, timestamp: int | None = None) -> str:
    """Builds the `X-Catalog-Fit-Sim-Signature` header value the caller
    (signal-portfolio-commercial's own backend) sends. Used by that
    service's own client code and by this module's own tests -- never by
    this service itself, which only ever verifies."""
    ts = timestamp if timestamp is not None else int(time.time())
    signed_payload = f"{ts}.{_AUDIENCE}.".encode() + payload
    signature = hmac.new(secret.encode(), signed_payload, hashlib.sha256).hexdigest()
    return f"t={ts},v1={signature}"


def verify_catalog_fit_sim_signature(
    payload: bytes,
    sig_header: str,
    secret: str,
    *,
    secret_previous: str | None = None,
    tolerance_seconds: int = _DEFAULT_TOLERANCE_SECONDS,
    now: float | None = None,
) -> None:
    """Raises on any failure -- a malformed header, a signature that
    doesn't match (wrong secret, tampered body, or a signature computed
    for a different audience/scheme entirely), or a timestamp outside the
    replay-tolerance window. Returns None so a caller cannot accidentally
    ignore a False result and proceed anyway.

    `secret` (CURRENT) is checked first. If it does not match and
    `secret_previous` (PREVIOUS) is a non-empty string, that secret is
    checked too -- this is the whole of the rotation window described
    in this module's own docstring: a signature must still
    cryptographically match one of the two real configured secrets,
    there is no bypass. Both checks use `hmac.compare_digest`."""
    timestamp, provided_signature = _parse_signature_header(sig_header)

    signed_payload = f"{timestamp}.{_AUDIENCE}.".encode() + payload
    expected_signature = hmac.new(secret.encode(), signed_payload, hashlib.sha256).hexdigest()
    matched = hmac.compare_digest(expected_signature, provided_signature)
    if not matched and secret_previous:
        expected_signature_previous = hmac.new(
            secret_previous.encode(), signed_payload, hashlib.sha256
        ).hexdigest()
        matched = hmac.compare_digest(expected_signature_previous, provided_signature)
    if not matched:
        raise CatalogFitSimSignatureMismatchError(
            "catalog fit-sim signature does not match the expected value for this payload"
        )

    current_time = now if now is not None else time.time()
    if abs(current_time - timestamp) > tolerance_seconds:
        raise StaleCatalogFitSimTimestampError(
            f"catalog fit-sim timestamp {timestamp} is outside the {tolerance_seconds}s replay-tolerance "
            f"window (current time {current_time})"
        )
